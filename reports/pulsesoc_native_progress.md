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
- Media Viewer Foundation: shared full-screen native image/video viewer, pinch-to-zoom image structure, swipe-down close, previous/next navigation, processing-status checks, share/save/profile hooks, metadata display, and integrations for Feed/Post/Profile, Messenger attachments, and Status media hooks.
- Marketplace Browse + Listing Detail Foundation: native Marketplace tab, search/browse through existing marketplace API, listing cards, listing detail modal, media gallery through NativeMediaViewer, save/report/contact seller hooks, safe checkout routing, offline cache, and marketplace deep-link routing.
- Search + Discovery Foundation: native Search tab/route, debounced global search through existing `/api/pulse/search`, recent and suggested searches, discovery tabs, grouped result cards, pull-to-refresh, cached result fallback, native destination routing, `/pulse/search` deep-link routing, and web fallback for unsupported result URLs.
- Saved Content + Collections Foundation: native Saved tab/route, saved item list, type filters, collection filters, create/rename/delete collection actions, remove/move saved item actions, saved search, offline cache, item deep-link routing, and `/pulse/saved` deep-link routing through existing saved APIs.
- Groups/Communities + Rooms Foundation: native Groups tab/detail route, thin read-only group JSON bridge, communities browse/search, room rail, group detail, rules/member metadata, compact group feed preview, join/leave, report, group chat open, room open, offline cache, and group/room deep-link routing through existing group, room, and Messenger APIs.
- Architecture Health Report + Shared Core Consolidation: native architecture audit, shared cache helper under `mobile-native/src/core/cache.ts`, first refactor of Groups/Saved/Marketplace cache wrappers, duplicate-pattern inventory, production WebView safety check, and next Live Discovery recommendation.
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
- `reports/pulsesoc_native_media_viewer_progress.md`
- `reports/pulsesoc_native_marketplace_progress.md`
- `reports/pulsesoc_native_search_progress.md`
- `reports/pulsesoc_native_saved_progress.md`
- `reports/pulsesoc_native_groups_progress.md`
- `reports/pulsesoc_native_architecture_health.md`
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
- `scripts/pulsesoc_native_media_viewer_audit.py`
- `scripts/pulsesoc_native_marketplace_audit.py`
- `scripts/pulsesoc_native_search_audit.py`
- `scripts/pulsesoc_native_saved_audit.py`
- `scripts/pulsesoc_native_groups_audit.py`
- `scripts/pulsesoc_native_architecture_health_audit.py`

## Remaining Major Features

- Advanced camera/compression/editor tools
- Live discovery
- Native LiveKit calls
- Full-screen incoming calls
- Growth Center
- Crypto/market alerts
- Intelligence Center
- Premium/entitlements
- Creator Studio

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
- Marketplace routes: `/pulse/marketplace`, `/pulse/marketplace/create`, `/pulse/merchant/dashboard`, `/pulse/merchant/<username>`
- Marketplace APIs: `/api/pulse/marketplace/search`, `/api/pulse/marketplace/seller/apply`, `/api/pulse/marketplace/listings/create`, `/api/pulse/marketplace/media/upload`, `/api/pulse/marketplace/listings/save`, `/api/pulse/marketplace/listings/report`
- Marketplace data/business logic: `marketplace_listings`, `marketplace_product_media`, `marketplace_sellers`, `marketplace_saved_products`, `marketplace_reports`, `marketplace_orders`, seller readiness, promotions, and moderation/revenue safety services
- Search APIs and web bridge: `/api/pulse/search`, `/pulse/search`, `static/js/pulse_search_bridge.js`, and search handling in `static/js/pulse_home_core.js`
- Saved APIs and web route: `/pulse/saved`, `GET/POST /api/pulse/saved`, saved collections, delete, and move endpoints
- Groups and rooms routes: `/pulse/groups`, `/pulse/groups/create`, `/pulse/groups/<group_slug>`, `POST /api/pulse/groups/create`, join/leave APIs, chat-open APIs, invite/report/update/moderation APIs, group post/comment APIs, `pulse_default_room_cards()`, and `pulse_ensure_default_rooms(...)`
- Live surfaces: `/pulse/live`, `/pulse/live/studio`, `/api/pulse/live/start`, `/api/pulse/live/<id>/state`, LiveKit direct playback fallback, Mux egress handling, live-session state, live chat, replay/feed insertion, and the existing live audit suite.
- Current native reuse surface: repeated API wrappers, AsyncStorage cache functions, screen-level loading/empty/error/offline states, native/web fallback routing, card layouts, action busy states, media preview/viewer hooks, and tab/stack route patterns across Feed, Messenger, Notifications, Profile, Reels, Status, Marketplace, Search, Saved, and Groups.

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
- marketplace listing moderation/safety
- seller trust/readiness
- marketplace save/report behavior
- marketplace order/payment/payout rules
- PulseSoc search ranking/grouping/result routing
- saved item collection ownership and removal
- group membership, roles, moderation, invite links, group chats, and group post/comment rules
- shared native routing, caching, error, card, and media-viewer behavior that should be consolidated before deeper Live/Calls/Premium work
- LiveKit/Mux session authority, live room state, live chat, replay creation, feed insertion, destination handling, and creator/host permissions

## Recommended Next Feature

Recommendation: build Native Live Discovery + Live Viewer Foundation next.

This should come before native Live hosting, native LiveKit calls, Creator Studio, Growth Center, and Premium.

## Why This Comes Next

- Production already has Live routes, Live Studio, LiveKit/Mux bridge behavior, LiveKit direct fallback, live sessions, live chat, replay/feed insertion, and several focused live audits.
- The native app now has the social prerequisites: Feed, Notifications, Profile, Search, Saved, Groups/Rooms, Messenger, Media Viewer, and the first shared-core cache cleanup.
- Viewer/discovery is safer than native hosting because server-owned LiveKit/Mux/session state stays authoritative.
- Hosting/Studio can remain a safe web fallback while native validates discovery, viewer routing, and supported playback paths first.
- This recommendation is based on the current live backend plus native migration progress, not a predetermined roadmap.

## Reusable Existing PulseSoc Logic

Reuse directly:

- Existing `/pulse/live`, `/pulse/live/studio`, `/api/pulse/live/start`, `/api/pulse/live/<id>/state`, and related LiveKit/Mux live API routes.
- Existing LiveKit/Mux bridge, direct fallback, egress handling, and playback URL selection behavior.
- Existing live session, live chat, live destination, replay, and feed insertion database/services.
- Existing moderation, notification, profile, and feed insertion behavior.
- Existing native navigation, notification routing, Search/Saved/Profile/Messenger/Media Viewer integrations, and shared `readJsonCache`/`writeJsonCache` cache helpers.

Do not duplicate in native:

- Backend business logic.
- LiveKit token/session authorization.
- Mux egress/processing decisions.
- Host/cohost/restream business rules.
- Live moderation and destination behavior.
- Replay/feed insertion logic.

## What Must Be Rebuilt Natively

- Native Live discovery screen.
- Live session cards and rails.
- Live viewer shell.
- Native playback for supported HLS/Mux/LiveKit direct payloads.
- Chat/read-only interaction hooks where existing APIs support them.
- Loading, empty, offline, and error states.
- Notification/search/profile routing into native Live where supported.
- Web fallback for Go Live, Studio, cohost, restream, and unsupported playback modes.

## Dependencies And Blockers

Dependencies:

- Confirm live API and route payload shape for discovery and viewer state.
- Confirm playback URL fields for HLS/Mux/LiveKit direct fallback.
- Confirm chat/read-only behavior and whether viewer chat is safe to expose before native hosting.
- Preserve Studio/Go Live web fallback.

Blockers:

- Device video playback and background/resume behavior still require simulator or real-device verification.
- LiveKit egress quota or source-state issues can affect Mux HLS availability.
- Native hosting, cohost, camera, mic, and restream controls are intentionally out of scope for the first viewer slice.

## Risk Level

Risk: Medium-high.

Reasons:

- Live playback has more device-specific behavior than feed/profile/search screens.
- Risk stays controlled by making discovery/viewer read-first and leaving Studio/hosting on the existing web fallback.
- Backend risk stays low if native only consumes existing live state and playback contracts.

## Estimated Complexity

Complexity: Medium-high.

Recommended first slice:

- Inspect live routes and payloads.
- Build read-only native discovery first.
- Build viewer only for supported playback payloads.
- Add native chat/read-only hooks only where existing APIs support them cleanly.
- Keep Go Live/Studio/cohost/restream on web fallback.
- Add a focused native Live audit and document unverified device playback behavior honestly.

Defer from first slice:

- Native Live hosting and camera broadcast.
- Native cohost/restream controls.
- Native LiveKit calls.
- Background audio/camera/mic controls.
- Any new Live business logic.

## Safest Implementation Plan

1. Inspect current production live routes, APIs, playback fields, and live audit evidence.
2. Add a native Live API wrapper that only consumes existing backend payloads.
3. Build a read-only Live Discovery screen using existing cards/loading/cache conventions.
4. Build a Live Viewer shell for supported playback URLs only.
5. Route unsupported host/Studio/cohost/restream states to the existing web fallback.
6. Wire notification/search/profile navigation into native Live where payloads are supported.
7. Add a native Live progress report and static audit.
8. Run the standard native verification suite and document device-only playback gaps.

## Recommendation Summary

Build Native Live Discovery + Live Viewer Foundation next. It is the highest-leverage next step because the backend already has substantial LiveKit/Mux/live-session infrastructure, while the native app now has enough navigation, media, profile, notification, search, and shared-cache infrastructure to add a viewer slice without taking on native hosting risk.
