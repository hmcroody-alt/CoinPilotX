# PulseSoc Native Profile V2 — iPhone 16 Pro Installation

Date: 2026-07-15

## Device and toolchain

- Device: iPhone 16 Pro
- iOS: 18.7.3
- Connection used for build/install: USB
- Pairing: paired
- Developer Mode: enabled
- Xcode: 26.6 (17F113)
- Workspace: `mobile-native/ios/PulseSocNative.xcworkspace`
- Scheme: `PulseSocNative`
- Configuration: Release

## Side-by-side protection

- Development bundle identifier: `com.pulsesoc.nativeapp.dev`
- Development display name: `PulseSoc Native Dev`
- Production bundle identifier remains: `com.pulsesoc.app`
- The installer explicitly refuses the production bundle identifier.
- The production WebView application was not built, uninstalled, or targeted.

## Build and installation result

- Signed device build: passed.
- Signing: Apple Development, repository-configured team, automatic provisioning.
- Embedded JavaScript bundle: verified before installation.
- API environment: production PulseSoc API; local QA and fixture flags removed.
- Installation: passed.
- First automated launch: blocked because the physical iPhone was locked.
- Follow-up launch: pending unlocked USB reconnection.

## Profile V2 verification already completed in Xcode Simulator

- iPhone 16 Pro simulator build: passed on iOS 26.5.
- Xcode UI test: 1 passed, 0 failed.
- Compact-width visual inspection: passed.
- iPhone 16 Pro Max visual inspection: passed.
- Living Profile header, owner actions, module rail, Posts/Media/About tabs, and Profile customization entry were exercised.
- System and saved reduced-motion paths are implemented and audit-covered.

## Physical-device checks still requiring the unlocked phone and owner

- Launch and foreground the installed development app.
- Confirm the production and development icons remain side by side.
- Enter an existing account credential privately if authentication is required.
- Inspect the Profile V2 header, customization, keyboard, avatar/cover pickers, and network recovery.
- Exercise Message and Follow only with a controlled second account.

No UDID, certificate fingerprint, token, credential, provisioning secret, or private key is recorded in this report.
