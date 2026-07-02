# PulseSoc Notification Phase 2 Delivery Adapters

## Scope

Phase 2 extends the PulseSoc Notification System foundation with real delivery adapter plumbing for Web Push/PWA, FCM, APNs, Brevo Email, and Brevo SMS. The implementation keeps in-app notifications as the stable source of truth and routes delivery jobs through provider-aware adapters without fake success states.

## What Already Existed

- A Phase 1 notification foundation in `services/pulsesoc_notification_system.py` with notification records, preferences, delivery jobs, device-token records, unread counts, and admin simulation helpers.
- Web Push and Expo push infrastructure in `services/push_service.py`, including subscription delivery, provider failure handling, and invalid subscription cleanup.
- Push subscription APIs in `bot.py`, including `/api/push/public-key`, `/api/push/status`, and `/api/push/subscribe`.
- A service worker at `static/service-worker.js` with push event and notification click handling.
- A notification frontend controller at `static/notifications.js` with notification list, unread badge refresh, preferences, and push subscription support.
- Brevo transactional email support in `services/email_service.py`.
- Brevo SMS support in `services/sms_service.py`, including safe opt-in and verified-phone checks.
- Legacy event-source code remains in places such as `services/notification_service.py`, `services/alert_engine.py`, Messenger routes, live routes, security flows, and payment/subscription handlers. Phase 2 documents that surface but does not aggressively reroute every event source yet.

## What Was Missing

- Central Phase 2 adapter routing from notification delivery jobs.
- Adapter readiness checks for Web Push, FCM, APNs, Brevo Email, and Brevo SMS.
- Provider-aware delivery job statuses for `config_missing`, `skipped_by_preference`, `skipped_no_device`, and `skipped_no_contact`.
- Sound and vibration metadata on notifications and push payloads.
- WEB_PUSH environment aliases alongside the existing VAPID names.
- Safe permission onboarding before browser notification permission prompts.
- Admin-only delivery-job drain endpoint for QA.
- A Phase 2 migration and audit script.

## What Was Created Or Repaired

- Added Phase 2 delivery routing to `services/pulsesoc_notification_system.py`.
- Added Web Push, FCM, APNs, Brevo Email, and Brevo SMS adapter paths with honest skip/failure behavior.
- Added retry/backoff handling and provider response tracking for delivery jobs.
- Added notification `sound_key` and `vibration_json` support.
- Added device-token `push_provider` and `environment` fields.
- Added `WEB_PUSH_PUBLIC_KEY`, `WEB_PUSH_PRIVATE_KEY`, and `WEB_PUSH_SUBJECT` support while preserving existing `VAPID_*` variables.
- Hardened service-worker notification URLs with `safeNotificationUrl(...)`.
- Added PulseSoc-styled push permission onboarding before the browser permission prompt.
- Added `POST /api/admin/notifications/process-delivery` for admin-only QA processing.
- Added `migrations/pulsesoc_notification_delivery_phase2.sql`.
- Added `scripts/notification_delivery_adapters_phase2_audit.py`.

## Environment Variables

Required or supported variables:

- `WEB_PUSH_PUBLIC_KEY`
- `WEB_PUSH_PRIVATE_KEY`
- `WEB_PUSH_SUBJECT`
- `VAPID_PUBLIC_KEY`
- `VAPID_PRIVATE_KEY`
- `VAPID_SUBJECT`
- `FCM_PROJECT_ID`
- `FCM_CLIENT_EMAIL`
- `FCM_PRIVATE_KEY`
- `FCM_SERVER_KEY`
- `APNS_TEAM_ID`
- `APNS_KEY_ID`
- `APNS_PRIVATE_KEY`
- `APNS_BUNDLE_ID`
- `APNS_USE_SANDBOX`
- `BREVO_API_KEY`
- `BREVO_SENDER_EMAIL`
- `BREVO_SENDER_NAME`
- `BREVO_REPLY_TO`
- `SUPPORT_EMAIL`
- `PRODUCT_NAME`
- `COMPANY_NAME`
- `BREVO_SMS_SENDER`
- `SMS_SENDER_NAME`
- `PULSESOC_NOTIFICATION_DELIVERY_AUTOPROCESS_ENABLED`
- `PULSESOC_NOTIFICATION_DELIVERY_BATCH_SIZE`

## Adapter Status

### Web Push

Web Push is wired through the existing `push_service.send_push(...)` path. It can send real browser/PWA notifications when a valid subscription exists and Web Push/VAPID keys are configured. Missing keys or missing devices produce explicit skip/config states instead of fake success.

### FCM

FCM adapter structure is present for Android/native token delivery. It supports legacy server-key delivery when configured and has a modern service-account readiness path. If credentials are missing, jobs are marked `config_missing`.

### APNs

APNs adapter structure is present for iOS/iPadOS native token delivery. It builds APNs payloads with title, body, badge, sound, deep link, and category metadata. If APNs credentials or optional runtime dependencies are missing, jobs are marked safely instead of pretending to send.

### Brevo Email

Brevo Email is connected through the existing email service. Email delivery is restricted to important default categories such as security, payment, account, verification, marketplace/order, creator payout, and system notifications unless explicitly requested by preferences.

### Brevo SMS

Brevo SMS is connected through the existing SMS service. SMS delivery is restricted to urgent or high-priority categories and still requires a verified phone number plus user opt-in. Missing contact/config/preference states are tracked explicitly.

## Event Source Audit

- Messenger message notifications currently have existing push traces and legacy notification paths.
- Live started/invite/co-host flows exist in the live route and service layer, but full adapter migration is left for Phase 3.
- Like/comment/follow/repost events still need a clean event-source migration pass.
- Security login/new-device/password-change events are good candidates for immediate adapter routing.
- Payment/subscription events already have email/payment infrastructure and should be migrated carefully to avoid duplicate provider sends.
- Crypto alerts and verification/admin events can route through the notification foundation once their existing emitters are consolidated.

## Security Checks

- Users can only register and manage their own device tokens through authenticated routes.
- Admin test processing is protected by admin API authorization.
- Provider secrets stay server-side and are referenced only through environment variables.
- Web Push public key is the only provider key exposed to the frontend.
- Notification click URLs are sanitized in the service worker before opening.
- Sensitive preview text is reduced when notification preview privacy is disabled.
- Delivery jobs record skipped/failure states instead of silently failing.

## QA Results

Automated verification passed:

- Python compile checks for notification services, push service, bot routes, and audits.
- JavaScript syntax checks for notification frontend and service worker.
- Phase 1 notification foundation audit.
- Phase 2 delivery adapter audit.
- `git diff --check`.

The Phase 2 audit verifies:

- Provider adapters exist.
- Adapter configuration-missing behavior is safe.
- Missing device/contact/preference states are tracked.
- Sound/vibration metadata is persisted.
- Service worker deep links are sanitized.
- Permission onboarding is present.
- PostgreSQL-compatible migration does not use SQLite-only autoincrement syntax.

## Known Limitations

- Production provider credentials were not validated from this local environment.
- APNs/FCM native locked-screen delivery requires real iOS/Android device-token registration and provider credentials.
- Web Push end-to-end delivery requires configured Web Push keys and an authenticated browser subscription.
- Provider receipt webhooks are not implemented in this phase.
- Event sources are not fully migrated from every legacy notification path yet.

## Phase 3 Remaining Work

- Migrate Messenger, Live, Security, Payment, Premium, Marketplace, Verification, and Crypto event sources into the central event intake.
- Add provider receipt webhook handling.
- Add digests, grouping, batching, and quiet-hour scheduling UI.
- Complete APNs/FCM real-device QA.
- Add CallKit/Android incoming-call notification flows.
- Add admin delivery observability UI.
