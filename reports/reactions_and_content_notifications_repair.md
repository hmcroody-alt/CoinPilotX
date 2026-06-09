# Reactions And Content Notifications Repair

Date: 2026-06-09

- Post, reel, video, comment, group-post, status, live, and message reaction tables/routes already exist.
- Post reactions use one reaction per user per post and allow reaction changes.
- Reel/video reactions route through the post reaction engine for consistent counts.
- Notification payloads include actor, entity, metadata, and deep links.
- Notification categories now explicitly include reaction, comment, reply, status view, chat, marketplace, teacher, premium, and security buckets.
- Immediate push delivery is attempted when a Pulse notification is created.

Remaining QA: run authenticated web/iOS/Android reaction flows with real users to confirm animation and haptic feel on device.

