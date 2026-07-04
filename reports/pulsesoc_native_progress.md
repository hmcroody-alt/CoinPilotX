# PulseSoc Native Migration Progress

Date: 2026-07-04

## Current Native State

The native app lives separately under `mobile-native/`. The current production WebView app remains untouched and continues to serve existing PulseSoc users.

Completed native foundations:

- App shell: Expo React Native, native stack/tab navigation, native deep-link configuration.
- Auth/session: login, signup, restore, logout through existing mobile auth APIs.
- API base URL/session safety: normalized API base URL, cookie-backed session reuse, network failure handling.
- Push registration: Expo push token registration through existing `/api/push/subscribe`.
- Mission Control: basic native connection to `/api/dashboard/mission-control`.
- Messenger: conversation list, conversation screen, text send, retry, receipts, typing, sync polling, offline cache, image/file/voice upload paths using existing Messenger APIs.
- Messenger hardening: corrupt-cache fallback, foreground/background sync recovery, upload-in-flight guard, long-thread list settings.
- Notifications: native notification center, unread/badge sync, mark read, mark all read, delete, preferences, push permission state, foreground badge refresh, background tap routing structure, and native/web target fallback.
- Home Feed + Post Detail: native feed list, pagination, pull-to-refresh, offline cache, post detail, comments, add comment, reactions, save, repost, share hook, image media cards, and `/pulse/post/<post_id>` deep-link routing through existing PulseSoc APIs.
- Pulse AI: basic chat through existing `/api/pulse/assistant/chat`.
- Profile: native current profile, public profile route, profile posts/media/about tabs, profile edit, avatar/cover upload/remove, profile theme selection, offline cache, and profile deep links through existing PulseSoc profile/feed/theme APIs.
- Settings: session controls, push registration, notification preferences entry.

Completed supporting reports/audits:

- `reports/pulsesoc_native_app_api_contract.md`
- `reports/pulsesoc_native_app_migration_plan.md`
- `reports/pulsesoc_native_dependency_graph.md`
- `reports/pulsesoc_native_phase1_device_qa.md`
- `reports/pulsesoc_native_messenger_progress.md`
- `reports/pulsesoc_native_messenger_device_qa.md`
- `reports/pulsesoc_native_notifications_progress.md`
- `reports/pulsesoc_native_feed_progress.md`
- `reports/pulsesoc_native_profile_progress.md`
- `scripts/pulsesoc_native_app_foundation_audit.py`
- `scripts/pulsesoc_native_phase1_device_qa_audit.py`
- `scripts/pulsesoc_native_messenger_audit.py`
- `scripts/pulsesoc_native_messenger_device_qa_audit.py`
- `scripts/pulsesoc_native_notifications_audit.py`
- `scripts/pulsesoc_native_feed_audit.py`
- `scripts/pulsesoc_native_profile_audit.py`

## Remaining Major Features

- Feed composer
- Reels native player
- Reels detail/actions/comments
- Status viewer
- Status creator
- Native media viewer
- Camera capture/upload/compression
- Live discovery
- Native LiveKit calls
- Full-screen incoming calls
- Growth Center
- Crypto/market alerts
- Intelligence Center
- Marketplace
- Premium/entitlements
- Creator Studio
- Groups/communities
- Saved content
- Search/discovery

## Codebase Reconnaissance

The next recommendation is based on the current codebase, not guesswork.

Existing backend/web surfaces inspected:

- Feed page and shell: `pulse_page_html(...)`, `/pulse`
- Feed API: `GET /api/pulse/feed`
- Post create: `POST /api/pulse/posts`
- Post detail: `GET /api/pulse/posts/<post_id>` and `GET /api/pulse/post/<post_id>`
- Post reactions: `POST /api/pulse/posts/<post_id>/react`
- Post comments: `GET/POST /api/pulse/posts/<post_id>/comments`
- Save, pin, repost, delete, report, follow: existing `/api/pulse/posts/*`, `/api/pulse/follow`, `/api/pulse/report`, saved-content APIs
- Media upload: existing `/api/pulse/media/upload` and `media_service.save_upload(...)`
- Feed engine: `services/pulse_feed_engine.py`
- Feed ranking: `services/pulse_feed_ranking_engine.py`
- Moderation: `services/pulse_moderation_engine.py`
- Notifications into feed/post targets: `static/notifications.js` and server target resolver
- Profile routes: `/pulse/profile`, `/pulse/profile/<profile_key>`, `/pulse/profile/edit`
- Profile APIs/media/theme: `/api/pulse/profile/me`, `/api/pulse/profile/update`, `/api/pulse/profile/avatar`, `/api/pulse/profile/cover`, `/api/pulse/profile/avatar/remove`, `/api/pulse/profile/cover/remove`, `/api/pulse/premium/profile-theme`
- Reels APIs: `/api/pulse/reels/feed`, `/api/pulse/reels/<reel_id>/react`, comments, save, repost, share, not-interested, follow creator
- Status APIs: `/api/pulse/status/rail`, `/api/pulse/status`, view, react, reply, share

Existing data/business logic that should remain server-authoritative:

- `pulse_posts`
- `pulse_comments`
- `pulse_reactions`
- `pulse_post_saves`
- `pulse_saved_items`
- `pulse_media_assets`
- `chat_media_uploads`
- `users`
- feed ranking and visibility rules
- moderation/risk status
- premium identity rendering data
- notification fanout
- mention notifications
- media storage/Mux processing
- saved-content collections
- follow graph

## Recommended Next Feature

Recommendation: build Native Reels Player + Reel Detail next.

This should come before Status creator, Camera capture, Marketplace, or Calls.

## Why This Comes Next

- Home Feed, Post Detail, Profile, Messenger, and Notifications now cover the core social graph and notification targets.
- The largest remaining user-facing web fallback is media consumption, especially Reels.
- The backend already exposes mature Reels APIs for feed, detail/actions/comments, save, repost, share, not-interested, and creator follow behavior.
- Reels will establish reusable native video playback, media controls, creator headers, comment overlays, and gesture patterns needed by Status, media viewer, Creator Studio, Marketplace video previews, and later camera upload flows.
- Native Reels is high leverage because it improves a performance-sensitive surface without requiring native camera creation or LiveKit call handling yet.

## Reusable Existing PulseSoc Logic

Reuse directly:

- `GET /api/pulse/reels/feed`
- Reels detail route/API where available from the current backend
- `POST /api/pulse/reels/<reel_id>/react`
- Reels comments read/create APIs
- Reels save/repost/share/not-interested APIs
- existing creator follow behavior
- existing media/Mux/R2 payload URLs
- existing moderation, visibility, ranking, creator identity, premium marks, and notification side effects

Do not duplicate in native:

- Reels ranking
- media transcoding/processing rules
- moderation/risk state
- creator entitlement decisions
- reaction/comment/save/repost persistence
- notification dispatch
- follow graph rules

## What Must Be Rebuilt Natively

- Native vertical Reels player.
- Native video rendering and buffering states.
- Creator header and profile navigation.
- Reels actions: react, comment, save, repost, share, not interested where APIs exist.
- Comments overlay or detail screen.
- Pull/gesture navigation between reels.
- Offline metadata cache where safe.
- Deep-link routing from notifications into native Reel detail.
- Loading, empty, offline, and error states.

## Dependencies And Blockers

Dependencies:

- Confirm exact Reels feed/detail payload shape from the current backend.
- Confirm Expo AV or native video library behavior against existing Mux/R2 video URLs.
- Confirm deep-link patterns for `/pulse/reels` and `/pulse/reels/<reel_id>`.
- Confirm whether comments are returned on detail or require a separate endpoint.

Blockers:

- Real-device video playback, memory, buffering, audio focus, and scroll performance must be verified before production replacement.
- If the backend exposes a web-only Reel detail for some cases, a thin JSON adapter may be needed before full native deep-link parity.

## Risk Level

Risk: Medium-high.

Reasons:

- Reels is media-heavy and performance-sensitive.
- Native video playback must be smooth on real iOS and Android devices.
- The backend/API/business logic already exists, so most risk is native rendering, memory, buffering, audio, gesture, and device QA.

## Estimated Complexity

Complexity: Medium-high.

Recommended first slice:

- Reels feed list.
- Full-screen native video card.
- Play/pause/mute.
- Creator header and profile navigation.
- Reaction/save/share hooks.
- Comments read/create if existing APIs are straightforward.
- Deep link into Reel detail.
- Web fallback for unsupported media/detail cases.

Defer from first slice:

- Reel creation.
- Camera capture.
- Video compression.
- Advanced editing.
- Background upload.
- Complex audio remix/music tools.

## Safest Implementation Plan

1. Inspect current Reels web implementation, APIs, payloads, and media URL handling again before coding.
2. Add a typed native Reels API wrapper around existing endpoints only.
3. Build a native Reels feed screen with one video per viewport and conservative buffering.
4. Add Reel Detail/deep-link routing.
5. Add action hooks only after read/playback behavior is stable.
6. Keep web fallback for unsupported video/media cases.
7. Add a focused Reels audit and run install/typecheck/Expo doctor gates.

## Recommendation Summary

Build Native Reels Player + Reel Detail next. The core social foundation is now native enough to support creator/profile context, and Reels gives the greatest leverage for replacing media-heavy web fallbacks while reusing existing PulseSoc media, ranking, moderation, and action APIs.
