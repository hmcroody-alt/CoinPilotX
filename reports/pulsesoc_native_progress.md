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
- `scripts/pulsesoc_native_app_foundation_audit.py`
- `scripts/pulsesoc_native_phase1_device_qa_audit.py`
- `scripts/pulsesoc_native_messenger_audit.py`
- `scripts/pulsesoc_native_messenger_device_qa_audit.py`
- `scripts/pulsesoc_native_notifications_audit.py`
- `scripts/pulsesoc_native_feed_audit.py`
- `scripts/pulsesoc_native_profile_audit.py`
- `scripts/pulsesoc_native_reels_audit.py`

## Remaining Major Features

- Feed composer
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

Recommendation: build Native Status Viewer + Status Detail next.

This should come before Status creator, Camera capture, Marketplace, or Calls.

## Why This Comes Next

- Home Feed, Post Detail, Profile, Notifications, and Reels now cover the core social graph plus the first media-heavy playback surface.
- Status is the next largest social/media fallback still living primarily in the web experience.
- The backend already exposes mature Status APIs for rail, create, view tracking, reactions, replies, sharing, music, AI story generation, and media upload association.
- Reels established reusable native primitives for vertical media playback, metadata cache, creator headers, action controls, profile navigation, gesture handling, and notification deep links.
- A native Status viewer reuses those primitives without taking on the heavier native camera/compression/editor work yet.

## Reusable Existing PulseSoc Logic

Reuse directly:

- `GET /api/pulse/status/rail`
- `POST /api/pulse/status/<status_id>/view`
- `POST /api/pulse/status/<status_id>/react`
- `POST /api/pulse/status/<status_id>/reply`
- `POST /api/pulse/status/<status_id>/share`
- Existing `/api/pulse/status` create behavior for later creator work, but not required for the first viewer slice.
- Existing `/api/pulse/media/upload` media pipeline and `pulse_status` media associations.
- Existing Status music/search and AI story generation logic for later creator phases.
- Existing moderation, visibility, expiration, analytics, creator identity, profile, premium marks, and notification side effects.

Do not duplicate in native:

- Status expiration and visibility rules.
- Media authorization and processing rules.
- Moderation/risk state.
- Reply/reaction/share persistence.
- Viewer analytics persistence.
- Notification dispatch.
- Creator entitlement decisions.

## What Must Be Rebuilt Natively

- Native Status rail.
- Full-screen Status viewer with tap-through navigation and progress bars.
- Native image/video rendering using the existing media URLs.
- Reply composer and reaction controls.
- Share hook.
- Creator header and profile navigation.
- View tracking.
- Offline metadata cache where safe.
- Deep-link routing from notifications into native Status detail.
- Loading, empty, expired, offline, and error states.

## Dependencies And Blockers

Dependencies:

- Confirm exact Status rail/detail payload shape from the current backend.
- Confirm whether individual `/pulse/status/<id>` notification links should use a dedicated JSON detail adapter or the rail payload plus focus behavior.
- Confirm Expo AV behavior for Status videos using the existing media URLs.
- Confirm Status reply payload shape and notification side effects.

Blockers:

- Real-device image/video progression, tap zones, audio/mute behavior, and background recovery must be verified before production replacement.
- If the backend exposes a web-only Status detail for some cases, a thin JSON adapter may be needed before full native deep-link parity.

## Risk Level

Risk: Medium-high.

Reasons:

- Status is media-heavy, time-sensitive, and notification-driven.
- Native image/video progression must feel immediate and match PulseSoc expiration/view tracking behavior.
- The backend/API/business logic already exists, so most risk is native rendering, gesture timing, media playback, audio, and device QA.

## Estimated Complexity

Complexity: Medium-high.

Recommended first slice:

- Status rail.
- Full-screen native viewer.
- Tap left/right progression.
- Progress bars.
- Creator header and profile navigation.
- View tracking.
- Reaction/reply/share hooks.
- Deep link into Status detail.
- Safe fallback for unsupported or expired statuses.

Defer from first slice:

- Status creation.
- Camera capture.
- Video/image compression.
- Advanced editing.
- Background upload.
- Complex music/AI story creator tools.

## Safest Implementation Plan

1. Inspect current Status web implementation, APIs, payloads, and media URL handling again before coding.
2. Add a typed native Status API wrapper around existing endpoints only.
3. Build a native Status rail and viewer using reusable media/player/profile/action primitives from Reels where safe.
4. Add Status detail/deep-link routing.
5. Add view tracking, reaction, reply, and share hooks only after viewer behavior is stable.
6. Keep safe fallback for unsupported, unavailable, or expired status cases.
7. Add a focused Status audit and run install/typecheck/Expo doctor gates.

## Recommendation Summary

Build Native Status Viewer + Status Detail next. Reels has now established the native media and gesture foundation; Status gives the greatest leverage for replacing the next media-heavy web fallback while reusing existing PulseSoc Status APIs, media pipeline, expiration rules, moderation, analytics, and notification behavior.
