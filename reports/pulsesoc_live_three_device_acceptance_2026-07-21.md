# PulseSoc Native Three-Device Live Production Acceptance Test

Date: 2026-07-21
Branch: `release/undx-nexus-core-v4`
Build under test: `9680a9f5f31f2528ab42aa0621f09971fc58bd36`
Mission result: **NOT OBSERVED**

## Pre-Flight

Required repository state was confirmed:

- Branch: `release/undx-nexus-core-v4`
- HEAD: `9680a9f5f31f2528ab42aa0621f09971fc58bd36`
- Unrelated dirty files were present before this mission and were preserved:
  - `mobile-native/app.json`
  - `mobile-native/ios/PulseSocNative.xcodeproj/project.pbxproj`
  - `mobile-native/src/assets/brand/pulsesoc-logo-mark.png` deleted in worktree
  - `reports/security/security_findings_ledger_a0cc15cd.md`
  - `mobile-native/assets/`

No code changes were made during this acceptance-test mission.

## Device Discovery

`xcrun devicectl list devices` showed only one available physical iPhone:

| Role | Device | Identifier | Availability | Model | iOS |
| --- | --- | --- | --- | --- | --- |
| Available physical device | P3r7or | `F45E640F-6D02-514E-877C-B764E8D6818F` | available (paired) | iPhone 16 Pro (`iPhone17,1`) | 18.7.3 |
| Unavailable device | iPad (3) | `FA5CAAF9-9F8B-56F2-ADAC-6A05ABCE2B18` | unavailable | iPad (A16) | not observed |
| Unavailable device | iPhone | `2415AD7C-6069-5206-91F5-EB92EB7C4D11` | unavailable | iPhone17,1 | not observed |
| Unavailable device | iPhone33 | `43ADE5BD-647D-5456-A757-32CB12F1F381` | unavailable | iPhone 14 | not observed |

The required physical matrix needs three simultaneously available devices:

- Device A: Host
- Device B: Guest
- Device C: Viewer

Only Device A/B/C candidate hardware count available from this Mac: `1`.

## Installed App State

`xcrun devicectl device info apps --device F45E640F-6D02-514E-877C-B764E8D6818F` confirmed:

| App | Bundle ID | Version | Bundle Version |
| --- | --- | --- | --- |
| PulseSoc | `com.pulsesoc.nativeapp` | 1.0 | 1 |
| PulseSoc Native Dev | `com.pulsesoc.nativeapp.dev` | 1.0 | 1 |

The build from commit `9680a9f5f31f2528ab42aa0621f09971fc58bd36` was previously built, installed, and launched on `P3r7or` as the dev sidecar bundle `com.pulsesoc.nativeapp.dev`.

## Account Matrix

The required three-account matrix was not executable because only one physical device was available.

| Role | Account | Device | Result |
| --- | --- | --- | --- |
| Host | not assigned | not available | NOT OBSERVED |
| Guest | not assigned | not available | NOT OBSERVED |
| Viewer | not assigned | not available | NOT OBSERVED |

## Network Matrix

| Check | Result |
| --- | --- |
| Device A network type | wired-connected iPhone visible to Xcode; app network path not interactively tested |
| Device B network type | NOT OBSERVED |
| Device C network type | NOT OBSERVED |
| Wi-Fi to cellular transition | NOT OBSERVED |
| Cellular to Wi-Fi transition | NOT OBSERVED |
| Temporary network loss | NOT OBSERVED |

## Audio Route Matrix

| Route | Result |
| --- | --- |
| Built-in speaker | NOT OBSERVED |
| Wired headphones | NOT OBSERVED |
| Bluetooth headphones / AirPods | NOT OBSERVED |
| Bluetooth disconnect during Live | NOT OBSERVED |
| Bluetooth reconnect during Live | NOT OBSERVED |

## Acceptance Tests

| Test | Required Observation | Result |
| --- | --- | --- |
| Host audio to viewer | Viewer hears host after host starts Live | NOT OBSERVED |
| Host video to viewer | Viewer sees host video | NOT OBSERVED |
| Host mute/unmute | Viewer audio stops and resumes | NOT OBSERVED |
| Guest request | Guest sends request and host receives it | NOT OBSERVED |
| Guest approval | Host approves and guest receives approval | NOT OBSERVED |
| Co-host token | Guest obtains co-host token | NOT OBSERVED |
| Guest publication | Guest publishes mic and camera | NOT OBSERVED |
| Publish-complete route | Server confirms guest publish complete | NOT OBSERVED |
| Host hears guest | Host hears guest after approval | NOT OBSERVED |
| Guest hears host | Guest hears host after approval | NOT OBSERVED |
| Viewer hears both | Viewer hears host and guest | NOT OBSERVED |
| Viewer sees both | Viewer sees host and guest tiles | NOT OBSERVED |
| Guest leave/rejoin | Guest leaves, disappears, rejoins successfully | NOT OBSERVED |
| End Live cleanup | Camera/mic indicators turn off and room closes | NOT OBSERVED |

## Background and Interruption Matrix

| Scenario | Result |
| --- | --- |
| Host backgrounds and returns | NOT OBSERVED |
| Guest backgrounds and returns | NOT OBSERVED |
| Viewer backgrounds and returns | NOT OBSERVED |
| Incoming phone-call interruption | NOT OBSERVED |
| Siri interruption | NOT OBSERVED |
| Control Center interaction | NOT OBSERVED |
| Screen lock/unlock | NOT OBSERVED |

## Failures Found

No runtime Live failure was observed because the required three-device test could not be started.

Failure classification: `NOT_OBSERVED_DEVICE_MATRIX_UNAVAILABLE`

## Fixes Applied

None in this mission.

The code-side Live repair remains the pushed commit:

- `9680a9f5f31f2528ab42aa0621f09971fc58bd36`

## Retest Results

No retest was possible without three simultaneously available physical devices and three separate authenticated accounts.

## Final Result

**NOT OBSERVED**

Live cannot be marked production-accepted yet. The code-side repair is complete, but the required physical proof is still missing:

- Viewer hears host
- Guest can request/join
- Guest publishes mic and camera
- Host hears guest
- Guest hears host
- Viewer hears host and guest
- Viewer sees host and guest
- Guest leave/rejoin works
- Audio route changes recover
- Live ends cleanly

## Next Required Action

Connect or provide three physical devices at the same time, each signed into a separate test account:

1. Host iPhone
2. Guest iPhone
3. Viewer iPhone

Then rerun the full acceptance matrix from this report against commit `9680a9f5f31f2528ab42aa0621f09971fc58bd36` or a newer explicitly selected build.
