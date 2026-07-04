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
- Reels Player + Reel Detail: native full-screen vertical Reels feed, Expo AV video playback, Mux/R2 media URL reuse, infinite scrolling, pull-to-refresh, metadata cache, comments, reactions, save, repost, share, follow creator, not interested, report, view tracking, profile navigation, and `/pulse/reels/<reel_id>` deep-link routing through existing Reels APIs.
- Status Viewer + Status Detail: native Status rail, Status list, full-screen viewer, image/video/text rendering, tap navigation, view tracking, reactions, replies, shares, music display, offline metadata cache, and `/pulse/status/<status_id>` deep-link routing through existing Status APIs.
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
- `reports/pulsesoc_native_reels_progress.md`
- `reports/pulsesoc_native_status_progress.md`
- `scripts/pulsesoc_native_app_foundation_audit.py`
- `scripts/pulsesoc_native_phase1_device_qa_audit.py`
- `scripts/pulsesoc_native_messenger_audit.py`
- `scripts/pulsesoc_native_messenger_device_qa_audit.py`
- `scripts/pulsesoc_native_notifications_audit.py`
- `scripts/pulsesoc_native_feed_audit.py`
- `scripts/pulsesoc_native_profile_audit.py`
- `scripts/pulsesoc_native_reels_audit.py`
- `scripts/pulsesoc_native_status_audit.py`

## Remaining Major Features

- Feed composer
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
- Status data/business logic: `pulse_status`, `pulse_status_media`, `pulse_status_music`, `pulse_status_views`, `pulse_status_reactions`, `pulse_status_replies`

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

Recommendation: build Native Media Capture + Upload Foundation next.

This should come before Status creator, Feed composer, Marketplace media posting, Creator Studio publishing, or advanced Camera tools.

## Why This Comes Next

- Home Feed, Post Detail, Profile, Reels, and Status now consume existing PulseSoc media natively.
- The next major gap is media creation: image/video picking, camera capture, compression handoff, upload progress, retries, and reusable upload state.
- The backend already exposes the shared media pipeline through `/api/pulse/media/upload`, `media_service.save_upload(...)`, `media_service.resolve_media(...)`, R2/Mux processing, moderation status, and context types such as `pulse_status`.
- A shared native media layer unlocks multiple future features at once: Feed composer, Status creator, Marketplace listings, Creator Studio, richer Profile uploads, and Messenger attachment polish.
- This keeps business logic server-authoritative while rebuilding only device-specific camera, picker, upload, and progress behavior.

## Reusable Existing PulseSoc Logic

Reuse directly:

- `POST /api/pulse/media/upload`
- Existing upload context types such as `pulse_status`, post/media contexts, profile avatar/cover contexts, and Messenger media contexts where applicable.
- `media_service.save_upload(...)`
- `media_service.resolve_media(...)`
- Existing R2 storage and Mux/transcoding behavior.
- Existing moderation, media authorization, file validation, processing status, thumbnail, and CDN URL behavior.
- Existing `/api/pulse/status` creation contract for later Status creator work.
- Existing `/api/pulse/posts` creation contract for later Feed composer work.
- Existing notification, creator, premium, visibility, and privacy rules triggered by server-side create endpoints.

Do not duplicate in native:

- Media authorization.
- Storage destination decisions.
- Mux/R2 routing.
- Moderation/risk rules.
- Premium/creator entitlement checks.
- Post or Status creation business rules.
- Notification dispatch.
- Server-side validation.

## What Must Be Rebuilt Natively

- Image picker.
- Video picker.
- Camera capture.
- Camera/microphone permission states.
- Upload queue UI.
- Upload progress UI.
- Failed upload retry.
- Safe cancellation.
- Local preview.
- Basic image/video compression handoff where native libraries support it.
- Shared upload helper/state machine usable by Feed, Status, Profile, Messenger, Marketplace, and Creator Studio.

## Dependencies And Blockers

Dependencies:

- Confirm Expo Image Picker/Camera package choices against the current Expo SDK.
- Confirm native media permission copy and denied-state behavior on iOS and Android.
- Confirm accepted file types and size limits from the existing backend/upload audits.
- Confirm whether native compression should be first-slice lightweight or deferred behind server-side processing.

Blockers:

- Real-device camera, gallery permissions, large-video upload behavior, background interruption, and memory use must be verified before production replacement.
- Native compression may require additional Expo/native dependencies and device testing.

## Risk Level

Risk: High.

Reasons:

- Media capture/upload touches device permissions, large files, memory pressure, network interruption, retries, and user trust.
- It feeds multiple future creation surfaces, so a weak shared design would create duplicate feature-specific upload logic.
- The backend/API/business logic already exists, so most risk is native device handling and reusable upload-state correctness.

## Estimated Complexity

Complexity: High.

Recommended first slice:

- Shared native media picker/camera permission helper.
- Shared upload state machine around `/api/pulse/media/upload`.
- Local preview for selected images/videos.
- Upload progress, retry, cancel, and failure states.
- A minimal test harness screen or integration point that does not publish user content yet.
- Static audit proving no duplicated backend media rules.

Defer from first slice:

- Full Feed composer.
- Full Status creator.
- Advanced editing.
- Music/audio remix.
- Background uploads.
- Marketplace and Creator Studio publishing flows.

## Safest Implementation Plan

1. Inspect existing web upload manager, `/api/pulse/media/upload`, media audits, and backend validation before coding.
2. Add a typed native media upload API wrapper around existing endpoints only.
3. Build reusable native picker/camera permission helpers.
4. Build a reusable upload state machine with progress, retry, cancellation, and safe error handling.
5. Add a small native integration surface without publishing content.
6. Reuse the layer in Status creator and Feed composer only after the foundation passes install/typecheck/Expo doctor/audit and device QA.
7. Add a focused media capture/upload audit and keep production WebView untouched.

## Recommendation Summary

Build Native Media Capture + Upload Foundation next. Status and Reels now give PulseSoc native media consumption; the highest leverage next step is shared native media creation infrastructure that reuses the existing PulseSoc media pipeline and unlocks multiple creation features without duplicating backend logic.
