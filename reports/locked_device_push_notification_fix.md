# Locked-Device Push Notification Fix

## Root cause

New comment notifications were reaching locked devices because the comment path used the central PulseSoc notification helper with a `push` channel and a category that could be enabled for push.

Most other event paths were either:

- using the new central helpers but falling back to category defaults where `push` was disabled unless the user had an explicit category row, or
- entering through the legacy compatibility bridge, which forced `channels=["in_app"]` and could never create a locked-device push job from the central notification system.

That meant many events were saved as in-app notifications and appeared after opening PulseSoc, but they did not reliably create a push delivery job for locked-device delivery.

## What changed

- Added locked-device push defaults for high-value categories: messages, calls, comments, mentions, follows, live, security, payments, billing, verification, marketplace, creator, premium, crypto, and system.
- Kept noisy categories preference-controlled: likes, reposts, broad social, suggestions, digest, and marketing.
- Split central notification categories so calls and follows are not blocked by noisy social defaults.
- Updated the legacy bridge so older notification calls infer eligible delivery channels instead of forcing in-app only.
- Preserved global push permission gating: push still requires `enable_push_notifications` and a registered push device/subscription.
- Added sound and vibration metadata for calls, social interactions, creator, verification, premium, and system events.
- Added APNs/FCM invalid token cleanup for central device-token rows.

## Event types verified

The locked-device audit verifies central push delivery jobs for:

- New comments and replies
- New messages
- Image messages
- Voice messages
- Video messages
- Missed calls
- Incoming calls
- Follows
- Likes when the likes category is enabled
- Reposts when the reposts category is enabled
- Mentions
- Live started
- Live invite
- Co-host request
- Security login/new device
- Password changed
- Payment failed
- Premium activated
- Verification/creator update
- Admin warning
- Critical crypto alert
- System announcement

It also verifies:

- Likes remain preference-controlled when not enabled.
- Blocked actors do not create pushable social notifications.
- Each pushable event has a deep link, category, priority, sound key, and vibration metadata.

## Provider behavior

The fix does not fake delivery. If APNs, FCM, or Web Push credentials are missing, central jobs still enter the push delivery path and then skip/fail honestly with provider status such as `config_missing` or `skipped_no_device`.

For browser/PWA users, `/api/push/subscribe` continues to register both the legacy `push_subscriptions` path and the central `notification_device_tokens` path. The central dispatcher continues routing Web Push through `push_service`, which is the working locked-device provider path.

## QA performed

- `venv/bin/python -m py_compile services/pulsesoc_notification_system.py scripts/locked_device_push_notification_audit.py`
- `venv/bin/python scripts/locked_device_push_notification_audit.py`

Result: passed with 22 central push delivery jobs created and 1 active audit device token.

## Remaining production checks

Actual phone lock-screen delivery still depends on:

- Valid APNs/FCM/Web Push credentials in production.
- User push permission granted.
- Active device/subscription rows.
- User category preferences not explicitly disabling that notification type.
- OS-level notification settings allowing lock-screen alerts, sound, and vibration.

The server-side event-to-push-job path is now verified. Production device QA should confirm each provider receives and displays the push while the phone is locked.
