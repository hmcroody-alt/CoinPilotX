# PulseSoc Native — Unified Music Experience & Content Preview Parity

**Date:** 2026-07-21
**Repo / branch:** `CoinPilotX` @ `release/undx-nexus-core-v4`
**Target:** `mobile-native`
**Verdict:** ✅ **GO** — the confirmed client-side publishing defect is fixed; validation is green; deployed to the physical iPhone P3r7or.

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

## 4. Tests

- `src/core/__tests__/attachedMusicAudioPolicy.test.ts` — extended with 7 post-adapter cases (post-level object, top-level mirror, media fallback, precedence, no-music original-audio, nullish). **20/20 pass.**
- `src/api/__tests__/createPayloadMusic.test.ts` (new) — asserts the defense-in-depth music metadata is serialized verbatim by `createReel`/`createStatus`, and that muting is not forced when no music is attached. **4/4 pass.**

## 5. Validation commands

| Command | Result |
|---------|--------|
| `npx tsc --noEmit` | ✅ exit 0 |
| `npx jest --runInBand --silent` | ✅ **365 passed / 38 suites**, 0 failures |
| `git diff --check` | ✅ clean (no whitespace errors) |

Diff: **9 files, ~221 insertions / 5 deletions** (8 tracked + 1 new test). No changes to `bot.py` or any backend file.

## 6. Physical-device validation

Built Release and installed to **P3r7or** (iPhone 16 Pro, `F45E640F-6D02-514E-877C-B764E8D6818F`) via the detached `xcodebuild` + `devicectl` recipe (`expo run:ios` remains broken on Xcode 26 here). Build + install + launch status recorded below.

## 7. Follow-ups (out of the client-only scope)

1. **Finding B (backend):** make `pulse_attach_music_to_content` surface a non-fatal warning when a submitted `music_track_id` fails the creator-safe JOIN, so the composer can tell the user the track wasn't attached instead of silently publishing original audio. Requires `bot.py` (owned by another session).
2. **Finding C (consolidation):** retire `StatusCreator.tsx` in favor of the unified `HomePulseComposer` status mode so statuses get the shared preview + music-preview audio, eliminating the last duplicate composer path.
