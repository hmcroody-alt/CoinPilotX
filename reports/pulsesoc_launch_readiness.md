# PulseSoc App Store Launch Readiness

- Generated: 2026-07-02T01:03:04+00:00
- Recommendation: **DO NOT RELEASE**
- Passing checks: 66
- Warnings: 1
- Manual/unverified gates: 5
- Failed gates: 0
- Release blockers: 5

## Release Decision

Release is allowed only when this report says RELEASE and production/manual gates are verified.

## Blockers

- **manual release / Railway runtime/log watch**: Set PULSESOC_RAILWAY_WATCH_VERIFIED=1 after checking restarts, memory/CPU, 5xx rate, DB locks, and tracebacks.
- **manual release / physical iPhone/PulseShell QA**: Set PULSESOC_PHYSICAL_DEVICE_QA_VERIFIED=1 after launch, login, feed, Reels, Live Studio, push, upload, and deep-link checks pass on the approved build.
- **manual release / App Store Connect release status**: Set PULSESOC_APP_STORE_READY=1 only when the approved version is Pending Developer Release or Ready for Distribution.
- **manual release / provider credentials**: Set PULSESOC_PROVIDERS_READY=1 after Brevo, push, Stripe, Mux/LiveKit, and Cloudflare/Railway status are confirmed.
- **manual release / monitoring staffing**: Set PULSESOC_RELEASE_MONITOR_READY=1 when someone is watching the 0-15m, 15-60m, 1-6h, and 24h windows.

## Monitoring Plan

- 0-15 minutes: Railway status, restart count, request latency, HTTP 5xx rate, signup/login/reset errors.
- 15-60 minutes: DB locks, queue backlog, push/email/SMS provider errors, Live start errors.
- 1-6 hours: memory/CPU, bot traffic, provider throttling, Stripe webhook retries, Cloudflare/Railway rate limits.
- First 24 hours: support queue, App Store rollout metrics, crash reports, stale asset/cache complaints.

## Rollback Plan

- Redeploy the last known good commit from Git/Railway if crash loops or sustained 5xx appear.
- Use environment kill switches: PULSESOC_DISABLE_SIGNUP, PULSESOC_DISABLE_LIVE, PULSESOC_DISABLE_COHOST, PULSESOC_FREEZE_PAYMENTS, PULSESOC_THROTTLE_MESSAGING, PULSESOC_DISABLE_UPLOADS.
- Pause providers with BREVO_EMAIL_ENABLED=0, BREVO_SMS_ENABLED=0, PUSH_ASYNC_DELIVERY_ENABLED=0, EMAIL_OPPORTUNISTIC_PROCESSOR_ENABLED=0, PUSH_OPPORTUNISTIC_PROCESSOR_ENABLED=0.
- Disable risky optional layers with PULSE_AI_ENABLED=false, PULSE_CRYPTO_AI_ENABLED=false, PULSE_ADS_BILLING_ENABLED=false, PULSE_PREMIUM_DISABLED=true.
- Keep /health and /health/database under watch before returning traffic to full features.

## App Store Connect Step When Green

App Store Connect -> PulseSoc -> approved version -> Release This Version -> Confirm.

## Detailed Checks

| Category | Gate | Status | Detail | Evidence |
| --- | --- | --- | --- | --- |
| route health | health | PASS | HTTP 200, 0.7ms | `/health` |
| route health | homepage | PASS | HTTP 302, 4.9ms | `/` |
| route health | signup | PASS | HTTP 302, 4.8ms | `/signup` |
| route health | login | PASS | HTTP 302, 4.7ms | `/login` |
| route health | forgot password | PASS | HTTP 200, 23.9ms | `/forgot-password` |
| route health | PulseSoc Home | PASS | HTTP 200, 33.1ms | `/pulse` |
| route health | Reels | PASS | HTTP 200, 14.8ms | `/pulse/reels` |
| route health | Messages | PASS | HTTP 200, 7.1ms | `/pulse/messages` |
| route health | Notifications | PASS | HTTP 200, 24.8ms | `/pulse/notifications` |
| route health | Live Studio | PASS | HTTP 200, 25.2ms | `/pulse/live/studio` |
| auth stability | forgot password generic response | PASS | HTTP 200; account existence not exposed | `/forgot-password` |
| database schema | users table | PASS | ready | `users` |
| database schema | sessions table | PASS | ready | `sessions` |
| database schema | password_reset_tokens table | PASS | ready | `password_reset_tokens` |
| database schema | email_verification_tokens table | PASS | ready | `email_verification_tokens` |
| database schema | failed_email_queue table | PASS | ready | `failed_email_queue` |
| database schema | pulse_notifications table | PASS | ready | `pulse_notifications` |
| database schema | notification_delivery_logs table | PASS | ready | `notification_delivery_logs` |
| database schema | push_delivery_jobs table | PASS | ready | `push_delivery_jobs` |
| database schema | user_device_tokens table | PASS | ready | `user_device_tokens` |
| database schema | conversations table | PASS | ready | `conversations` |
| database schema | pulse_conversations table | PASS | ready | `pulse_conversations` |
| database schema | pulse_messages table | PASS | ready | `pulse_messages` |
| database schema | comm_v2_conversations table | PASS | ready | `comm_v2_conversations` |
| database schema | comm_v2_messages table | PASS | ready | `comm_v2_messages` |
| database schema | pulse_live_sessions table | PASS | ready | `pulse_live_sessions` |
| database schema | pulse_live_guest_requests table | PASS | ready | `pulse_live_guest_requests` |
| database schema | pulse_live_viewers table | PASS | ready | `pulse_live_viewers` |
| database schema | pulse_live_chat table | PASS | ready | `pulse_live_chat` |
| database schema | pulse_posts table | PASS | ready | `pulse_posts` |
| database schema | crypto_alerts table | PASS | ready | `crypto_alerts` |
| database indexes | push_delivery_jobs hot indexes | PASS | ready | `push_delivery_jobs` |
| database indexes | failed_email_queue hot indexes | PASS | ready | `failed_email_queue` |
| database indexes | pulse_live_sessions hot indexes | PASS | ready | `pulse_live_sessions` |
| database indexes | pulse_live_guest_requests hot indexes | PASS | ready | `pulse_live_guest_requests` |
| database indexes | pulse_notifications hot indexes | PASS | ready | `pulse_notifications` |
| queue safety | SMS launch posture | PASS | SMS outbox exists or SMS provider is disabled-safe | `BREVO_SMS_ENABLED` |
| security | production debug mode | PASS | Flask run path does not enable debug=True | `bot.py` |
| security | no obvious committed secrets | PASS | secret-like assignment patterns were not found | `static scan` |
| security | Stripe webhook signature | PASS | unsigned live webhooks are rejected | `bot.py/services/payment_provider.py` |
| security | PulseShell secrets | PASS | mobile shell does not expose server secrets | `mobile/pulse-react-native/App.tsx` |
| security | account deletion reachable | PASS | delete account UI/API are present | `templates/account.html` |
| security | report/block reachable | PASS | report/block surfaces remain wired | `bot.py/pulse_communications_v2/routes.py` |
| kill switches | signup | PASS | PULSESOC_DISABLE_SIGNUP is referenced | `PULSESOC_DISABLE_SIGNUP` |
| kill switches | live | PASS | PULSESOC_DISABLE_LIVE is referenced | `PULSESOC_DISABLE_LIVE` |
| kill switches | cohost | PASS | PULSESOC_DISABLE_COHOST is referenced | `PULSESOC_DISABLE_COHOST` |
| kill switches | payments | PASS | PULSESOC_FREEZE_PAYMENTS is referenced | `PULSESOC_FREEZE_PAYMENTS` |
| kill switches | messaging | PASS | PULSESOC_THROTTLE_MESSAGING is referenced | `PULSESOC_THROTTLE_MESSAGING` |
| kill switches | uploads | PASS | PULSESOC_DISABLE_UPLOADS is referenced | `PULSESOC_DISABLE_UPLOADS` |
| kill switches | premium | PASS | PULSE_PREMIUM_DISABLED is referenced | `PULSE_PREMIUM_DISABLED` |
| kill switches | ai | PASS | PULSE_AI_ENABLED is referenced | `PULSE_AI_ENABLED` |
| kill switches | sms | PASS | BREVO_SMS_ENABLED is referenced | `BREVO_SMS_ENABLED` |
| kill switches | email | PASS | BREVO_EMAIL_ENABLED is referenced | `BREVO_EMAIL_ENABLED` |
| kill switches | push | PASS | PUSH_ASYNC_DELIVERY_ENABLED is referenced | `PUSH_ASYNC_DELIVERY_ENABLED` |
| kill switches | marketplace billing | PASS | PULSE_ADS_BILLING_ENABLED is referenced | `PULSE_ADS_BILLING_ENABLED` |
| kill switches | crypto AI | PASS | PULSE_CRYPTO_AI_ENABLED is referenced | `PULSE_CRYPTO_AI_ENABLED` |
| kill switches | server kill switch map | PASS | high-risk request switches are enforced in before_request | `services/pulse_security_core.py` |
| notification safety | durable push queue | PASS | push uses durable queued worker with bounded retries | `services/push_service.py` |
| notification safety | durable email queue | PASS | email uses outbox queue with bounded retries and idempotency | `bot.py` |
| notification safety | push payload deep links | PASS | push payloads support badge/sound/deep links | `static/sw.js/services/push_service.py` |
| live safety | Live Studio route | PASS | Studio and start API exist | `bot.py` |
| live safety | co-host launch flag | PASS | co-host can be disabled without deploy | `services/pulse_security_core.py` |
| live safety | server-generated LiveKit tokens | PASS | LiveKit JWT generation/claim logging stays server-side | `bot.py` |
| performance | service worker cache version | PASS | cache names: coinplotx-cache-v22-launch-readiness, coinplotx-cache-v22-launch-readiness | `static/sw.js/static/service-worker.js` |
| performance | runtime JS no-store | PASS | runtime JS/CSS fetches bypass stale cache | `static/sw.js` |
| mobile | PulseShell App Review audit surface | PASS | native shell has bridge and permission strings | `mobile/pulse-react-native` |
| production | production HTTP gate not run | WARN | Set PULSESOC_LAUNCH_BASE_URL=https://pulsesoc.com and rerun before release. | `` |
| manual release | Railway runtime/log watch | MANUAL | Set PULSESOC_RAILWAY_WATCH_VERIFIED=1 after checking restarts, memory/CPU, 5xx rate, DB locks, and tracebacks. | `` |
| manual release | physical iPhone/PulseShell QA | MANUAL | Set PULSESOC_PHYSICAL_DEVICE_QA_VERIFIED=1 after launch, login, feed, Reels, Live Studio, push, upload, and deep-link checks pass on the approved build. | `` |
| manual release | App Store Connect release status | MANUAL | Set PULSESOC_APP_STORE_READY=1 only when the approved version is Pending Developer Release or Ready for Distribution. | `` |
| manual release | provider credentials | MANUAL | Set PULSESOC_PROVIDERS_READY=1 after Brevo, push, Stripe, Mux/LiveKit, and Cloudflare/Railway status are confirmed. | `` |
| manual release | monitoring staffing | MANUAL | Set PULSESOC_RELEASE_MONITOR_READY=1 when someone is watching the 0-15m, 15-60m, 1-6h, and 24h windows. | `` |
