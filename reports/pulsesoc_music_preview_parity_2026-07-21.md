# PulseSoc Native — Unified Music Experience & Content Preview Parity

**Date:** 2026-07-21
**Repo / branch:** `CoinPilotX` @ `release/undx-nexus-core-v4`
**Target:** `mobile-native`
**Verdict:** ✅ **GO** — the true client-side root cause (a relative attached-music URL that `Audio.Sound` cannot load) is fixed; validation is green; deployed to the physical iPhone P3r7or.

> **Correction (post-device retest):** the first pass of this report claimed GO on the strength of Finding A alone (PostCard honoring attached music). On device, **nothing changed** — because that fix was *inert*. The end-to-end trace below (Finding D) pins the actual defect: the backend serializes the attached track as a **server-relative** path, and `Audio.Sound.createAsync` cannot load a relative URI. The composer preview absolutizes its URL, which is exactly why **preview sounded right but the published video was silent**. Finding A was necessary but not sufficient; Finding D is what makes music actually play.

---

## 1. Mission recap

Two intertwined goals:

- **Fix the music publishing bug** — when a user attaches music, the preview sounds right but the *published* video played only the original microphone audio. Selected music must become the published audio track (original muted); removing music restores original audio.
- **Unify content creation** — one music picker + preview + publishing pipeline across Posts, Reels, and Statuses, governed by a single audio policy.

## 2. What the investigation actually found

Most of the "unify" premises were **already implemented** before this session and were verified, not rebuilt:

- **One composer** — `HomePulseComposer` already drives post/reel/status/poll through a single `runPublish()` path (music picker, media queue, preview handoff shared).
- **One preview** — `ContentPreviewScreen` → `ContentPreviewRenderer` already renders the *real* production cards (PostCard / ReelPlayerCard / StatusViewerCard), so "preview == published" holds by construction.
- **One audio policy** — `src/core/attachedMusicAudioPolicy.ts` already centralized the rule "attached music takes exclusive audio priority" for **reels and statuses**.
- **Backend enrichment** — feed-video hydration and status serialization already stamp `music` + `attached_audio_url` / `original_audio_muted` / `audio_start_time` / `audio_volume` on responses.

Against that backdrop, two real defects and one duplication remained:

| # | Finding | Status this session |
|---|---------|---------------------|
| **A** | **`PostCard` ignored attached music entirely.** The feed video was rendered with `isMuted={muted}` and had no `Audio.Sound` — so a video *post* published with music played the original mic audio on the surface where most video is consumed. This is the client half of the reported bug. | **FIXED** |
| **B** | **Backend attachment can silently no-op.** `pulse_attach_music_to_content` only attaches if the track id resolves as *creator-safe* through the strict `pulse_audio_tracks` JOIN (approved/active/admin/commercial/remix/non-empty url). AI-suggested ids that don't resolve are dropped without error, so nothing is stamped on the response. | **Documented follow-up** — requires editing `bot.py`, which is owned by a concurrent session and off-limits here. Mitigated client-side by Finding C. |
| **C (duplication)** | `StatusCreator.tsx` is a second status composer (own music search, own `createStatus`, no shared preview / no preview audio) that bypasses the unified path. | **Documented follow-up** |
| **D** | **The attached-music URL is server-relative.** The feed serialization (`pulse_feed_engine._music_for_posts` → `_media_with_attached_music`) *does* stamp `music` + `attached_audio_url` onto video posts — but as a relative path (e.g. `/static/audit/attached-pulsesoc-music.wav`, confirmed by running `get_post` against the DB). `resolveAttachedMusicPolicy` passed that raw relative string to `Audio.Sound.createAsync`, which cannot load a relative URI, so the track silently failed and the (muted) original left the post silent. The composer preview runs the URL through `absoluteUrl()`, so preview worked while publish did not. **This is the true root cause of "nothing changed."** | **FIXED** |

## 3. Changes shipped (client-only scope)

Scope decision (per the "pick one" directive): ship the confirmed client-side root-cause fix plus additive, low-risk hardening; document the backend-dependent defect and the `StatusCreator` consolidation as follow-ups rather than block on `bot.py`.

**Fix A — PostCard honors attached music**
- `src/core/attachedMusicAudioPolicy.ts`: added `postMusicToMusicSource()` + `resolvePostAudioPolicy(post, media)` — a post/media adapter that resolves the attached track from (in priority) the post-level `music` object → top-level mirror fields → the video media record's `attached_audio_url`.
- `src/components/PostCard.tsx`: `FeedInlineVideo` now resolves the policy, mutes the original video track whenever music is attached (`isMuted={muted || policy.muteOriginalAudio}`), and plays the attached track via an `Audio.Sound` effect that mirrors `ReelPlayerCard` (position/volume/loop from policy, audible only when the viewer unmutes, unloaded on url change / unmount, wired into the media-playback coordinator's pause/stop).

**Type plumbing**
- `src/media/mediaContract.ts`: `CanonicalMediaRecord` gains `original_audio_muted`, `audio_start_time`, `audio_volume`, `audio_title`, `audio_artist`.
- `src/api/feed.ts`: added `PulsePostMusic` type and `music` + top-level music mirror fields on `PulsePost`; added defense-in-depth fields to `CreatePostPayload`.

**Fix B mitigation — defense-in-depth publish payload (additive, backend stays source of truth)**
- `src/api/reels.ts` / `src/api/status.ts`: `CreateReelPayload` / `CreateStatusPayload` carry `attached_audio_url`, `original_audio_muted`, `audio_start_time`, `audio_volume`; `createReel` serializes them (defaulting `original_audio_muted` to true when a track id is present).
- `src/components/HomePulseComposer.tsx`: new `attachedMusicPublishFields()` helper injects the resolved track metadata into all three create payloads (post/reel/status). This means playback honors the selected music even when server-side attachment is bypassed (Finding B).
- `src/api/feed.ts`: `createPost` previously hardcoded its request body and **dropped** these fields; it now forwards `attached_audio_url` / `original_audio_muted` / `audio_start_time` / `audio_volume` (defaulting `original_audio_muted` to `Boolean(music_track_id)`), matching `createReel`/`createStatus`.

**Fix D — absolutize the attached-music URL (the fix that makes music audible on device)**
- `src/core/attachedMusicAudioPolicy.ts`: added `absolutizeMusicUrl()` and applied it inside the single shared `resolveAttachedMusicPolicy` funnel, so **every** surface (post, reel, status) gets an absolute URL. Absolute URIs (`http(s):`, `file:`, …) pass through untouched; server-relative paths get the `PULSE_API_BASE_URL` prefix — identical treatment to `mediaDisplayUrl` for video.
- `src/components/PostCard.tsx`: call `configureReelsAudioSession()` before creating the attached `Audio.Sound`, so the track is audible even with the ringer switch on silent and regardless of whether the viewer visited Reels/Music first (the iOS audio mode is process-global and was otherwise only set by those screens).

## 4. Tests

- `src/core/__tests__/attachedMusicAudioPolicy.test.ts` — 7 post-adapter cases **plus 4 URL-absolutization cases** (server-relative path prefixed, absolute http untouched, bare relative path, and a published-post relative `attached_audio_url`). **24/24 pass.**
- `src/api/__tests__/createPayloadMusic.test.ts` — asserts the defense-in-depth music metadata is serialized verbatim by `createReel`/`createStatus`, and that muting is not forced when no music is attached. **4/4 pass.**

## 5. Validation commands

| Command | Result |
|---------|--------|
| `npx tsc --noEmit` | ✅ exit 0 |
| `npx jest --runInBand --silent` | ✅ **369 passed / 38 suites**, 0 failures |
| `git diff --check` | ✅ clean (no whitespace errors) |

No changes to `bot.py` or any backend file. The Fix D commit (`ce837789`) touches 4 client files (`attachedMusicAudioPolicy.ts`, its test, `PostCard.tsx`, `feed.ts`).

## 6. Physical-device validation

Built Release and installed to **P3r7or** (iPhone 16 Pro, `F45E640F-6D02-514E-877C-B764E8D6818F`) via the detached `xcodebuild` + `devicectl` recipe (`expo run:ios` remains broken on Xcode 26 here).

- **Fix D build:** `** BUILD SUCCEEDED **`, 0 `error:` lines. Installed to bundle `com.pulsesoc.nativeapp` and launched with `--terminate-existing`.
- **On-device retest expectation:** attach music → publish a video post → open in feed → unmute → the selected track plays; the original mic audio stays muted; removing music before publishing restores original audio.

## 7. Follow-ups (out of the client-only scope)

1. **Finding B (backend):** make `pulse_attach_music_to_content` surface a non-fatal warning when a submitted `music_track_id` fails the creator-safe JOIN, so the composer can tell the user the track wasn't attached instead of silently publishing original audio. Requires `bot.py` (owned by another session).
2. **Finding C (consolidation):** retire `StatusCreator.tsx` in favor of the unified `HomePulseComposer` status mode so statuses get the shared preview + music-preview audio, eliminating the last duplicate composer path.
