# PulseSoc Native Media Viewer Progress

Date: 2026-07-04

## Scope

The native Media Viewer foundation lives only under `mobile-native/`. It does not touch production WebView paths, production web templates, backend routes, media workers, storage, authorization, moderation, Mux, R2, notification routing, or feed/business logic.

Server APIs stay authoritative. Native Media Viewer is a shared client-side presentation layer for existing PulseSoc media payloads.

## Existing Web/Backend Implementation Inspected

Current PulseSoc media surfaces and backend contracts inspected before implementation:

- Feed/Post media payloads returned by `GET /api/pulse/feed` and `GET /api/pulse/posts/<post_id>`.
- Existing media URL normalization in `mobile-native/src/api/feed.ts`.
- Existing native media upload and processing polling in `mobile-native/src/media/nativeMediaUpload.ts`.
- Existing processing status API: `GET /api/pulse/media/<media_id>/status`.
- Existing Reels native playback shell.
- Existing Status native playback shell.
- Existing Messenger media attachment payloads.
- Existing Profile media tab, which reuses native `PostCard`.
- Existing PulseSoc media upload pipeline over `/api/pulse/media/upload`.

## Implemented Native Foundation

- Shared `NativeMediaViewer` component.
- Shared full-screen image viewer.
- Shared video viewer/player shell using Expo AV native controls.
- Pinch-to-zoom structure for images through native gesture handling.
- Swipe-down close gesture.
- Next/previous media navigation.
- Loading, buffering, unsupported, processing, error, and retry states.
- Share hook with default native share fallback.
- Save hook support where a parent surface provides it.
- Author/Profile navigation hook support where a parent surface provides it.
- Media metadata display with title/subtitle/author/source context.
- Processing-status handling through existing `pollNativeMediaProcessing`.
- Reusable `mediaViewerItemFromPulseMedia(...)` adapter for existing PulseSoc media payloads.
- Integration targets declared for Feed/Post, Messenger, Profile, Status, Reels, Marketplace, and Creator Studio.

## Integrated Native Surfaces

- Feed/Post media cards through shared `PostCard`.
- Post Detail media through shared `PostCard`.
- Profile media tab through shared `PostCard`.
- Messenger image/video attachments.
- Status list card long-press hook while preserving normal Status viewer tap behavior.

## Reuse-First Boundaries

Native Media Viewer does not implement its own:

- Media authorization.
- R2/Mux routing.
- First-party stream routing.
- Media processing decisions.
- Media moderation.
- Visibility/privacy rules.
- Premium or creator entitlement checks.
- Parent content reaction/comment/save/share business rules.
- Notification fanout.
- Server-side validation.

Those remain owned by the existing PulseSoc backend, database, media pipeline, and parent surface APIs.

## Native-Only Layer

The rebuilt native layer is limited to:

- Full-screen media presentation.
- Image zoom UI.
- Native video playback shell.
- Gesture close/navigation UI.
- Viewer metadata display.
- Loading/error/processing UI.
- Parent action hooks.
- Explicit unsupported-media fallback.

## Known Gaps

- Reels and Status retain their specialized full-screen players for this slice; the shared viewer is available as an integration target but does not replace those timeline players.
- Marketplace and Creator Studio do not have native screens yet, so they are integration targets only.
- Advanced editing, export/download, background playback, and multi-track audio are not included.
- Pinch zoom is implemented as first-slice native gesture support; device tuning is still required.

## Device-Only Behavior Not Verified

The following are not marked as passed without device access:

- Real-device pinch-to-zoom feel.
- Real-device swipe-down close feel.
- Large image memory behavior.
- Long video playback stability.
- HLS/Mux playback behavior on physical iOS and Android devices.
- Media viewer performance under rapid previous/next navigation.

## Next Recommendation

Recommended next native feature: Native Marketplace Browse + Listing Detail Foundation.

Reason: the native app now has feed/profile/messaging/media creation and viewing foundations. Marketplace is the next high-leverage feature because it can reuse profile identity, media viewer, media upload hooks, notification routing, existing product/listing APIs, server-side moderation, authorization, premium rules, and payment/business logic while rebuilding only browse/detail native UI first.
