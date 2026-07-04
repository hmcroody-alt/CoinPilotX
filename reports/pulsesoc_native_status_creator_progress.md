# PulseSoc Native Status Creator Progress

Date: 2026-07-04

## Scope

The native Status Creator foundation lives only under `mobile-native/`. It does not touch production WebView paths, production web templates, backend routes, media workers, Status storage, moderation, privacy, music, AI story, or notification behavior.

Server APIs stay authoritative. Native Status Creator is a new client for the existing PulseSoc Status and media upload pipeline.

## Existing Web/Backend Implementation Inspected

Current PulseSoc Status creator and backend surfaces inspected before implementation:

- Web Status composer flow in `static/js/pulse_home_core.js`.
- Web Status page composer flow in `pulse_status_page()`.
- Camera-to-Status flow in the existing web camera route.
- `POST /api/pulse/status`.
- `GET /api/pulse/status/rail`.
- `POST /api/pulse/media/upload`.
- `GET /api/pulse/media/<media_id>/status`.
- `GET /api/pulse/status/music/search`.
- `GET /api/pulse/status/music/trending`.
- `POST /api/pulse/status/ai-story`.
- Existing Status payload fields: `status_type`, `body`, `visibility`, `duration_hours`, `media_ids`, `music_media_id`, `music_track_id`, `effect_name`, `sticker`, `link_url`, and `ai_context`.
- Existing Status storage and behavior: `pulse_status`, `pulse_status_media`, `pulse_status_music`, `pulse_status_views`, `pulse_status_reactions`, `pulse_status_replies`, and `pulse_status_shares`.

## Implemented Native Foundation

- Native Status composer entry from the native Status screen.
- Native Status creator modal.
- Text Status creation through existing `POST /api/pulse/status`.
- Image Status attachment through the shared native media upload hook.
- Video Status attachment through the shared native media upload hook.
- Camera image attachment through the shared native media upload hook.
- Upload preview through the shared `MediaUploadPreview` component.
- Upload progress, retry, and cancellation via the shared native media upload foundation.
- Privacy selector for `public`, `followers`, and `private`.
- Duration selector for API-supported hour values.
- Music search/trending hooks through existing Status music APIs.
- AI Story generation hook through the existing Status AI API.
- Publish loading, success, and failure states.
- Draft-safe local state retained until successful publish or user cancel.
- Status rail/list refresh after publish.
- Web fallback note only for unsupported advanced editor tools.

## Reuse-First Boundaries

Native Status Creator does not implement its own:

- Status privacy logic.
- Status expiration rules.
- Status moderation.
- Visibility authorization.
- Music approval rules.
- AI story generation logic.
- Media authorization.
- R2/Mux routing.
- Media processing decisions.
- View, reaction, reply, or share persistence.
- Premium or creator entitlement checks.
- Notification fanout.
- Server-side validation.

Those remain owned by the existing PulseSoc backend, database, and services.

## Native-Only Layer

The rebuilt native layer is limited to:

- Creator UI.
- Status type selector UI.
- Text input.
- Privacy and duration selector UI.
- Attachment picker/camera controls.
- Music search/selection UI.
- AI prompt/generation UI.
- Local preview.
- Upload progress/retry/cancel UI.
- Publish loading/error UI.
- Status rail refresh trigger after successful creation.

## Known Gaps

- Advanced camera effects, stickers, link cards, and full editor styling remain in the web creator until native parity is ready.
- Multi-attachment publishing is not implemented in the first native slice.
- Background upload is not implemented.
- Offline draft persistence is not implemented beyond local modal state.
- Music preview playback is not included in this foundation.

## Device-Only Behavior Not Verified

The following are not marked as passed without device access:

- Real camera capture from the Status Creator.
- Real gallery image/video picking from the Status Creator.
- Large video upload behavior.
- Upload cancellation timing on iOS and Android.
- Accepted/denied camera and photo permission flows on real devices.
- Status rail refresh behavior after a real published media Status on physical devices.

## Next Recommendation

Recommended next native feature: Native Media Viewer Foundation.

Reason: Feed, Post Detail, Profile media, Messenger attachments, Reels, Status, and future Marketplace/Creator Studio all need one reusable full-screen media viewer. The current native app already has media cards, Status/Reels video rendering, and shared upload infrastructure, so a shared viewer should come next before building another isolated media surface.
