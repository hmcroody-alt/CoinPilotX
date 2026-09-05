# Preserved iOS XCUITests

Nothing in this directory is wired into an Xcode target, and nothing here runs
in CI. It exists so that device-QA harnesses survive in git history rather than
in a stash reflog, which is one `git stash clear` away from gone.

## `PulseSocNativeCameraStudioQATests.swift`

A 271-line XCUITest that drives Camera Studio end to end on a simulator or
device — route in, capture modes (Feed/Status/Reel, Photo/Video), the camera and
microphone permission prompts, the gallery picker, snap, record, flip, and the
preview/publish controls — capturing a numbered screenshot at each step. It also
covers the sign-in entry and recovery path. It launches the parallel dev bundle
`com.pulsesoc.nativeapp.dev` with `PULSESOC_NATIVE_QA_XCTEST=1`.

Recovered on 2026-09-04 from `stash@{1}`
(`undx-v3-pre-integration-preservation-20260719`), where it was the only copy —
byte-identical in `stash@{2}` and `stash@{3}`. It was never lost so much as
stranded: it lived at `mobile-native/ios/PulseSocNativeUITests/`, and the iOS
project has since been renamed `PulseSocNative` → `PulseSoc`, so the target that
would have compiled it no longer exists under that name.

### What it would take to run again

1. Add a UI-testing target to `mobile-native/ios/PulseSoc.xcodeproj` and put this
   file in it. That is a real change to a generated project — `expo prebuild`
   regenerates `ios/`, so the target has to survive a prebuild or be re-added by
   a config plugin. Deliberately not done as part of a release closeout.
2. Re-check the accessibility labels it taps. It matches English strings
   (`"Feed"`, `"Snap"`, `"Flip"`, `"Publish"`) and the app has since been fully
   localized, so on a non-English device every one of those queries misses.
   Matching on testIDs would make it locale-independent.

Until both are done this is a reference, not a test. It is kept because the
sequence it encodes — which controls exist, in what order, and which system
prompts interrupt them — is the expensive part, and re-deriving it from scratch
costs far more than storing 271 lines.
