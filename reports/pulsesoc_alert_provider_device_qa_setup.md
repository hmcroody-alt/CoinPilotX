# PulseSoc Alert Provider + Device QA Setup

Date: 2026-07-04

## Scope

This report prepares Native Alert Management for real provider/device QA.

No new major user-facing feature was built. Production WebView paths were not modified.

Alert Management remains server-authoritative. The native app should continue to reuse existing PulseSoc alert APIs, alert engine logic, notification delivery logs, provider routing, Premium gates, channel readiness, and deep-link routing.

## Current Repo State

Native app configuration inspected:

- `mobile-native/app.json`
- `mobile-native/eas.json`
- `mobile-native/src/api/push.ts`
- `mobile-native/src/navigation/linking.ts`
- `mobile-native/src/navigation/notificationRouting.ts`

Backend/provider code inspected:

- `services/native_push_readiness.py`
- `services/push_service.py`
- `services/alert_engine.py`
- `services/notification_service.py`
- `services/pulsesoc_notification_system.py`
- `services/sms_service.py`
- `.env.example`

Existing repo readiness:

- Expo native app declares `scheme: "pulsesoc"`.
- Expo native app declares iOS bundle identifier `com.pulsesoc.nativeapp`.
- Expo native app declares Android package `com.pulsesoc.nativeapp`.
- Expo native app includes `expo-notifications`.
- Native push registration uses `expo-notifications`.
- Native push registration posts Expo tokens to existing `/api/push/subscribe`.
- Android notification permission `POST_NOTIFICATIONS` is declared.
- Android notification channels are created for `messages` and `alerts`.
- Notification tap routing handles `/pulse/alerts`, `/pulse/alerts/<id>`, `/dashboard/crypto/alerts`, and `/dashboard/crypto/alerts?alert_id=<id>`.
- Alert dispatch writes delivery state to `notification_delivery_logs`.
- Push provider traces use `push_trace_id`.
- Durable push queues use `push_delivery_jobs`.
- Expo provider receipts use `expo_push_tickets`.
- Alert worker status uses `alert_worker_heartbeat`.

## Critical Identity Finding

The parallel native app is intentionally separate from the existing production WebView app:

- Parallel native app: `com.pulsesoc.nativeapp`
- Existing production mobile/WebView app materials: `com.pulsesoc.app`

The backend helper `services/native_push_readiness.py` currently expects APNs bundle ID `com.pulsesoc.app`.

## QA Identity Decision

Decision for provider/device QA:

- Native provider/device QA target: `com.pulsesoc.nativeapp`
- Protected production app identity: `com.pulsesoc.app`

The current production WebView app identity must remain untouched. Do not modify production provider credentials, production APNs bundle IDs, production provisioning, or any existing `com.pulsesoc.app` release materials for this native QA pass.

Native QA should use credentials, builds, and provider metadata that are explicitly scoped to `com.pulsesoc.nativeapp`. If backend readiness tooling needs to validate both app identities, implement that as scoped multi-bundle readiness support rather than replacing the production identity.

## APNs Readiness

Repo support exists:

- `.env.example` declares `APNS_TEAM_ID`, `APNS_KEY_ID`, `APNS_PRIVATE_KEY`, `APNS_BUNDLE_ID`, and `APNS_USE_SANDBOX`.
- `services/native_push_readiness.py` validates APNs key loading and private-key formatting without exposing secrets.
- `services/pulsesoc_notification_system.py` has APNs provider send logic.

Remaining external setup:

- Apple Developer Program access.
- APNs key or certificate that is valid for `com.pulsesoc.nativeapp`.
- Provisioning profile for `com.pulsesoc.nativeapp`.
- Backend runtime APNs configuration or scoped readiness override for the native QA identity.
- `APNS_USE_SANDBOX=true` for development/sandbox device builds if applicable.
- A physical iPhone with the installed `com.pulsesoc.nativeapp` development/internal build.

APNs QA commands:

```bash
venv/bin/python scripts/push_credentials_readiness_audit.py --allow-missing-runtime-env
venv/bin/python scripts/push_credentials_readiness_audit.py --json --allow-missing-runtime-env
```

Pass criteria:

- APNs readiness reports credentials loaded and parseable.
- APNs bundle ID matches the installed native QA app target: `com.pulsesoc.nativeapp`.
- Alert push to iPhone appears on lock screen.
- Tapping the notification routes to native Alert Management.
- Provider failures are logged without crashing alert delivery.
- No production `com.pulsesoc.app` credential, provisioning, or provider state is modified.

## FCM Readiness

Repo support exists:

- `.env.example` declares `FCM_PROJECT_ID`, `FCM_CLIENT_EMAIL`, `FCM_PRIVATE_KEY`, and legacy `FCM_SERVER_KEY`.
- `services/native_push_readiness.py` validates Firebase Admin initialization without exposing secrets.
- `services/pulsesoc_notification_system.py` has FCM provider send logic.

Remaining external setup:

- Firebase project for the selected app identity.
- Android app registered with package `com.pulsesoc.nativeapp` for the parallel native app.
- FCM service account credentials available as runtime environment variables.
- Physical Android device or emulator with Google Play services.

Pass criteria:

- FCM readiness reports initialized safely.
- Android receives a market alert notification.
- Android uses a high-importance alert channel.
- Tapping the notification routes to native Alert Management.
- `DeviceNotRegistered` or invalid token failures disable stale tokens.
- No existing production Android/WebView app provider identity is modified.

## Expo Push Readiness

Repo support exists:

- `mobile-native/src/api/push.ts` uses `Notifications.getExpoPushTokenAsync(...)`.
- `mobile-native/src/api/push.ts` passes `EXPO_PUBLIC_EXPO_PROJECT_ID` when configured.
- Registered tokens are sent to `/api/push/subscribe` with provider `expo`.
- `services/push_service.py` detects Expo push tokens and sends through `https://exp.host/--/api/v2/push/send`.
- `services/push_service.py` records Expo ticket IDs in `expo_push_tickets`.

Remaining external setup:

- Expo/EAS project linked for `mobile-native`.
- `EXPO_PUBLIC_EXPO_PROJECT_ID` set or available through EAS config.
- iOS/Android notification credentials configured in EAS for `com.pulsesoc.nativeapp`.
- Physical devices for push token registration.

Exact setup commands:

```bash
cd mobile-native
npx eas-cli login
npx eas-cli init
export EXPO_PUBLIC_EXPO_PROJECT_ID=<eas-project-id>
npm run build:ios:development
npm run build:android:development
```

Device test commands:

```bash
cd mobile-native
EXPO_PUBLIC_PULSE_API_BASE_URL=https://pulsesoc.com npm run start:qa
```

Then open the installed development build on the device, sign in, register push, and run native Alert Management channel tests.

Pass criteria:

- Device returns an Expo push token.
- `/api/push/subscribe` stores the token.
- `/api/alerts/channel-readiness` shows push ready for the signed-in device/user.
- `POST /api/alerts/test/push` returns a success or queued/sent state.
- `push_delivery_jobs` and `expo_push_tickets` show the provider path.

## EAS Build Identity

Current repository state:

- `mobile-native/app.json` declares iOS bundle identifier `com.pulsesoc.nativeapp`.
- `mobile-native/app.json` declares Android package `com.pulsesoc.nativeapp`.
- `mobile-native/eas.json` includes development, simulator, preview, and production profiles for the parallel native app.

Native provider/device QA build expectation:

- iOS development/internal builds must install as `com.pulsesoc.nativeapp`.
- Android development/internal builds must install as `com.pulsesoc.nativeapp`.
- EAS credentials must be created or selected for the native QA identity only.
- Existing `com.pulsesoc.app` credentials must remain untouched.

EAS identity checks:

```bash
cd mobile-native
npx expo config --type public
npx eas-cli credentials
```

## Push Token Registration Provider Metadata

Current repository state:

- Native registration sends Expo tokens to existing `/api/push/subscribe`.
- Native registration includes provider `expo`.
- Native registration includes subscription metadata and `device_type: "native"`.
- Native registration uses the existing session/account authority.

Native provider/device QA expectations:

- Token registration should identify the client as native, not the production WebView shell.
- The backend should be able to distinguish the native QA app identity from existing production app tokens through provider metadata, app version/build metadata, bundle/package identity where available, or scoped QA account/device records.
- Delivery debugging should correlate push subscription records, `push_delivery_jobs`, `expo_push_tickets`, and `notification_delivery_logs`.

Do not add native-only entitlement or alert-delivery logic to the client. The backend remains authoritative.

## SMS Readiness

Repo support exists:

- `.env.example` declares Brevo SMS variables.
- `services/sms_service.py` implements SMS readiness, test SMS, alert SMS, and delivery logging.
- Alert channel readiness checks SMS provider config, phone, phone verification, and opt-in.

Remaining external setup:

- Brevo SMS provider enabled.
- `BREVO_SMS_API_KEY` or configured Brevo SMS key path.
- `BREVO_SMS_SENDER` or `SMS_SENDER_NAME`.
- QA account with verified E.164 phone number.
- QA account opted in to SMS.

Pass criteria:

- `/api/alerts/channel-readiness` reports SMS ready for the QA account.
- `POST /api/alerts/test/sms` returns sent/queued state.
- QA phone receives SMS.
- `notification_delivery_logs` records the SMS provider status.
- Provider rejection logs `failed`, `not_configured`, or the returned provider status clearly.

## Email Readiness

Repo support exists:

- Email delivery uses `services/email_service.py`.
- Notification email delivery logs to `notification_delivery_logs`.
- Channel readiness reports email availability based on provider/account state.

Remaining external setup:

- Brevo email credentials configured.
- Verified sender configured.
- QA account with verified/reachable email.
- Email delivery queue/worker active if using queued jobs.

Pass criteria:

- `/api/alerts/channel-readiness` reports email ready.
- `POST /api/alerts/test/email` returns sent/queued state.
- QA inbox receives the alert test.
- Delivery logs include provider state and trace IDs where available.

## Telegram Readiness

Repo support exists:

- `services/alert_engine.py` checks Telegram token and user `telegram_chat_id`.
- `services/alert_engine.py` sends Telegram test/alert messages through Telegram Bot API.
- Telegram failures are logged to `notification_delivery_logs`.

Remaining external setup:

- Telegram bot token configured.
- QA account connected to Telegram Companion.
- QA Telegram chat ID stored on the user record.

Pass criteria:

- `/api/alerts/channel-readiness` reports Telegram ready.
- `POST /api/alerts/test/telegram` returns sent state.
- QA Telegram account receives message.
- Provider failures are explicit and logged.

## Notification Tap Deep Links

Existing native routing handles:

- `/pulse/alerts`
- `/pulse/alerts/<alert_id>`
- `/dashboard/crypto/alerts`
- `/dashboard/crypto/alerts?alert_id=<alert_id>`
- `pulsesoc://alerts/<alert_id>` through custom scheme normalization
- HTTPS PulseSoc URLs such as `https://pulsesoc.com/dashboard/crypto/alerts?alert_id=<alert_id>`

Device QA matrix:

| Source | Expected Result |
| --- | --- |
| Push data `deep_link=/dashboard/crypto/alerts?alert_id=1` | Native Alert Management opens selected alert |
| Push data `url=/pulse/alerts/1` | Native Alert Management opens selected alert |
| Custom scheme `pulsesoc://alerts/1` | Native Alert Management opens selected alert |
| Missing target | Notification Center opens |
| Unsupported external host | Rejected and routes to Notification Center |

Not verified yet:

- Installed-app custom-scheme launch on iOS.
- Installed-app custom-scheme launch on Android.
- Cold-start notification tap.
- Background notification tap.
- Foreground notification tap.

Native QA target:

- All installed-app alert tap tests should use `com.pulsesoc.nativeapp`.
- Production `com.pulsesoc.app` tap behavior should not be changed during this QA pass.

## Lock-Screen Behavior Plan

Existing server payload includes:

- `lock_title`
- `lock_headline`
- `lock_body`
- `show_on_lock_screen`
- `sound_key`
- `vibration`
- `badge`
- `deep_link`

Existing native behavior:

- `Notifications.setNotificationHandler(...)` requests alert, sound, and badge presentation.
- Android channel `alerts` exists in native registration.

Known QA watch item:

- `services/push_service.py` defaults Expo `channelId` to `default` for non-message pushes unless payload data includes `channel_id` or `channelId`.
- Alert push provider/device QA should verify whether Android market alerts land on the intended alert channel. If they do not, add a scoped backend patch to set `channelId: "alerts"` for `market_alert` payloads.

Lock-screen pass criteria:

- iOS lock screen shows `PULSESOC ALERT` or approved PulseSoc alert copy.
- Android lock screen shows PulseSoc alert copy.
- Sound/vibration respects user/device settings.
- Badge updates where supported.
- Sensitive content remains constrained by server/user notification preferences.

## Provider Failure States

Expected failure states to verify:

- `not_configured`
- `permission_denied`
- `skipped_no_device`
- `skipped_by_preference`
- `rate_limited`
- `failed`
- `invalid`
- `dead_letter`
- `duplicate`

Failure-state pass criteria:

- Native UI shows the server-returned message.
- Alert Management does not claim delivery success.
- Failure is logged in `notification_delivery_logs`, `alert_delivery_jobs`, `push_delivery_jobs`, or `expo_push_tickets` as appropriate.
- Invalid push tokens are disabled.
- In-app fallback is created when external channels fail and the alert rule did not request in-app.

## Provider Success States

Expected success/accepted states to verify:

- `created`
- `queued`
- `sent`
- `accepted`
- `submitted`
- `delivered` where provider receipt supports it

Success-state pass criteria:

- Native UI displays the server-returned success message.
- Alert history shows the event after refresh.
- Delivery log includes provider, channel, alert rule ID, alert event ID, status, and timestamp.
- Push ticket or provider receipt can be traced with `push_trace_id` or provider ticket ID.

## Logs Needed For Debugging

Database tables:

- `alert_rules`
- `alert_events`
- `notification_delivery_logs`
- `alert_delivery_jobs`
- `push_delivery_jobs`
- `expo_push_tickets`
- `push_subscriptions`
- `user_device_tokens`
- `notification_preferences`
- `alert_worker_heartbeat`

Useful queries:

```sql
SELECT id, user_id, channel, status, provider, alert_rule_id, alert_event_id, error_message, created_at
FROM notification_delivery_logs
WHERE user_id = :user_id
ORDER BY id DESC
LIMIT 50;
```

```sql
SELECT id, alert_rule_id, symbol, status, delivery_status, message, created_at
FROM alert_events
WHERE user_id = :user_id
ORDER BY id DESC
LIMIT 50;
```

```sql
SELECT id, job_id, user_id, push_type, status, attempts, trace_id, last_error, provider_response, created_at, processed_at
FROM push_delivery_jobs
WHERE user_id = :user_id
ORDER BY id DESC
LIMIT 50;
```

```sql
SELECT provider_ticket_id, user_id, subscription_id, trace_id, status, error_code, checked_at, created_at
FROM expo_push_tickets
WHERE user_id = :user_id
ORDER BY id DESC
LIMIT 50;
```

```sql
SELECT worker_name, last_run_at, last_success_at, checked_count, triggered_count, error_count, last_error
FROM alert_worker_heartbeat
ORDER BY last_run_at DESC
LIMIT 20;
```

Runtime logs:

- Push trace logs containing `push_trace_id`.
- Alert worker logs around `evaluate_all_active_alerts(...)`.
- Provider response summaries from admin notification/push diagnostics.
- EAS device build logs when push token registration fails.
- iOS Console logs for APNs receipt/tap behavior.
- Android `adb logcat` logs for notification channel/tap behavior.

Provider delivery log checklist:

- Confirm alert rule ID.
- Confirm alert event ID.
- Confirm user/account ID.
- Confirm provider/channel name.
- Confirm delivery status.
- Confirm provider ticket or receipt ID when available.
- Confirm `push_trace_id` correlation.
- Confirm notification tap target/deep link.
- Confirm fallback notification creation when an external provider fails.

## Physical Device Alert Test Plan

### iPhone

1. Build/install the development build for `com.pulsesoc.nativeapp`.
2. Sign in with a QA account.
3. Accept notification permission.
4. Register push from Settings.
5. Confirm `/api/push/subscribe` stored the Expo token.
6. Open Alert Management.
7. Confirm channel readiness shows push ready.
8. Run `Test` on an alert.
9. Run channel test for push.
10. Lock the phone and trigger/send an alert.
11. Verify lock-screen display.
12. Tap the notification.
13. Confirm native Alert Management opens the correct alert detail.
14. Inspect delivery logs and Expo ticket/receipt state.
15. Confirm no production `com.pulsesoc.app` provider state changed.

### Android

1. Build/install the development build for `com.pulsesoc.nativeapp`.
2. Sign in with a QA account.
3. Accept notification permission.
4. Register push from Settings.
5. Confirm Android notification channels exist.
6. Confirm `/api/push/subscribe` stored the Expo token.
7. Confirm channel readiness shows push ready.
8. Run `Test` on an alert.
9. Lock the phone and trigger/send an alert.
10. Verify lock-screen display and sound/vibration.
11. Tap the notification.
12. Confirm native Alert Management opens the correct alert detail.
13. Inspect `adb logcat`, delivery logs, and Expo ticket/receipt state.
14. Confirm no production WebView app provider state changed.

## Rollback And No-Production-Impact Plan

Rules for native provider/device QA:

- Keep `com.pulsesoc.app` protected for the current production app.
- Use `com.pulsesoc.nativeapp` for native QA builds and provider credentials.
- Do not overwrite production APNs, FCM, Expo, SMS, email, or Telegram credentials.
- Do not change production notification copy, production WebView routes, production provider routing, or production subscription behavior during this QA identity setup.
- If native provider QA fails, disable or remove only the native QA credentials/builds/tokens and leave production provider state unchanged.

Rollback checks:

- Remove or disable native QA device tokens from the QA account if a token is bad.
- Disable native QA APNs/FCM credentials in EAS/provider consoles if misconfigured.
- Re-run channel readiness for the QA account.
- Confirm production `com.pulsesoc.app` readiness remains unchanged.
- Confirm production users continue receiving existing WebView/app notifications.

## Commands

Static repo verification:

```bash
npm ci --prefix mobile-native --no-audit --no-fund --progress=false
npm run --prefix mobile-native typecheck
cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose
venv/bin/python scripts/pulsesoc_alert_provider_device_qa_audit.py
git diff --check
```

Provider readiness:

```bash
venv/bin/python scripts/push_provider_configuration_audit.py
venv/bin/python scripts/push_credentials_readiness_audit.py --allow-missing-runtime-env
```

Device builds:

```bash
cd mobile-native
npm run build:ios:development
npm run build:android:development
npm run start:qa
```

Local device prerequisites:

```bash
xcrun simctl list devices available
adb devices
```

## What Cannot Be Verified Yet

Not verified in this setup pass:

- APNs real delivery.
- FCM real delivery.
- Expo push token registration on physical device.
- SMS provider delivery.
- Email provider delivery.
- Telegram provider delivery.
- Lock-screen behavior.
- Cold-start notification tap routing.
- Background notification tap routing.
- Physical-device deep links.
- Provider receipt reconciliation against real devices.

Reasons:

- No physical device flow was executed in this mission.
- Local machine still needs Android/iOS tooling completion from the previous device QA report.
- Provider credentials are external runtime secrets and were not exposed or modified.
- The native QA identity is now selected as `com.pulsesoc.nativeapp`, but credentials, provisioning, provider setup, and physical-device delivery remain unverified.

## Next Recommendation

Next highest-value action: configure provider credentials for `com.pulsesoc.nativeapp` and run the first physical-device push registration pass.

Do this before building another large native feature.

Safest next slice:

1. Keep `com.pulsesoc.app` protected for production.
2. Configure EAS project ID and APNs/FCM credentials for `com.pulsesoc.nativeapp`.
3. Add scoped multi-bundle APNs readiness support if backend readiness must validate both production and native QA identities.
4. Install development builds on one iPhone and one Android device.
5. Register push from Settings.
6. Run Alert Management push/channel tests.
7. Record delivery logs, provider responses, notification-tap results, and lock-screen behavior.
8. Fix only provider/device blockers found.
