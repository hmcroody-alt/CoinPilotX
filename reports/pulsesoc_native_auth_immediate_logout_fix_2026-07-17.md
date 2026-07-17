# PulseSoc Native Auth Immediate Logout Fix

Date: 2026-07-17

## Incident

Owner report: every login to the native app immediately returned to the signed-out experience.

This was treated as an authentication/session continuity defect, not a UI problem.

## Root Cause

The backend already issued a native mobile session envelope containing:

- canonical `user_id`
- mobile `access_token`
- rotating `refresh_token`
- expiry metadata

The native client persisted that envelope, but normal API requests only sent the persistent refresh cookie. The mobile access token was not sent as `Authorization: Bearer ...`.

That made signed-in startup requests depend on `restore_account_from_persistent_cookie()`. That server path can rotate the persistent refresh cookie while the app is launching multiple authenticated requests. A later request using stale session material could trigger session invalidation, and `App.tsx` would immediately switch the app to `signedOut`.

## Fix

Native:

- `mobile-native/src/api/pulseApi.ts` now loads the secure session envelope before requests.
- If the envelope has a non-expired `accessToken`, native sends it as `Authorization: Bearer <token>`.
- If the access token is missing or within the expiry buffer, native refreshes through the existing single-flight refresh path before protected requests and reloads the secure envelope.
- Existing cookie behavior remains as compatibility and refresh fallback.
- Existing 401 refresh and single-flight recovery remain unchanged.

Backend:

- `bot.py` now resolves `account_user_id()` from a valid mobile Bearer access token before falling back to persistent-cookie refresh.
- The token is HMAC verified using the existing `COINPILOTX_SECRET_KEY` signature.
- The payload must contain a valid user ID, device hash, and unexpired `exp`.
- The access token hash must match an active `mobile_security_sessions` row with an unexpired `access_expires_at`.
- The token device hash must match the stored mobile session device hash.

## Compatibility

- Existing WebView cookie sessions remain unchanged.
- Existing native refresh-token rotation remains unchanged.
- Existing logout and logout-all behavior remain unchanged.
- Existing production mobile auth endpoints remain unchanged.
- No duplicate auth backend or native-only identity store was introduced.

## Verification

Passed:

- `venv/bin/python scripts/pulsesoc_native_auth_immediate_logout_audit.py`
- `npm run --prefix mobile-native typecheck`
- `python3 -m py_compile bot.py`
- `git diff --check`

Pending owner/device proof:

- Owner signs into the currently installed native app and confirms the app remains signed in.
- Relaunch/force-quit session restoration on the physical iPhone.
- Authenticated Home, Messages, Reels, Profile smoke after owner login.

## Result

The native app now uses the existing server-issued mobile access token for ordinary authenticated API requests and refreshes it before it falls back to refresh-cookie-only requests. This removes the startup refresh-cookie race that could immediately log a valid user out after login.
