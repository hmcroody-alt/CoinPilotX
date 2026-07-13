# PulseSoc Native Reels Futuristic Design and Deep Wiring

Date: 2026-07-12

Active subsystem: Native Reels

Freeze decision: **not simulator-parity frozen**

Replacement decision: **not ready to replace production WebView Reels**

## Outcome

Native Reels now preserves the production route family and feed lanes while presenting one dominant full-screen video per measured viewport. Comments are excluded from the feed request and never render on the Reel canvas; they load only after Comment opens the native sheet. Reactions have a compact production-backed selector, attached music is reduced to a micro-attribution while its real audio source is coordinated with the video, and production Live records now survive native normalization and route into the existing Live viewer.

## Production-to-native matrix

| Capability | Production source | Native reuse/result | Status |
|---|---|---|---|
| Feed and lanes | `/pulse/reels`, `/api/pulse/reels/feed`; `for_you`, `following`, `trending`, `music`, `live` | `listReels`, lane-scoped cache, horizontal lane rail | Wired |
| Detail/deep links | `/pulse/reels/<id>` | existing `ReelDetail`, linking and notification routing | Preserved |
| Playback | production media/Mux URLs | existing `expo-av` `Video`, one active card, offscreen/background/sheet pause | Wired; simulator HLS verified, physical playback pending |
| Paging/preload | production full-screen vertical feed | measured viewport snap, two initial cells, window size three | Wired |
| Reactions | existing feed reaction engine and `/react` | optimistic apply, replacement/removal reconciliation, six production types | Wired |
| Comments/replies | existing Reel comment GET/POST and parent IDs | hidden until sheet, lazy load, composer, reply target, comment reaction | Partially wired |
| Save/share/follow | existing Reel endpoints | optimistic save/follow rollback, native share sheet | Wired |
| Music | production audio record and licensing filters | tiny attribution, compact detail, attached `Audio.Sound`, original-audio mute guard | Wired; physical mixing pending |
| Live | production Live records merged into Reels | fixed string-ID normalization, Live badge and Join Live to existing `LiveDetail` | Wired; physical Live pending |
| Creator | production/native camera and creator routing | existing `CameraStudio` target `reel` | Preserved |
| More/moderation | repost, not-interested, report, promotion | compact native sheet using existing endpoints/routes | Wired where contracts exist |
| Offline | production authorization plus native cache | lane-scoped cached feed and reconnect state | Partial; mutation queues unavailable |
| Analytics | existing view/watch endpoint | active-watch duration uses existing `trackReelView` | Partial |
| Realtime | production event emission | existing refresh/cache behavior only | Incomplete; event invalidation not yet integrated |

## Existing code reused

- `ReelsScreen`, `ReelPlayerCard`, `api/reels`, `expo-av` player, media normalization, Mux/HLS resolution, pagination, Reel/comment/reaction/save/share/follow/report endpoints, native profile, creator, Growth Center and Live routes.
- Existing backend reaction engine, comment/reply model, feed ranking, audio licensing records, Live merge, notifications, event emission, privacy, moderation and WebView route aliases remain authoritative.
- No duplicate backend, video cache, reaction service, comment service, music service, Live engine, analytics service, or route family was created.

## UX implementation

- Video remains the dominant layer; five primary actions use compact glass surfaces.
- Reaction controls provide pressed/selected states and a short vibration; long press opens the selector.
- Comments are hidden by default, loaded only on Comment, keyboard-safe, and playback pauses while open.
- Attached music defaults to a low-opacity note and truncated title. Tapping opens a compact detail sheet.
- Feed lanes, Create, author/profile, Follow, Save, Share, More, processing, buffering, offline and Live states remain accessible without a persistent metadata panel.
- Safe layout uses actual container height plus safe-area top spacing instead of assuming the full device window.

## Deterministic QA

Localhost-only, explicit `EXPO_PUBLIC_PULSESOC_QA_REELS_FIXTURES=1` fixtures cover populated, music-attached, long-caption, processing, comments and Live records. Optional `EXPO_PUBLIC_PULSESOC_QA_REELS_STATE` entry states make comments, reaction, music and Live evidence repeatable. Both gates are unavailable against production API bases and contain no credentials.

## Verification

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`: passed (811 packages installed).
- `npm run --prefix mobile-native typecheck`: passed after final UI changes.
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`: 17/17 checks passed.
- Focused deep-wiring audit and existing native Reels audit: passed.
- Production playback, mobile playback, music, Live, engagement, action-button, sound-persistence, layout, load-speed and pipeline audits: passed.
- Release simulator build: passed with an embedded JS bundle and development identity override.
- Simulator install/launch: passed on iPhone 17 Pro simulator. Captured populated main, comments, reaction selector, music detail and Live states.
- Signed Release device build: passed with Apple Development signing and the development provisioning profile for `com.pulsesoc.nativeapp.dev`.
- Physical iPhone 16 Pro install/launch: passed on iOS 18.7.3. App inventory confirmed production `com.pulsesoc.app` and development `com.pulsesoc.nativeapp.dev` remain installed side by side.
- Physical interaction: not counted. Installation and command-line launch do not prove Reel playback, gestures, audio, comments, reactions, Live, interruption or thermal behavior.
- `git diff --check`: required again immediately before commit.

## Evidence classification

- Simulator-verified: development identity, embedded Release launch, localhost QA account bootstrap, root Reels routing, full-height layout, Dynamic Island separation, populated HLS canvas, hidden-comments baseline, comments sheet, reaction selector, music detail and Live card.
- Controlled-backend verified: production Reels, comment/reply, reaction, save/share/follow, music, Live and view contracts through repository audits; no route or endpoint family was replaced.
- Mock-state verified: populated, attached-music, processing and Live records; comments and reaction sheets use production-compatible shapes.
- Physical-device verified: signed build, install, launch, Developer Mode, app identity, and side-by-side production/development inventory only.
- Physical-device-only and still open: real vertical snapping, playback/audio mixing, mute, tap/double-tap/long-press, haptics, keyboard/send, share sheet, background/interruption behavior, realtime insertion/deletion, cached playback and thermal/memory behavior.

## Honest status

- Overall Reels capability: 76%
- UI design: 84%
- Visual quality: 78%
- Interaction completion: 73%
- Deep wiring: 68%
- Feed switching: 86%
- Video playback: 78%
- Paging: 86%
- Preloading: 72%
- Author/profile: 76%
- Caption/metadata: 68%
- Reactions: 82%
- Comments: 72%
- Replies: 48%
- Sharing: 80%
- Saving: 84%
- Follow/unfollow: 80%
- Music: 74%
- Audio mixing: 58%
- Live: 65%
- Join Live routing: 88%
- More menu: 66%
- Moderation: 58%
- Realtime: 38%
- Offline/reconnect: 52%
- Analytics: 44%
- Loading/empty/error: 66%
- Accessibility: 72%
- Responsive behavior: 82%
- Performance: 78%
- Xcode Simulator QA: 62%
- iPhone 16 Pro QA: 14%
- Device-size coverage: 25%
- Backend/business reuse: 94%
- Frontend utility reuse: 88%
- Existing native component reuse: 92%

## Remaining gaps and next exact mission

Reels must remain active. The next mission should add the shared event-invalidation/reconciliation layer for Reel create/edit/delete/reaction/comment/Live events; complete comment editing/deletion/reporting and reply expansion; add production-authorized mutation queues if contracts permit; and perform the full simulator-width plus physical playback/audio/gesture/thermal matrix. Until that work is proven, Reels cannot be frozen or replace the WebView implementation.
