# Dashboard Account Command Center Report

## Scope

Completed the PulseSoc Dashboard ACCOUNT section as a backend-managed command layer for:

- Profile
- Verification
- Account Health
- Security
- Settings

## Implementation

- Added `services/dashboard_account_command_center.py` as the server-side account command data layer.
- Added additive schema support for profile audits, verification requests/documents, account health events, warnings, strikes, restrictions, sessions/devices, user settings, and account audit logs.
- Wired Mission Control account widgets to real backend state instead of static labels.
- Added Dashboard account routes:
  - `/dashboard/account/profile`
  - `/dashboard/account/verification`
  - `/dashboard/account/health`
  - `/dashboard/account/security`
  - `/dashboard/account/settings`
  - `/api/dashboard/account/state`
  - `/api/dashboard/account/settings`
  - `/api/dashboard/account/verification/request`
  - `/api/dashboard/account/verification/appeal`
  - `/api/dashboard/account/verification/document`
- Added admin route `/admin/account-command` and verification decision endpoint.

## State Labels

Dashboard account cards now use backend state:

- `ON`: available and wired
- `ACTION`: user action needed
- `REVIEW`: backend/admin review pending
- `WARNING`: account/security/health issue exists
- `LOCK`: premium/entitlement gate

## Admin Management

Admins can review:

- Verification requests
- Profile audit history
- Account health events

Admins can approve, reject, request more information, or suspend verification requests through server-side permission checks.

## Legal Name Correction

No new legacy legal-name references were added. PulseSoc branding remains unchanged, and the correct legal display name remains `CoinPlotXAI Inc.` where legal copy is touched.
