# PulseSoc Native Owner iPhone Test Setup

Date: 2026-07-06

Scope: prepare the installed parallel native iPhone app so Roody can personally log in, explore PulseSoc Native, and give feedback while development continues.

No production WebView route, production app identity, production auth policy, or backend business rule was changed.

## Device

- Device: iPhone 16 Pro
- iOS: 18.7.3
- UDID: `00008140-000E2D9A2EE8801C`
- Native bundle ID: `com.pulsesoc.nativeapp`
- Installed app found by `devicectl`: `PulseSoc Native   com.pulsesoc.nativeapp   0.1.0`

## Temporary Owner QA Account

Initial setup created a temporary account through the existing production mobile auth API:

- Register endpoint: `POST https://pulsesoc.com/api/mobile/auth/register`
- Login verification endpoint: `POST https://pulsesoc.com/api/mobile/auth/login`
- Auth weakening: none
- Production WebView changes: none
- Committed credential material: none

The account is QA-marked through its username/display name because the current user schema inspected in this workspace does not expose a dedicated `is_test_account` or equivalent production-safe user flag.

- Original username: `roody_native_qa_20260706`
- Display name: `Roody Native QA`
- Production user ID returned by mobile auth: `28`
- Initial login verification: successful through the existing mobile auth API
- Security update: the original account was revoked after the temporary password was exposed outside a secure handoff path.

Do not use the original username or prior temporary password.

Replacement credential status:

- Replacement username: `roody_native_qa_20260706_r3`
- Replacement password storage: macOS Keychain only
- Keychain service: `PulseSocNativeOwnerQA`
- Keychain account: `roody_native_qa_20260706_r3`
- Password retrieval command for the local owner machine only:

```bash
security find-generic-password -s PulseSocNativeOwnerQA -a roody_native_qa_20260706_r3 -w
```

The replacement password is not stored in this report, source code, config, or Git history. Production mobile auth POST requests began timing out during replacement registration/login confirmation, so replacement login remains pending confirmation until `/api/mobile/auth/login` and `/api/mobile/auth/register` respond normally again.

## Install / Launch Result

Command run:

```bash
npx expo run:ios --device 00008140-000E2D9A2EE8801C
```

Result:

- CocoaPods installed successfully.
- Xcode auto-signed with team `87ZC69AGSR`.
- Native build succeeded.
- App installed to the physical iPhone.
- Metro started and bundled `index.ts` for iOS.
- `devicectl` confirmed `com.pulsesoc.nativeapp` is installed.

Warnings observed:

- `devicectl` JSON version warning for physical Apple devices.
- Metal toolchain Swift search-path warnings.
- `expo-av` deprecation warning for SDK 54.

No build, signing, install, or bundle failure was observed.

## Roody Owner Test Steps

1. Open `PulseSoc Native` on the iPhone.
2. Log in with:
   - Username: `roody_native_qa_20260706_r3`
   - Temporary password: retrieve from macOS Keychain only after production mobile auth POSTs recover.
3. Walk through each major native surface:
   - Home Feed
   - Messenger
   - Profile
   - Reels
   - Status
   - Marketplace
   - Seller Store
   - Activity Inbox
   - Notifications
   - Settings
   - Camera Studio
   - Calls screen
   - Creator
   - Growth
   - Premium
   - Intelligence / Alerts
4. Record feedback with iPhone screen recording or screenshots.
5. Note:
   - route or screen name
   - what you tapped
   - what happened
   - expected behavior
   - screenshot/video timestamp
6. Send bugs, suggestions, and visual quality notes back to Codex for triage.

## What Roody Can Test Now

- App install/launch and signed-out native route behavior on the physical iPhone.
- Signed-in native navigation and primary tabs after replacement login is confirmed.
- Read paths for Feed, Profile, Reels, Status, Marketplace, Activity Inbox, Notifications, Settings, Premium, Creator, Growth, and Intelligence where backend permissions allow.
- Seller Store and Marketplace management gates as a real QA user.
- Camera Studio screen, permissions, gallery/camera prompts, and visible handoff behavior on a physical iPhone.
- Calls screen and incoming/call route shells.

## Still Unstable / Release Blockers

- Physical camera/microphone capture, gallery upload, video compression, retry/cancel, and publish IDs still need manual owner/device evidence.
- Push notifications, APNs/FCM token behavior, lock-screen behavior, and notification tap routing still require provider/device QA.
- LiveKit two-device calls, Bluetooth/speaker routing, background audio, and lock-screen calls remain release blockers.
- Android physical-device QA remains incomplete.
- Production mobile auth register/login POST requests timed out during credential rotation; health and session endpoints stayed healthy.
- Some creator, seller, premium, commerce, and intelligence actions may show server-side eligibility or safe web/provider fallback because the backend remains authoritative.

## What Codex Should Keep Building

Do not block ongoing development on owner feedback collection unless a critical, security, data-loss, production-breaking, or future-development-blocking issue appears.

Highest-value next action: confirm or expose the authenticated server event cursor endpoint for the native polling sync layer.

Reason: the native app now has broad feature coverage and owner testing can begin, but the event sync layer still needs a production-confirmed cursor feed so Activity, Orders, Seller Store, Marketplace, Messenger, Calls, Safety, Verification, Alerts, and Intelligence stay coherent without relying on full refresh fallback.

## Verification

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`: passed
- `npm run --prefix mobile-native typecheck`: passed
- `cd mobile-native && EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`: passed, 17/17
- Original production mobile auth register/login: passed before revocation
- Original QA account revocation through `/api/account/delete`: passed
- Replacement credential generation and Keychain storage: passed
- Replacement registration/login confirmation: blocked by production mobile auth POST timeout
- Physical iPhone install/build/bundle: passed
- `devicectl` installed-app confirmation: passed

## Migration Estimate

- Native foundation/parity coverage: 84%
- System consistency confidence: 75%
- Release QA confidence: 63%
