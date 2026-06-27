# PulseSoc Network Privacy and Security Review

Date: 2026-06-27

## Security Boundary

The Network Command Center is designed around safe aggregate state.

It does not expose:

- Private message bodies
- Deleted message bodies
- Reporter identities
- Raw push tokens
- Device secrets
- Provider credentials
- Internal worker tokens
- Private user settings from other accounts

## User Permissions

User-facing Network dashboard routes require an authenticated PulseSoc account and only build state for the current account.

## Admin Permissions

Admin Network Command Center routes require `command_center.view`.

General admin diagnostics remain aggregate and redacted. Private message body access is intentionally not part of this surface and would require a separate role-gated, audited workflow.

## Data Handling

The state layer:

- Uses server-side table detection.
- Returns zero for unavailable tables.
- Aggregates counts only.
- Avoids raw SQL writes.
- Avoids destructive migrations.
- Avoids unsafe client-controlled routing.

## Auditability

The backend registry now registers Network management surfaces for:

- Notifications
- Messages
- Friends
- Followers / Following
- Groups
- Blocks & Mutes
- Bans
- Push Delivery
- Message Health
- Network Audit Logs

These surfaces route to protected admin pages and existing audit-backed operational tools.

## Remaining Risk

Future phases that add admin mutation buttons for blocks, mutes, bans, or group moderation must add CSRF, rate limits, role checks, and explicit audit records before enabling writes.

