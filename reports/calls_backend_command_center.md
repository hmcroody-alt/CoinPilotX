# Calls Backend Command Center

## What was missing

PulseSoc had Communications Engine service routes and call diagnostics, but there was no visible admin/backend surface where an operator could inspect calls, provider configuration, participant state, incoming-call notification delivery, quality reports, and failure timelines from one place.

## What was added

- Added a visible `Calls` admin navigation entry.
- Registered `network.calls` in the backend management registry.
- Added `/admin/calls` command-center pages for recent, active, failed, missed, detail, timeline, delivery, inspector, and config testing.
- Added API diagnostics for active calls, failed calls, timelines, inspectors, and admin force-end.
- Added backend summary/list/timeline/inspector helpers.
- Added admin force-end support for stale or broken active calls.
- Added realtime emission evidence in call events so delivery diagnostics can show whether the in-app incoming-call event was emitted.

## Admin routes added

- `/admin/calls`
- `/admin/calls/recent`
- `/admin/calls/active`
- `/admin/calls/failed`
- `/admin/calls/missed`
- `/admin/calls/<call_id>`
- `/admin/calls/<call_id>/timeline`
- `/admin/calls/<call_id>/delivery`
- `/admin/calls/<call_id>/inspector`
- `/admin/calls/<call_id>/force-end`
- `/admin/calls/test-config`

## API routes added or extended

- `GET /api/admin/calls/active`
- `GET /api/admin/calls/failed`
- `GET /api/admin/calls/<call_id>/timeline`
- `GET /api/admin/calls/<call_id>/inspector`
- `POST /api/admin/calls/<call_id>/force-end`

Existing routes still provide:

- `GET /api/admin/calls/recent`
- `GET /api/admin/calls/<call_id>`
- `GET /api/admin/calls/<call_id>/delivery`
- `POST /api/admin/calls/test-config`

## Dashboard cards added

- LiveKit Config
- Active Calls
- Calls Today
- Failed Calls
- Missed Calls
- Average Duration
- Average Quality
- Notification Delivery
- Last Error

## Diagnostics added

The command center shows:

- LiveKit configured/missing state without secret values.
- Call records and participant rows.
- Call timeline events.
- Incoming-call and missed-call notification records.
- Push/call delivery job status.
- Recipient device-token/subscription presence.
- Recipient mute/block policy result where available.
- Realtime incoming-call event emission/failure evidence.
- Quality reports.
- Raw safe metadata for inspection.

## Security checks

- Admin pages use server-side admin session checks.
- API diagnostics require admin access.
- Force end requires an admin session.
- Provider secret values are not rendered.
- The frontend still does not contain LiveKit API secrets.

## QA results

- Static audit added at `scripts/calls_backend_command_center_audit.py`.
- Verification commands are expected to run as part of the commit pass.
- Browser QA should verify the admin page after logging in as an admin because unauthenticated local curl should redirect to admin login.

## Remaining blockers

- Real provider room state depends on configured LiveKit variables.
- Whether a recipient actually opened the incoming overlay is still not fully tracked unless the frontend emits a future explicit acknowledgement event.
- Media track publish/subscription evidence depends on provider/webhook or client quality events.
