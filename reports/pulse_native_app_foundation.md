# Pulse Native App Foundation

Status: prepared, not scaffolded yet.

Decision:
- React Native is the recommended native app path because Pulse already has a JavaScript-heavy frontend and existing web APIs.

Boundary:
- Native implementation was not started in this phase because the mission explicitly says not to start native app implementation until the web/in-app notification system is working.

Ready API surfaces:
- Pulse feed
- Reels
- Videos
- Messages
- Notifications
- Notification preferences
- Push subscription/device registration foundation

Recommended next native scope:
- Create a React Native project after web notification QA passes.
- Use existing login/session APIs.
- Add device token registration through the same notification device model.
- Add deep links for Pulse feed, messages, videos, reels, notifications, and premium.
