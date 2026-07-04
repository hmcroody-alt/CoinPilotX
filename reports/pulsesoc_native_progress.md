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

## Remaining Major Features

- Status creator
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

Recommendation: build Native Status Creator Foundation next.

This should come before Marketplace posting, Creator Studio publishing, or advanced Camera tools.

## Why This Comes Next

- Native Status viewing/detail is already built, so Status creation completes the Status loop.
- The shared native media upload foundation and Feed Composer now prove the media-pick/upload/publish pattern.
- The backend already exposes mature Status creation APIs, privacy/expiration rules, media associations, music search/trending, AI story generation, reactions, replies, shares, view tracking, and notification behavior.
- Status Creator is the next highest-leverage creation surface because it reuses the same media layer while unlocking a major mobile-first PulseSoc workflow.
- It should be implemented before advanced camera effects so the basic Status publishing contract is stable first.

## Reusable Existing PulseSoc Logic

Reuse directly:

- Existing Status creation API: `POST /api/pulse/status`.
- Existing Status rail/view APIs: `GET /api/pulse/status/rail` and `POST /api/pulse/status/<status_id>/view`.
- Existing media upload API: `POST /api/pulse/media/upload`.
- Shared native media upload hook/service and preview component.
- Existing Status privacy, expiration, visibility, media association, music, and AI story contracts.
- Existing media authorization, R2/Mux processing, thumbnails, and CDN URLs.
- Existing moderation, analytics, notification fanout, and profile identity behavior.

Do not duplicate in native:

- Status privacy and expiration rules.
- Moderation/risk rules.
- Music approval rules.
- AI story generation logic.
- View/reaction/reply/share persistence.
- Media authorization or storage decisions.
- Mux/R2 routing.
- Premium/creator entitlement checks.
- Notification dispatch.
- Server-side validation.

## What Must Be Rebuilt Natively

- Native Status creator sheet/screen.
- Text Status input.
- Photo/video Status attachment using the shared native upload foundation.
- Camera entry through the shared media layer.
- Status privacy selector.
- Duration/expiration selector using existing API-supported values.
- Status type inference for text/photo/video.
- Media attachment using the shared native upload foundation.
- Upload progress and retry wiring in the creator.
- Submit/publishing state.
- Draft clearing after successful Status.
- Error, empty, offline, and permission-denied states.
- Status rail refresh after successful creation.

## Dependencies And Blockers

Dependencies:

- Confirm exact native Status creation payload shape against the current backend.
- Confirm supported duration/privacy values.
- Confirm media ID handling and status type inference for image/video.
- Confirm whether music/AI story should be included in the first slice or deferred.

Blockers:

- Real-device media picking/upload within Status Creator must be verified before production replacement.
- Music/AI story flows may require additional UI and should be deferred if they threaten the first slice.

## Risk Level

Risk: Medium-high.

Reasons:

- Status Creator publishes time-sensitive user content, so privacy, expiration, media state, and notification side effects matter.
- Most business logic already exists server-side, so risk is mainly native payload mapping, media attachment state, creator UX, and device QA.
- The shared upload and Status viewer foundations lower the media/rendering risk.

## Estimated Complexity

Complexity: Medium-high.

Recommended first slice:

- Creator entry from native Status.
- Text-only Status creation.
- Optional image/video attachment through the shared media upload layer.
- Privacy and duration selectors using existing API values.
- Status rail refresh after publish.
- Loading, validation, permission, upload, publishing, and error states.
- Static audit proving no duplicated Status/privacy/media business rules.

Defer from first slice:

- Advanced camera effects.
- Music picker and AI story generation if they make the first slice too broad.
- Scheduled/status campaign tools.
- Advanced editing.
- Music/audio remix.
- Background uploads.
- Marketplace and Creator Studio publishing flows.

## Safest Implementation Plan

1. Inspect current Status creator and `POST /api/pulse/status` payload handling before coding.
2. Add or extend native Status API wrappers only around existing Status creation endpoints.
3. Build a native creator entry on Status.
4. Wire text-only Status publishing first.
5. Add image/video attachment through the shared native upload foundation.
6. Refresh native Status rail after successful creation and keep server privacy/expiration rules authoritative.
7. Add a focused Status Creator audit and keep production WebView untouched.

## Recommendation Summary

Build Native Status Creator Foundation next. Native Status viewing and shared media publishing are now in place, and Status Creator is the highest-leverage next creation feature because it reuses existing Status APIs, media upload, privacy, expiration, moderation, analytics, notification, and music/AI contracts while rebuilding only the native creator UI.
