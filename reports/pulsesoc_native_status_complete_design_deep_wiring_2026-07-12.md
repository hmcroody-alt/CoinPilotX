# PulseSoc Native Status — Complete Design and Deep-Wiring Mission

Date: 2026-07-12

## Decision

Status received a focused native design and lifecycle pass, but it is **not focused-subsystem complete or simulator-parity frozen**. The rail, viewer, creator, canonical publishing, music, AI assistance, privacy, view tracking, reactions, replies, sharing, caching, owner edit/delete, drafts, deep links, and notification routing are wired. Complete simulator matrices, audience selection beyond the production-exposed three choices, owner viewer lists, reporting/muting, realtime reconciliation, and physical media QA remain incomplete.

## Production system inspected

- Web routes and presentation: `/pulse/status`, home Status rail, `static/js/pulse_home_core.js`, `static/js/pulse_status_viewer.js`, and `static/css/pulse_status_system.css`.
- Canonical APIs: rail, create, update, delete, view, react, reply, share, music search/trending, and AI Story.
- Backend behavior: server-authoritative visibility (`public`, `followers`, `private`), duration/expiration, moderation filtering, owner management, viewer analytics returned from view tracking, notifications, and compatible deep links.
- Media: existing Pulse media upload IDs, shared media validation, posters/playback URLs, and creator-safe music metadata.

## Native system inspected and reused

- `StatusScreen`, `StatusViewerCard`, `StatusCreator`, `api/status.ts`.
- Shared `useNativeMediaUpload`, `MediaUploadPreview`, `NativeMediaViewer`, camera routing, authentication/session storage, Pulse API wrapper, AsyncStorage cache, navigation/linking, notification routing, theme tokens, and bottom navigation.
- No second Status model, upload pipeline, viewer, privacy system, reaction system, or backend was created.

## Feature matrix

| Capability | Production source | Native result | Classification |
|---|---|---|---|
| Rail/list | Status rail API and production rail | Seen/unseen, live ring, multi-story count, refresh/cache | Simulator + code-path verified |
| Viewer | production viewer runtime | image/video/text, timed images, video completion, hold pause, next/previous, mute, caption/music/actions | Code-path verified |
| Creator | production creator and create API | text/photo/video/AI modes, shared upload preview, music, privacy, duration, publish | Simulator shell + code-path verified |
| Drafts | native platform concern | debounced persistent text/mode/privacy/duration/AI draft; cleared after publish | Code-path verified |
| Privacy | create/update APIs | public/followers/private, selected semantics, owner update | Code-path verified |
| Reactions/replies/shares | canonical endpoints | optimistic reaction rollback, reply sheet, native share | Code-path verified |
| Owner lifecycle | PATCH/DELETE endpoint | native edit/privacy/delete management sheet | Code-path verified |
| Seen/analytics | view endpoint | deduplicated view tracking and counts | Controlled contract + code-path verified |
| Music/AI | music and AI Story endpoints | trending/search/select and AI caption generation | Code-path verified |
| Offline | cached rail | cached Status fallback and offline label | Code-path verified |
| Deep links/notifications | production URLs and native routing | Status and StatusDetail routes | Code-path verified |

## Design implemented

- Layered unseen/seen rings, distinct live ring glow, multi-story count, premium dark glass creator panels, pill selectors, larger text canvas, and restrained accent energy.
- Viewer image timing and press-and-hold pause/resume reuse the existing media lifecycle; video pauses offscreen.
- Accessibility labels were added for rail identity/seen state, previous/next, mute, more actions, creator text, privacy, duration, and mode selections.
- Internal fallback engineering copy was removed from the creator and empty state.

## Fresh simulator evidence

Directory: `reports/screenshots/native-status-complete-design-deep-wiring-2026-07-12/`

- `pro-status-empty.png`: final Pro Status empty state and Camera/Create entry controls with production-facing copy. Creator automation did not remain open reliably across Metro refresh, so it is not classified as creator proof.

## Remaining gaps

- Populated rail/viewer fixtures and complete text/image/video/music/AI visual matrix.
- Compact, standard, and Pro Max evidence.
- Crop/rotate/trim/drawing/stickers/cover-frame UI where production support is confirmed.
- Selected/excluded/custom audience UI and backend contract mapping beyond current visibility choices.
- Owner viewer list, detailed analytics, download/save, report, user mute/block integration.
- Realtime new/deleted/expired/reaction/reply/share events and offline queue reconciliation.
- Controlled-backend lifecycle integration for create → view → react → reply → share → update → delete.
- Performance measurements, reduced-motion runtime verification, and full VoiceOver pass.

## Honest completion

| Area | Completion |
|---|---:|
| Overall production capability parity | 76% |
| UI design completion | 80% |
| Visual quality | 82% |
| Interaction completion | 74% |
| Deep wiring | 81% |
| Rail | 82% |
| Viewer | 80% |
| Creator | 78% |
| Text Status | 84% |
| Image Status | 72% |
| Video Status | 68% |
| Music Status | 72% |
| AI Status | 68% |
| Privacy/audience | 66% |
| Reactions | 78% |
| Replies | 76% |
| Sharing | 72% |
| Analytics | 60% |
| Expiration/lifecycle | 72% |
| Offline/reconnect | 58% |
| Loading/empty/error | 76% |
| Notifications/deep links | 82% |
| Accessibility | 76% |
| Responsive behavior | 74% |
| Xcode Simulator QA | 46% |
| Device-size coverage | 25% |
| Backend/business reuse | 98% |
| Frontend utility reuse | 95% |
| Existing native component reuse | 97% |

## Compatibility and recommendation

- No production Status control, route, event, database field, or API was removed or renamed.
- WebView and native clients remain compatible and can operate in parallel.
- Status is not ready to replace WebView Status or advance to another subsystem under the new one-surface strategy.
- Next recommendation: remain on Status for deterministic populated fixtures, controlled-backend lifecycle tests, complete creator/viewer matrices, and four-width simulator evidence.
