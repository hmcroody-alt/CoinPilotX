# PulseSoc Mobile API Integration Map

## Auth

- Session: `GET /api/mobile/auth/session`
- Login: `POST /api/mobile/auth/login`
- Signup: `POST /api/mobile/auth/register`
- Forgot password: `POST /api/mobile/auth/recover`
- Logout: `POST /api/mobile/auth/logout`

## Core Social

- Home Feed: `GET /api/pulse/feed`
- Create post: `POST /api/pulse/posts`
- Reels feed: `GET /api/pulse/reels/feed`
- Create reel: `POST /api/pulse/reels/create`
- Videos: `GET /api/pulse/videos`
- Profile: `GET /api/pulse/profile/me`
- Profile update: `POST /api/pulse/profile/update`
- Avatar upload: `POST /api/pulse/profile/avatar`
- Cover upload: `POST /api/pulse/profile/cover`

## Messages

- Conversations: `GET /api/pulse/messages/conversations`
- Conversation detail: `GET /api/pulse/messages/<conversation_id>`
- Send message: `POST /api/pulse/messages/<conversation_id>/send`
- Media upload: `POST /api/pulse/messages/media/upload`
- Communications v2 health and conversation APIs: `/api/pulse/comm/v2/*`

## Notifications and Push

- Notifications: `GET /api/pulse/notifications`
- Unread count: `GET /api/pulse/notifications/unread-count`
- Mark read: `POST /api/pulse/notifications/read`
- Preferences: `GET/PATCH /api/pulse/notifications/preferences`
- Push registration: `POST /api/push/subscribe`

## Marketplace and Payments

- Seller application: `POST /api/pulse/marketplace/seller/apply`
- Marketplace media upload: `POST /api/pulse/marketplace/media/upload`
- Create listing: `POST /api/pulse/marketplace/listings/create`
- Checkout: `POST /api/pulse/payments/checkout`

## Premium

- Checkout: `POST /api/premium/checkout`
- Activate: `POST /api/pulse/premium/activate`
- Identity effects: `GET/POST /api/pulse/premium/identity-effects`
- Profile theme: `GET/POST /api/pulse/premium/profile-theme`

## UNDX

- Chat: `POST /api/undx/chat`
- Agent council: `GET/POST /api/undx/agent-council`
- Kernel scan: `GET/POST /api/undx/kernel/scan`
- Kernel propose/apply/validate/git: `POST /api/undx/kernel/*`

## Media Upload

- General upload: `POST /api/pulse/media/upload`
- Mux direct upload: `POST /api/pulse/media/mux/direct-upload`
- Mux completion: `POST /api/pulse/media/mux/direct-upload/complete`
- Media status: `GET /api/pulse/media/<media_id>/status`
