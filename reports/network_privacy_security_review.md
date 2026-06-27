# PulseSoc Network Privacy and Security Review

Date: 2026-06-27

## Security Boundary

The Pulse Network operating layer is designed around safe aggregate state. It does not expose:

- Private message bodies
- Deleted message bodies
- Reporter identities
- Raw device registrations
- Device secrets
- Provider credentials
- Internal worker tokens
- Private user settings from other accounts
- Exact private geolocation
- Hidden/private status visibility

## User Permissions

User-facing Network dashboard routes require an authenticated PulseSoc account and only build state for the current account. The state layer accepts the current user from the server session and does not trust client-supplied user IDs.

## Admin Permissions

Admin Network Command Center routes require `command_center.view`. General admin diagnostics remain aggregate and redacted. Private message body access is intentionally excluded from this surface and would require a separate role-gated, audited workflow.

## Backend Registry

The backend management registry now includes the expanded Network inventory so launch readiness can detect whether Network modules are backend-visible:

- Notifications
- Messages
- Friends
- Followers / Following
- Groups
- Status Activity
- Community Activity
- Network Health
- Delivery Intelligence
- Notification Intelligence
- Relationship Intelligence
- Connection Analytics
- Audience Mapping
- Growth Signals
- Pulse Delivery Matrix
- Network Security
- Community Intelligence
- Creator Reach
- Connection Recovery
- Blocks & Mutes
- Bans
- Push Delivery
- Message Health
- Network Audit Logs

## Data Handling

The state layer:

- Uses server-side table detection.
- Returns zero for unavailable tables.
- Aggregates counts only.
- Avoids raw SQL writes.
- Avoids destructive migrations.
- Avoids unsafe client-controlled routing.
- Avoids unsafe HTML.
- Keeps internal provider state and secrets out of rendered UI.

## Auditability

Network state changes are designed to flow through audit-backed systems:

- Friend request actions
- Follow/unfollow actions
- Block/unblock and mute/unmute
- Bans and restrictions
- Group membership and role changes
- Notification retries
- Push delivery failures
- Message health events
- Admin command-center actions

## Remaining Risk

The current implementation does not add new direct admin mutation buttons for blocks, mutes, bans, group moderation, or provider retry execution. Future writable controls must add CSRF, rate limits, server-side role checks, explicit audit records, and rollback/recovery behavior before enabling writes.
