# PulseSoc Push Real Device QA

Date: 2026-06-25

## Status

Code-level and local runtime audits pass, but real-device delivery is not marked complete in this report.

Physical device proof still requires the user to test the newly pushed build/deployment on:

- iPhone locked screen
- iPhone unlocked/foreground
- Android locked screen
- Android unlocked/foreground
- Installed PWA with browser closed, if PWA push is expected

## Required Test Matrix

1. User B logs into PulseSoc on iPhone and accepts notifications.
2. Confirm `/api/push/devices` shows an active iOS/Expo device for User B, with no full token exposed.
3. User A sends a direct message to User B while User B is inside the active conversation.
4. Expected: realtime message only, no noisy duplicate native push.
5. User B moves elsewhere in the app.
6. User A sends a direct message.
7. Expected: inbox preview/unread badge updates and push is allowed by policy.
8. User B locks the iPhone.
9. User A sends a direct message.
10. Expected: lock-screen notification with sound/vibration if OS settings allow.
11. Tap the notification.
12. Expected: exact conversation opens.
13. Repeat the same flow on Android.
14. Verify muted conversations suppress noisy push while preserving unread.
15. Verify private-preview mode hides message body on lock screen.
16. Verify invalid/uninstalled tokens are deactivated after provider errors.

## Evidence To Capture

- Device registration trace showing platform/provider/timestamp.
- Provider request/response trace with token hashes only.
- Lock-screen screenshot or recording for iPhone.
- Lock-screen screenshot or recording for Android.
- Tap-to-conversation recording.
- Admin notification-delivery page showing chat push attempts and outcomes.

## Completion Criteria

Do not mark PulseSoc chat notifications complete until at least one iPhone and one Android receive a real native chat notification, and tapping each notification opens the exact conversation.
