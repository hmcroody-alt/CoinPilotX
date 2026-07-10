# PulseSoc LogiNexus Shared Motion System

Date: 2026-07-10

## Scope

This milestone starts the shared LogiNexus motion foundation after the global navigation foundation.

It does not redesign screens, add product features, modify backend contracts, or change WebView behavior.

## Implemented

- Added `mobile-native/src/theme/logiNexusMotion.ts` as the first shared native motion utility.
- Centralized:
  - motion durations from existing `logiNexus.motion` tokens
  - standard easing
  - ambient pulse sequencing
  - reduced-motion preference detection
- Migrated `UserDashboardScreen` energy-ring motion from a hardcoded loop to the shared ambient pulse helper.
- Migrated `IncomingCallLayer` incoming-call and floating-call pulse motion from hardcoded loops to the shared ambient pulse helper.
- Added reduced-motion fallbacks so ambient pulses settle into a static readable state instead of looping.

## Design Intent

Motion should support the feeling of a living system without becoming noisy. The foundation favors short, reusable, native-driver animations and explicit reduced-motion handling.

## Verification

- Static audit target: `scripts/pulsesoc_logi_nexus_motion_audit.py`
- Required shared checks:
  - shared motion utility exists
  - ambient pulse helper exists
  - reduced-motion hook exists
  - dashboard and incoming-call surfaces consume the shared utility

## Remaining Motion Work

- Add shared helpers for reveal, press, success, failure, list loading, drawer transition, bottom-sheet transition, and page transition.
- Migrate remaining one-off pressed styles and future subsystem animations into shared primitives during each subsystem pass.
- Verify reduced-motion behavior in Xcode iPhone Simulator accessibility settings and physical iPhone release QA.
