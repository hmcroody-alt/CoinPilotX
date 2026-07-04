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
- `scripts/pulsesoc_native_app_foundation_audit.py`
- `scripts/pulsesoc_native_phase1_device_qa_audit.py`
- `scripts/pulsesoc_native_messenger_audit.py`
- `scripts/pulsesoc_native_messenger_device_qa_audit.py`
- `scripts/pulsesoc_native_notifications_audit.py`

## Remaining Major Features

- Home Feed
- Post detail
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

Recommendation: build Native Home Feed + Post Detail next.

This should come before Reels, Status creator, Marketplace, or Calls.

## Why This Comes Next

- Notifications now route or fall back to posts, reels, status, alerts, and profiles. The highest-value next step is to reduce those web fallbacks by making the core feed/post target native.
- Home is currently only a Mission Control placeholder. A real native PulseSoc app needs the main feed as the primary signed-in surface.
- The backend feed contract is already mature: `GET /api/pulse/feed` uses existing ranking, visibility, moderation, media hydration, video detail links, and status discovery signals.
- Post detail already has server-authoritative APIs for comments, reactions, save, repost, pin, delete, and moderation-aware visibility.
- This builds on Messenger and Notifications without requiring device-only camera, LiveKit, lock-screen, or heavy video QA first.
- Reels and Status depend on the same media/rendering primitives. A native feed/post slice will establish reusable media cards, author headers, reaction rows, comment lists, pagination, offline cache, and target routing.

## Reusable Existing PulseSoc Logic

Reuse directly:

- `GET /api/pulse/feed`
- `GET /api/pulse/posts/<post_id>`
- `GET /api/pulse/post/<post_id>`
- `POST /api/pulse/posts/<post_id>/react`
- `GET/POST /api/pulse/posts/<post_id>/comments`
- `POST /api/pulse/posts/<post_id>/save`
- `POST /api/pulse/posts/<post_id>/repost`
- `POST /api/pulse/follow`
- existing media payload fields from `media_service.resolve_media(...)`
- existing visibility/moderation/ranking from `pulse_feed_engine`
- existing notification generation from post/comment/reaction flows
- existing profile identity payloads and premium marks supplied by the backend

Do not duplicate in native:

- feed ranking
- trust/moderation decisions
- post visibility rules
- blocked/deleted content rules
- premium entitlement decisions
- media processing state
- notification dispatch
- mention parsing
- saved-content persistence

## What Must Be Rebuilt Natively

- Feed screen replacing the current native Mission Control placeholder as the primary Home tab content.
- Native post cards with author, text, media, counters, reactions, and action menus.
- Native pagination and pull-to-refresh.
- Native media rendering for images and basic videos, using existing media URLs.
- Native post detail screen.
- Native comments list and comment composer.
- Native optimistic reaction/save/comment UI with server reconciliation.
- Native deep-link routing for `/pulse/post/<post_id>`.
- Offline cache for the last loaded feed page and opened post details.
- Native loading, empty, permission, and failure states.

## Dependencies And Blockers

Dependencies:

- Confirm exact `GET /api/pulse/feed` response shape with live authenticated test data or fixtures.
- Confirm media payload shape for images, video, Mux/HLS, broken/processing media, and posts without media.
- Confirm `/api/pulse/posts/<post_id>` returns enough detail for a native post detail screen, including comments and viewer state.
- Reuse existing `pulse_media_renderer` behavior conceptually, but do not copy DOM/CSS code directly.
- Add a native feed audit before commit.

Blockers:

- Real-device media performance remains unverified in this environment.
- Native video playback may require a dedicated Expo AV/native video pass if feed videos are common.
- Composer/camera upload should not be included in the first feed slice unless the read-only + reactions/comments path is stable.

## Risk Level

Risk: Medium.

Reasons:

- Feed is a high-traffic, user-facing surface.
- Payloads are rich and media-heavy.
- Mistakes in native caching or optimistic actions could confuse users if not reconciled with the server.
- However, the backend APIs and business rules already exist, so the native work is mostly UI, pagination, rendering, and safe interaction wiring.

## Estimated Complexity

Complexity: Medium-high.

Recommended first slice:

- Feed list
- Pull-to-refresh
- Pagination
- Image media rendering
- Basic video placeholder or native playback where safe
- Reactions
- Save
- Post detail
- Comments read/create
- Deep link to post detail
- Offline cache

Defer from first slice:

- New post composer
- Camera upload
- Video compression
- Advanced Reels-style playback
- Status creator
- Full moderation/report menus beyond existing server routes

## Safest Implementation Plan

1. Inspect and document the exact feed/post payload shape from existing backend code and, if available, authenticated API responses.
2. Add `mobile-native/src/api/feed.ts` as a typed wrapper around existing feed/post endpoints.
3. Add native cache helpers for feed and post details using `AsyncStorage`.
4. Replace the Home tab placeholder with a native feed list while preserving Mission Control as a small status panel or secondary section.
5. Add `PostDetailScreen` and route `/pulse/post/:postId` into native navigation.
6. Implement read-only post cards first: author, body/title, timestamps, media, counters.
7. Add reactions/save/comment interactions after read-only rendering is stable.
8. Add focused audit: `scripts/pulsesoc_native_feed_audit.py`.
9. Run `npm ci`, `npm run typecheck`, Expo doctor, feed audit, `git diff --check`, and scoped staging.
10. Document anything not device-verified, especially video playback, long-feed scroll performance, and media memory behavior.

## Recommendation Summary

Build Native Home Feed + Post Detail next. It converts the highest-value notification fallback into native UI, establishes reusable content/media primitives for Reels and Status, and uses mature existing backend APIs without duplicating PulseSoc business logic.
