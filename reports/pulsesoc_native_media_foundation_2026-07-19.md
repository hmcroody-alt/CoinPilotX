# PulseSoc Native Media Foundation — 2026-07-19

## Scope and release judgment

This milestone maps and strengthens the shared native media foundation. It does not redesign Reels. Production remains authoritative and no native-only media backend, database, attachment identity, Reel identity, transcoder, catalog, signing service, or engagement service was added.

**No native-only media backend** is introduced by this work.

Foundation implementation confidence: **86%**. Static contracts, simulator rendering, signed physical-device compilation, installation, and launch pass. Authenticated physical Reel playback, cross-client uploads, and sustained physical-device performance remain release gates.

## Sources inspected

WebView: `static/js/pulse_media_renderer.js`, `static/js/pulse_upload_manager.js`, `static/js/pulse_media_picker.js`, `static/js/pulse_messages_v2.js`, `static/js/pulse_status_viewer.js`, and the production Reels/feed/status composer code in `bot.py`.

Backend: `services/media_service.py`, `services/media_storage.py`, `services/messenger_media_foundation.py`, `services/music_service.py`, `services/reel_ranking_engine.py`, `services/upload_progress_service.py`, `pulse_communications_v2/service.py`, and route handlers in `bot.py`.

Native: media/upload/viewer APIs and components under `mobile-native/src/media`, `mobile-native/src/api/{feed,reels,status,messenger,live}.ts`, Reels/Status/Live player surfaces, Pulse Radio, voice-message playback, calls, and session cleanup.

## Production contract matrix

| Lifecycle | Production WebView/backend | Native adapter | Identity rule |
|---|---|---|---|
| Feed/Status/Reel upload | `POST /api/pulse/media/upload` multipart `file`, context fields | `nativeMediaUpload.ts` XHR with progress/cancel | Returned `media.id` is canonical |
| Messenger attachment | init → upload → complete under `/api/messages/media` | `uploadMessengerMedia` | Returned `attachment_id` is canonical; send uses `attachment_ids` |
| Processing refresh | `GET /api/pulse/media/<id>/status` | `pollNativeMediaProcessing`, `refreshCanonicalMediaAccess` | Refreshes same media ID |
| Reel feed | `GET /api/pulse/reels/feed` with limit/offset/tab | `listReels` | `reel_id` preserved and deduplicated |
| Reel publish | `POST /api/pulse/reels/create` with `media_ids` and music reference | `createReel` | No duplicate Reel/media record |
| Engagement | production react/comments/save/repost/share/view routes | `api/reels.ts` | Server response reconciles state |
| Playback | production playback/HLS/Mux metadata | Expo AV adapters | URL is access data, never identity |

Storage providers, processing workers, Mux configuration, signing, moderation, privacy, and deletion remain backend-owned. Native supplies no credentials and logs no signed URL.

## Canonical media schema

`CanonicalMediaRecord` preserves media, attachment, owner, post, Reel, message, and Status IDs; MIME/container/codec/duration/dimensions/file size; playback/thumbnail/HLS/Mux metadata; processing/transcoding/moderation/visibility/privacy/expiration; music/original-audio attribution; and deletion timestamps. Existing API shapes remain structurally compatible through `PulseMedia`.

## Playback and audio ownership

`mediaPlaybackCoordinator.ts` is the single native ownership arbiter. Priority is call → recording → Live → voice → viewer → Status/Reel → preview → Radio. Claiming an owner pauses the previous owner; lower-priority playback cannot interrupt a call. App backgrounding releases/stops the active owner. Reels, Status, full-screen viewer, Live, calls, voice messages, and Pulse Radio use this coordinator.

Offscreen Reel video pauses. Attached Reel audio is created only for the active Reel and unloaded on deactivation. This removes the former mounted-card audio duplication.

## Signed access and cache policy

Stable public URLs may be cached with bounded feed/Reels snapshots. URLs containing signing credentials (`X-Amz-*`, signature, policy, key-pair, or token) are removed before persistence. `refreshCanonicalMediaAccess` re-fetches the same canonical record through the production status route; it does not mint IDs or persist credentials. Logout stops playback and removes user-scoped feed, Reel, Status, post, and Messenger caches.

## Reels data, engagement, music, and creation readiness

Production offset pagination, lane selection, normalization, cache-first recovery, canonical-ID deduplication, view tracking, reactions, comments, save, repost, share, follow, not-interested, report, and deep-link routes are wired. Music track ID, attached-audio URL, start time, volume, and original-audio mute metadata remain server-shaped. Reel creation reuses uploaded canonical `media_ids` and `music_track_id`.

Privacy, blocking, visibility, moderation, ranking, publication eligibility, and deletion are not reimplemented locally; native renders backend-authorized records and actions.

## Central Reels player implementation

Only `ReelPlayerCard` and its direct data/state handoff were redesigned. The approved Reels lane selector/header and the global bottom navigation were not modified. The central surface now provides near-edge-to-edge video inside the frozen shell; compact canonical creator identity and follow state; a vertical production-wired like/comment/share/save/more rail; caption rendering with readable mentions and hashtags; compact music/original-audio attribution; explicit mute and user pause controls; a lightweight progress treatment; and separate processing, buffering, offline, secure-URL-refresh, recoverable-error, removed, restricted, and moderation-unavailable states.

Single-tap pause is preserved for the mounted Reel and double-tap uses the production `like` reaction. The visible Reel alone claims the existing media playback coordinator. Offscreen and preempted Reel video/audio pause, attached audio is allocated only after ownership is granted, app backgrounding releases the owner, and calls/recording/Live/voice/viewer playback retain higher priority. Secure playback recovery refreshes the existing canonical media record through `/api/pulse/media/<media_id>/status`; it never creates replacement media or exposes raw URL/storage errors.

## Upload compatibility and processing

Images/videos use the same production multipart route and server limits (5 MB image, 8 MB GIF, 150 MB video defaults). Messenger uses the production media-foundation init/upload/complete sequence. The unused legacy Messenger upload adapter was removed. Upload progress, cancellation, retry entry points, processing polling, and thumbnail/playback metadata reconciliation remain native adapters over production services.

## Verification evidence

- TypeScript: **PASS** (`tsc --noEmit`) after the central player implementation.
- Expo Doctor: **PASS**, 17/17 checks from the completed foundation checkpoint; focused resume run returned without a reported failure.
- Media foundation audit: **PASS** (`scripts/pulsesoc_native_media_foundation_audit.py`).
- Reels audit: **PASS** (`scripts/pulsesoc_native_reels_audit.py`), including canonical routes, shared playback ownership, secure URL refresh, inactive attached-audio release, and frozen-shell spacing.
- Diff whitespace validation: **PASS** (`git diff --check`).
- iPhone 16 Pro simulator compile: **PASS** with Xcode 26.6 (`** BUILD SUCCEEDED **`).
- Simulator runtime: **PASS** using a local-only runtime QA account and local-only Reels fixtures. The central Reel rendered below the unchanged lane header; the fixture video advanced between evidence captures, and the like/comment/share/save/more, author/follow, caption, original-audio, mute, and progress treatments were visible. No production media or identity was copied into fixtures.
- Simulator root cause note: the initial white screen was the Debug app waiting for Metro at `localhost:8081`; starting the repository dev server restored the native UI. It was not a Reel render crash.
- Physical iPhone 16 Pro compile/sign: **PASS** with the existing Debug development identity (`com.pulsesoc.nativeapp`, display name `PulseSoc Native Dev`).
- Physical iPhone install: **PASS** through `devicectl`; production remains side by side because the development bundle identity is separate.
- Physical iPhone launch: **PASS** through `devicectl`.
- Authenticated physical Reel playback: **NOT YET VERIFIED**; it requires the user to sign in to the installed development app and leave an actual production-authorized Reel visible for controlled observation.

## Physical-device measurements

Simulator fixture video playback advanced and the central overlays remained responsive during the focused visual check. No honest sustained physical measurements are recorded yet. First metadata, first cover, first playable frame, swipe latency, buffering, memory, CPU, thermal, battery, weak-network recovery, Wi-Fi/cellular switching, lock/unlock, call interruption, signed-URL expiry, Low Power Mode, and long-session cache growth remain **not measured** until the controlled authenticated iPhone run.

## Cross-client compatibility

Contract inspection confirms both clients target the same media, attachment, Reel, post, Status, message, music, and engagement routes. WebView-to-native and native-to-WebView controlled upload/playback tests remain required before staged rollout. No database migration is required and no destructive production write was made during this mission.

## Remaining blockers

1. Controlled production-compatible media accounts and samples are needed for full cross-client upload/playback proof.
2. Sustained physical-device performance and signed-URL expiry tests must be measured.
3. Realtime processing event names must be validated end-to-end; canonical status polling is currently the proven recovery path.
4. Low Power Mode adaptation needs measured policy confirmation before claims of optimized playback.
5. The installed physical build must be exercised after real-account sign-in before physical Reel playback or internal-beta readiness can be claimed.

## Reels UI release gate

The central Reels experience is implemented and simulator-verified inside the frozen header/navigation shell. Internal beta/staged media rollout is **not yet approved** until authenticated physical playback, cross-client creation, realtime processing reconciliation, privacy/moderation parity, interruption testing, and performance measurements pass.

## Files changed and repository evidence

Implementation files and audit results are included in this commit. The final commit hash and push confirmation are reported from Git history and the Codex completion response so the report does not contain a self-invalidating commit hash.
