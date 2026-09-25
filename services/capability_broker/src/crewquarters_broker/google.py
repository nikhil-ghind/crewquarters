"""Google OAuth (web-server flow) and the narrow Gmail and Sheets operations.

PLAN.md section 10.3. Only ``gmail.readonly`` and ``spreadsheets`` are ever requested.
Security properties:

* ``state`` is 256 random bits, single use, and expires after ten minutes; only its
  hash is kept. PKCE (S256) binds the code to this broker.
* A second random value, the browser binding, is returned to the control API to set as
  an HttpOnly cookie. The callback requires it, so an attacker cannot make the owner's
  browser complete the attacker's authorization (login CSRF).
* The redirect URI is exact: ``CQ_PUBLIC_BASE_URL`` + the fixed callback path.
* Refresh tokens are envelope-encrypted; access tokens live only in memory.
* One Google account per device: connecting again replaces the previous connection.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
import time
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from crewquarters_broker import mail
from crewquarters_broker.config import BrokerSettings
from crewquarters_broker.errors import needs_connection, permission_denied, provider_error
from crewquarters_broker.models import OAuthConnection
from crewquarters_secret_store import Keyring
from crewquarters_secret_store import db as secret_db
from crewquarters_shared import audit
from crewquarters_shared.errors import PlatformError, invalid, not_found
from crewquarters_shared.timeutil import utcnow

log = logging.getLogger("crewquarters.broker.google")

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - endpoint, not a secret
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
GMAIL_URL = "https://gmail.googleapis.com/gmail/v1/users/me"
SHEETS_URL = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPES = {
    "gmail.readonly": "https://www.googleapis.com/auth/gmail.readonly",
    "spreadsheets": "https://www.googleapis.com/auth/spreadsheets",
}
STATE_TTL_SECONDS = 600
EXPIRY_MARGIN_SECONDS = 60
RECONNECT = "Google access expired or was revoked. Reconnect Google in Connections."


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@dataclass(frozen=True)
class _Pending:
    user_id: uuid.UUID
    code_verifier: str
    binding_hash: str
    expires_at: float


class GoogleConnector:
    def __init__(
        self,
        settings: BrokerSettings,
        keyring: Keyring,
        sessions: async_sessionmaker[AsyncSession],
        http: httpx.AsyncClient,
    ) -> None:
        self.settings = settings
        self.keyring = keyring
        self.sessions = sessions
        self.http = http
        self._pending: dict[str, _Pending] = {}
        self._access: dict[uuid.UUID, tuple[str, float]] = {}

    # --- Connect ------------------------------------------------------------------

    def start(self, user_id: uuid.UUID, capabilities: list[str]) -> dict[str, str]:
        """Begin consent for the given capabilities (incremental: earlier grants are kept)."""
        if not capabilities or not set(capabilities) <= SCOPES.keys():
            raise invalid("INVALID_SCOPE", "Choose gmail.readonly and/or spreadsheets.")
        if not self.settings.google_client_id:
            raise PlatformError("NOT_CONFIGURED", "Google OAuth client is not configured.", 409)
        now = time.monotonic()
        self._pending = {k: p for k, p in self._pending.items() if p.expires_at > now}
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        binding = secrets.token_urlsafe(32)
        self._pending[_hash(state)] = _Pending(
            user_id, verifier, _hash(binding), now + STATE_TTL_SECONDS
        )
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest())
        params = {
            "client_id": self.settings.google_client_id,
            "redirect_uri": self.settings.google_redirect_uri,
            "response_type": "code",
            "scope": " ".join(SCOPES[c] for c in sorted(set(capabilities))),
            "access_type": "offline",
            "include_granted_scopes": "true",
            "prompt": "consent",
            "state": state,
            "code_challenge": challenge.rstrip(b"=").decode(),
            "code_challenge_method": "S256",
        }
        return {"authorizationUrl": f"{AUTH_URL}?{urlencode(params)}", "browserBinding": binding}

    async def callback(
        self, *, state: str, code: str | None, error: str | None, binding: str | None
    ) -> uuid.UUID:
        """Finish consent. Returns the connection id. The state is consumed even on failure."""
        pending = self._pending.pop(_hash(state), None)
        if pending is None or pending.expires_at <= time.monotonic():
            raise PlatformError(
                "OAUTH_STATE_INVALID", "This sign-in link expired or was used.", 400
            )
        if binding is None or not hmac.compare_digest(_hash(binding), pending.binding_hash):
            raise PlatformError(
                "OAUTH_STATE_INVALID", "Start Google sign-in from this browser.", 400
            )
        if error or not code:
            raise PlatformError("OAUTH_DENIED", "Google access was not granted.", 400)
        try:
            token = await self._token_request(
                {
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": self.settings.google_redirect_uri,
                    "code_verifier": pending.code_verifier,
                }
            )
        except _InvalidGrant:
            raise PlatformError(
                "OAUTH_CODE_INVALID", "Google rejected the sign-in code.", 400
            ) from None
        granted = sorted(
            n for n, url in SCOPES.items() if url in str(token.get("scope", "")).split()
        )
        refresh = token.get("refresh_token")
        if not granted or not isinstance(refresh, str):
            raise PlatformError("OAUTH_SCOPE_MISSING", "Google did not grant offline access.", 400)
        access = str(token["access_token"])
        subject = None
        if "gmail.readonly" in granted:
            profile = await self._api("GET", f"{GMAIL_URL}/profile", access)
            subject = profile.get("emailAddress")
        async with self.sessions() as db:
            for old in (
                await db.scalars(
                    select(OAuthConnection).where(OAuthConnection.provider == "google")
                )
            ).all():
                await self._delete(db, old)
            conn = OAuthConnection(
                id=uuid.uuid4(),
                user_id=pending.user_id,
                provider="google",
                provider_subject=subject,
                scopes=granted,
                status="CONNECTED",
                last_checked_at=utcnow(),
            )
            db.add(conn)
            await db.flush()
            secret = await secret_db.store(
                db,
                self.keyring,
                provider="google",
                owner_type="oauth_connection",
                owner_id=conn.id,
                plaintext=refresh.encode(),
            )
            conn.encrypted_secret_id = secret.id
            audit.record(
                db,
                action="connection.google.connected",
                actor_type="user",
                actor_id=pending.user_id,
                target_type="oauth_connection",
                target_id=conn.id,
                metadata={"scopes": granted},
            )
            await db.commit()
        self._access[conn.id] = (access, time.monotonic() + float(token.get("expires_in", 0)))
        return conn.id

    async def disconnect(self, user_id: uuid.UUID) -> None:
        async with self.sessions() as db:
            conn = await self._connection(db)
            if conn is None:
                return
            if conn.encrypted_secret_id is not None:
                refresh = await secret_db.load(
                    db, self.keyring, conn.encrypted_secret_id, provider="google"
                )
                try:
                    await self.http.post(REVOKE_URL, data={"token": refresh.decode()})
                except httpx.HTTPError:
                    log.warning("google token revocation failed; deleting locally")
            await self._delete(db, conn)
            audit.record(
                db,
                action="connection.google.disconnected",
                actor_type="user",
                actor_id=user_id,
                target_type="oauth_connection",
                target_id=conn.id,
            )
            await db.commit()

    async def test(self) -> dict[str, Any]:
        async with self.sessions() as db:
            conn = await self._connection(db)
            if conn is None:
                raise needs_connection("google", "Connect Google first.")
            self._access.pop(conn.id, None)
        await self._access_token()
        return await self.status()

    async def status(self) -> dict[str, Any]:
        async with self.sessions() as db:
            conn = await self._connection(db)
        if conn is None:
            return {"status": "NOT_CONNECTED", "grantedCapabilities": [], "lastCheckedAt": None}
        return {
            "status": conn.status,
            "grantedCapabilities": list(conn.scopes) if conn.status == "CONNECTED" else [],
            "lastCheckedAt": conn.last_checked_at,
            "account": conn.provider_subject,
            "detail": conn.status_detail,
        }

    # --- Gmail and Sheets ---------------------------------------------------------

    async def gmail_list(
        self, query: str, page_token: str | None, max_results: int
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"q": query, "maxResults": max_results}
        if page_token:
            params["pageToken"] = page_token
        data = await self._call("gmail.readonly", "GET", f"{GMAIL_URL}/messages", params=params)
        return {
            "messages": [
                {"id": m.get("id"), "threadId": m.get("threadId")}
                for m in data.get("messages") or []
            ],
            "nextPageToken": data.get("nextPageToken"),
        }

    async def gmail_get(self, message_id: str, max_chars: int) -> dict[str, Any]:
        data = await self._call(
            "gmail.readonly",
            "GET",
            f"{GMAIL_URL}/messages/{quote(message_id, safe='')}",
            params={"format": "full"},
        )
        return mail.sanitize(data, max_chars)

    async def sheets_read(self, spreadsheet_id: str, cell_range: str) -> dict[str, Any]:
        data = await self._call("spreadsheets", "GET", self._values_url(spreadsheet_id, cell_range))
        return {"range": data.get("range"), "values": data.get("values") or []}

    async def sheets_append(
        self, spreadsheet_id: str, cell_range: str, values: list[list[Any]]
    ) -> dict[str, Any]:
        data = await self._call(
            "spreadsheets",
            "POST",
            self._values_url(spreadsheet_id, cell_range) + ":append",
            params={"valueInputOption": "RAW", "insertDataOption": "INSERT_ROWS"},
            json={"values": values},
        )
        updates = data.get("updates") or {}
        return {
            "updatedRange": updates.get("updatedRange"),
            "updatedRows": updates.get("updatedRows"),
        }

    async def sheets_update(
        self, spreadsheet_id: str, cell_range: str, values: list[list[Any]]
    ) -> dict[str, Any]:
        data = await self._call(
            "spreadsheets",
            "PUT",
            self._values_url(spreadsheet_id, cell_range),
            params={"valueInputOption": "RAW"},
            json={"values": values},
        )
        return {"updatedRange": data.get("updatedRange"), "updatedRows": data.get("updatedRows")}

    # --- Internals ------------------------------------------------------------------

    @staticmethod
    def _values_url(spreadsheet_id: str, cell_range: str) -> str:
        # RAW input: values are stored as text, so a formula such as =IMPORTXML(...) coming
        # from untrusted content is never evaluated.
        return f"{SHEETS_URL}/{quote(spreadsheet_id, safe='')}/values/{quote(cell_range, safe='')}"

    async def _connection(self, db: AsyncSession) -> OAuthConnection | None:
        return (
            await db.scalars(select(OAuthConnection).where(OAuthConnection.provider == "google"))
        ).first()

    async def _delete(self, db: AsyncSession, conn: OAuthConnection) -> None:
        self._access.pop(conn.id, None)
        secret_id = conn.encrypted_secret_id
        await db.delete(conn)
        await db.flush()
        if secret_id is not None:
            await secret_db.delete(db, secret_id)

    async def _call(self, scope: str, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        token, conn_scopes = await self._access_token()
        if scope not in conn_scopes:
            raise needs_connection("google", f"Grant Google {scope} access in Connections.")
        try:
            return await self._api(method, url, token, **kwargs)
        except _Expired:
            token, _ = await self._access_token(force=True)
            return await self._api(method, url, token, **kwargs)

    async def _access_token(self, force: bool = False) -> tuple[str, list[str]]:
        async with self.sessions() as db:
            conn = await self._connection(db)
            if conn is None or conn.encrypted_secret_id is None:
                raise needs_connection("google", "Connect Google in Connections.")
            if conn.status != "CONNECTED":
                raise needs_connection("google", RECONNECT)
            cached = self._access.get(conn.id)
            if cached and not force and cached[1] - EXPIRY_MARGIN_SECONDS > time.monotonic():
                return cached[0], list(conn.scopes)
            refresh = await secret_db.load(
                db, self.keyring, conn.encrypted_secret_id, provider="google"
            )
            try:
                token = await self._token_request(
                    {"grant_type": "refresh_token", "refresh_token": refresh.decode()}
                )
            except _InvalidGrant:
                conn.status, conn.status_detail = "NEEDS_ATTENTION", RECONNECT
                self._access.pop(conn.id, None)
                audit.record(
                    db,
                    action="connection.google.expired",
                    actor_type="service",
                    actor_id="capability-broker",
                    target_type="oauth_connection",
                    target_id=conn.id,
                    outcome="failure",
                )
                await db.commit()
                raise needs_connection("google", RECONNECT) from None
            access = str(token["access_token"])
            self._access[conn.id] = (access, time.monotonic() + float(token.get("expires_in", 0)))
            conn.last_checked_at = utcnow()
            await db.commit()
            return access, list(conn.scopes)

    async def _token_request(self, data: dict[str, str]) -> dict[str, Any]:
        body = {
            **data,
            "client_id": self.settings.google_client_id,
            "client_secret": self.settings.google_client_secret.get_secret_value(),
        }
        try:
            resp = await self.http.post(TOKEN_URL, data=body)
        except httpx.HTTPError:
            raise PlatformError("PROVIDER_UNAVAILABLE", "Google is unreachable.", 503) from None
        if resp.status_code == 400 and _json(resp).get("error") == "invalid_grant":
            raise _InvalidGrant
        if resp.status_code >= 400:
            raise provider_error("Google", resp.status_code)
        return _json(resp)

    async def _api(self, method: str, url: str, token: str, **kwargs: Any) -> dict[str, Any]:
        try:
            resp = await self.http.request(
                method, url, headers={"authorization": f"Bearer {token}"}, **kwargs
            )
        except httpx.HTTPError:
            raise PlatformError("PROVIDER_UNAVAILABLE", "Google is unreachable.", 503) from None
        if resp.status_code == 401:
            raise _Expired
        if resp.status_code == 404:
            raise not_found("Google resource", url.rsplit("/", 1)[-1])
        if resp.status_code == 403:
            raise permission_denied("Google refused access to this resource.")
        if resp.status_code >= 400:
            raise provider_error("Google", resp.status_code)
        return _json(resp)


class _Expired(Exception):
    pass


class _InvalidGrant(Exception):
    pass


def _json(resp: httpx.Response) -> dict[str, Any]:
    try:
        data = resp.json()
    except ValueError:
        return {}
    return data if isinstance(data, dict) else {}
