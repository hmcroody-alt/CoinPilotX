# PulseSoc Intelligence Alert Cadence

## Goal

PulseSoc Intelligence now has a scheduler-ready cadence rule:

```txt
Send one Intelligence alert every 3 hours.
```

The cadence is implemented as an engine-level job, not a page-load side effect. User pages continue to read from cached/database state only.

## Cadence Rule

- Cadence key: `global_three_hour_intelligence_alert`
- Interval: `10800` seconds / 3 hours
- First run: due immediately when cadence state is seeded
- Next run: `last_run_at + 3 hours`
- One selected event per run
- No multiple-alert burst per run
- Repeated non-forced runs before `next_run_at` return `not_due`

## Priority Order

The scheduler chooses one already accepted high-confidence signal in this order:

1. Security Signal
2. World Event
3. Crypto Signal
4. Market Signal
5. PulseSoc Feature Discovery
6. Daily Briefing / PulseSoc Pulse

If no eligible accepted external/internal signal exists, the scheduler creates a safe PulseSoc Discovery fallback from the internal feature registry. It never fabricates market, security, crypto, or world data.

## Delivery Path

The cadence job uses the existing Intelligence and central notification pipeline:

```txt
cadence due
-> select one accepted event or safe PulseSoc Discovery fallback
-> queue_event_delivery(...)
-> process_delivery_queue(...)
-> pulsesoc_notification_system.intake_event(...)
-> notification_delivery_jobs push row
-> existing push adapter / Web Push / FCM / APNs
```

This keeps the locked-screen route aligned with messages, comments, and reactions.

## User Controls

Users can still manage Intelligence delivery through the existing stream preferences:

- Disable stream
- Disable push
- Change frequency
- Enable quiet hours
- Raise/lower confidence threshold
- Give feedback

The cadence runner respects disabled streams and quiet hours only when quiet hours are enabled by the user.

## Admin Controls

The Galaxy Intelligence Center now shows:

- Cadence interval
- Due/scheduled state
- Next alert time
- Last run time
- Last event id
- `Send next alert now` control
- Cadence status API link

Admin APIs:

- `GET /api/admin/intelligence/cadence/status`
- `POST /api/admin/intelligence/cadence/send-now`

The force-send API is admin-only and uses the existing mass-send guard when no single target user is provided.

## Worker

The existing worker can now run cadence:

```bash
venv/bin/python scripts/pulsesoc_intelligence_worker.py --cadence
```

Force a run:

```bash
venv/bin/python scripts/pulsesoc_intelligence_worker.py --cadence --force
```

For QA against one account:

```bash
venv/bin/python scripts/pulsesoc_intelligence_worker.py --cadence --force --target-user-id <user_id>
```

## Files Changed

- `services/pulsesoc_intelligence_engine.py`
- `pulse_communications_v2/routes.py`
- `templates/admin_galaxy_intelligence_center.html`
- `static/js/pulsesoc_intelligence_center.js`
- `scripts/pulsesoc_intelligence_worker.py`
- `migrations/pulsesoc_intelligence_engine.sql`
- `scripts/pulsesoc_alert_cadence_audit.py`
- `reports/pulsesoc_alert_cadence.md`

## QA Result

The audit runs against an isolated database and verifies:

- First cadence state is due immediately.
- One cadence run creates/selects one alert.
- Delivery goes through the existing central notification push job path.
- Next run advances by approximately 3 hours.
- A second non-forced run does not spam another alert.
- Admin status and force-send APIs exist.
- Admin UI is wired.

## Known Limitations

- Production delivery still depends on the worker/cron process invoking `scripts/pulsesoc_intelligence_worker.py --cadence`.
- Real locked-screen display still depends on user push permission, valid device subscription/token, and configured Web Push/FCM/APNs provider.
- The local shell audit does not mass-send to production users.
