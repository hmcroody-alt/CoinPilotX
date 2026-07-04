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
- Media Capture + Upload Foundation: shared native image picker, video picker, camera entry point, permission states, file validation, upload progress, retry, cancellation, processing-status polling, reusable upload hook/service, and reusable media preview component over existing PulseSoc media APIs.
- Feed Composer Foundation: native composer entry from Home Feed, text/title publishing, visibility selector, image/video/camera attachment through the shared media upload layer, upload preview/progress/retry/cancel, publish states, and feed refresh through existing PulseSoc post APIs.
- Status Creator Foundation: native Status composer entry, text/image/video Status publishing, camera/gallery integration, shared upload preview/progress/retry/cancel, privacy/duration selectors, music search/trending hooks, AI Story generation hook, and Status rail refresh through existing PulseSoc Status APIs.
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
- `reports/pulsesoc_native_media_upload_progress.md`
- `reports/pulsesoc_native_feed_composer_progress.md`
- `reports/pulsesoc_native_status_creator_progress.md`
- `scripts/pulsesoc_native_app_foundation_audit.py`
- `scripts/pulsesoc_native_phase1_device_qa_audit.py`
- `scripts/pulsesoc_native_messenger_audit.py`
- `scripts/pulsesoc_native_messenger_device_qa_audit.py`
- `scripts/pulsesoc_native_notifications_audit.py`
- `scripts/pulsesoc_native_feed_audit.py`
- `scripts/pulsesoc_native_profile_audit.py`
- `scripts/pulsesoc_native_reels_audit.py`
- `scripts/pulsesoc_native_status_audit.py`
- `scripts/pulsesoc_native_media_upload_audit.py`
- `scripts/pulsesoc_native_feed_composer_audit.py`
- `scripts/pulsesoc_native_status_creator_audit.py`

## Remaining Major Features

- Native media viewer
- Advanced camera/compression/editor tools
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

Recommendation: build Native Media Viewer Foundation next.

This should come before Marketplace, Creator Studio, or advanced Camera tools.

## Why This Comes Next

- Feed, Post Detail, Profile media, Messenger attachments, Reels, Status, and Feed Composer now all surface media in native screens.
- The app already has media cards, Status/Reels video rendering, shared upload state, and backend media processing status polling, but it does not yet have one reusable full-screen media viewer.
- A shared native media viewer removes repeated one-off image/video handling before larger media-heavy features such as Marketplace, Creator Studio, Camera editor tools, and Live.
- Notification and deep-link fallbacks already route into Post/Reel/Status/Profile. Media tap-through should become native before another major media creation surface is added.
- This recommendation is based on the current native migration state and the existing PulseSoc media pipeline, not a predetermined roadmap.

## Reusable Existing PulseSoc Logic

Reuse directly:

- Existing media payloads returned by Feed, Post Detail, Profile, Messenger, Reels, and Status APIs.
- Existing media upload API: `POST /api/pulse/media/upload`.
- Existing processing status API: `GET /api/pulse/media/<media_id>/status`.
- Existing Mux/R2 playback URLs, thumbnails, poster URLs, valid URLs, and authorization behavior.
- Existing report/share/save/reaction/comment APIs for the parent surface when available.
- Existing notification and deep-link target routing for Post/Reel/Status/Profile.
- Existing media moderation, visibility, premium/creator entitlement, and storage rules.
- Shared native media helpers: media URL normalization, media kind detection, upload result IDs, and processing polling.

Do not duplicate in native:

- Media authorization.
- R2/Mux routing.
- Processing-state decisions.
- Moderation/risk rules.
- Visibility/privacy rules.
- Premium/creator entitlement checks.
- Parent content reaction/comment/save/share business logic.
- Notification dispatch.
- Server-side validation.

## What Must Be Rebuilt Natively

- Reusable full-screen media viewer component.
- Image viewing with pinch/zoom-ready structure.
- Native video playback surface with mute/play/pause/retry states.
- Swipe/dismiss gestures.
- Previous/next navigation for media galleries.
- Shared loading, empty, unsupported, offline, and error states.
- Parent surface action hooks for share, save, report, profile navigation, and comments where APIs already exist.
- Processing-status handling for media that is uploaded but not ready.
- Integration points from Feed/Post Detail, Profile media, Messenger attachments, Status, Reels, and future Marketplace/Creator Studio.

## Dependencies And Blockers

Dependencies:

- Inventory the current media payload shapes across Feed, Profile, Messenger, Reels, Status, and uploads.
- Confirm the strongest reusable native video component already in use for Reels/Status.
- Confirm which parent surfaces should expose viewer actions in the first slice.
- Keep all media authorization and processing decisions server-authoritative.

Blockers:

- Real-device video playback, memory pressure, swipe gesture smoothness, and large media behavior must be verified before production replacement.
- Pinch/zoom may require an added native gesture/zoom dependency or a constrained first slice if the current dependency set is not enough.

## Risk Level

Risk: Medium-high.

Reasons:

- Media viewer quality directly affects the highest-volume native surfaces.
- Video memory, gesture responsiveness, and large attachments can regress performance if implemented as one-off viewers.
- Reusing existing media URLs, processing status, and authorization keeps backend risk low, but real-device QA is required.

## Estimated Complexity

Complexity: Medium-high.

Recommended first slice:

- Shared `NativeMediaViewer` component.
- Single-image and single-video viewing.
- Gallery previous/next navigation where a surface already has multiple media items.
- Tap-to-dismiss and native-safe loading/error/retry states.
- Integrations from Feed/Post Detail, Profile media, Messenger attachments, and Status cards.
- Static audit proving media business rules stay backend-owned.

Defer from first slice:

- Advanced editing.
- Download/export behavior.
- Background playback.
- Complex multi-track audio.
- Creator Studio-specific analytics overlays.
- Marketplace listing-specific purchase actions.

## Safest Implementation Plan

1. Inspect media payload shapes in existing native Feed, Profile, Messenger, Reels, Status, and upload code.
2. Extract a shared viewer around existing native media URL/kind helpers and Expo AV playback already proven in Reels/Status.
3. Wire Feed/Post Detail image cards first.
4. Add Profile media and Messenger attachment entry points.
5. Add Status card/viewer entry points where it does not conflict with story navigation.
6. Keep unsupported media on explicit fallback states and preserve existing web fallback for cases not yet native.
7. Add a focused Media Viewer audit and keep production WebView untouched.

## Recommendation Summary

Build Native Media Viewer Foundation next. The native app now creates and consumes posts, Status, Reels, profile media, and attachments; a shared viewer gives those surfaces one fast native media experience while preserving PulseSoc media authorization, processing, moderation, privacy, storage, and notification behavior on the server.
