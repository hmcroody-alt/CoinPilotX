# Brevo Email and SMS Notification Setup

Date: 2026-06-06

## Summary

Pulse now has a centralized multi-channel notification foundation for in-app, Brevo email, and Brevo SMS delivery. The app creates the in-app notification first, then attempts email and SMS only when the provider is enabled, the user preference allows it, and the user has the required destination data.

Security notifications remain mandatory for in-app and email delivery. SMS still requires an opted-in, verified phone number.

## Brevo Account Status

- Brevo login: confirmed through QA Browser.
- Sender identities: previously verified for `noreply@pulsesoc.com`, `support@pulsesoc.com`, and `security@pulsesoc.com`.
- Sending domain: `pulsesoc.com` is documented as authenticated in Brevo.
- SPF: documented as configured through the PulseSoc DNS setup.
- DKIM: documented as configured and accepted by Brevo.
- DMARC: documented as present/accepted by Brevo, with monitor-style readiness noted in the earlier DNS report.
- Transactional email: account dashboard shows transactional activity.
- SMS: Brevo dashboard shows SMS available in plan usage, but `0 credits left`.
- Webhook callbacks: not changed during this pass. Delivery status logging is ready in the app; provider-side webhook callback creation should be done only after confirming the final Brevo callback event set.

Evidence from earlier verified setup remains in:

- `reports/pulsesoc_brevo_verification.md`
- `reports/pulsesoc-evidence/brevo-pulsesoc-authenticated-2026-06-06.png`
- `reports/pulsesoc-evidence/brevo-pulsesoc-senders-verified-2026-06-06.png`

## Railway Variables

Verified present in Railway without exposing values:

- `BREVO_API_KEY`
- `BREVO_SENDER_EMAIL`
- `BREVO_SENDER_NAME`
- `BREVO_SMS_API_KEY`
- `BREVO_SMS_ENABLED`
- `BREVO_SMS_SENDER`
- `BREVO_SMS_REPLY_EMAIL`

Added in Railway:

- `BREVO_EMAIL_ENABLED=true`

Required values should remain stored only in Railway or other secret storage. No API keys were read, printed, committed, or rotated.

## App Infrastructure Added

- `NotificationService.send()`
- `sendEmailNotification()`
- `sendSmsNotification()`
- `sendInAppNotification()`
- `sendMultiChannelNotification()`

The centralized service supports:

- In-app notifications through `pulse_notifications`.
- Delivery rows through `pulse_notification_deliveries`.
- Legacy/admin delivery visibility through `notification_delivery_logs`.
- Brevo transactional email through the existing email service.
- Brevo SMS through the existing SMS service.
- Per-category preferences.
- Mandatory security email/in-app delivery.
- SMS opt-in and verified-phone checks.
- Email and SMS rate limiting.
- Duplicate notification suppression.

## Notification Categories Connected

- Account
- Premium
- Social
- Live
- Status
- Marketplace
- Crypto
- Security

Representative events are mapped for signup, email/phone verification, password reset, Founder Premium activation, payment success/failure, follows, comments, direct messages, live events, status reactions, marketplace updates, crypto alerts, and security events.

## Templates Created

Reusable Pulse-branded app templates were added for:

- Welcome email
- Founder Premium activation
- Payment receipt
- Password reset
- New follower
- New message
- Crypto alert
- Security alert

Templates use Pulse branding, PulseSoc.com, and the legal line that Pulse is operated by CoinPlotXAI Inc.

## Admin Visibility

Existing admin notification pages remain available:

- `/admin/notifications`
- `/admin/notifications/email`
- `/admin/notification-delivery`

These expose provider health, email health, delivery status, failed sends, and notification logs without exposing secret values.

## QA Results

Completed:

- Railway Brevo variables inspected safely.
- `BREVO_EMAIL_ENABLED=true` added.
- Brevo dashboard login confirmed.
- Brevo dashboard showed email usage and transactional activity.
- Brevo dashboard showed SMS channel availability but zero SMS credits.
- App-side notification audit passed.

Blocked:

- Live SMS send QA is blocked by `0 credits left` in Brevo and should not proceed without approval to purchase or allocate SMS credits.
- Live end-to-end email/SMS event QA requires a controlled test account and destination inbox/phone number.
- Provider-side Brevo webhook callbacks were not changed in this pass to avoid accidental delivery-status side effects before final callback scope approval.

## Remaining Blockers

- Confirm whether SMS credits should be purchased or assigned.
- Confirm country-specific SMS sender requirements for the initial Pulse user base.
- Create provider-side Brevo delivery webhooks only after choosing the exact delivery/open/click/bounce event set.
- Run real-world signup, password reset, Founder Premium, follower, message, and crypto alert tests with approved test inboxes and phone numbers.
