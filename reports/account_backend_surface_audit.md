# Account Backend Surface Audit

## Admin Surfaces

The Account Command Center now exposes backend management pages for:

- `/admin/account-command/profile`
- `/admin/account-command/verification`
- `/admin/account-command/account-health`
- `/admin/account-command/security`
- `/admin/account-command/settings`
- `/admin/account-command/advanced-security`
- `/admin/account-command/identity-protection`
- `/admin/account-command/session-intelligence`
- `/admin/account-command/device-intelligence`
- `/admin/account-command/security-timeline`
- `/admin/account-command/threat-detection`
- `/admin/account-command/login-analytics`
- `/admin/account-command/audit`

## Management Coverage

The backend surfaces provide overview metrics, search, filters through query input, safe recent record tables, empty states and security-boundary text. Verification decisions continue to use the existing decision endpoint that requires a public reason.

## Registry Updates

The backend management registry now points account features at their specific management routes instead of routing every account feature to the generic overview.

## Access Control

Admin account surfaces call `require_admin_page("users.manage")`. Non-admin users are blocked by the audit.

## Redaction

The admin views do not display private verification document files, raw push tokens, passwords, database URLs, private keys or internal-only moderator notes.
