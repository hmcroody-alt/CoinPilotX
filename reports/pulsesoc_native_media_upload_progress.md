# PulseSoc Native Media Capture + Upload Progress

Date: 2026-07-04

## Scope

The native media upload foundation lives only under `mobile-native/`. It does not touch production WebView paths, production web templates, backend routes, media workers, storage services, R2/Mux logic, or moderation rules.

Server APIs stay authoritative. Native media upload is a reusable device/UI layer over the existing PulseSoc media pipeline.

## Existing Web/Backend Implementation Inspected

Current PulseSoc media upload surfaces inspected before implementation:

- `POST /api/pulse/media/upload`
- `GET /api/pulse/media/<media_id>/status`
- `POST /api/pulse/media/<media_id>/repair`
- `POST /api/pulse/media/mux/direct-upload`
- `POST /api/pulse/media/mux/direct-upload/complete`
- `media_service.save_upload(...)`
- `media_service.resolve_media(...)`
- `upload_progress_service.stage_upload(...)`
- `camera_filter_engine.validate_media(...)`
- `chat_media_uploads`
- `pulse_media_assets`
- `pulse_camera_captures`
- Existing Status creator upload flow using `context_type=pulse_status`.
- Existing Messenger media upload flow.
- Existing Profile avatar/cover upload flow.
- Existing Marketplace media upload flow.
- Existing Mux/R2 processing and readiness status behavior.

## Implemented Native Foundation

- Shared native image picker.
- Shared native video picker.
- Shared native camera entry point.
- Media permission denied states for camera and library access.
- Shared file validation for image/video extension and size.
- Shared native upload service using existing `/api/pulse/media/upload`.
- Upload progress through native `XMLHttpRequest` progress events.
- Upload cancellation through `XMLHttpRequest.abort()`.
- Upload retry through reusable hook state.
- Processing status polling through existing `/api/pulse/media/<media_id>/status`.
- Reusable `useNativeMediaUpload(...)` hook.
- Reusable `MediaUploadPreview` component.
- Integration target contract for Status creator, Feed composer, Profile avatar/cover, Messenger attachments, Marketplace, and Creator Studio.

## Reuse-First Boundaries

Native media upload does not implement its own:

- Storage destination decisions.
- R2 upload behavior.
- Mux processing.
- Media moderation.
- Server authorization.
- Premium/creator entitlement checks.
- Status creation business rules.
- Feed post creation business rules.
- Marketplace listing rules.
- Notification dispatch.

Those remain owned by the existing PulseSoc backend and database.

## Native-Only Layer

The rebuilt native layer is limited to:

- Image/video selection.
- Camera launch.
- Permission state handling.
- Local media preview.
- Upload progress rendering.
- Retry/cancel UX.
- Polling already uploaded media readiness.
- Reusable client-side upload state.

## Integration Hooks

The foundation exports reusable primitives for future native features:

- Status creator: upload selected/captured media with `context_type=pulse_status`, then pass returned `media.id` into existing `/api/pulse/status`.
- Feed composer: upload with post/media context, then pass returned media IDs/URLs into existing post creation APIs.
- Profile avatar/cover: either keep using existing profile-specific upload endpoints or reuse picker/preview/validation state before calling current avatar/cover APIs.
- Messenger attachments: reuse picker/preview/validation state before sending through existing Messenger media endpoint.
- Marketplace: upload with `context_type=marketplace_product`, then pass returned media into existing listing creation flow.
- Creator Studio: reuse picker/upload/progress state before publishing through existing creator APIs.

## Known Gaps

- No native publishing surface is enabled by this foundation. It prepares media infrastructure only.
- Large direct-to-Mux upload is documented as an existing backend option but is not wired into the first native upload slice.
- Native compression is not implemented yet; the first slice relies on existing server-side processing and Mux/R2 behavior.
- Real-device camera/gallery permission flows are not verified in this environment.

## Device-Only Behavior Not Verified

The following are not marked as passed without device access:

- iOS camera permission behavior.
- Android camera permission behavior.
- iOS gallery permission behavior.
- Android gallery permission behavior.
- Large video memory behavior.
- Background interruption during upload.
- Native upload progress accuracy on physical devices.
- Mux processing timing after a real video upload.

## Next Recommendation

Recommended next native feature: Native Feed Composer Foundation.

Reason: the native app already has Home Feed, Post Detail, Profile, Notifications, Reels, Status viewing, and now shared media upload infrastructure. Feed composer is the highest-leverage creation surface to connect next because it reuses the existing post APIs and the new shared media upload layer without taking on the full complexity of Status creator editing, camera effects, Live, or Creator Studio.
