# Pulse Notification Event Coverage

Date: 2026-06-06

## Summary

Pulse notification production wiring now routes core user actions into both:

- `pulse_notifications`
- `pulse_notification_deliveries`

The legacy `notifications` table is still preserved for compatibility, but centralized alerts now also mirror into the Pulse notification center.

## Covered Event Groups

- Social: follow, follow request accepted, mention, comment, reply, like, share, save
- Messages: new message, group/room invite semantic types, voice message
- Status: reaction, mention
- Videos/Reels: like, comment, mention
- Live: live started, replay available, live invite type support
- Premium: renewal/payment success/payment failure mapped through premium alert types
- Security: new login, new device, password changed, email/email-change security type support
- Roast/Arena Battle: challenge, accepted challenge, battle-result type support

## Implementation Notes

- `notify_user(...)` now writes a Pulse notification and an in-app delivery row.
- `send_user_alert(...)` now mirrors legacy alerts into `pulse_notifications`.
- Video detail like/comment actions use video-specific endpoints so they can create `video_like`, `video_comment`, and `video_mention`.
- Mentions are detected from `@public_player_id` handles and routed with contextual deep links.
- Live start/replay notifications fan out to followers with a capped recipient count.

## Known External Validation Needed

Real browser/device push and Brevo account checks require account/device access. These were not operated from this turn because browser-control tools were not available.
