# PulseSoc Native Feed Composer Progress

Date: 2026-07-04

## Scope

The native Feed Composer foundation lives only under `mobile-native/`. It does not touch production WebView paths, production web templates, backend routes, feed ranking, moderation, visibility rules, media workers, or notification behavior.

Server APIs stay authoritative. Native Feed Composer is a new client for the existing PulseSoc post creation and media upload pipeline.

## Existing Web/Backend Implementation Inspected

Current PulseSoc feed composer and post backend surfaces inspected before implementation:

- Web composer publish flow in `pulse_page_html(...)`.
- `POST /api/pulse/posts`.
- `POST /api/pulse/media/upload`.
- `GET /api/pulse/feed`.
- Existing post payload fields: `body`, `title`, `post_type`, `visibility`, `media_ids`, `tags`.
- `pulse_feed_engine.create_post(...)`.
- Existing post creation events: `pulse_post_created` and `new_post`.
- Existing moderation, visibility, media attachment, mention, notification, and feed refresh behavior.

## Implemented Native Foundation

- Native composer entry from Home Feed.
- Native composer modal.
- Text post creation through existing `POST /api/pulse/posts`.
- Title field using the existing post payload.
- Visibility selector for `public`, `followers`, and `private`.
- Image attachment through the shared native media upload hook.
- Video attachment through the shared native media upload hook.
- Camera image attachment through the shared native media upload hook.
- Upload preview through the shared `MediaUploadPreview` component.
- Upload progress, retry, and cancellation via the shared native media upload foundation.
- Validation for empty composer state.
- Publish loading, success, and failure states.
- Draft-safe local state retained until successful publish or user cancel.
- Feed refresh after publish.
- Web fallback note only for unsupported advanced composer options.

## Reuse-First Boundaries

Native Feed Composer does not implement its own:

- Feed ranking.
- Post moderation.
- Visibility authorization.
- Mention parsing.
- Notification fanout.
- Media authorization.
- R2/Mux routing.
- Server-side validation.
- Premium/creator entitlement rules.

Those remain owned by the existing PulseSoc backend and database.

## Native-Only Layer

The rebuilt native layer is limited to:

- Composer UI.
- Text/title input.
- Visibility selector UI.
- Attachment picker/camera controls.
- Local preview.
- Upload progress/retry/cancel UI.
- Publish loading/error UI.
- Feed refresh trigger after successful creation.

## Known Gaps

- Advanced web composer options such as polls, rich formatting, scheduled posts, music attach, and advanced creator tools are intentionally not included in this first slice.
- Offline draft persistence is not implemented beyond local modal state.
- Multi-attachment publishing is not implemented in the first native slice.

## Device-Only Behavior Not Verified

The following are not marked as passed without device access:

- Real camera capture from the composer.
- Real gallery image/video picking from the composer.
- Large video upload behavior.
- Upload cancellation timing on iOS and Android.
- Feed refresh behavior after a real published media post on physical devices.

## Next Recommendation

Recommended next native feature: Native Status Creator Foundation.

Reason: the shared native media upload foundation and Feed Composer now prove the native creation loop for standard posts. Status Creator should come next because the Status viewer already exists, the backend status creation contract is mature, and it can reuse the same media picker/upload/preview primitives while preserving existing Status privacy, expiration, music, AI story, media pipeline, moderation, and notification behavior.
