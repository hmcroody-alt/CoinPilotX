# PulseSoc Native Account, Security & Privacy QA Sweep

Date: 2026-07-05

## Scope

Short authenticated QA sweep for the native Account, Security, Privacy, and Sessions/Devices foundation.

No new major feature was built during the sweep. One scoped route-hardening fix was applied after QA found direct account/privacy URLs falling back to Home.

## Environment

- Native web build: `EXPO_PUBLIC_PULSE_API_BASE_URL=http://localhost:5108 npm run web:qa`
- QA URL: `http://localhost:8094`
- Local backend: `http://127.0.0.1:5107`
- Local QA proxy: `http://localhost:5108`
- QA account: temporary local SQLite account, not production
- Production WebView routes: untouched

The temporary local account was created through the existing `/api/mobile/auth/register` endpoint. Because local email delivery is not configured, the temporary local SQLite user was marked email-verified for QA only, then authenticated through the existing `/api/mobile/auth/login` endpoint. No production auth behavior was changed.

## Verified

Authenticated browser QA passed for:

- Login through native web UI.
- Session rendering after login.
- Settings screen entries:
  - Account Center
  - Security Center
  - Privacy Center
  - Sessions and devices
- Native Account Center route: `/pulse/settings/account`
- Native Security Center route: `/pulse/settings/security`
- Native Privacy Center route: `/pulse/settings/privacy`
- Native Sessions/Devices route: `/pulse/settings/devices`
- Dashboard account alias: `/dashboard/account/settings`
- Dashboard security alias: `/dashboard/account/security`
- Account settings alias: `/account/settings`
- Account security alias: `/account/security`
- Privacy center alias: `/privacy-center`
- Privacy settings save action through `/api/dashboard/account/settings`
- 2FA enable action through `/api/account/2fa/enable`
- Security score/history refresh after enabling 2FA
- No browser console error-level entries during the account/security/privacy checks

## Issue Found And Fixed

### Direct account/privacy aliases fell back to Home

Observed:

- `/dashboard/account/settings`
- `/dashboard/account/security`
- `/account/settings`
- `/account/security`
- `/privacy-center`

These URLs served safely but rendered Home instead of the native account/security/privacy surfaces.

Root cause:

- Initial URL linking only mapped `/pulse/settings/:section`.
- Notification routing already understood account/security/privacy targets, but React Navigation web initial linking needed explicit route aliases.

Fix:

- Added native route aliases:
  - `AccountSettings`
  - `AccountSecurity`
  - `AccountWebSettings`
  - `AccountWebSecurity`
  - `AccountPrivacy`
  - `AccountDevices`
- Mapped those aliases in `mobile-native/src/navigation/linking.ts`.
- Updated `AccountCenterScreen` to infer the right section from alias route names.

Retest:

- All aliases listed above rendered the correct native Account, Security, or Privacy Center.

## Not Verified

- Real email/SMS verification provider delivery.
- Native password-change flow.
- Native account export flow.
- Native account deletion flow.
- Physical iOS/Android behavior.
- Push/provider notification delivery for account/security events.

These remain release/provider/device blockers, not development blockers.

## Result

No critical, security, production-breaking, or data-loss issues were found.

Account/Security/Privacy is stable enough to continue native roadmap work, with provider/device verification tracked separately.
