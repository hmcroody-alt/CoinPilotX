# Native Reels — Phase 3: Guest Management, In-Broadcast Moderation, Dedicated Replay Viewer

Date: 2026-07-20
Branch: `release/undx-nexus-core-v4`
Targets: physical iPhone **P3r7or** (iPhone 16 Pro) + booted Xcode Simulator (PulseSoc iPhone 16 Pro).

## Scope of this phase

The three items previously documented as "not yet built / backend-blocked" in the Phase 2 report:
guest invite/management sheet, in-broadcast moderation UI, and a dedicated replay viewer. Before
building anything, the actual backend (`bot.py`) was re-verified rather than trusting the stale
Phase 1 blocker table. Result: guest mute/unmute/remove and replays are **already real**; only
host-initiated *outbound* invite, chat-message delete, and viewer timeout have no server route.
Everything below is wired to real endpoints; nothing fakes a capability the backend lacks.

## Verified backend contracts used

- `GET /api/pulse/live/<id>/join-requests` → `{ ok, requests: [...], guests: [...] }`, host-gated
  (`bot.py:40932`). Returns both pending requests and the active on-stage guests.
- `POST /api/pulse/live/<id>/join-requests/<requestId>/<accept|deny>` → host-gated (`bot.py:40953`).
- `POST /api/pulse/live/<id>/guests/<guestId>/<mute|unmute|remove>` → host-gated, returns
  `{ ok, status, guest_id }` (`bot.py:41166`). `mute/unmute` toggles `audio_muted`; `remove` sets
  the guest `status` to `removed` and stops their publish slot.
- `POST /api/pulse/live/<id>/end` builds the replay via `mux_recording_playback_id` →
  `https://stream.mux.com/{id}.m3u8` and indexes it as a `replay` (`bot.py:41222`). Replays are real
  Mux assets.

## What is real and shipped

### 1. Guest-management API layer (`src/api/live.ts`, `src/live/liveSession.ts`)
- New pure type `LiveGuest` + `normalizeLiveGuest` / `normalizeLiveGuests` in `liveSession.ts`
  (dedupes by guest id, preserves backend layout order, no React/native deps).
- `listGuestManagement(liveId)` → `{ requests, guests }` from the single real `GET /join-requests`.
- `guestAction(liveId, guestId, action)` + `muteGuest` / `unmuteGuest` / `removeGuest` wrappers,
  wired to the real guest-action endpoint.

### 2. In-broadcast moderation + guest management (`src/screens/LiveHostSessionScreen.tsx`)
The host session now polls `listGuestManagement` (replacing the requests-only poll) so it surfaces
both pending requests and active guests. Existing **Guest requests** section keeps accept/deny. A new
**On stage** section lists each active guest with:
- **Mute / Unmute** — toggles the guest's audio via the real endpoint, optimistically reflecting
  `audioMuted` and reconciled on the next 5s poll.
- **Remove** — behind a destructive confirmation `Alert`; drops the guest from the list on success.

State is optimistic-then-reconciled; failures raise an honest `Alert` and leave the list unchanged.

### 3. Dedicated replay viewer (`src/screens/ReplayViewerScreen.tsx`, route `ReplayViewer`)
A full-screen viewer for a finished broadcast's recording, distinct from replay-as-inline-video:
- Plays the real recorded Mux HLS asset via expo-av `Video` with **native scrubbing controls**.
- Resolves the URL from the route param, or—when only a `liveId` is passed—fetches `getLiveState`
  and uses `livePlaybackUrl`. If nothing resolves, shows an honest **"Replay unavailable"** surface
  ("No recording is available… Replays appear once the recording finishes processing"); on a player
  error it shows a "could not be played / may still be processing" state. It **never** shows a blank
  or fake player.
- Registered in `AppNavigator` (`headerShown:false`). `ReelsScreen.joinLiveReel` now routes
  `classifyReelMedia(reel) === "replay"` items to `ReplayViewer` (passing liveId, replay/video URL,
  poster, title, creator); active live items still route to `LiveDetail`.

## Honestly NOT built (no backend route — not faked)
- **Host-initiated outbound invite** (host picks a viewer and pulls them up). There is a viewer→host
  *request* flow (`/join-request`) and host accept/deny, but no host→viewer invite endpoint. The
  guest-management UI therefore manages *incoming* requests + active guests only; it does not present
  a fake "invite" control.
- **Chat-message delete** — the chat table has a `deleted_at` soft-delete column but no delete route.
- **Viewer timeout/suspend** — no endpoint.

These require new `bot.py` endpoints; per the mission's anti-fakery mandate the native UI does not
present them as working.

## Verification
- `npx tsc --noEmit` — **0 errors**.
- `npx jest` — **18 suites / 205 tests passing** (added 2 tests covering `normalizeLiveGuest(s)`).
- Physical P3r7or: `Build Succeeded`, 0 errors, installed 100%.
- Simulator (PulseSoc iPhone 16 Pro): `Build Succeeded`, installed + opened.

Note: end-to-end guest moderation requires a live broadcast with a second user accepted as a guest;
device/sim validate build, nav, classification, wiring, and the honest fallback/error states, not a
two-party live moderation round-trip.
