# Media Reliability Foundation — Final Report

**Mission:** PulseSoc — Media Reliability Foundation / WhatsApp-level media experience (P0)
**Branch:** `release/full-sweep-20260826` @ `16d4a98a`
**Date:** 2026-09-03
**Scope agreed with requester:** *"Build the missing engine"* — add `expo-media-library` +
`expo-sharing`; build `mediaCache` (LRU/quota/integrity + per-account namespacing),
`mediaDownloader` (queue/resume/retry), `saveToGallery` with real permission handling, and
file-share; wire into `NativeMediaViewer` so every existing surface inherits it. Stages
5, 6, 7, 8, 35 plus regression tests.

Stages outside that scope are reported as **N/A (out of session scope)** rather than PASS.
Calling untouched work a pass is the "fake PASS" the brief forbids.

---

## Stage results

| # | Stage | Result | Evidence |
| --- | --- | --- | --- |
| 0 | Repo truth / baseline | **PASS** | Branch, HEAD, ahead/behind and pre-existing dirty WIP recorded before any edit. CLAUDE.md found stale (wrong branch, wrong bash mount) and not relied on. |
| 1 | Canonical media object exists | **PASS (pre-existing)** | `mediaContract.ts`; documented in `docs/media/MEDIA_CANONICAL_OBJECT.md`. |
| 2 | Shared storage/CDN access | **PASS (pre-existing)** | `mediaAccess.ts`. |
| 3–4 | Server-side transcode / thumbnails | **N/A** | Backend; out of scope. |
| 5 | Native cache: bounded, verified, scoped | **PASS** | `mediaCache.ts` + 15 tests. 256 MB / 14 d / 128 MB headroom; size-verified reads; atomic `.part` → commit. |
| 6 | Downloader: queued, resumable, bounded, idempotent | **PASS** | `mediaDownloader.ts` + 11 tests. 3 concurrent, 3 attempts, selective retry, one transfer per key. |
| 7 | Save to gallery with real permissions | **PASS** | `mediaActions.ts` + 8 tests. `writeOnly` entitlement, limited-access handled, download-before-prompt. |
| 8 | Share the real file, degrade to link | **PASS** | `shareMedia` + 4 tests. `{mode: "file" \| "link"}`. |
| 9–13 | Per-surface viewer integrations | **PASS (inherited)** | All six consumers of `NativeMediaViewer` inherit save/share without per-surface code. Not device-verified — see Stage 44. |
| 14 | Progress reporting | **PASS** | `fraction: null` when the total is unknown; never a faked `0`. |
| 15–20 | Upload-side stages | **N/A** | `MediaUploadManager` pre-existing; not in scope. |
| 21 | One shared media viewer | **PASS (pre-existing)** | `NativeMediaViewer.tsx`. |
| 22 | Shared action row, fixed order | **PASS** | `MEDIA_ACTION_ORDER = react, reply, forward, share, save`, asserted as data. |
| 23–28 | Reels / Status / Communities specifics | **N/A** | Out of scope. |
| 29 | Network transition resilience | **FAIL — cannot verify** | Resume logic is implemented and unit-tested; Wi-Fi↔cellular transition requires a device. Checklist in `MEDIA_DEVICE_QA.md`. |
| 30 | Disk-pressure safety | **PASS** | `ensureRoomFor` refuses before the first byte; test asserts **zero** network calls. |
| 31 | Cancellation | **PASS** | `cancelMediaDownload` / `pauseMediaDownload`; `AbortError` → `cancelled`. |
| 32 | Zero-byte / corrupt refusal | **PASS** | Zero-byte commit refused and file deleted; truncated file reads as a miss. |
| 33–34 | Moderation / expiry surfaces | **N/A** | Out of scope. |
| 35 | Per-account isolation + purge on sign-out | **PASS** | Scope in the path not the key; hostile id normalised (`../../etc` → `uetc`); `clearAllMediaCaches()` on sign-out; 5 tests. |
| 36 | Idempotent concurrent requests | **PASS** | 3 concurrent + 1 rotated-signature caller → 1 transfer, 1 `fileUri`, `active === 0` after. |
| 37 | No URL in user-facing copy | **PASS** | Asserted across download, save and share messages (`not.toMatch(/https?:/)`). |
| 38 | Bounded, selective retry | **PASS** | 403 not retried (1 attempt); 3 network errors → 3 attempts then `reason: "network"`. |
| 39 | Action order is data, not per-screen | **PASS** | Test asserts the exact array. |
| 40 | Accessibility of media actions | **PASS** | Roles, labels, `accessibilityState={{disabled, busy}}`, polite live-region status line. |
| 41 | Telemetry without leaking | **PASS** | `MediaEvent` has no URL-shaped field; ownership test asserts it stays that way; failure codes derived structurally. |
| 42 | No audio-path contamination | **PASS** | Audio gate passes; every protected path cross-checked against `git status` — zero overlap; ownership test forbids `setAudioModeAsync`/`AVAudioSession`/`setCategory` in `src/media/`. |
| 43 | Typecheck + full test suite | **PASS** | `tsc --noEmit` clean. Jest: **313 suites passed**, **5172 tests passed**, 1 skipped (the scratch placeholder below). |
| 44 | Physical-device QA | **FAIL — not performed** | `expo-media-library` / `expo-sharing` are native; the current dev client does not contain them. Requires `eas build --profile development`. Checklist written. |
| 45 | Cross-platform QA (iOS + Android) | **FAIL — not performed** | Same reason. |
| 46 | Memory / long-scroll soak | **FAIL — not performed** | Requires a device. Idempotency and bounded concurrency reduce the risk but do not prove it. |
| 47 | Architecture enforced by test | **PASS** | `mediaFoundationOwnership.test.ts` — single-owner assertions for downloader, media-library, sharing, cache root; no byte-fetching in `screens/` or `components/`. |
| 48 | Documentation | **PASS** | Six documents; see below. |
| 49 | Explicit staging | **PASS** | Files staged by name. No `git add -A` at any point. Pre-existing Private Office / premium-entitlements WIP untouched and unstaged. |

**Totals:** 25 PASS · 4 FAIL (all device-QA, none code) · rest N/A by agreed scope.

---

## Known issues, stated plainly

1. **Four device-QA stages fail.** Nothing in this change set has run on real hardware. The
   native modules are not in the current dev client. Until `eas build --profile development`
   is run and `docs/media/MEDIA_DEVICE_QA.md` is worked through, save-to-gallery and
   file-share are *implemented and unit-tested*, not *proven*.

2. **`npm run i18n:validate` fails on this working tree — and it is not this mission's
   failure.** 10 errors, "5 missing key families" per non-`en` locale. Proven pre-existing:
   `git archive HEAD | node scripts/validate-i18n.mjs` reports `OK — 11 locales`, 2800/2800
   each. The working tree has `en` at 2818 and the others at 2813; the five `en`-only keys
   are `discovery.crypto.marketPulse.regime.{breadth,hint,leader,title,unavailable}`, which
   belong to the uncommitted Private Office WIP that already had all 22 catalog files dirty
   before this session began. No catalog was touched here, and another mission's
   half-finished work was deliberately not "fixed" underneath it.

3. **One file must be deleted by hand.** `mobile-native/src/media/__dbg__/` was a scratch
   probe. The sandbox cannot unlink files in the mounted workspace (`rm`/`mv` →
   "Operation not permitted"), so its contents were reduced to a single documented
   `it.skip` — Jest fails a suite containing zero tests — and it is **not staged**. Please run:

   ```
   rm -rf mobile-native/src/media/__dbg__
   ```

4. **The audio gate compares committed history.** `scripts/realtime_audio_change_gate.py`
   diffs HEAD against origin/main, and this work is uncommitted, so its PASS does not by
   itself cover these edits. Every `categories[].paths` entry in
   `config/realtime-audio-protected-paths.json` was therefore cross-checked by hand against
   `git status --porcelain`: zero overlap. Re-run the gate after committing.

---

## Files changed

**Modified**

```
mobile-native/package.json                          expo-media-library ~18.2.1, expo-sharing ~14.0.8
mobile-native/package-lock.json
mobile-native/app.json                              NSPhotoLibraryAddUsageDescription + plugin config
mobile-native/src/components/NativeMediaViewer.tsx  Save-to-Photos button, file share, status line
mobile-native/src/media/mediaSessionCleanup.ts      clearAllMediaCaches on sign-out
mobile-native/src/media/mediaTelemetry.ts           structural failure-reason detection
mobile-native/src/session/auth.ts                   setMediaCacheScope in stateFor  (file was ALREADY
                                                    dirty with unrelated Private Office WIP —
                                                    NOT staged, see caveat below)
```

**Added**

```
mobile-native/src/media/mediaCache.ts
mobile-native/src/media/mediaDownloader.ts
mobile-native/src/media/mediaActions.ts
mobile-native/src/media/__tests__/mediaCache.test.ts
mobile-native/src/media/__tests__/mediaDownloader.test.ts
mobile-native/src/media/__tests__/mediaActions.test.ts
mobile-native/src/media/__tests__/mediaFoundationOwnership.test.ts
docs/media/MEDIA_RELIABILITY_ARCHITECTURE.md
docs/media/MEDIA_CANONICAL_OBJECT.md
docs/media/MEDIA_CACHE_POLICY.md
docs/media/MEDIA_SECURITY_MODEL.md
docs/media/MEDIA_FAILURE_HANDLING.md
docs/media/MEDIA_DEVICE_QA.md
MEDIA_FOUNDATION_FINAL_REPORT.md
```

**Deliberately NOT staged** — pre-existing Private Office / premium-entitlements WIP:
`bot.py`, `services/db.py`, `services/business_os/entitlements/schema.py`,
`mobile-native/src/api/premium.ts`, `ProfileHeader.tsx`, `AppNavigator.tsx`,
`ProfileScreen.tsx`, all 22 `src/i18n/catalogs/*/{core,extended}.json`, and the untracked
`services/private_office/`, `tests/private_office/`, `mobile-native/src/entitlements/`,
`PREMIUM_REGRESSION_MATRIX.md`, `PRIVATE_OFFICE_*.md`.

**Caveat on `src/session/auth.ts` — action required before this branch is useful.**
This file already carried Private Office changes when the session began, so staging it would
have swept another mission's uncommitted work into this commit. It is therefore left
unstaged, which means **the media cache-scoping call is present in the working tree but not
in the staged change set**. The single media hunk is the `setMediaCacheScope(...)` call
inside `stateFor` plus its import. Stage that hunk on its own before committing:

```
git add -p mobile-native/src/session/auth.ts     # accept only the setMediaCacheScope hunks
```

Without it the code still compiles and every test passes — `mediaCache` simply stays on the
`anon` scope — but Stage 35 account isolation is inert. Do not commit the media foundation
without it.

**Staged change set as it currently stands:** 20 files (7 modified, 13 added). Verified with
`git diff --cached --name-status`; no Private Office file appears in it. `git add -A` was
never run.

**Housekeeping note:** `.git/` contains roughly 80 quarantined `index.lock.*` / `HEAD.lock.*`
files from earlier sessions, plus stray `.git/objects/*/tmp_obj_*` files that could not be
unlinked from the sandbox. They are harmless but worth sweeping locally.

---

## Definition of Done — assessment

The brief states the mission is not complete merely because media displays and buttons
exist. Measured against that:

**Done.** One shared engine with single, test-enforced ownership. No surface re-implements
download, cache, save or share. No path can report "Saved" without a completed write. A
malformed, truncated, empty or forbidden file produces a distinguishable, non-crashing
result. Account isolation is structural and purged on sign-out. No URL reaches a log or a
user-facing string. Realtime audio untouched and asserted so.

**Not done.** The mission cannot be signed off as P0-complete until Stages 44/45/46 and 29
are executed on real iOS and Android hardware against a dev client that contains the new
native modules. Everything above is a strong static and unit-level case; none of it is
device evidence.
