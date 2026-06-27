# PulseSoc Network Backend Management QA

Date: 2026-06-27

## Automated QA

Primary audit:

```bash
venv/bin/python scripts/network_command_center_audit.py
```

Coverage:

- Unauthenticated Network dashboard routes redirect to login.
- Authenticated Network dashboard routes render.
- Network state API requires login.
- Network state API returns privacy redaction flags.
- Network state API returns the Network Intelligence panel.
- Every subsystem has intelligence, automation, protection, and recovery layer copy.
- Every subsystem state uses the strict state label list.
- No user Network route exposes the internal technology name.
- No user Network route renders a generic `Open` action or legacy `ON` state.
- Non-admin users cannot access admin Network Command Center routes.
- Admin session can open every Network Command Center route.
- Admin Network routes do not expose credential environment names.
- Admin Network routes do not expose broken internal admin links.

## Route Coverage

User routes:

- `/dashboard/network`
- `/dashboard/network/notifications`
- `/dashboard/network/messages`
- `/dashboard/network/friends`
- `/dashboard/network/followers`
- `/dashboard/network/groups`
- `/dashboard/network/status-activity`
- `/dashboard/network/community-activity`
- `/dashboard/network/network-health`
- `/dashboard/network/delivery-intelligence`
- `/dashboard/network/notification-intelligence`
- `/dashboard/network/relationship-intelligence`
- `/dashboard/network/connection-analytics`
- `/dashboard/network/audience-mapping`
- `/dashboard/network/growth-signals`
- `/dashboard/network/delivery-matrix`
- `/dashboard/network/network-security`
- `/dashboard/network/community-intelligence`
- `/dashboard/network/creator-reach`
- `/dashboard/network/connection-recovery`
- `/api/dashboard/network/state`

Admin routes:

- `/admin/network-command-center`
- `/admin/network-command-center/notifications`
- `/admin/network-command-center/messenger`
- `/admin/network-command-center/friends`
- `/admin/network-command-center/followers`
- `/admin/network-command-center/groups`
- `/admin/network-command-center/status-activity`
- `/admin/network-command-center/community-activity`
- `/admin/network-command-center/network-health`
- `/admin/network-command-center/delivery-intelligence`
- `/admin/network-command-center/notification-intelligence`
- `/admin/network-command-center/relationship-intelligence`
- `/admin/network-command-center/connection-analytics`
- `/admin/network-command-center/audience-mapping`
- `/admin/network-command-center/growth-signals`
- `/admin/network-command-center/delivery-matrix`
- `/admin/network-command-center/network-security`
- `/admin/network-command-center/community-intelligence`
- `/admin/network-command-center/creator-reach`
- `/admin/network-command-center/connection-recovery`
- `/admin/network-command-center/blocks-mutes`
- `/admin/network-command-center/bans`
- `/admin/network-command-center/push-delivery`
- `/admin/network-command-center/message-health`
- `/admin/network-command-center/audit`

## Manual QA Notes

Manual browser QA should be rerun on deployment because this local pass uses a temporary SQLite audit database. The automated audit verifies route existence, permissions, redaction, and internal-link integrity without touching production data.

Recommended production smoke after deploy:

- Desktop `/dashboard/network`.
- Mobile `/dashboard/network`.
- Desktop `/admin/network-command-center`.
- Mobile `/admin/network-command-center`.
- Confirm no horizontal overflow.
- Confirm no console errors.
- Confirm normal Feed, Messenger, Notifications, and Dashboard navigation still load.
