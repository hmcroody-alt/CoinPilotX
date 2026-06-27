# PulseSoc Network Backend Management QA

Date: 2026-06-27

## Automated QA

Audit script:

```bash
venv/bin/python scripts/network_command_center_audit.py
```

Coverage:

- Unauthenticated user dashboard Network routes redirect to login.
- Authenticated user dashboard Network routes render successfully.
- Network state API requires login.
- Network state API returns privacy redaction flags.
- Non-admin users cannot access admin Network Command Center routes.
- Admin session can open every Network Command Center route.
- Internal LogiNexus terminology is not exposed in user Network pages.
- Secret environment names are not rendered in admin diagnostics.

## Route Coverage

User:

- `/dashboard/network`
- `/dashboard/network/notifications`
- `/dashboard/network/messages`
- `/dashboard/network/friends`
- `/dashboard/network/followers`
- `/dashboard/network/groups`
- `/api/dashboard/network/state`

Admin:

- `/admin/network-command-center`
- `/admin/network-command-center/notifications`
- `/admin/network-command-center/messenger`
- `/admin/network-command-center/friends`
- `/admin/network-command-center/followers`
- `/admin/network-command-center/groups`
- `/admin/network-command-center/blocks-mutes`
- `/admin/network-command-center/bans`
- `/admin/network-command-center/push-delivery`
- `/admin/network-command-center/message-health`
- `/admin/network-command-center/audit`

## Manual QA Notes

QA browser smoke test completed against a temporary local SQLite database on
`http://127.0.0.1:5098` with one seeded standard user and one seeded owner admin.
No production data was used.

Desktop user checks:

- `/dashboard/network` rendered `Network Command`.
- `/dashboard/network/notifications` rendered `Notifications Command`.
- `/dashboard/network/messages` rendered `Messages Command`.
- `/dashboard/network/friends` rendered `Friends Command`.
- `/dashboard/network/followers` rendered `Followers Command`.
- `/dashboard/network/groups` rendered `Groups Command`.
- All user Network pages had no horizontal overflow.
- User Network pages did not expose internal LogiNexus terminology.

Desktop admin checks:

- `/admin/network-command-center` rendered `Network Command Center`.
- All 10 admin Network section pages rendered without 404s.
- Admin pages did not expose `DATABASE_URL`, `COMMAND_CENTER_INTERNAL_TOKEN`,
  `APNS_PRIVATE_KEY`, or `VAPID_PRIVATE_KEY`.
- Admin pages had no horizontal overflow.

Mobile checks:

- `/dashboard/network` rendered at 390px width with no horizontal overflow.
- `/admin/network-command-center` rendered at 390px width with no horizontal overflow.
- Browser console error log was empty during the final responsive pass.
