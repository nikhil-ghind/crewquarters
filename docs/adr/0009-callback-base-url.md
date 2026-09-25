# ADR 0009: One source for the callback base URL

- Status: Accepted
- Date: 2026-09-25
- Owner: Nikhil Hiro Ghind (Person 1)

## Context

OAuth and Twilio callbacks reach the device through one public HTTPS origin (PLAN.md section 14.3). Two places described that origin:

- The capability broker builds the Google redirect URI and the Twilio voice, gather and status URLs from `CQ_PUBLIC_BASE_URL`.
- The control API stored an owner-editable `callbackBaseUrl` setting (PLAN.md section 5.1, "Settings").

The two could disagree, and the setting changed nothing: the broker never read it.

A runtime-editable value is also unsafe here:

- Google rejects any redirect URI that isn't registered exactly.
- Twilio signs the exact callback URL, and the broker checks the signature against the URL it built.
- The reverse proxy and tunnel must route that hostname to the broker.

Changing the origin from the UI alone would break sign-in and calls, and the cause would not be visible.

## Decision

`CQ_PUBLIC_BASE_URL` is the only source.

- It is set by the installer, on both the control API and the broker.
- The broker keeps building callback URLs from it.
- `GET /api/v1/settings` returns it read-only as `callbackBaseUrl`. It also returns `callbackUrls` (`googleRedirectUri`, `twilioCallbackBase`), so the setup wizard can show the exact values to register.
- `PATCH /api/v1/settings` with `callbackBaseUrl` returns `422 SETTING_READ_ONLY`. The field remains in the request schema, marked deprecated, so older clients get a clear error instead of a silently ignored value. Any stored `callbackBaseUrl` row is ignored.

## Alternatives considered

The broker could read the setting from the control API or the `settings` table each time it is used, falling back to the environment variable.

- This would need changes in the broker's Google and Twilio code paths.
- It would add a control-API dependency to every Twilio webhook.
- It would still leave the Google registration, the proxy and the tunnel out of sync after a UI edit.

Changing the origin is an installation-time task: register the new URI and update the tunnel. It stays in the environment.

## Consequences

- Changing the callback origin means editing `CQ_PUBLIC_BASE_URL` and restarting the control API and the broker.
- The UI shows the value and cannot edit it.
- The deployment workstream must set the same `CQ_PUBLIC_BASE_URL` on both services.
