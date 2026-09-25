"""Twilio outbound calls with a fixed script (PLAN.md section 12.1).

* The broker, not the agent, builds the TwiML: the owner-approved disclosure first, then
  the owner-approved script (the agent may only fill in ``{name}``) inside a bounded
  speech gather.
* Every callback must carry a valid ``X-Twilio-Signature`` computed over the original
  public URL (``CQ_PUBLIC_BASE_URL`` + path + query), and its CallSid must match. A
  signed callback that arrives before the broker has recorded Twilio's answer (the call
  is still ``CREATING`` or ``IN_DOUBT``) adopts its CallSid, so the callee still hears
  the disclosure. Rejected callbacks are audited at most once a minute per reason.
* A call is keyed by (run, idempotency key). A retry returns the same call and never
  redials. If Twilio's answer to the create request is lost, the call is ``IN_DOUBT``:
  the agent gets ``OUTCOME_UNKNOWN`` and the call is never retried automatically.
* ``maxCalls`` is enforced under a per-run advisory lock, so concurrent requests with
  different idempotency keys cannot overshoot it.
* Full phone numbers are used once to place the call and are never stored or logged.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, NoReturn
from xml.sax.saxutils import escape, quoteattr

import httpx
from sqlalchemy import func, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_broker.config import BrokerSettings
from crewquarters_broker.errors import needs_connection, permission_denied
from crewquarters_broker.metrics import BrokerMetrics
from crewquarters_broker.models import TelephonyCall
from crewquarters_secret_store import Keyring, SecretStoreError
from crewquarters_secret_store import db as secret_db
from crewquarters_secret_store.db import ProviderProfile
from crewquarters_shared import audit
from crewquarters_shared.errors import PlatformError, invalid, not_found
from crewquarters_shared.redaction import mask_phone
from crewquarters_shared.timeutil import utcnow

log = logging.getLogger("crewquarters.broker.twilio")

API_URL = "https://api.twilio.com/2010-04-01"
CALLBACK_PATH = "/api/v1/callbacks/twilio"
DISCLOSURE = "Hello. This is an automated demo call from Crewquarters."  # when none is configured
E164 = re.compile(r"^\+[1-9]\d{7,14}$")
ACCOUNT_SID = re.compile(r"^AC[0-9a-fA-F]{32}$")
CALL_SID = re.compile(r"^CA[0-9a-fA-F]{32}$")
TERMINAL = frozenset({"completed", "busy", "no-answer", "failed", "canceled"})
_RANK = {"CREATING": 0, "IN_DOUBT": 0, "queued": 1, "initiated": 2, "ringing": 3, "in-progress": 4}
# Internal states reported in Twilio's vocabulary (broker-sdk.openapi.yaml, Call.state).
_REPORTED = {"CREATING": "queued", "IN_DOUBT": "failed"}
MAX_TRANSCRIPT = 500
TEST_CALL_MESSAGE = (
    "This is a Crewquarters test call confirming that calling works. No reply is needed. Goodbye."
)
TEST_CALL_INTERVAL_SECONDS = 60.0
# First key of the per-run advisory lock that serializes call creation ("CQ" + 1).
CALL_LOCK_NAMESPACE = 0x43510001
# Rejected callbacks write at most one audit row per reason in this window, with a count.
REJECTION_AUDIT_SECONDS = 60.0
# Decrypted credentials used only to check callback signatures are reused this long.
CALLBACK_CREDENTIALS_SECONDS = 60.0


def signature(auth_token: str, url: str, params: Mapping[str, str]) -> str:
    """Twilio's request signature: HMAC-SHA1 over the URL plus sorted POST params."""
    payload = url + "".join(f"{k}{params[k]}" for k in sorted(params))
    digest = hmac.new(auth_token.encode(), payload.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def voice_twiml(disclosure: str, script: str, gather_url: str, response_seconds: int) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?><Response>'
        f"<Say>{escape(disclosure)}</Say>"
        f'<Gather input="speech" method="POST" action={quoteattr(gather_url)} '
        f'timeout="{response_seconds}" speechTimeout="auto">'
        f"<Say>{escape(script)}</Say></Gather>"
        "<Say>No response was received. Goodbye.</Say></Response>"
    )


GATHER_TWIML = (
    '<?xml version="1.0" encoding="UTF-8"?><Response>'
    "<Say>Thank you. Goodbye.</Say><Hangup/></Response>"
)


def view(call: TelephonyCall) -> dict[str, Any]:
    """The contract's ``Call``; the full number is never returned."""
    return {
        "id": str(call.id),
        "idempotencyKey": call.idempotency_key,
        "toMasked": f"***{call.destination_last4}",
        "state": _REPORTED.get(call.state, call.state),
        "answered": call.state in ("in-progress", "completed") or call.transcript is not None,
        "speechCaptured": call.transcript is not None,
        "transcript": call.transcript,
        "durationSeconds": call.duration_seconds,
        "errorCode": call.error_code,
        "createdAt": call.created_at,
        "updatedAt": call.updated_at,
    }


def outcome_unknown(call_id: uuid.UUID) -> PlatformError:
    return PlatformError(
        "OUTCOME_UNKNOWN",
        "Twilio did not confirm the call. It will not be retried automatically.",
        503,
        {"callId": str(call_id)},
    )


class TelephonyService:
    def __init__(
        self,
        settings: BrokerSettings,
        keyring: Keyring,
        sessions: async_sessionmaker[AsyncSession],
        http: httpx.AsyncClient,
        metrics: BrokerMetrics,
    ) -> None:
        self.settings = settings
        self.keyring = keyring
        self.sessions = sessions
        self.http = http
        self.metrics = metrics
        self._last_test_call = -TEST_CALL_INTERVAL_SECONDS
        # reason -> (monotonic time of the last audit row, rejections since that row)
        self._rejections: dict[str, tuple[float, int]] = {}
        self._callback_creds: tuple[float, dict[str, str]] | None = None
        # Set by the fake provider mode to simulate Twilio's callbacks.
        self.on_created: Callable[[uuid.UUID, str, str], Awaitable[None]] | None = None

    # --- Credentials ----------------------------------------------------------------

    async def configure(
        self, user_id: uuid.UUID, account_sid: str, auth_token: str, from_number: str
    ) -> dict[str, Any]:
        """Save (or replace) the Twilio account, then validate it without placing a call."""
        if not ACCOUNT_SID.match(account_sid):
            raise invalid("INVALID_INPUT", "The Twilio account SID is not valid.")
        if not E164.match(from_number):
            raise invalid("INVALID_INPUT", "The caller number must be in E.164 format.")
        if not 16 <= len(auth_token) <= 128:
            raise invalid("INVALID_INPUT", "The Twilio auth token is not valid.")
        plaintext = json.dumps(
            {"accountSid": account_sid, "authToken": auth_token, "fromNumber": from_number}
        ).encode()
        async with self.sessions() as db:
            profile = await self._profile(db)
            if profile is None:
                profile = ProviderProfile(
                    id=uuid.uuid4(), owner_id=user_id, provider="twilio", display_name="Twilio"
                )
                db.add(profile)
                await db.flush()
                secret = await secret_db.store(
                    db,
                    self.keyring,
                    provider="twilio",
                    owner_type="provider_profile",
                    owner_id=profile.id,
                    plaintext=plaintext,
                )
                profile.encrypted_secret_id = secret.id
            else:
                assert profile.encrypted_secret_id is not None
                await secret_db.replace(db, self.keyring, profile.encrypted_secret_id, plaintext)
            profile.settings = {"accountSidSuffix": account_sid[-4:], "fromLast4": from_number[-4:]}
            profile.status = "UNTESTED"
            audit.record(
                db,
                action="connection.twilio.saved",
                actor_type="user",
                actor_id=user_id,
                target_type="provider_profile",
                target_id=profile.id,
            )
            await db.commit()
        self._callback_creds = None
        return await self.test()

    async def test(self) -> dict[str, Any]:
        creds = await self._credentials()
        try:
            resp = await self.http.get(
                f"{API_URL}/Accounts/{creds['accountSid']}.json",
                auth=(creds["accountSid"], creds["authToken"]),
            )
            ok = resp.status_code == 200
        except httpx.HTTPError:
            ok = False
        async with self.sessions() as db:
            profile = await self._profile(db)
            assert profile is not None
            profile.status = "CONNECTED" if ok else "ERROR"
            profile.last_checked_at = utcnow()
            await db.commit()
        return await self.status()

    async def test_call(self, user_id: uuid.UUID, to: str) -> dict[str, Any]:
        """Place one call that speaks a fixed test message (PLAN.md section 13.10). The owner
        confirms it in the UI; it is not tied to a run, needs no callbacks, and is limited
        to one a minute and, in live mode, to the allowed numbers."""
        self._check_destination(to)
        now = time.monotonic()
        if now - self._last_test_call < TEST_CALL_INTERVAL_SECONDS:
            raise PlatformError(
                "RATE_LIMITED",
                "Wait a minute between test calls.",
                429,
                {"retryAfterSeconds": int(TEST_CALL_INTERVAL_SECONDS)},
            )
        creds = await self._credentials()
        self._last_test_call = now
        twiml = (
            '<?xml version="1.0" encoding="UTF-8"?><Response>'
            f"<Say>{escape(TEST_CALL_MESSAGE)}</Say><Hangup/></Response>"
        )
        try:
            resp = await self.http.post(
                f"{API_URL}/Accounts/{creds['accountSid']}/Calls.json",
                data={"To": to, "From": creds["fromNumber"], "Twiml": twiml, "Timeout": "30"},
                auth=(creds["accountSid"], creds["authToken"]),
            )
        except httpx.HTTPError:
            raise PlatformError("PROVIDER_UNAVAILABLE", "Twilio is unreachable.", 503) from None
        placed = resp.status_code < 300
        async with self.sessions() as db:
            profile = await self._profile(db)
            assert profile is not None
            profile.status = "CONNECTED" if placed else "ERROR"
            profile.last_checked_at = utcnow()
            audit.record(
                db,
                action="connection.twilio.test_call",
                actor_type="user",
                actor_id=user_id,
                target_type="provider_profile",
                target_id=profile.id,
                outcome="success" if placed else "failure",
                metadata={"to": mask_phone(to), "providerStatus": resp.status_code},
            )
            await db.commit()
        if not placed:
            raise PlatformError(
                "PROVIDER_ERROR",
                "Twilio did not accept the test call.",
                502,
                {"providerStatus": resp.status_code},
            )
        return {"placed": True, "to": mask_phone(to), "status": resp.json().get("status")}

    def _check_destination(self, to: str) -> None:
        if not E164.match(to):
            raise invalid("INVALID_REQUEST", "The destination must be an E.164 phone number.")
        if self.settings.provider_mode == "live" and to not in self.settings.twilio_allowed_numbers:
            raise permission_denied(
                "Calls are limited to verified numbers in CQ_TWILIO_ALLOWED_NUMBERS.",
                to=mask_phone(to),
            )

    async def disconnect(self, user_id: uuid.UUID) -> None:
        async with self.sessions() as db:
            profile = await self._profile(db)
            if profile is None:
                return
            secret_id = profile.encrypted_secret_id
            await db.delete(profile)
            await db.flush()
            if secret_id is not None:
                await secret_db.delete(db, secret_id)
            audit.record(
                db,
                action="connection.twilio.disconnected",
                actor_type="user",
                actor_id=user_id,
                target_type="provider_profile",
                target_id=profile.id,
            )
            await db.commit()
        self._callback_creds = None

    async def status(self) -> dict[str, Any]:
        async with self.sessions() as db:
            profile = await self._profile(db)
        if profile is None:
            return {"status": "NOT_CONNECTED", "grantedCapabilities": [], "lastCheckedAt": None}
        if not profile.enabled:
            status = "DISABLED"
        else:
            status = "CONNECTED" if profile.status == "CONNECTED" else "NEEDS_ATTENTION"
        return {
            "status": status,
            "grantedCapabilities": ["call.fixed_script"] if status == "CONNECTED" else [],
            "lastCheckedAt": profile.last_checked_at,
            "account": f"AC…{profile.settings.get('accountSidSuffix', '')}",
            "fromNumber": f"***{profile.settings.get('fromLast4', '')}",
        }

    # --- Calls ------------------------------------------------------------------------

    async def create_call(
        self,
        *,
        run_id: uuid.UUID,
        idempotency_key: str,
        to: str,
        disclosure: str,
        script: str,
        response_seconds: int,
        max_calls: int,
    ) -> dict[str, Any]:
        self._check_destination(to)
        creds = await self._credentials()
        async with self.sessions() as db:
            # Serialize creation per run until this transaction commits, so the count below
            # cannot race another request's insert (distinct keys would overshoot maxCalls).
            await db.execute(
                text("SELECT pg_advisory_xact_lock(:namespace, hashtext(:run))"),
                {"namespace": CALL_LOCK_NAMESPACE, "run": str(run_id)},
            )
            existing = await self._by_key(db, run_id, idempotency_key)
            if existing is not None:
                if existing.state == "IN_DOUBT":
                    raise outcome_unknown(existing.id)
                return view(existing)
            count = await db.scalar(
                select(func.count())
                .select_from(TelephonyCall)
                .where(TelephonyCall.run_id == run_id)
            )
            if (count or 0) >= max_calls:
                raise PlatformError(
                    "CALL_LIMIT_REACHED", f"This run may place at most {max_calls} calls.", 409
                )
            call = TelephonyCall(
                id=uuid.uuid4(),
                run_id=run_id,
                idempotency_key=idempotency_key,
                destination_hash=self._destination_hash(to),
                destination_last4=to[-4:],
                disclosure=disclosure,
                script=script,
                response_seconds=response_seconds,
                state="CREATING",
            )
            db.add(call)
            try:
                await db.commit()
            except IntegrityError:  # a concurrent retry with the same key won the insert
                await db.rollback()
                winner = await self._by_key(db, run_id, idempotency_key)
                assert winner is not None
                return view(winner)
        base = f"{self.settings.twilio_base_url()}{CALLBACK_PATH}"
        form = {
            "To": to,
            "From": creds["fromNumber"],
            "Url": f"{base}/voice/{call.id}",
            "Method": "POST",
            "StatusCallback": f"{base}/status/{call.id}",
            "StatusCallbackMethod": "POST",
            "StatusCallbackEvent": ["initiated", "ringing", "answered", "completed"],
            "Timeout": "30",
        }
        try:
            resp = await self.http.post(
                f"{API_URL}/Accounts/{creds['accountSid']}/Calls.json",
                data=form,
                auth=(creds["accountSid"], creds["authToken"]),
            )
        except httpx.HTTPError:
            return await self._in_doubt(call.id)
        if resp.status_code >= 500:  # Twilio may or may not have placed it
            return await self._in_doubt(call.id)
        if resp.status_code >= 400:  # rejected: no call was placed
            log.warning("twilio rejected call %s with status %s", call.id, resp.status_code)
            return await self._set(call.id, state="failed", error_code="PROVIDER_REJECTED")
        body = resp.json()
        sid, state = str(body["sid"]), str(body.get("status") or "queued")
        result = await self._created(call.id, sid, state)
        if self.on_created is not None:
            await self.on_created(call.id, sid, to)
        return result

    async def get_call(self, run_id: uuid.UUID, call_id: uuid.UUID) -> dict[str, Any]:
        async with self.sessions() as db:
            call = await db.get(TelephonyCall, call_id)
        if call is None or call.run_id != run_id:
            raise not_found("Call", call_id)
        return view(call)

    # --- Callbacks ----------------------------------------------------------------------

    async def verify_callback(
        self, path_and_query: str, params: Mapping[str, str], sent_signature: str | None
    ) -> None:
        # Cheap checks first: a request without a well-formed signature costs no decrypt.
        if not sent_signature:
            await self._reject("missing_signature", path_and_query)
        elif not _well_formed(sent_signature):
            await self._reject("malformed_signature", path_and_query)
        creds = await self._signing_credentials()
        url = f"{self.settings.twilio_base_url()}{path_and_query}"
        expected = signature(creds["authToken"], url, params)
        if not hmac.compare_digest(expected, sent_signature or ""):
            await self._reject("bad_signature", path_and_query)

    async def _reject(self, reason: str, path_and_query: str) -> NoReturn:
        """Refuse a callback. Every rejection is counted in metrics, but the audit log gets
        at most one row per reason per ``REJECTION_AUDIT_SECONDS``, carrying the number of
        rejections since the previous row, so forged traffic cannot flood it."""
        self.metrics.callback_rejections.labels("twilio").inc()
        now = time.monotonic()
        last, pending = self._rejections.get(reason, (-REJECTION_AUDIT_SECONDS, 0))
        pending += 1
        if now - last >= REJECTION_AUDIT_SECONDS:
            self._rejections[reason] = (now, 0)
            async with self.sessions() as db:
                audit.record(
                    db,
                    action="callback.twilio.rejected",
                    actor_type="anonymous",
                    outcome="denied",
                    metadata={"reason": reason, "count": pending, "path": path_and_query},
                )
                await db.commit()
        else:
            self._rejections[reason] = (last, pending)
        raise PlatformError("SIGNATURE_INVALID", "Invalid Twilio signature.", 403)

    async def voice(self, call_id: uuid.UUID, params: Mapping[str, str]) -> str:
        call = await self._callback_call(call_id, params)
        gather_url = f"{self.settings.twilio_base_url()}{CALLBACK_PATH}/gather/{call.id}"
        return voice_twiml(call.disclosure, call.script, gather_url, call.response_seconds)

    async def gather(self, call_id: uuid.UUID, params: Mapping[str, str]) -> str:
        await self._callback_call(call_id, params)
        speech = params.get("SpeechResult", "").strip()[:MAX_TRANSCRIPT]
        async with self.sessions() as db:
            call = await db.get(TelephonyCall, call_id, with_for_update=True)
            assert call is not None
            if call.transcript is None and speech:  # the first answer wins
                call.transcript = speech
            await db.commit()
        return GATHER_TWIML

    async def status_callback(self, call_id: uuid.UUID, params: Mapping[str, str]) -> None:
        await self._callback_call(call_id, params)
        new = params.get("CallStatus", "")
        async with self.sessions() as db:
            call = await db.get(TelephonyCall, call_id, with_for_update=True)
            assert call is not None
            if _advances(call.state, new):
                call.state = new
                duration = params.get("CallDuration", "")
                if duration.isdigit():
                    call.duration_seconds = int(duration)
                if new == "failed":
                    call.error_code = params.get("ErrorCode") or "CALL_FAILED"
            await db.commit()

    # --- Internals ------------------------------------------------------------------------

    async def _callback_call(self, call_id: uuid.UUID, params: Mapping[str, str]) -> TelephonyCall:
        """The call a signature-checked callback is for.

        Twilio can call back before the broker has stored its answer to the create request,
        or after that answer was lost (``IN_DOUBT``). The call id in the callback URL names
        exactly one call, so a callback for a call without a CallSid adopts Twilio's."""
        sid = params.get("CallSid", "")
        async with self.sessions() as db:
            call = await db.get(TelephonyCall, call_id)
            if (
                call is not None
                and call.provider_sid is None
                and call.state in ("CREATING", "IN_DOUBT")
                and CALL_SID.match(sid)
                and params.get("To", "")[-4:] in ("", call.destination_last4)
            ):
                call = await self._adopt(db, call_id, sid)
        if call is None or not call.provider_sid or sid != call.provider_sid:
            raise not_found("Call", call_id)
        return call

    async def _adopt(self, db: AsyncSession, call_id: uuid.UUID, sid: str) -> TelephonyCall | None:
        try:
            await db.execute(
                update(TelephonyCall)
                .where(TelephonyCall.id == call_id, TelephonyCall.provider_sid.is_(None))
                .values(provider_sid=sid, state="queued", error_code=None)
            )
            await db.commit()
        except IntegrityError:  # the CallSid already belongs to another call
            await db.rollback()
        else:
            log.info("call %s adopted its CallSid from a callback", call_id)
        db.expunge_all()
        return await db.get(TelephonyCall, call_id)

    async def _created(self, call_id: uuid.UUID, sid: str, state: str) -> dict[str, Any]:
        """Record Twilio's answer. A callback may already have adopted the CallSid and moved
        the call on, so the state only ever advances."""
        async with self.sessions() as db:
            call = await db.get(TelephonyCall, call_id, with_for_update=True)
            assert call is not None
            if call.provider_sid is None:
                call.provider_sid = sid
            elif call.provider_sid != sid:
                log.warning("twilio answered call %s with a different CallSid", call_id)
            if _advances(call.state, state):
                call.state = state
            await db.commit()
            await db.refresh(call)
            return view(call)

    async def _in_doubt(self, call_id: uuid.UUID) -> dict[str, Any]:
        """The create request's outcome is unknown: mark the call ``IN_DOUBT`` and raise
        ``OUTCOME_UNKNOWN``, unless a callback has already shown that Twilio placed it."""
        async with self.sessions() as db:
            await db.execute(
                update(TelephonyCall)
                .where(TelephonyCall.id == call_id, TelephonyCall.provider_sid.is_(None))
                .values(state="IN_DOUBT", error_code="OUTCOME_UNKNOWN")
            )
            await db.commit()
            call = await db.get(TelephonyCall, call_id)
        assert call is not None
        if call.state == "IN_DOUBT":
            raise outcome_unknown(call_id)
        return view(call)

    async def _signing_credentials(self) -> dict[str, str]:
        """Credentials for signature checks, reused briefly so that a flood of forged
        callbacks does not decrypt the secret for every request."""
        now = time.monotonic()
        if self._callback_creds is not None and self._callback_creds[0] > now:
            return self._callback_creds[1]
        creds = await self._credentials()
        self._callback_creds = (now + CALLBACK_CREDENTIALS_SECONDS, creds)
        return creds

    async def _credentials(self) -> dict[str, str]:
        async with self.sessions() as db:
            profile = await self._profile(db)
            if profile is None or profile.encrypted_secret_id is None or not profile.enabled:
                raise needs_connection("twilio", "Configure Twilio in Connections.")
            try:
                raw = await secret_db.load(
                    db, self.keyring, profile.encrypted_secret_id, provider="twilio"
                )
            except SecretStoreError:  # e.g. its master key version is no longer loaded
                log.error("the saved twilio credentials cannot be decrypted")
                raise needs_connection(
                    "twilio", "The saved Twilio credentials cannot be read. Save them again."
                ) from None
        creds: dict[str, str] = json.loads(raw)
        return creds

    async def _profile(self, db: AsyncSession) -> ProviderProfile | None:
        return (
            await db.scalars(select(ProviderProfile).where(ProviderProfile.provider == "twilio"))
        ).first()

    async def _by_key(self, db: AsyncSession, run_id: uuid.UUID, key: str) -> TelephonyCall | None:
        return (
            await db.scalars(
                select(TelephonyCall).where(
                    TelephonyCall.run_id == run_id, TelephonyCall.idempotency_key == key
                )
            )
        ).first()

    async def _set(self, call_id: uuid.UUID, **fields: Any) -> dict[str, Any]:
        async with self.sessions() as db:
            call = await db.get(TelephonyCall, call_id)
            assert call is not None
            for name, value in fields.items():
                setattr(call, name, value)
            await db.commit()
            await db.refresh(call)
            return view(call)

    def _destination_hash(self, number: str) -> str:
        key = self.settings.secret_key.get_secret_value().encode()
        return hmac.new(key, b"cq-phone-v1|" + number.encode(), hashlib.sha256).hexdigest()


def _well_formed(sent: str) -> bool:
    """Twilio's signature is the base64 of a 20-byte HMAC-SHA1."""
    try:
        return len(base64.b64decode(sent, validate=True)) == 20
    except ValueError:
        return False


def _advances(current: str, new: str) -> bool:
    """Callbacks can arrive late or twice: never move backwards or leave a terminal state."""
    if current in TERMINAL or not new:
        return False
    if new in TERMINAL:
        return True
    return _RANK.get(new, -1) > _RANK.get(current, 0)
