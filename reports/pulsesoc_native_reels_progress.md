# PulseSoc Native Reels Player + Reel Detail Progress

Date: 2026-07-04

## Scope

The native Reels foundation lives only under `mobile-native/`. It does not touch production WebView paths, production web templates, or backend route behavior.

Server APIs stay authoritative. Native Reels is a new client for existing PulseSoc Reels, media, moderation, notification, profile, and social-action behavior.

## Existing Web/Backend Implementation Inspected

Current PulseSoc Reels backend/web surfaces inspected before implementation:

- `/pulse/reels`
- `/pulse/reels/<reel_id>`
- `GET /api/pulse/reels/feed`
- `POST /api/pulse/reels/<reel_id>/view`
- `POST /api/pulse/reels/<reel_id>/react`
- `GET/POST /api/pulse/reels/<reel_id>/comments`
- `POST /api/pulse/reels/<reel_id>/save`
- `POST /api/pulse/reels/<reel_id>/repost`
- `POST /api/pulse/reels/<reel_id>/share`
- `POST /api/pulse/reels/<reel_id>/not-interested`
- `POST /api/pulse/reels/<reel_id>/follow-creator`
- `POST /api/pulse/report`
- `pulse_reel_payload(...)`
- `pulse_reel_feed_payload(...)`
- `pulse_reel_comment_payload(...)`
- Existing Mux/R2/media URL payload behavior.
- Existing notification target routing for Reels URLs.

## Implemented Native Foundation

- Native Reels tab registered in the main tab navigator.
- Native Reel Detail route registered in the stack navigator.
- Deep links for `/pulse/reels` and `/pulse/reels/<reel_id>`.
- Notification tap routing into native Reel Detail where supported.
- Full-screen vertical Reels feed with paged scrolling.
- Infinite scrolling through the existing feed endpoint.
- Pull to refresh.
- Offline metadata cache through AsyncStorage.
- Initial Reel focus for native detail links when the reel is already in the feed payload/cache.
- Native video renderer through Expo AV.
- Mux playback ID support where available.
- First-party playback/poster/media URL fallback through existing PulseSoc media payloads.
- Muted autoplay.
- Tap to mute/unmute.
- Double-tap fire reaction.
- Long-press smart reaction.
- Playback progress bar.
- Buffering indicator.
- Processing/unavailable fallback states.
- Creator header with native Profile navigation.
- Reactions.
- Comments list.
- Add comment.
- Save.
- Repost.
- Share.
- Follow creator.
- Not interested.
- Report.
- View tracking calls using the existing backend endpoint.

## Reuse-First Boundaries

Native Reels does not implement its own:

- Reels ranking.
- Recommendation logic.
- Visibility rules.
- Moderation rules.
- Media authorization.
- Mux/R2 processing policy.
- Reaction persistence.
- Comment persistence.
- Save/repost/share persistence.
- Follow graph rules.
- Report handling.
- Notification dispatch.

Those remain owned by the existing PulseSoc backend and database.

## Native-Only Layer

The rebuilt native layer is limited to:

- Vertical video feed UI.
- Native video rendering.
- Gesture handling.
- Native comments sheet.
- Native action controls.
- Native loading/offline/error states.
- Native deep-link routing.
- Native metadata cache.

## Known Gaps

- Some backend surfaces expose Reel detail as a web route, while native detail currently relies on feed/cache payloads plus comments/action endpoints.
- Real-device HLS/Mux behavior is not verified in this environment.
- 60fps scroll performance is not verified in this environment.
- Audio focus, Bluetooth/route behavior, background resume, and memory pressure are not verified in this environment.
- Unsupported or unavailable media intentionally falls back to safe share/open-web-link behavior instead of bypassing media authorization.

## Device-Only Behavior Not Verified

The following are not marked as passed without device access:

- Smooth 60fps vertical scrolling on physical iOS and Android devices.
- Real Mux HLS playback behavior on iOS and Android.
- Dropped frame behavior during rapid swipes.
- Audio route changes, mute state persistence, and background recovery.
- Memory pressure during long Reels sessions.
- Notification tap behavior from the lock screen.

## Next Recommendation

Recommended next native feature: Native Status Viewer + Status Detail.

Reason: Reels now establishes the native video/media, gesture, action, comment, profile-navigation, and notification deep-link primitives needed by Status. The current backend already exposes reusable Status APIs for rail, create, view, react, reply, and share, and Status remains one of the largest media/social web fallbacks after Reels.
