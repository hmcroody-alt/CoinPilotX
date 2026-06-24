# PulseSoc Mission Control Dashboard Inventory

Generated for the role-aware PulseSoc Dashboard rebuild.

## Current Entry Point

- `/dashboard` is now the user-facing Mission Control page.
- `/api/dashboard/mission-control` returns the same sanitized widget inventory for the current authenticated user.
- Existing portfolio/dashboard APIs remain in place for backward compatibility.

## Role Model

- Free/member users see core account, network, media, safety, marketplace, and creator basics.
- Premium-only modules remain visible but locked when `free_visible_locked` is enabled.
- Creator-only and seller-only modules are locked unless the user has creator/seller signals.
- Admin/moderator modules are hidden from normal users, never shown as locked cards.
- Server-side role checks use the current account plus an active `admin_users` match or active admin session.

## Dashboard Categories

### Account Command Center

- Profile
- Verification
- Account Health
- Security
- Settings
- Advanced Security

### Pulse Network

- Notifications
- Messages
- Friends
- Followers / Following
- Groups
- Status Activity
- Community Activity

### Creator Studio

- My Posts
- Reels
- Videos
- Statuses
- Live Studio
- Audience Analytics
- Content Performance
- Best Posting Time
- Creator Score
- Creator Tools

### Intelligence Center

- Scam Shield
- Scam Alerts
- Pulse Intelligence
- AI Insights
- Safety Scan
- Recommendations

### Economy & Earnings

- Wallet
- Earnings
- Marketplace
- Seller Tools
- Subscriptions
- Premium
- Creator Revenue
- Payouts

### Pulse Radio & Media

- Pulse Radio
- Music Library
- Video Library
- Saved Media
- Upload Music
- Playlists

### Moderation / Safety

- Reports Submitted
- Blocked Users
- Appeals
- Moderation Status
- Content Removals

### Admin / Moderator Only

- Reports Queue
- Blocked IPs
- Suspicious Domains
- Admin Actions
- Audit Logs
- Platform Metrics
- Infrastructure Health
- Push Notification Health
- LiveKit / Mux Health

## Security Notes

- Widget access is computed in `services/pulse_dashboard_mission_control.py`.
- Normal users do not receive admin/moderator widgets in the HTML or JSON payload.
- User metrics are scoped to the authenticated user's own id.
- Locked cards route to real upgrade or setup destinations.
- No secrets, tokens, provider credentials, filesystem paths, or raw database internals are emitted.

## Remaining Risk

- Some metrics depend on optional tables and return safe zero values when those tables are absent.
- Admin/moderator entitlement is conservative and should be revisited if a separate staff identity provider is introduced.
