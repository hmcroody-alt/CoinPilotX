# PulseSoc Native iPhone 16 Pro Installation — 2026-07-16

## Outcome

The current native PulseSoc build was compiled, signed, installed, and launched on the cable-connected iPhone 16 Pro without replacing the production WebView app.

## Device and toolchain

- Device: iPhone 16 Pro
- iOS: 18.7.3
- Connection: USB
- Pairing: paired and available
- Developer Mode: enabled sufficiently for signed development installation and launch
- Xcode visibility: available physical destination
- Xcode: 26.6 (build 17F113)
- Workspace: `mobile-native/ios/PulseSocNative.xcworkspace`
- Scheme: `PulseSocNative`
- Configuration: Release source build with a command-line development identity override
- Destination identifier: intentionally omitted

## Production protection

- Production app: `PulseSoc`
- Production bundle identifier: `com.pulsesoc.app`
- Production version/build: 1.0.0 (27)
- Development app: `PulseSoc Native Dev`
- Development bundle identifier: `com.pulsesoc.nativeapp.dev`
- Development version/build: 0.1.0 (1)
- Side-by-side installation after update: PASS
- Production app preserved after update: PASS

Only `com.pulsesoc.nativeapp.dev` was built, installed, and launched. The production `com.pulsesoc.app` identity was queried after installation and remained present as a separate app.

## Build, signing, installation, and launch

- Build result: PASS (`** BUILD SUCCEEDED **`)
- Signing style: automatic Apple Development signing through the repository's configured team
- Signing team: `87ZC69AGSR`
- Artifact bundle identifier: `com.pulsesoc.nativeapp.dev`
- Artifact display name: `PulseSoc Native Dev`
- Embedded JavaScript bundle: present, 7,071,922 bytes
- Strict recursive code-signature verification: PASS
- Installation result: PASS
- Launch result: PASS
- Process remained alive five seconds after launch: PASS
- Immediate crash observed: NO

## Verification gates

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`: PASS
- `npm run --prefix mobile-native typecheck`: PASS
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`: PASS (17/17)
- Device build: PASS
- Artifact signature: PASS
- Install and launch: PASS
- Post-install side-by-side identity query: PASS
- `git diff --check`: PASS

## Runtime environment

- API base URL embedded for the device build: `https://pulsesoc.com`
- Localhost or `127.0.0.1` production leakage: not used by the installation command
- Authentication reachability from the handset: USER SMOKE TEST REQUIRED
- Status API reachability from the handset: USER SMOKE TEST REQUIRED
- Media API reachability from the handset: USER SMOKE TEST REQUIRED
- Realtime reachability from the handset: USER SMOKE TEST REQUIRED

The successful process launch proves the signed native runtime starts on the handset. It does not, by itself, prove an authenticated production request or media/realtime exchange.

## Physical-device smoke status

| Check | Result |
| --- | --- |
| Cold native process launch | PASS |
| No immediate crash | PASS |
| Production WebView app retained | PASS |
| Development app retained separately | PASS |
| Login or cached-session restoration | USER SMOKE TEST REQUIRED |
| Home and bottom navigation | USER SMOKE TEST REQUIRED |
| Status rail | USER SMOKE TEST REQUIRED |
| Status viewer | USER SMOKE TEST REQUIRED |
| Status creator and keyboard | USER SMOKE TEST REQUIRED |
| Camera and photo-library permission | USER SMOKE TEST REQUIRED |
| Video and music selection | USER SMOKE TEST REQUIRED |
| Upload initiation | USER SMOKE TEST REQUIRED |
| Radio playback and audio interruptions | USER SMOKE TEST REQUIRED |
| Background and foreground restoration | USER SMOKE TEST REQUIRED |
| Dynamic Island and safe-area visual review | USER SMOKE TEST REQUIRED |

No personal media was selected or uploaded during the automated installation.

## Known limitations

- CoreDevice can install, launch, query installed identities, and confirm the process, but it cannot certify the handset's visible UI or complete permission prompts without user interaction.
- A production track was not played, and phone-call, Low Power Mode, VoiceOver, camera, library, microphone, upload, and realtime behavior remain hardware interaction gates.
- No secrets, device identifier, certificates, provisioning profiles, passwords, tokens, or private keys are included in this report.

## Next exact Status device test

Open `PulseSoc Native Dev`, sign in with an existing PulseSoc account, open Home, then exercise Status in this order: rail → viewer → hold-to-pause → close → creator → keyboard → privacy → photo picker → camera permission → video picker → music selector → save draft. Use safe test content and stop before upload unless the user deliberately chooses to publish.

## Freeze decision

Physical-device testing is now enabled, but Status should remain active until the user completes the visible interaction and permission matrix above. Installation success alone is not evidence that Status is ready to freeze.
