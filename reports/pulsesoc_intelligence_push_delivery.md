# PulseSoc Intelligence Locked-Screen Push Delivery

## Why Alerts Were Silent

Intelligence alerts were entering the in-app Intelligence delivery queue, but several default-enabled streams were seeded with `push_enabled=false`. The Intelligence queue also passed explicit channel lists into the central notification system, so the notification system's default locked-device push categories could not automatically add `push`.

The result was:

```
accepted Intelligence signal
-> intelligence_delivery_jobs with ["in_app"]
-> central notification record
-> no push delivery job
-> in-app alert only
```

## Working Message/Comment Route

Messages, comments, and reactions already use the central PulseSoc notification system with push included:

```
PulseSoc event
-> pulsesoc_notification_system.intake_event(...)
-> notification record
-> notification_delivery_jobs row for push
-> push provider / Web Push / FCM / APNs
-> locked-screen notification
```

The Intelligence route is now aligned with that flow.

## What Changed

- Default-enabled Intelligence streams are now push eligible unless the user disables push.
- Existing default-pack stream rows are upgraded to push-on when they have not been manually changed by the user.
- User push changes now mark `push_user_set` metadata so future default migrations do not override explicit choices.
- Instant, forecast, feature discovery, and due digest deliveries can create `push` channels when user settings allow it.
- Intelligence notifications now include lock-screen payload metadata:
  - `type=intelligence_pulse`
  - `category=intelligence`
  - `priority=normal/high/urgent`
  - `title`
  - `body`
  - `deep_link=/pulse/alerts?...`
  - `sound_key=pulse_signal` or `alert`
  - `vibration=standard` or `strong`
  - `show_on_lock_screen=true`
- The service worker now recognizes Intelligence pushes, uses `/pulse/alerts` as the fallback target, applies a safe badge icon, and tags notifications as `pulsesoc-intelligence-*`.
- Admin delivery diagnostics now include downstream central `notification_delivery_jobs`.
- Admin UI now has a clear test control: `Send locked-screen test Intelligence Pulse`.

## Payload Example

```json
{
  "type": "intelligence_pulse",
  "category": "intelligence",
  "priority": "high",
  "title": "Pulse Discovery: PulseSoc feature discovery test",
  "body": "This admin-only test verifies the Intelligence alert queue, notification intake, delivery logs, and CTA rendering.",
  "deep_link": "/pulse/alerts?event=123",
  "sound_key": "pulse_signal",
  "vibration": "standard",
  "badge": true,
  "show_on_lock_screen": true
}
```

## Files Changed

- `services/pulsesoc_intelligence_engine.py`
- `services/pulsesoc_notification_system.py`
- `static/service-worker.js`
- `templates/admin_galaxy_intelligence_center.html`
- `migrations/pulsesoc_intelligence_engine.sql`
- `scripts/pulsesoc_intelligence_push_delivery_audit.py`
- `reports/pulsesoc_intelligence_push_delivery.md`

## Admin Test Result

The audit uses an isolated database, enables push preferences for an audit user, sends an admin Intelligence test Pulse, and verifies:

- Intelligence delivery job includes `push`.
- Central notification is `intelligence_pulse`.
- Central `notification_delivery_jobs` includes a `push` job.
- Payload contains title/body/deep link/sound/vibration/lock-screen metadata.

## Mobile Lock-Screen QA

Physical locked-phone delivery cannot be completed inside this local shell. The code path now creates the same central push delivery job shape used by working message/comment/reaction notifications. Final device QA still requires a real user with:

- Push permission granted.
- Active Web Push/FCM/APNs device token/subscription.
- Intelligence stream enabled.
- Push enabled for that stream and notification category.
- Quiet hours/digest rules allowing the delivery.

## Remaining Blockers

- If the recipient has no active push token/subscription, the push adapter will skip with `skipped_no_device`.
- If Web Push/FCM/APNs provider credentials are missing, delivery will be queued/skipped with provider diagnostics rather than fake success.
- Digest-only alerts still wait until digest processing time; they are not pushed instantly.
