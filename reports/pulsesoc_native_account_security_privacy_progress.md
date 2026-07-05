# PulseSoc Native Account, Security & Privacy Foundation

Date: 2026-07-05

## Scope

Built the native Account, Security, Privacy, and Sessions/Devices foundation for the parallel `mobile-native` app.

This does not replace production WebView settings. It adds a native client layer that reuses the existing PulseSoc backend.

## Production Code Inspected

Existing production routes and APIs found in `bot.py`:

- `GET /api/account/status`
- `GET /api/dashboard/account/settings`
- `POST /api/dashboard/account/settings`
- `GET /api/account/security`
- `POST /api/account/verify-email`
- `POST /api/account/verify-phone`
- `POST /api/account/2fa/enable`
- `POST /api/account/2fa/disable`
- `POST /api/account/recovery-codes/generate`
- `GET /api/account/security-events`
- `GET /api/account/trusted-devices`
- `DELETE /api/account/trusted-devices/<device_id>`
- `POST /api/account/reauthenticate`
- `POST /api/account/sessions/revoke-all`
- `/pulse/settings/account`
- `/pulse/settings/security`
- `/pulse/settings/privacy`
- `/pulse/settings/devices`
- `/dashboard/account/security`
- `/dashboard/account/settings`
- `/account/settings`
- `/account/delete`
- `/privacy-center`

Existing notification preference APIs remain available through the existing native `NotificationPreferencesScreen`.

## Native Implementation

Added:

- `mobile-native/src/api/account.ts`
- `mobile-native/src/screens/AccountCenterScreen.tsx`
- `AccountCenter` native stack route
- Settings screen entries for:
  - Account Center
  - Security Center
  - Privacy Center
  - Sessions and Devices
- Deep-link/notification routing for account/security/privacy/device targets.

Native sections:

- Account status and account experience settings.
- Security score, email/phone verification request actions, 2FA enable/disable, recovery-code generation, reauthentication, and session revoke.
- Privacy settings for profile visibility, message requests, status replies, notifications, and ads personalization.
- Trusted devices list and device removal.
- Security event list.
- Offline cache fallback for the Account Center state.

## Reuse Boundary

Reused:

- Existing backend auth/session authority.
- Existing dashboard account settings validation.
- Existing account security payload.
- Existing email/phone verification queues.
- Existing 2FA state updates.
- Existing recovery-code generation.
- Existing security event database.
- Existing trusted-device database.
- Existing session revoke route.
- Existing protected web routes for sensitive flows.

Not duplicated:

- Password change rules.
- Account deletion rules.
- Email/SMS/OTP provider behavior.
- Security scoring logic.
- Privacy policy or retention logic.
- Server-side authorization.

## Safe Web Fallbacks

Kept on existing protected web flows:

- Password and email profile management: `/account/settings`
- Account deletion: `/account/delete`
- Advanced privacy/data controls: `/privacy-center`
- Advanced dashboard security: `/dashboard/account/security`

Reason: these flows are security-sensitive and should not be recreated as native-only logic without a dedicated reauthentication and provider QA pass.

## QA Status

Static verification is required before merge:

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`
- `npm run --prefix mobile-native typecheck`
- `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`
- `venv/bin/python scripts/pulsesoc_native_account_security_privacy_audit.py`
- `git diff --check`

Practical QA browser evidence:

- `curl -I http://localhost:8094/pulse/settings/security` returned `200 OK`.
- `curl -I http://localhost:8094/pulse/settings/privacy` returned `200 OK`.
- `curl -I http://localhost:8094/pulse/settings/devices` returned `200 OK`.
- Built-in QA browser navigation to `http://localhost:8094/pulse/settings/security` rendered the native Login gate because the QA browser session was signed out.
- Built-in QA browser console had no error-level entries for this route check.

Browser/device status:

- Native route and UI are designed for practical QA.
- Authenticated Account Center click-through was not verified in this pass because no signed-in QA browser session was available.
- Provider delivery for email/SMS/verification remains backend/provider QA.
- Sensitive delete/password flows remain web fallback.
- Physical-device verification is not claimed in this report.

## Risks

Risk level: medium-high.

Reasons:

- Account/security/privacy flows are sensitive.
- 2FA and recovery-code actions must stay server-authoritative.
- Provider delivery and reauth semantics can only be fully verified against real backend/provider configuration.

Mitigations:

- Thin API wrappers only.
- Server-owned validation and authorization.
- Read-first native UX.
- Sensitive flows stay on existing protected web routes.
- Offline cache is display-only and not authoritative.

## Next Recommended Action

Run a short practical QA sweep for Native Account, Security & Privacy before building another large feature.

Why: the feature touches sensitive settings, so the next highest-value step is confirming route reachability, signed-in loading, safe error handling, deep-link routing, and no accidental exposure of internal design language. Provider/device delivery can remain a release blocker rather than a development blocker.
