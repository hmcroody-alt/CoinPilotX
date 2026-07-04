# PulseSoc Native Home Feed + Post Detail Progress

Date: 2026-07-04

## Scope

This milestone builds the native Home Feed and Post Detail foundation inside `mobile-native/`. It does not touch production WebView paths, web templates, backend business logic, database logic, feed ranking, visibility rules, moderation rules, media processing, notification fanout, saved-content persistence, or repost/comment business rules.

The native app remains a faster client for the existing PulseSoc platform. Server APIs stay authoritative for feed selection, ranking, post visibility, moderation state, reactions, comments, saves, reposts, notification side effects, profile identity, and media payloads.

## Existing Web/Backend Implementation Inspected

Native implementation was mapped from the existing PulseSoc feed and post surfaces:

- Web Home Feed shell: `/pulse`
- Post detail route: `/pulse/post/<post_id>`
- Feed API: `GET /api/pulse/feed`
- Post detail APIs: `GET /api/pulse/posts/<post_id>` and `GET /api/pulse/post/<post_id>`
- Post reaction API: `POST /api/pulse/posts/<post_id>/react`
- Post save API: `POST /api/pulse/posts/<post_id>/save`
- Post repost API: `POST /api/pulse/posts/<post_id>/repost`
- Post comments API: `GET/POST /api/pulse/posts/<post_id>/comments`
- Feed engine: `services/pulse_feed_engine.py`
- Feed ranking: `services/pulse_feed_ranking_engine.py`
- Moderation: `services/pulse_moderation_engine.py`
- Existing notification target routing for `/pulse/post/<post_id>`

## Reused API Contract

Native Feed/Post uses these existing endpoints:

- `GET /api/pulse/feed`
- `GET /api/pulse/posts/<post_id>`
- `POST /api/pulse/posts/<post_id>/react`
- `POST /api/pulse/posts/<post_id>/save`
- `POST /api/pulse/posts/<post_id>/repost`
- `GET /api/pulse/posts/<post_id>/comments`
- `POST /api/pulse/posts/<post_id>/comments`

No native-only feed ranking, visibility, moderation, reaction, comment, save, repost, or notification business rules were introduced.

## Implemented

- Native Home Feed screen using `GET /api/pulse/feed`.
- Feed pagination with `next_offset` and `has_more`.
- Pull-to-refresh.
- Offline feed cache through `AsyncStorage`.
- Native Post Detail screen.
- Offline post detail cache through `AsyncStorage`.
- Author header with avatar/name/handle/timestamp.
- Text/title post rendering.
- Native image media cards where supported by existing media URLs.
- Explicit web fallback for video/attachment cases that need the later native media pass.
- Reaction buttons/count reconciliation through the existing reaction endpoint.
- Comment preview on feed cards.
- Post comments list.
- Add comment through the existing comments endpoint.
- Save/unsave hook through the existing save endpoint.
- Repost hook through the existing repost endpoint.
- Native share hook using the canonical PulseSoc post URL.
- Deep-link routing from `/pulse/post/<post_id>` into native Post Detail.
- Notification tap routing from post targets into native Post Detail.
- Loading, empty, offline, and error states.

## Native Routing Behavior

Supported native post route now:

- `/pulse/post/<post_id>`

Notification taps that resolve to `/pulse/post/<post_id>` now open the native Post Detail screen. Reels, Status, Alerts, Marketplace, Premium, and other unsupported notification targets continue to use the existing safe web fallback until those native screens exist.

## Native Rebuild Boundaries

Rebuilt natively:

- Feed list UI
- Post cards
- Pull-to-refresh and pagination
- Native image cards
- Post Detail UI
- Comment list and composer
- Native navigation/deep-link routing
- Offline cache restore

Still server-authoritative:

- Feed ranking
- Feed visibility
- Moderation/trust checks
- Blocked/deleted content behavior
- Reaction persistence
- Comment persistence and mention side effects
- Save/repost persistence
- Media pipeline and processing state
- Notification dispatch

## Device-Only Behavior Not Verified

The following need iOS/Android simulator or real-device QA:

- Long-feed scroll performance.
- Large image memory behavior.
- Native image cache behavior under poor network.
- Video playback smoothness. Current implementation intentionally falls back to web for video/attachment media until the dedicated media/Reels pass.
- Notification tap routing from killed/background app state.
- Keyboard behavior for comment composer on real devices.

Source verification is in place, but these are not marked as passed without device access.

## Current Status

Native Home Feed + Post Detail has a reusable foundation that preserves the existing PulseSoc backend behavior while moving the core signed-in feed and post notification targets into native screens. It is not a full production replacement until media-heavy feed QA, real-device notification deep-link QA, and post composer/camera/media creation are added and verified.
