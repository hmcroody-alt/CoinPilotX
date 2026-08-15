# PulseSoc — Hybrid Spatial Motion Console: Final Engineering Report

Date: 2026-08-14
Branch: `feature/spatial-console` (base `codex/insight-image-pipeline` @ `6e67e408`)
Commits: `74a32e2c` (implementation) + `a8e7f57e` (audio-gate declaration)

## What was built

**Touch spatial layer** (earlier stages, verified this run): edge-to-edge
horizontal spatial pager for the Home Feed and Reels (virtualization ±1,
per-category position state, all post types and actions preserved), immersive
bottom-nav behavior (hides only after a committed transition settles, ~220ms
hide / ~180ms reveal), Messages visual refinements (vertical inbox untouched),
and the Spatial Create Console: + morphs to ×, dimmed context (existing
background color at 0.86 opacity — no new colors), fanned touch-only carousel
with exactly six modes in mission order — Photo, Video, Create a Signal,
Camera, Create Reel ("Record or upload clips"), Go Live at the far end. Go
Live shows a warning and requires explicit confirmation, then opens only the
existing `LiveStudio` setup route. A unit contract pins that no mode can ever
reach `NativeLiveHost`, `LiveDetail`, or `Call`, and no params contain
autopublish.

**Motion layer** (this run): a deterministic, pure 11-state tilt state machine
(Unavailable, Disabled, Calibrating, Neutral, Preview Left/Right, Armed,
Committed, Returning-to-Neutral, Cooldown, Suspended) driven by a DeviceMotion
pipeline: 80ms sampling, low-pass filter (α 0.25), dead zone, 12-sample
neutral-angle calibration persisted per user, instability detection from
accelerometer magnitude. Slight tilt produces a parallax preview
(transform/opacity only); a sustained tilt commits a page turn with a haptic
tick through the **same settled-index pipeline as swipe** — there is no second
navigation path. Return-to-neutral is required before the next commit, with a
cooldown after each. Touch always cancels tilt. Tilt is structurally
suspended in Messages, in the Create Console, while typing/keyboard is up,
while overlays are open, when backgrounded, when the screen reader or reduce
motion is on, and when the device lies flat or moves unstably.

**Consent and onboarding**: motion never activates silently. Settings default
to `swipe-only` + `onboarded: false`; the sanitizer rejects any stored or
patched value that isn't an explicit valid choice. A 4-step onboarding flow
(intro with animated demo, local-only privacy statement, mode choice,
calibration explanation) is the only path to enabling motion; "Keep swipe
only" is a first-class answer. Settings → Accessibility → Spatial Motion
offers mode (Swipe only / +Parallax / +Tilt), sensitivity (low/medium/high),
scope (Feed/Reels/both), tilt haptics, recalibrate, and replay tutorial —
controls other than setup appear only after onboarding.

**Privacy** (§20): raw motion data is processed in memory on-device and never
persisted or transmitted. Only high-level preferences (mode, sensitivity,
scope, haptics, calibrated baseline angle, onboarded) are stored.

## Rollback

Nine `EXPO_PUBLIC_*` flags, **all defaulting OFF** — an unshipped env var
means byte-identical legacy behavior. Layered: sub-flags require the console
master; `spatialMotionEnabled` requires the master; both tilt flags require
motion. Motion adds a consent layer on top: flags ship capability, onboarding
ships behavior. No legacy code deleted, no migrations. Full table in
`docs/spatial-console-rollback.md`.

## Quality gates (all run in-sandbox against `74a32e2c`)

| Gate | Result |
|---|---|
| `npx tsc --noEmit` | clean |
| `npm run i18n:validate` | OK — 11 locales |
| Full jest (chunked, complete coverage of all 221 listed suites) | **221 suites / 3,764 tests passing** |
| Spatial suites (flags, pager math, color baseline, state machine, settings consent, create-console contract) | 7 suites / 41 tests passing |
| Color regression (`colorBaseline.test.ts`) | baseline pinned, no new colors |
| `realtime_audio_change_gate.py --base 6e67e408 --head HEAD` | **Declaration accepted** |
| `npm run test:realtime-audio-critical` | 191 tests passing |
| `npm run test:realtime-audio` | 310 tests passing |
| `npm run test:realtime-audio-architecture` (native + backend) | 22 + 19 tests passing |
| Protection suite (`run_protection_suite.py`) | all checks pass except `test_agora_token_generation.py` — see caveats |

Two pre-existing test mocks needed extending (`HomeScreen.actions`,
`ReelsScreen.actions`): they partially mocked `BottomNavVisibility` and didn't
provide `useIsFocused`, which the screens now read (inertly, flags off). Mock
updates only; no production behavior change.

## Audio / livestream protection

No audio-session, LiveKit/Agora, RTC, or publication path was touched. The
gate fired solely on the dependency watch for `package.json` (the one-line
`expo-sensors` addition); a full declaration addendum was appended to
`reports/realtime_audio_change_declaration.md` and the gate now accepts it.
The `expo-av` allowlist count is unchanged.

## Honest caveats — what was NOT verified

- **No device or simulator run.** This sandbox has no iOS/Android toolchain.
  No claim is made about on-device behavior, tilt feel, haptics, or sensor
  latency. Device QA is owed before any flag is enabled.
- **`expo-sensors` is declared but not installed.** The sandbox's npm registry
  access is blocked (403), so `package-lock.json` is not yet updated. Run
  `npm install` in `mobile-native/` on a networked machine; the lockfile diff
  must show only the expo-sensors subtree. Until then the motion code degrades
  safely to "unavailable" via its dynamic require.
- **Backend Agora token tests could not run as specified.** pytest and the
  `agora_token_builder` pip package are absent in the sandbox and not
  installable. The unittest fallback ran 13 tests: 10 passed, 3 failed purely
  on the missing package. The diff contains zero backend changes
  (`git diff 6e67e408..HEAD` outside `mobile-native/` and `docs/` is empty),
  so these are environmental. CI must re-run them green.
- **Static checks don't replace device QA** for livestream, push, checkout, or
  uploads (per project policy) — none of those paths were modified, but the
  policy stands.

## Enabling in production (suggested order)

1. `npm install` (lockfile), then a development build.
2. Device QA with `EXPO_PUBLIC_SPATIAL_CONSOLE=1` only (touch layer).
3. Add `EXPO_PUBLIC_SPATIAL_MOTION=1` + `EXPO_PUBLIC_TILT_PARALLAX=1`
   (parallax before tilt).
4. `EXPO_PUBLIC_TILT_NAVIGATION=1` last, after tilt-feel QA on real hardware.

Rollback at any step is unsetting the respective var.
