# PulseSoc Native Composer Production Parity Report

Date: 2026-07-17
Status: native implementation and installation complete; production release remains blocked on controlled cross-client publishing and hands-on physical media QA.

## Authoritative sources inspected

### Production WebView Composer

- `bot.py` — rendered Home Composer markup, supported mode controls, 3,000-character editor, audience selector, file picker, upload/publish flow, Live/Reel gateways, success reconciliation, and create sheet.
- `static/js/pulse_home_core.js` — Composer state, four-file upload queue, file validation, media previews, progress, music selection, post validation, canonical publish call, and feed reload.
- `static/js/pulse_upload_manager.js` — production browser upload progress, cancellation, and lock behavior.
- `static/css/pulse_composer_premium.css` — expanded Composer interaction and responsive presentation.
- `static/js/pulse_camera_engine.js` — production camera-to-post and camera-to-Reel contracts.

### Backend and processing

- `bot.py` — `POST /api/pulse/posts`, `POST /api/pulse/media/upload`, Reel creation routes, Live Studio routes, account authentication, media validation, music attachment, realtime event emission, and response hydration.
- `services/pulse_feed_engine.py` — canonical post types, aliases, validation, moderation, canonical database insertion, media attachment, background jobs, post hydration, and feed visibility.
- `services/media_service.py` — upload type/size policy, storage, canonical media IDs, media attachment, and resolved playback metadata.
- `services/upload_progress_service.py` — staged upload and processing response contract.
- `services/pulse_moderation_engine.py` — authoritative text moderation state.
- `services/realtime_engine.py` and the `pulse_emit_event` calls in `bot.py` — post publication reconciliation events.
- `services/pulse_security_core.py` — server-side creation rate limiting.

### Native implementation

- `mobile-native/src/components/HomePulseComposer.tsx` — Home collapsed/expanded Composer, local draft, single-media selection, publication and retry.
- `mobile-native/src/components/FeedComposer.tsx` — modal feed Composer.
- `mobile-native/src/api/feed.ts` — canonical feed/post route and response normalization.
- `mobile-native/src/media/nativeMediaUpload.ts` — native picker, validation, upload, cancellation, and processing polling.
- `mobile-native/src/media/useNativeMediaUpload.ts` — single-attachment upload state.
- `mobile-native/src/media/MediaUploadPreview.tsx` — selected/uploaded media state.
- `mobile-native/src/api/camera.ts` and `mobile-native/src/screens/CameraStudioScreen.tsx` — canonical camera post/Reel routes.
- `mobile-native/src/api/reels.ts` and `mobile-native/src/screens/ReelsScreen.tsx` — Reel feed contracts and creation gateway.
- `mobile-native/src/api/live.ts` and `mobile-native/src/screens/LiveScreen.tsx` — Live discovery/viewer and current safe hosting fallback.
- `mobile-native/src/screens/HomeScreen.tsx` — Composer integration and canonical post feed insertion.

## Required implementation matrix

| Capability | WebView source | Backend source | Native source | Reusable directly | Must be ported | Missing before this mission | Risk | QA method |
|---|---|---|---|---|---|---|---|---|
| Collapsed default | `bot.py`, `pulse_home_core.js` | n/a | `HomePulseComposer.tsx` | Existing draft detection | Compact intent-only shell | Collapsed shell exposes full modes/actions/transmit | High UI/intent risk | Simulator cold launch with and without draft |
| Text post | `pulse_home_core.js` | `POST /api/pulse/posts`, `pulse_feed_engine.create_post` | `feed.createPost` | Route and canonical response | Preserve exact body and server state | Native trims body and has retry duplication exposure | High data-integrity risk | Controlled text/emoji/hashtag/link tests |
| Character limit | `maxlength=3000` | Backend accepts up to 5,000 but Web contract is 3,000 | `MAX_BODY=3000` | Native limit | Keep Web contract | Present | Low | Boundary audit and simulator typing |
| Poll / Question | `postType=poll` | Canonical `poll`; alias `question -> poll` | Not implemented | Same post route | Add mode and question validation | Missing | Medium | API contract fixture and native publish |
| Scam alert | `postType=scam_report` | Canonical `scam_report`; moderation | Not implemented | Same post route | Add mode and 24-character client guard | Missing | Medium | Validation plus controlled publish |
| Photo post | `pulse_home_core.js` | media upload plus post `image` | Single asset supported | Upload and post routes | Match server limits and preserve attachment ID | Present for one photo | Medium | Library selection/upload/publish |
| Multi-photo / mixed image-video | Four-file Web queue | Up to eight IDs in engine; Web product limit is four | Single asset only | Upload endpoint per file | Multi-attachment coordinator, ordering, per-item states | Missing | High | 2-4 files, remove, partial failure, order |
| Video post | Web upload queue | media upload plus post `video`, processing jobs | Single video supported | Upload/poll/post route | Align 150 MB authoritative default and processing state | Partially implemented | High | Video library and background/foreground |
| Reel | Dedicated Web `/api/pulse/reels/create`; camera routes | Canonical Reel record and compatibility post | Camera route exists; Home mode uses ordinary post route | Media upload and camera route | Dedicated library-video Reel create call/reconciliation | Incorrect route | Critical | Create once and confirm Reel ID/post ID |
| Live | Web opens Live Studio | Existing Live session APIs and Studio | Current safe Studio fallback | Existing gateway | Keep gateway; never fabricate a Live post | Correct gateway | Low | Simulator navigation and physical permissions in Studio |
| Approved music | Web music catalog and `music_track_id` | Server attaches approved track after post creation | Home opens music library without returning a track | Existing catalog and post route field | Native selection-return contract | Missing attachment | High licensing/metadata risk | Controlled approved-track publish |
| Feeling / activity | UI insertion rail only; no canonical backend field | No canonical post field | Native appends `Feeling:` into body | None | Do not treat as structured metadata | Fabricated transformation | Medium compatibility risk | Audit prevents noncanonical field/body mutation |
| Location | UI insertion rail only; no canonical post field in create API | No canonical field | Not implemented | None | Keep unavailable until backend contract exists | Correctly absent | Low | Audit/report |
| Mentions / hashtags / links | Inserted into text | Stored in body; explicit tags array supported | Text entry plus one topic tag | Canonical text/body and tags | Preserve text; extract no fake IDs | Basic text works | Medium | Cross-client rendering |
| Audience/privacy | Web `public/followers/private` | Engine validates same values | Same three values | Direct | None beyond accessibility/state | Present | Low | All three server payloads |
| Community/group post | Separate production routes | Group authorization routes | Native route gateway only | Existing route | Do not submit through generic post API | No native inline group target | High auth risk | Gateway plus membership server checks |
| Draft persistence | Web session/local behavior; upload state retained in UI | No canonical generic server draft endpoint found | AsyncStorage local draft | Local draft is sanctioned fallback | Versioned multi-attachment draft and missing-file recovery | Single-media only | High content-loss risk | Force quit/relaunch and explicit discard |
| Upload lifecycle | Browser queue and Upload Manager | Canonical upload/processing endpoints | Single upload hook | Endpoint/polling/cancel | Per-item queue and aggregate state | Missing multi-item coordination | Critical | cancel/retry/partial failure tests |
| Idempotent publish | Browser disables button; no server post idempotency key found | No compatible post idempotency contract found | `publishing` guard only | UI lock | Persist in-flight fingerprint; reconcile server response/events; never blind-retry ambiguous success | Retry can duplicate after timeout | Critical | double tap, timeout-after-success simulation |
| Feed reconciliation | Web reload plus realtime event | Server returns hydrated canonical post and emits events | Insert response then refresh/filter by ID | Canonical returned post | Reconcile response/refresh without duplicates | Basic ID filter exists | Medium | HTTP-before-event and event-before-HTTP tests |
| Offline safety | Web keeps in-memory content and reports request error | Publication requires server | AsyncStorage draft | Draft persistence | Network-aware message; no automatic publish | Basic preservation exists | Medium | offline publish and reconnect |
| Permissions | Browser invokes picker/camera on intent | n/a | Expo permission-on-action | Direct | Better denial/Settings guidance and microphone only in Live/voice flows | Partial | Medium | denial/limited access/device QA |
| Accessibility | Semantic Web controls/progress | n/a | Partial labels/states | Existing labels | State announcements, 44-point targets, logical compact/expanded focus | Partial | Medium | VoiceOver and Dynamic Type |
| Performance | Isolated Web queue items | n/a | Home owns composer state, single preview | Existing memoized API modules | Isolate attachment progress and avoid Home list churn | Not measured | Medium | render/expand/type/upload measurements |

## Canonical production post model

- Canonical post ID: `pulse_posts.id`, returned as `post_id` and hydrated `post.id`.
- Canonical author ID: authenticated production `user_id`; the client never supplies or mirrors it.
- Canonical types available to the feed engine: `text`, `image`, `video`, `gif`, `poll`, `replay`, `scam_report`, `arena_result`, `roast_clip`, and `live`; only modes exposed by the production Home Composer will be exposed inline by native.
- Home Composer types proven by the WebView: Post (`text`), Reel (`video` with dedicated Reel route), Poll/Question (`poll`), Scam Alert (`scam_report`), and Live Studio gateway.
- Canonical body/title/tags/visibility/media IDs: `body`, `title`, `tags`, `visibility`, and ordered `media_ids` sent to the production API.
- Visibility: `public`, `followers`, or `private` for ordinary Home posts.
- Canonical attachment ID: `chat_media_uploads.id`, returned by `/api/pulse/media/upload`, optionally mirrored in `pulse_media_assets`, then bound to the post by the server.
- Moderation/publication: `pulse_moderation_engine` decides the moderation status; native must display the returned state and cannot declare approval itself.
- Music: approved `music_track_id` is attached server-side; arbitrary local music files are not canonical post music.
- Realtime/feed placement: the backend emits `pulse_post_created` and `new_post`; native reconciles with the hydrated HTTP response using the canonical post ID.
- No canonical Home Composer fields were found for structured feeling, location, custom audience, scheduling, documents, voice, or generic group destination. Those capabilities must remain separate gateways or unavailable until production exposes formal contracts.

## Implemented native Composer

- Home starts with an intent-only collapsed Composer unless a persisted draft exists. Text, attachment order, selected approved music, audience, mode, completed upload IDs, and an ambiguous in-flight publication fingerprint survive relaunch.
- Expanded creation supports canonical Post, Reel, Live gateway, Poll, and Scam Alert behavior. Marketplace, Question, Camera Studio, and the full music library remain explicit production gateways.
- Text is sent exactly as entered. Newlines, emoji, mentions, hashtags, and links remain canonical body content; the client does not fabricate mention, feeling, location, or community IDs.
- Photo Library supports up to four ordered selections, previews, removal, reordering, per-item progress, cancellation, retry, and reuse of completed canonical attachment IDs after a partial failure.
- Validation now matches the authoritative default service limits: JPG/PNG/WEBP up to 5 MB, GIF up to 8 MB, and MP4/MOV/WEBM up to 150 MB. HEIC is rejected before upload because the production upload service does not currently accept it.
- Library Reels now call `POST /api/pulse/reels/create`, require one video, preserve caption/audience/music, and require both canonical Reel and compatibility-post identifiers before clearing the draft.
- Approved music comes from `POST /api/pulse/music/ai-suggest`. Native can preview, select, attribute, remove, and publish the canonical `music_track_id`; arbitrary local audio is never treated as catalog music.
- Feeling remains visible because it exists in the production interface, but it now explains that no structured feeling contract exists. Native no longer rewrites the post body with a native-only `Feeling:` prefix.
- Post and Reel retries first reconcile `my_posts` and, for Reels, the Reel feed using timestamp, exact body/caption, visibility, and ordered media IDs. If reconciliation cannot run, retry stops and preserves the draft rather than blindly republishing.
- `/pulse/compose` now restores the native expanded Composer. It no longer falls through to an external Web fallback.

## Routes and APIs reused

| Purpose | Production contract |
|---|---|
| Ordinary post, poll, scam alert | `POST /api/pulse/posts` |
| Media upload | `POST /api/pulse/media/upload` |
| Media processing state | Existing native wrapper for the canonical media processing route |
| Reel creation | `POST /api/pulse/reels/create` |
| Approved music | `POST /api/pulse/music/ai-suggest` |
| Duplicate reconciliation | `GET /api/pulse/feed?feed=my_posts` and canonical Reel feed |
| Live | Existing Live Studio gateway; no synthetic Live post |

Authentication remains cookie/session based through `pulseApi`; author IDs are never client supplied. Post, Reel, and attachment IDs are accepted only from server responses.

## Supported and deliberately unavailable types

Supported inline: text, image, multi-image, image/video mixed media within the four-item Web product limit, ordinary video, Reel, poll/question text, scam alert, approved music attachment, hashtags, mentions, links, and public/followers/private visibility.

Supported by gateway: camera photo/video/Reel, Live Studio, Marketplace, community Questions, and the full approved music library.

Not exposed as inline native fields: structured feeling/activity, location, scheduled post, document, voice/audio post, custom audience, and generic community/group destination. The audited generic production post contract has no canonical field or authorization model for these. Adding native-only representations would break cross-client parity.

## Draft, upload, and reconciliation behavior

- Draft key: `pulsesoc.native.home.composer.draft.v1`; legacy single-media drafts migrate into the ordered queue.
- Publication never occurs automatically after restoration or reconnection.
- A successful upload remains reusable if another attachment fails.
- Draft clearing requires a complete canonical response. Moderation and processing states are presented from server output rather than inferred locally.
- Double taps are blocked while publishing. Ambiguous failures retain a persisted fingerprint and require a successful server reconciliation check before a retry can create a second request.
- The existing Home `onCreated` path receives the hydrated canonical post and filters by canonical ID, while normal feed refresh/realtime behavior remains unchanged.

## Accessibility and performance

- Composer expand/collapse, audience, modes, attachment actions, music preview/selection, retry, and publish expose button roles and explicit labels.
- Error/status and attachment progress containers use polite live announcements.
- Interactive Composer and queue controls use 44-point minimum targets; native text continues to honor Dynamic Type.
- Attachment progress lives in the queue hook rather than Home feed state. Preview video is paused, images use URI-backed native views, polling is bounded, and all active controllers are cancelled on reset.
- No production-grade Instruments trace was taken. Build/launch remained responsive on all tested simulators, but typing latency, peak memory, weak-network upload latency, and physical low-power measurements remain unverified.

## Automated verification

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`: PASS.
- `npm run --prefix mobile-native typecheck`: PASS.
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`: PASS, 17/17 checks.
- `venv/bin/python scripts/pulsesoc_native_pulse_composer_complete_audit.py`: PASS.
- `git diff --check`: PASS for the intended Composer scope.
- Xcode 26.6 Release simulator build, scheme `PulseSocNative`: PASS.
- Xcode 26.6 Debug physical-device build with automatic Apple Development signing: PASS.
- Legacy `scripts/pulsesoc_native_feed_composer_audit.py`: not applicable; it asserts the retired `FeedComposer` modal is mounted on Home. The completion audit targets the actual `HomePulseComposer`, APIs, media queue, and backend contracts.

## Simulator QA

The QA-only simulator build used a local authenticated fixture server and no production records. The native `/pulse/compose` deep link opened the expanded Composer on iOS 26.5 for:

| Device class | Result |
|---|---|
| Compact iPhone | PASS — launch, authenticated Home, expanded Composer, no crash |
| Standard iPhone | PASS — launch, authenticated Home, expanded Composer, no crash |
| Pro | PASS — launch, authenticated Home, expanded Composer, no crash |
| Pro Max | PASS — launch, authenticated Home, expanded Composer, no crash |

Evidence: `reports/screenshots/native-composer-production-parity-2026-07-17/`.

The simulator verified layout and native navigation. It did not create production posts or claim physical camera/microphone fidelity.

## Physical iPhone 16 Pro

- Device: iPhone 16 Pro, iOS 18.7.3, wired and paired.
- Build configuration: Debug with a bundled JavaScript application and production API base `https://pulsesoc.com`.
- Development bundle: `com.pulsesoc.nativeapp.dev`.
- Display name: `PulseSoc Native Dev`.
- Install: PASS.
- Launch/process: PASS; the application process remained running and production `/health` returned HTTP 200 from the Mac.
- Side-by-side safety: PASS at the identity/configuration level because the development bundle is distinct from Release. The device app inventory did not contain a separate PulseSoc App Store bundle at inspection time, so preservation of an installed WebView copy cannot be claimed as an observed device result.

Hands-on Photo Library, Camera, microphone, video capture, large upload, weak-network, force-quit-during-upload, Low Power Mode, and permission-denial interaction require the user to operate the unlocked phone. Those results remain BLOCKED/UNVERIFIED; no personal media was selected or uploaded.

## Cross-client compatibility and remaining blockers

Static contract parity is complete: native writes the same production routes, canonical post types, author session, visibility values, ordered media IDs, and music IDs consumed by WebView. No WebView posting code or backend schema was changed.

Production replacement is **not approved yet** because this run intentionally did not publish real content. The following must still be proven with controlled production accounts and safe test media:

1. Native text/photo/multi-photo/video/Reel posts appear once in WebView with identical post, author, media, audience, moderation, and Reel IDs.
2. WebView-created equivalents hydrate once in native.
3. A forced timeout after server success reconciles without a duplicate post or Reel.
4. Physical permissions, camera/microphone capture, background/foreground, weak network, force quit, large upload, Low Power Mode, and processing failure recovery.
5. The App Store WebView application is present if side-by-side visual confirmation is required.

## Completion and release readiness

- Contract audit and implementation: **95%**.
- Automated and simulator QA: **90%**.
- Physical-device installation/launch: **100%**.
- Required hands-on physical and cross-client publication QA: **20%**.
- Overall mission evidence: **76%**.
- Release readiness: **BLOCKED** pending the controlled tests above. The native development build is ready for the user’s device testing, but it is not yet safe to replace the WebView build in the App Store.

## Rollback plan

Revert the scoped Composer commit. The change does not migrate data, alter backend tables, or modify WebView creation code. Existing server posts, media, sessions, and the App Store application remain untouched. The development app can be removed independently because it has a separate bundle identity.

## Files changed

- `mobile-native/src/api/composerMusic.ts`
- `mobile-native/src/api/feed.ts`
- `mobile-native/src/api/reels.ts`
- `mobile-native/src/components/HomePulseComposer.tsx`
- `mobile-native/src/media/ComposerMediaQueue.tsx`
- `mobile-native/src/media/nativeMediaUpload.ts`
- `mobile-native/src/media/useComposerMediaQueue.ts`
- `mobile-native/src/navigation/notificationRouting.ts`
- `scripts/pulsesoc_native_pulse_composer_complete_audit.py`
- This report and four simulator screenshots.
