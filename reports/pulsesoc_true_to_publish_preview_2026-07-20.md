# True-to-Publish Preview — Mission Report

**Date:** 2026-07-20
**Surface:** PulseSOC Native (`mobile-native`)
**Principle enforced:** *If the preview looks correct, the published result must look the same.*
**Recommendation:** **GO** (conditional — see Remaining Risks)

---

## 1. Composer flows audited

The native creation surface funnels through **one composer**, `HomePulseComposer`
(`src/components/HomePulseComposer.tsx`), embedded in `HomeScreen`. It produces
every publishable content type from a single draft state:

| Mode          | Publish API            | Canonical model | Renderer            |
|---------------|------------------------|-----------------|---------------------|
| `post`        | `createPost`           | `PulsePost`     | `PostCard`          |
| `poll`        | `createPost` (poll)    | `PulsePost`     | `PostCard`          |
| `scam_report` | `createPost`           | `PulsePost`     | `PostCard`          |
| `status`      | `createStatus`         | `PulseStatus`   | `StatusViewerCard`  |
| `reel`        | `createReel`           | `PulseReel`     | `ReelPlayerCard`    |

Text, photo, video, carousel (up to 4 media), and music-attached variants are
all expressed as media count + `musicTrack` on the same draft, so they are
covered by the same preview path. Dedicated camera capture and the music
library feed back into this composer via existing handoffs, so their output is
also previewed before publish.

## 2. Shared renderer architecture

There is now **one canonical preview renderer**,
`src/components/preview/ContentPreviewRenderer.tsx`. It contains **no bespoke
preview markup** — it routes each content kind to the *exact* production feed
component (`PostCard` / `ReelPlayerCard` / `StatusViewerCard`) that renders the
published item. "Preview mode" is expressed by supplying inert (`noop`)
interaction callbacks: the content is fully rendered (layout, media, badges,
metadata, audio) but reactions/comments/share/follow/report/etc. do nothing —
the correct disabled, non-published state. Media playback is still driven by the
real renderer's `active` prop and the attached-music audio policy runs
unchanged.

Because preview and feed share the same components, layout/media rules/badges
are identical by construction rather than by duplicated code.

## 3. Draft-to-content normalization layer

`src/create/draftToContentModel.ts` (pure, no React/IO — unit-tested) is the
linchpin. It converts composer draft state into canonical
`PulsePost`/`PulseReel`/`PulseStatus` objects by routing through the **same**
`normalizePost` / `normalizeReel` / `normalizeStatus` functions the live feed
uses on server payloads. The only intended differences from published content:

- **Media points at local device URIs** until upload completes. To make this
  render identically, the shared `mediaDisplayUrl` helper (`src/api/feed.ts`)
  was widened to pass through any URI scheme (`file:`, `content:`, `ph:`,
  `data:`, `blob:`) rather than only `http(s)`. Server-relative paths still get
  the API-base prefix, so production behavior is unchanged (verified: 43
  feed/reels/status/mediaContract tests pass).
- If an upload has already completed, the real server media record is preferred,
  so the preview matches the published pipeline exactly.

Returns `null` when the draft has nothing publishable, which gates the
preview/publish action.

## 4. Preview presentation & controls

`src/screens/ContentPreviewScreen.tsx`, registered as a header-less
`fullScreenModal` route `ContentPreview` (`navigation/types.ts`,
`AppNavigator.tsx`). It receives a `token` (serialization-safe param) that keys
into an in-memory handoff store (`src/create/previewHandoff.ts`) carrying the
draft + a live publish callback across the nav boundary.

Controls implemented:

- **Edit** — returns to the composer via `goBack()` without publishing; draft
  state is owned by the composer and untouched, so nothing is lost.
- **Post / Publish** — delegates to the composer's `runPublish`.
- **Clean preview** toggle — hides the metadata/badge overlay for an unobstructed
  view.
- **Metadata** — content type, audience/visibility, media count, attached music.
- **Progress** — spinner + "Publishing…" state on the publish button.

## 5. Audio-policy integration

Attaching approved music sets `original_audio_muted: true` and the track's
`attached_audio_url` on the canonical reel/status. The existing
`resolveReelAudioPolicy` / `resolveStatusMusicPolicy` resolvers then report
`muteOriginalAudio: true` and `hasAttachedMusic: true` — the same
ATTACHED_MUSIC_AUDIO_PRIORITY behavior as the feed, with **no fallback** to the
clip's original audio. Covered by tests.

## 6. Publishing guardrails

All guardrails live in the composer's single publish path (`runPublish`), which
both the direct button and the preview delegate to:

- **Validation before preview** (`validatePublish`) — empty content, active
  uploads, poll must end in `?`, scam report minimum length, music-requires-media,
  reel-requires-exactly-one-video. The preview can never publish something the
  composer would reject.
- **Disable when data missing** — publish button gated on `hasPublishPayload`.
- **Duplicate-submit prevention** — preview screen guards with `publishingRef`;
  composer's existing `retryLastPublish` re-checks the server feed before any
  re-POST.
- **No false success** — the screen dismisses only on a genuine
  `{ ok: true }` result; the handoff token is consumed so it can't be replayed.
- **Draft preserved on failure** — on `{ ok: false }` the screen stays open,
  surfaces the reason, and the composer draft (persisted to AsyncStorage) is
  intact.

## 7. Accessibility

- Publish button relabeled "Preview before publishing" with an
  `accessibilityHint`; `accessibilityState` reflects disabled/busy.
- Preview screen: header role on the badge, live region on the error text,
  `switch` role + state on the clean-preview toggle, descriptive labels on
  Edit/Publish, and busy/disabled state on the publish control.
- Renderers are the production components, so their existing VoiceOver / Dynamic
  Type / Reduce-Motion behavior is inherited unchanged.

## 8. Verification results

- **Typecheck:** `tsc --noEmit` — **clean**.
- **New unit tests:** `draftToContentModel` (8) + `previewHandoff` (4) — **12/12 pass**.
  Cover: empty-draft gating, post/reel/status shapes, local-URI passthrough,
  reel + status audio-mute policy, poll/scam→post mapping, identity author,
  handoff stash/peek/clear/uniqueness.
- **Full suite:** **298 tests pass**, 30/31 suites pass.
- **Regression check:** feed/reels/status/mediaContract suites (43 tests) pass —
  the `mediaDisplayUrl` widening did not disturb production rendering.
- **Device:** P3r7or (iPhone 16 Pro) paired/available; Metro running on 8081. The
  on-device debug build loads JS from Metro, and this feature adds **no native
  modules**, so the change reaches P3r7or on a Metro reload without a native
  rebuild.

## 9. Preview-vs-published equivalence

Equivalence is structural, not visual-diff-based: preview and feed render the
**same component** fed by the **same `normalize*` output**. The only deltas are
(a) local vs. remote media URL and (b) inert interaction handlers — neither
affects layout, media framing, badges, metadata, or audio. This is the
strongest available guarantee that "preview looks correct ⇒ published looks the
same."

## 10. Remaining risks

1. **Global playback coordinator overlap.** The preview is presented over a
   still-mounted feed. The `mediaPlaybackCoordinator` singleton grants playback
   to the last claimer (the preview on mount), but a brief overlap with a feed
   video's claim is theoretically possible. Low impact; recommend a follow-up to
   suppress feed claims while a full-screen modal is presented.
2. **Automated visual-regression / on-device screenshot diffing** was not added
   (no harness exists in-repo). Equivalence is argued structurally (§9); a
   snapshot harness is a recommended follow-up.
3. **Pre-existing unrelated test failure.** `src/utils/format.test.ts` fails to
   load (`AsyncStorage is null`) via `core/localTime.ts`. `format.ts` was
   modified by a concurrent session (not this work) and needs the standard
   AsyncStorage jest mock. Left untouched to avoid clobbering that session.

## Recommendation

**GO**, conditional on accepting the follow-ups in §10 (playback-claim
suppression under modals; optional visual-regression harness). The core mission
requirement — an accurate, production-faithful native preview before publishing
every content type, with publishing guardrails and audio policy intact — is
implemented, type-safe, and unit-tested.
