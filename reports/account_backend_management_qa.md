# Account Backend Management QA

## Automated QA

Created `scripts/dashboard_account_command_center_audit.py` to verify:

- required additive account tables exist
- account state builds from backend data
- verification request and admin decision flow works
- server-side settings save and reject invalid values
- Dashboard account links render
- account state API does not expose secrets or private paths
- reserved usernames are rejected
- profile updates still work
- non-admin users are blocked from admin account tools
- admin account command page loads

## Manual Smoke Targets

Recommended browser smoke paths:

- `/dashboard`
- `/dashboard/account/profile`
- `/dashboard/account/verification`
- `/dashboard/account/health`
- `/dashboard/account/security`
- `/dashboard/account/settings`
- `/admin/account-command` as admin

## Regression Boundaries

The change does not move Feed, Messenger, Ads, Premium, Payments, Notifications, Live, Status, or Profile ownership. Existing profile and security routes remain active.
