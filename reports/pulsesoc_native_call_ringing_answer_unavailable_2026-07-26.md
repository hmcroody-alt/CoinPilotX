# PulseSoc Native Call Ringing, Answer, and Unavailable Flow

Date: 2026-07-26
Branch: `release/undx-nexus-core-v4`

## Result

Native call lifecycle repair implemented. No TestFlight build, EAS build, Transporter upload, App Store Connect submission, or tester assignment was performed.

## Root Cause

- The outgoing native call screen started the server call and immediately joined the LiveKit media room. The backend `join-token` route transitions a ringing call toward `connecting`, so caller ringback could stop before the recipient answered.
- The caller side had no single explicit state for pre-answer terminal outcomes such as missed, expired, declined, busy, or failed. That could leave the UI feeling stuck on a waiting state instead of telling the caller the person was unavailable.
- The CallKit bridge existed, but the foreground incoming-call layer did not report incoming calls to it when a native provider is available.
- Existing audits still contained stale checks for the previously rejected active-call mini popup and internal helper names.

## Repair

- Added deterministic call lifecycle predicates in `callToneLifecycle.ts`:
  - ringback only while outgoing status is `ringing` and media is not connected.
  - media connect only after `accepted`, `connecting`, `connected`, `active`, or `reconnecting`.
  - unavailable prompt only for outgoing calls that ended before media connected.
- Updated `CallScreen` so outgoing calls do not join the LiveKit room until the recipient answers or backend state advances out of ringing.
- Increased status polling cadence while a call is ringing so declined, missed, expired, failed, or accepted states reach the caller quickly.
- Added one terminal unavailable message for pre-answer failures: `The person you are calling is not available.` or a more specific equivalent for declined, busy, or failed.
- Stopped ring tones and cleaned CallKit state on terminal call transitions.
- Reported incoming foreground calls to the existing CallKit bridge when a provider is installed and enabled.
- Updated stale native call audits so they continue enforcing no active-call mini popup without blocking CallKit cleanup helpers.

## Phone-Grade Coverage

- Caller ringback: code-path verified.
- Caller pre-answer unavailable state: code-path verified.
- Caller does not hear incoming ringtone: code-path verified by tone predicate.
- Recipient foreground full-screen incoming UI: existing native layer preserved.
- Recipient foreground ringtone/vibration: existing tone lifecycle preserved.
- Answer/decline server mutations: existing `acceptCall` and `declineCall` APIs preserved.
- Dedicated call screen connection: preserved through `CallScreen` and `useNativeCallRoom`.
- System CallKit interface: bridge wiring improved, but production hardware behavior still requires a real native provider and VoIP push configuration.

## Physical-Device-Only Checks

- Locked-device incoming system call UI.
- App-killed incoming call UI.
- Real push delivery timing.
- CallKit/PushKit provider behavior.
- Real microphone and camera capture.
- Bluetooth, speaker, earpiece routing.
- Vibration behavior under device accessibility and silent settings.
- Two-device audio/video media handshake.

## Verification

- `npm run --prefix mobile-native typecheck`: passed.
- Focused native call Jest: `4` suites passed, `33` tests passed.
- Full native Jest: `85` suites passed, `1356` tests passed.
- `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`: `16/16` checks passed.
- `scripts/pulsesoc_call_system_full_functionality_audit.py`: passed.
- `scripts/pulsesoc_real_call_experience_audit.py`: passed.
- `scripts/pulsesoc_native_calls_audit.py`: passed after stale mini-popup assertion was tightened.
- `scripts/pulsesoc_native_call_p0_behavior_audit.py`: passed.
- `scripts/pulsesoc_native_incoming_calls_audit.py`: passed.
- `scripts/pulsesoc_fullscreen_incoming_call_audit.py`: passed.
- `scripts/pulsesoc_native_incoming_calls_qa_audit.py`: passed.
- `scripts/pulsesoc_native_calls_qa_audit.py`: passed.
- `git diff --check`: passed.
- Xcode iPhone 17 Pro Max Simulator build: passed with `mobile-native/ios/PulseSocNative.xcworkspace`, scheme `PulseSocNative`.
- Xcode iPhone 17 Pro Max Simulator install/launch: passed for bundle `com.pulsesoc.nativeapp.dev`.
- Simulator evidence: `reports/screenshots/native-call-ringing-2026-07-26/iphone17promax-launch.png`.

## Simulator and Device Notes

- The simulator proof is a build/install/launch proof. It does not prove real ringtone/vibration, microphone capture, speaker routing, Bluetooth, VoIP push, CallKit system UI, or audible two-party media.
- The launch landed on the development launcher because no Metro development server was running during the final screenshot capture.
- Xcode listed the available physical iPhones as offline, so physical-device call behavior was not verified in this mission.

## Release Judgment

- Foreground native call lifecycle: improved and code-path verified.
- Phone-grade locked/background/killed behavior: not release-complete until CallKit/VoIP native provider and real physical-device QA are verified.
