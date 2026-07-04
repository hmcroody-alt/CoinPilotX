# PulseSoc Native Alert Management QA Hardening

Date: 2026-07-04

## Scope

This was a hardening pass for the existing Native Alert Management foundation in `mobile-native/`.

No new major feature was built. Production WebView paths were not modified.

The native app continues to reuse the existing PulseSoc alert backend and keeps alert evaluation, Premium gates, channel readiness, provider delivery, notification delivery logs, cooldowns, dedupe, and crypto/market interpretation server-authoritative.

## Existing Backend Reused

Verified reuse targets:

- `GET /api/crypto/alerts`
- `POST /api/crypto/alerts`
- `PATCH /api/crypto/alerts/<alert_id>`
- `POST /api/crypto/alerts/<alert_id>/duplicate`
- `GET /api/crypto/alerts/<alert_id>/history`
- `GET /api/alerts`
- `POST /api/alerts/<alert_id>/pause`
- `POST /api/alerts/<alert_id>/resume`
- `POST /api/alerts/<alert_id>/delete`
- `POST /api/alerts/<alert_id>/test`
- `GET /api/alerts/events`
- `GET /api/alerts/channel-readiness`
- `POST /api/alerts/test/<channel>`

Production code inspected:

- `bot.py`
- `services/alert_engine.py`
- `services/dashboard_crypto_command_center.py`
- `services/notification_service.py`
- Web alert surface under `/dashboard/crypto/alerts`

## Hardening Changes

Updated `mobile-native/src/screens/AlertManagementScreen.tsx`.

Scoped fixes:

- Added native form validation for missing symbol.
- Added native form validation for invalid symbol shape.
- Added native form validation for missing target value.
- Added native form validation for non-numeric target value.
- Added native form validation for target values less than or equal to zero.
- Added native form validation for extremely large target values.
- Added native form validation requiring at least one delivery channel.
- Replaced platform-alert-only delete behavior with inline delete confirmation that is browser-testable and native-friendly.
- Added cancel state for delete confirmation.
- Added long-history copy when more than 12 events are present.
- Expanded empty-history copy so users understand history is backend-created after trigger/test events.
- Fixed success notices being cleared immediately by the refresh that follows create/edit/test/pause/resume/delete/duplicate actions.
- Fixed a selected-alert reload loop by removing `selectedId` from the initial-load callback dependency and tracking it through a ref.

No native alert trigger, provider, Premium, or notification business logic was added.

## QA Browser Environment

Built-in QA browser only. No Chrome Incognito or external browser was used.

Local QA API:

- Temporary local fixture API: `http://localhost:5128`
- Seeded active alert: BTC above 70000
- Seeded paused alert: ETH below 2500
- Seeded active alert: SOL moves up 8 percent
- Seeded long history: BTC with more than 12 events
- Seeded empty history: ETH
- Channel readiness variants: in-app ready, email ready, push permission needed, SMS not configured, Telegram not configured

Native web build:

```bash
EXPO_PUBLIC_PULSE_API_BASE_URL=http://localhost:5128 npm run web:qa
```

Server checks before browser navigation:

- `curl -I http://localhost:8094/pulse/alerts` returned `HTTP/1.1 200 OK`
- `curl http://localhost:5128/health` returned `{"ok": true, "service": "qa-alert-fixture"}`

Screenshot evidence:

- `reports/screenshots/pulsesoc_native_alert_management_qa_hardening_20260704.png`

## Browser-Verified Results

Verified:

- `/pulse/alerts` loads the native Alert Management screen.
- `/pulse/alerts/<alert_id>` routes into native Alert Management and selects the requested alert.
- `/dashboard/crypto/alerts` routes into native Alert Management.
- `/dashboard/crypto/alerts?alert_id=<alert_id>` routes into native Alert Management and selects the requested alert.
- Login and session restore worked against the local QA fixture.
- Active, paused, empty-history, and long-history alert states rendered.
- Missing symbol validation rendered.
- Non-numeric target validation rendered.
- Non-positive target validation rendered.
- Excessively large target validation rendered.
- No-channel validation rendered.
- Create alert succeeded and kept the server success notice visible after refresh.
- Edit alert validation rendered.
- Edit alert save updated the rendered card.
- Pause and resume state changes rendered.
- Duplicate created an additional alert row.
- Delete shows inline confirmation.
- Delete cancel renders a cancel notice.
- Confirm delete removes the duplicated alert from the list.
- Alert test success notice renders and persists after refresh.
- Alert test failure renders the server failure message.
- Channel readiness success and failure states render.
- Push/SMS/Telegram readiness failures remain safe and explicit.
- Long history is capped visually with a newest-12 count message.
- Safe fallback boundary remains visible for unsupported provider/admin features.

Console findings:

- No current alert runtime errors were observed.
- React Native Web emitted existing framework warnings for deprecated `pointerEvents` and `shadow*` style props. These are not alert-specific blockers.

## Issues Found And Fixed

### Success Notices Were Cleared After Refresh

Root cause:

- `saveForm(...)` and `runAction(...)` set the notice before calling `load("refresh")`.
- `load(...)` intentionally clears notice at the start of refresh.
- Create/edit/test success messages could disappear before users saw them.

Fix:

- Move success notice updates after the refresh completes.

Retest:

- Create alert now preserves the server message, for example `UNI alert created.`
- Test alert now preserves `Test alert queued.`

### Selected Alert Triggered An Unnecessary Initial Reload

Root cause:

- `load(...)` depended on `selectedId`.
- The mount effect depended on `load(...)`.
- Updating `selectedId` after create could recreate `load(...)`, rerun the mount load, and clear UI notices.

Fix:

- Track the current selected alert with a ref.
- Remove `selectedId` from the `load(...)` dependency list.

Retest:

- Creating a new alert selects the new alert detail and preserves success copy.

## Device/Provider Items Not Verified

Not verified in browser QA:

- APNs delivery
- FCM delivery
- Expo push token delivery on a physical device
- Lock-screen notification presentation
- Installed-app notification tap routing
- Background notification recovery
- Real SMS delivery
- Real email provider delivery
- Real Telegram delivery
- Physical-device deep links
- Simulator deep links

These remain device/provider QA items and must not be marked passed from browser testing.

## Recommendation

Next highest-value action: Native alert fixture hardening plus provider/device QA setup, not a new major feature.

Why:

- Native Alert Management now works in browser QA against seeded active, paused, long-history, empty-history, action-success, and action-failure states.
- The remaining highest-risk gaps are provider/device behavior: push tokens, notification taps, lock-screen behavior, SMS/email/Telegram delivery, and physical-device deep links.
- Before moving into camera, Live hosting, or LiveKit calls, the app should keep the QA-driven cadence and run a device/provider alert pass once external credentials and devices are available.

Safe next slice:

1. Add or maintain a durable seeded QA fixture for alerts.
2. Run real-device alert notification QA when APNs/FCM/device setup is available.
3. Then decide between native Camera/advanced media hardening or LiveKit calls based on the latest parity report and QA blockers.
