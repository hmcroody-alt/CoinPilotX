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
- Profile: native summary through existing account/session profile data and `/api/pulse/profile/me`.
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
- `scripts/pulsesoc_native_app_foundation_audit.py`
- `scripts/pulsesoc_native_phase1_device_qa_audit.py`
- `scripts/pulsesoc_native_messenger_audit.py`
- `scripts/pulsesoc_native_messenger_device_qa_audit.py`
- `scripts/pulsesoc_native_notifications_audit.py`
- `scripts/pulsesoc_native_feed_audit.py`

## Remaining Major Features

- Feed composer
- Profile detail and profile edit
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
- Profile APIs/media: `/api/pulse/profile/me`, `/api/pulse/profile/update`, `/api/pulse/profile/avatar`, `/api/pulse/profile/cover`
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

Recommendation: build Native Profile Detail + Profile Edit next.

This should come before Reels, Status creator, Marketplace, or Calls.

## Why This Comes Next

- Home Feed and Post Detail now expose author identity as a primary navigation path, but the native Profile tab is still only a signed-in summary.
- Notifications already route profile targets into the native Profile tab; the next safest improvement is to replace that summary with a real native profile surface and profile-edit flow.
- Profile is lower risk than Reels/Status/Camera/LiveKit because it reuses mature account/profile APIs and does not require native video, camera, microphone, lock-screen, or background audio behavior.
- A native profile foundation unlocks author/profile navigation from feed cards, post detail, notifications, Messenger identity, creator surfaces, follow/message actions, and profile posts.
- It keeps the migration moving through high-frequency social surfaces before the media-heavy Reels/Status pass.

## Reusable Existing PulseSoc Logic

Reuse directly:

- `GET /api/pulse/profile/me`
- `POST/PATCH /api/pulse/profile/update`
- `POST /api/pulse/profile/avatar`
- `POST /api/pulse/profile/cover`
- `POST /api/pulse/profile/avatar/remove`
- `POST /api/pulse/profile/cover/remove`
- existing `/pulse/profile` and `/pulse/profile/<profile_key>` behavior as the source of parity
- existing profile identity, premium marks, verification state, bio/location/social fields, avatar/cover storage, and media upload validation
- existing feed profile filtering or post APIs for profile posts where available
- existing follow/message/report authorization and safety rules where APIs are already present

Do not duplicate in native:

- profile authorization
- premium/verification decisions
- media validation/storage
- blocked/private visibility logic
- follow graph rules
- creator/premium entitlement checks
- moderation/report decisions

## What Must Be Rebuilt Natively

- Native profile detail screen.
- Native profile edit screen.
- Avatar and cover image picker/upload UI using existing profile media endpoints.
- Native author/profile navigation from feed and post detail.
- Profile post list using existing feed/profile filters where available.
- Follow/message/share/report hooks where existing APIs support them.
- Loading, empty, offline, and error states.

## Dependencies And Blockers

Dependencies:

- Confirm whether public profile detail has a JSON endpoint equivalent to `/pulse/profile/<profile_key>`; if not, add only the smallest backend adapter needed to expose existing server-side profile payloads without changing business logic.
- Confirm exact avatar/cover upload payload expectations on device.
- Confirm profile post filtering through `GET /api/pulse/feed?profile=...` or the existing profile post API before building a native profile post list.

Blockers:

- Public profile detail may currently be web-rendered only, which would require a thin JSON adapter before full native author profile navigation.
- Real-device image picker/upload QA remains required.

## Risk Level

Risk: Medium.

Reasons:

- Profile is user-facing and identity-sensitive.
- Avatar/cover uploads require native permission and file handling.
- The business rules are mature on the backend, so risk is mostly API shape confirmation, media upload handling, and parity with the web profile.

## Estimated Complexity

Complexity: Medium.

Recommended first slice:

- Current user profile detail.
- Profile edit.
- Avatar/cover display.
- Existing avatar/cover upload/remove APIs.
- Author profile route shape and safe web fallback if public JSON is missing.
- Profile post list only after confirming existing server support.

Defer from first slice:

- Advanced creator analytics.
- Marketplace storefront details.
- Premium creator tools.
- Full media gallery.
- Public profile JSON backend expansion beyond a minimal adapter if needed.

## Safest Implementation Plan

1. Inspect current web profile templates/routes and profile APIs again before coding.
2. Confirm public-vs-current-user profile data contracts and upload payloads.
3. Extend native API wrappers around existing profile endpoints only.
4. Build native Profile Detail and Profile Edit screens.
5. Add image picker upload controls with explicit denied-permission states.
6. Wire feed author/profile taps to native profile only for supported profile targets; keep web fallback for unsupported public profile cases.
7. Add a focused profile audit and run the same install/typecheck/Expo doctor gates.

## Recommendation Summary

Build Native Profile Detail + Profile Edit next. Feed/Post is now native enough to expose identity paths, and Profile is the safest next social foundation before moving into heavier native media surfaces like Reels, Status, and Camera.
