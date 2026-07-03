# PulseSoc Alert Delivery Activation

## Summary

PulseSoc Intelligence alerts now have an explicit delivery activation layer:

Collector signal -> confidence/dedupe -> user stream match -> Intelligence delivery queue -> central PulseSoc notification intake -> notification delivery jobs -> delivery logs and feedback.

User pages still load from database state only. External collectors and delivery processing remain outside normal page loads.

## What Changed

- Added `intelligence_delivery_jobs` as the Intelligence delivery queue.
- Added queue processing with retry, skipped, sent, failed, and canceled states.
- Added digest job generation/processing for lower-priority signals.
- Kept outbound delivery on the existing central notification system.
- Added `/pulse/alerts` as the friendly user alert surface.
- Added admin controls for test alert, manual queue, process queue, generate digests, cancel delivery, and inspect logs.
- Added richer user feedback buttons: Useful, Save, Not helpful, Wrong, Outdated, Too frequent.
- Updated new-user defaults so Creator, Music, and Technology streams are suggested/off by default while PulseSoc, Security, World, Crypto, Market, and Daily Briefing remain active experiences.

## Delivery Behavior

- High-priority or realtime events become instant alerts.
- Forecast-backed high-priority events can become forecast alerts.
- Lower-priority signals are queued into digests.
- Feature discovery signals use friendly PulseSoc CTA buttons.
- Disabled streams, confidence thresholds, priority filters, muted frequency, and quiet hours are checked before delivery.
- Delivery is deduped by user, event, stream, and delivery type.

## CTA Behavior

PulseSoc feature/discovery/system alerts can include CTA metadata such as:

- Open PulseSoc
- Explore Feature
- Share PulseSoc
- Download PulseSoc

The raw App Store URL is not rendered in user-facing alert cards. The frontend validates allowed external domains and uses buttons/share/copy fallback.

## Admin Controls

Added admin-only APIs:

- `POST /api/admin/intelligence/delivery/test`
- `POST /api/admin/intelligence/delivery/send`
- `POST /api/admin/intelligence/delivery/process`
- `POST /api/admin/intelligence/delivery/digests`
- `POST /api/admin/intelligence/delivery/cancel`
- `GET /api/admin/intelligence/delivery/logs`

Manual sends are audit-logged. Read-only/viewer-style admin roles are blocked from mass-send and digest-generation controls.

## Security and Privacy

- No provider secrets are exposed.
- No external source fetch runs during user page load.
- Admin broadcast controls require admin access.
- User delivery respects stream preferences and central notification preferences.
- Private conversations, calls, media, payment data, passwords, tokens, and secrets are not used by collectors.

## QA Results

- Static audit verifies queue, routes, admin controls, CTA safety, and report presence.
- Runtime audit uses a temporary SQLite database to verify:
  - accepted signal delivery queues and processes
  - central notification intake creates delivery records
  - duplicate signals dedupe
  - disabled stream stops delivery
  - admin test alert helper works

Locked-screen push requires valid user device subscriptions and configured push providers. This activation creates the correct notification and provider delivery jobs; actual lock-screen display still depends on the device/browser/provider state.

## Known Limitations

- Digest generation currently bundles stored accepted signals by stream/user and sends through the central notification path; scheduled production cadence must be wired to the deployment scheduler/worker.
- SMS/email delivery remains governed by the central notification provider configuration and user preferences.
- Real user matching currently uses enabled stream subscriptions, thresholds, priority filters, and target/broadcast limits. More advanced personalization ranking can build on this queue without changing the delivery contract.

## Verification Commands

```bash
venv/bin/python -m py_compile bot.py services/*.py scripts/pulsesoc_alert_delivery_activation_audit.py
node --check static/js/pulsesoc_intelligence_center.js
venv/bin/python scripts/pulsesoc_intelligence_engine_audit.py
venv/bin/python scripts/pulsesoc_intelligence_collectors_phase2_audit.py
venv/bin/python scripts/pulsesoc_alert_delivery_activation_audit.py
git diff --check
git diff --cached --check
curl -fsS http://127.0.0.1:5069/health
```
