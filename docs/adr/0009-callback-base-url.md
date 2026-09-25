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

## Revision (2026-09-25): separate Twilio callback origin

### Problem

One origin could not serve both providers:

- **Google** redirects the owner's **browser**. The redirect must reach the origin where the OAuth binding cookie was set: the address the owner uses, such as `http://localhost:8080` or the LAN HTTPS name.
- **Twilio** calls from the internet. It needs the public HTTPS **tunnel** origin.

With a single `CQ_PUBLIC_BASE_URL`, pointing it at the tunnel for Twilio moved the Google redirect to the tunnel hostname, and Google sign-in failed the browser-binding check.

### Decision

- **New setting.** `CQ_TWILIO_CALLBACK_BASE_URL` is optional. When it is unset or empty, `CQ_PUBLIC_BASE_URL` is used, so existing installations behave as before.
- **What each setting controls.**
  - The broker builds the Twilio voice, gather and status URLs from `CQ_TWILIO_CALLBACK_BASE_URL`, and validates Twilio signatures against it.
  - `CQ_PUBLIC_BASE_URL` controls only the Google redirect URI and the post-OAuth return to the UI.
  - Both come from `Settings.twilio_base_url()` in `crewquarters_shared.config`.
- **Settings API.** `GET /api/v1/settings` still returns `callbackBaseUrl` (from `CQ_PUBLIC_BASE_URL`). `callbackUrls.googleRedirectUri` comes from `CQ_PUBLIC_BASE_URL`, and `callbackUrls.twilioCallbackBase` comes from the Twilio origin. Both remain read-only. The response shape is unchanged; only field descriptions changed.
- **Browser origins.** The tunnel origin is not added to the allowed browser origins. Only callbacks use it.
- **Unchanged.** Both values are environment-only, for the reasons above. They must be the same on the control API and the broker (`compose.appliance.yaml` passes both to every platform service).

### Consequences

- The tunnel no longer affects Google sign-in: set `CQ_TWILIO_CALLBACK_BASE_URL=https://<tunnel host>` and leave `CQ_PUBLIC_BASE_URL` on the browser's origin.
- LAN HTTPS mode (docs/runbooks/lan-https.md) changes `CQ_PUBLIC_BASE_URL` to `https://NAME.local` without affecting Twilio.
- Google Cloud accepts only `localhost` or a public domain name as a redirect URI. On a headless device that is used over the LAN, the Google redirect therefore needs a real DNS name (see the runbook).
