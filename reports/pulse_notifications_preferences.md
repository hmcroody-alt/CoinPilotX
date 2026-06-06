# Pulse Notification Preferences

Status: implemented.

Page:
- `/pulse/settings/notifications`

Channels:
- In-app
- Push
- Email
- SMS

Categories:
- Messages
- Comments
- Likes
- Mentions
- Follows
- Lives
- Roast Battle
- Premium
- Security

Security:
- Security in-app notifications remain forced/recommended on by default.
- Email and push are enabled by default for security in the preference model.

Compatibility:
- Existing account notification experience settings remain available through `/api/notification-preferences`.
- Pulse category preferences use `/api/pulse/notifications/preferences`.
