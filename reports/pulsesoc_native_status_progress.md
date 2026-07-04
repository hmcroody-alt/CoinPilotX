# PulseSoc Native Status Viewer + Status Detail Progress

Date: 2026-07-04

## Scope

The native Status foundation lives only under `mobile-native/`. It does not touch production WebView paths, production web templates, or backend route behavior.

Server APIs stay authoritative. Native Status is a new client for existing PulseSoc Status, media, moderation, privacy, notification, analytics, music, reaction, reply, and share behavior.

## Existing Web/Backend Implementation Inspected

Current PulseSoc Status backend/web surfaces inspected before implementation:

- `/pulse/status`
- `/pulse/status/<status_id>`
- `GET /api/pulse/status/rail`
- `POST /api/pulse/status`
- `PATCH/DELETE /api/pulse/status/<status_id>`
- `POST /api/pulse/status/<status_id>/view`
- `POST /api/pulse/status/<status_id>/react`
- `POST /api/pulse/status/<status_id>/reply`
- `POST /api/pulse/status/<status_id>/share`
- `GET /api/pulse/status/music/search`
- `GET /api/pulse/status/music/trending`
- `POST /api/pulse/status/ai-story`
- `pulse_status_payload(...)`
- `pulse_status_row_for_viewer(...)`
- Existing `pulse_status`, `pulse_status_media`, `pulse_status_music`, `pulse_status_views`, `pulse_status_reactions`, `pulse_status_replies`, and `pulse_status_shares` database behavior.
- Existing notification target routing for `/pulse/status`, `/pulse/status/<id>`, and `pulse://status/<id>/reply/<reply_id>`.

## Implemented Native Foundation

- Native Status tab registered in the main tab navigator.
- Native Status detail route registered in the stack navigator.
- Deep links for `/pulse/status` and `/pulse/status/<status_id>`.
- Notification tap routing into native Status Detail where supported.
- Support for backend mobile deep links shaped like `pulse://status/<status_id>/reply/<reply_id>`.
- Native Status rail.
- Native Status list.
- Full-screen Status viewer.
- Image, video, and text Status rendering.
- Tap left/right navigation.
- Press/long-press reaction path through existing reaction endpoint.
- View tracking through the existing view endpoint.
- Reaction count update through the existing reaction endpoint.
- Reply composer through the existing reply endpoint.
- Share tracking through the existing share endpoint plus native share sheet.
- Music display where the existing payload provides music metadata.
- Author header and profile navigation where profile keys are available.
- Offline metadata cache through AsyncStorage.
- Loading, empty, offline, and error states.
- Safe share-link fallback for unavailable/unsupported media.

## Reuse-First Boundaries

Native Status does not implement its own:

- Status privacy.
- Status expiration.
- Status visibility.
- Status moderation.
- Media authorization.
- Media upload/processing policy.
- Music approval policy.
- AI story generation.
- View analytics persistence.
- Reaction persistence.
- Reply persistence.
- Share persistence.
- Notification dispatch.

Those remain owned by the existing PulseSoc backend and database.

## Native-Only Layer

The rebuilt native layer is limited to:

- Status rail UI.
- Full-screen viewer UI.
- Native image/video/text rendering.
- Native gesture/tap behavior.
- Reply composer UI.
- Native share handoff.
- Native loading/offline/error states.
- Native deep-link routing.
- Native metadata cache.

## Known Gaps

- Status creation is intentionally not included in this viewer/detail foundation.
- Native camera capture, picker, compression, and upload are still future work.
- Some Status author payloads include name/avatar/user ID but no public profile key, so author navigation is only available when the backend payload provides a profile key or username.
- Unsupported/unavailable media falls back to a safe share link instead of bypassing PulseSoc media authorization.

## Device-Only Behavior Not Verified

The following are not marked as passed without device access:

- Smooth tap-through Status progression on physical iOS and Android devices.
- Real video playback behavior for Status media on iOS and Android.
- Audio/mute behavior, route changes, and background recovery.
- Notification tap behavior from the lock screen.
- Memory behavior during long Status viewing sessions.

## Next Recommendation

Recommended next native feature: Native Media Capture + Upload Foundation.

Reason: Home Feed, Profile, Reels, and Status now consume media natively, but creation still depends on web/device fallbacks. A shared native media capture/upload layer reuses the existing PulseSoc media pipeline and unlocks Feed composer, Status creator, Marketplace media, Creator Studio, and richer Messenger/Profile upload flows without duplicating backend business logic.
