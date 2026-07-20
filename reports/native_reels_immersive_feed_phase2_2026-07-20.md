# Native Reels — Phase 2: Unified Immersive Media Feed

Date: 2026-07-20
Branch: `release/undx-nexus-core-v4`
Targets: physical iPhone **P3r7or** (iPhone 16 Pro) + booted Xcode Simulator (PulseSoc iPhone 16 Pro).

## Scope of this phase

Acceptance criterion #2 of the mission — **"Make native Reels the unified infinite feed for videos, reels, photos, carousels, livestreams, and replays, each with a purpose-built native renderer"** — plus criterion #4's first slice: **a live broadcast plays inline inside Reels instead of navigating away**. Infinite scroll (criterion #3) and the host/guest broadcast core (criterion #1) shipped in Phase 1.

## What is real and shipped

### 1. Pure media classifier (no React / native deps, fully unit-tested)
`src/reels/reelMediaKind.ts` classifies every feed item into exactly one render kind so the card can dispatch to a purpose-built surface:

- `video` — a single autoplaying clip (the classic Reel)
- `photo` — a single still image that holds until the user scrolls
- `carousel` — multiple slides swiped horizontally
- `livestream` — a broadcast that is live right now (played inline in-feed)
- `replay` — a finished broadcast's recording (played like a video)

Helpers `reelLiveSessionId`, `reelIsLiveContent`, `reelIsActiveLive`, and `reelMediaSlides` (mux → HLS, poster resolution, legacy `video_url` fallback, drops slides with no usable URL, preserves order) are exported and covered by **15 unit tests** in `src/reels/__tests__/reelMediaKind.test.ts`.

### 2. Purpose-built native renderers (`src/components/reels/`)
- **`ReelPhotoSurface`** — a full-bleed still `Image` (`contain` over the blurred poster). No timeline, no autoplay, no audio: the photo simply holds until the user scrolls, as specified.
- **`ReelCarouselSurface`** — a horizontal paging `FlatList` with position dots + an `n/total` counter. Video slides autoplay **only** while their slide is current AND the whole card is active, so leaving a slide (horizontally) or the card (vertically) pauses the clip. Images hold.
- **`ReelLiveViewerSurface`** — an in-feed LIVE viewer with a three-tier transport ladder, honest at every rung (see §3).

### 3. In-feed LIVE viewer — real subscribe, honest fallbacks (anti-fakery)
`ReelLiveViewerSurface` plays a live broadcast **inside the feed** rather than bouncing to a web page or detail screen. Transport priority:
1. **LiveKit subscribe** — mints a `viewer` token (`getLiveKitToken(liveId, "viewer")`) and connects with `publish: false` via the shared `useLiveBroadcastRoom`. Remote participants (host + co-hosts) render as real `VideoView` tiles in a 1-up / split / 2×2 stage, host prioritized, with an active-speaker ring. Reuses the WebRTC module already in the binary — **no native rebuild**.
2. **HLS fallback** — if the backend returns no viewer token or the room fails, and `reel.live.playback_url` exists, it plays that HLS URL via expo-av.
3. **Honest state** — if neither transport is available, it shows a labeled "this live isn't available" / "waiting for the host" surface. It **never** renders a fake camera preview: a video tile appears only when there is a real subscribed track, and HLS plays only when the backend actually handed us a URL.

The viewer connects only while its card is active and disconnects on scroll-away (releasing the LiveKit room and audio session), so exactly one live room is ever connected.

### 4. Card wiring — video effects guarded, chrome untouched
`ReelPlayerCard` now computes `classifyReelMedia(reel)` once and dispatches the media surface by kind. The video-only machinery (media-playback claim, attached-audio sync, `playAsync`, the tap-to-mute/like layer) is guarded to run **only** for `video`/`replay` kinds, so photo/carousel/live cards don't drive the absent `<Video>` ref. Watch-time tracking (`onViewable`) still fires for every kind. The locked Reels chrome — top author/follow, action rail, caption, progress, LIVE badge — is unchanged; `isLive` is now driven by the classifier (`kind === "livestream"`), so replays correctly no longer show a LIVE badge.

### 5. Live items play inline (no forced navigation)
`ReelsScreen` viewability only sets the active index — it never force-navigates for live items, so a live Reel now plays inline through the card. `joinLiveReel` (the "Join Live" button, and comments on a live) remains as the optional expand-to-full-live-with-chat affordance, exactly as scoped.

## Verification
- `npx tsc --noEmit` — **0 errors** in all Phase 2 code. (Pre-existing, unrelated type errors in `src/components/PostCard.tsx` come from an uncommitted in-progress edit not part of this work; the committed version compiles clean.)
- `npx jest` — **17 suites / 195 tests passing**.
- Physical P3r7or: `Build Succeeded`, 0 errors, installed 100%.
- Simulator (PulseSoc iPhone 16 Pro): `Build Succeeded`, installed.

Note: full end-to-end live viewing (subscribing to a real host publishing over LiveKit) requires a backend that grants a viewer token plus an active broadcast; the simulator/device validate build, nav, classification, and the honest fallback/error states, not live capture from a second broadcasting device.

## Not yet built (later phases)
Guest invite sheet, in-broadcast moderation UI (mute/kick/timeout/chat-delete), and a dedicated replay viewer beyond replay-as-video. These depend on the backend contracts documented in the Phase 1 report (`reports/native_live_broadcast_phase1_2026-07-19.md`) that do not yet exist server-side. The native UI must not present those actions as working until the contracts ship.
