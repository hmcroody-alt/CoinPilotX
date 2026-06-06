# Pulse Real Device Notification QA

Date: 2026-06-06

## Status

Not completed in this coding turn. Real-device and Brevo account checks require live access to Chrome Desktop, Safari, iPhone Safari/PWA, Android Chrome, and Brevo delivery logs.

## Ready For Test

- Pulse notification tables exist: `pulse_notifications` and `pulse_notification_deliveries`.
- Web push subscription endpoint exists: `/api/push/subscribe`.
- Push unsubscribe endpoint exists: `/api/push/unsubscribe`.
- Notification list, unread count, mark-read, delete, and preferences APIs exist.
- React Native foundation now registers Expo push tokens through the existing push subscription pipeline.

## Required Device Matrix

- Chrome Desktop: permission prompt, delivery, click route, unsubscribe.
- Safari Desktop: permission behavior, delivery support, click route.
- iPhone Safari PWA: install behavior, permission flow, delivery, routing.
- Android Chrome: permission flow, delivery, background click behavior, routing.

## Brevo Email QA

Pending live account verification:

- Password reset email delivery.
- Security alert delivery.
- Premium alert delivery.
- Notification digest delivery.
- SPF pass.
- DKIM pass.
- DMARC pass/aligned or monitor-only result.

## Pass Criteria

- Notification created in app.
- Delivery row written.
- User receives the correct channel notification.
- Click opens the correct Pulse route.
- Unread count updates.
- Preferences prevent disabled channels from sending.
- No secrets, tokens, webhook secrets, SMTP passwords, or private keys are exposed.
