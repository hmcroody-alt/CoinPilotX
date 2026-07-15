# PulseSoc Native Authentication, Session Refresh, and Reels Recovery

Date: 2026-07-14

## Outcome

The native shared-session implementation is repaired and the reported blank/nonworking device launch was reproduced as an artifact-packaging error: the latest physical install had been overwritten with a Debug build that expected Metro on the Mac. A clean Xcode Simulator reset now proves the real signed-out login and recovery surfaces, and a Release-mode development artifact with an embedded JavaScript bundle has replaced the broken physical development copy. Existing-account login, force-quit restoration, and Reels refresh evidence still require the owner to enter credentials privately.

## Root cause

- The native client persisted only the Flask session cookie and discarded the rotating refresh credential returned by production login.
- Concurrent 401 responses could each start a refresh, creating a refresh-rotation race.
- Refresh failures were collapsed into an empty result and an original 401 could be mislabeled as an expired session even when recovery failed temporarily.
- Reels classified every 403 as authentication expiration and its `Try again` action did not open real Sign In or preserve the intended Reels destination.
- The final physical install used Debug configuration. Its native binary launched, but Debug resolves JavaScript through Metro and contained no standalone `main.jsbundle`; when the phone could not reach the Mac-only Metro session, the app presented no usable login UI.

## Implemented repair

- Added one versioned native session envelope containing canonical user ID, access token metadata, and rotating refresh token metadata.
- Stored the envelope in iOS Keychain with `AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY` and separate development/release Keychain services.
- Preserved the existing production cookie session because current production APIs remain cookie-authoritative; the refresh token restores that canonical server session.
- Serialized refresh with one module-level in-flight promise.
- Rotated the secure envelope before discarding the old credential and rejected any canonical user-ID mismatch.
- Replayed the failed request once after successful refresh.
- Preserved Keychain state for network and 5xx recovery failures; cleared credentials only on authoritative 401/403 refresh rejection.
- Routed invalid sessions to the real Auth navigator and preserved `/pulse/reels` for post-login restoration.
- Separated restricted-account 403 handling from expired-session handling in Reels.
- Restored the physical QA install to Release configuration with command-line development bundle/display-name overrides, production API configuration, all simulator fixture flags removed, and an embedded JavaScript bundle.
- Added a guarded installation script that refuses the production App Store bundle identifier, verifies the embedded bundle and development identity before installation, and targets only `com.pulsesoc.nativeapp.dev`.
- Updated Xcode UI automation to target the actual development bundle and cover clean login, password/email recovery, and return-to-login navigation.

## Canonical identity and duplication

- Backend audit existing user ID: controlled non-production fixture, canonical ID preserved.
- Duplicate user created: NO.
- Duplicate profile created: NO native profile store or identity database exists.
- Production authentication contract replaced: NO.
- WebView/native parallel-session policy changed: NO.

## Session lifecycle verification

| Check | Result | Evidence |
|---|---|---|
| Existing email/username production route | PASSED (code and backend fixture) | `/api/mobile/auth/login` |
| Canonical production `user_id` | PASSED (backend fixture) | `scripts/pulsesoc_native_auth_incident_audit.py` |
| Keychain envelope | PASSED (code audit) | `mobile-native/src/session/sessionStore.ts` |
| Development/release Keychain isolation | PASSED (code audit) | distinct `keychainService` values |
| Refresh rotation | PASSED (backend fixture) | old and new refresh credentials differ; values not logged |
| Single-flight refresh | PASSED (code audit) | one shared refresh promise |
| Request replay | PASSED (code audit) | exactly one replay with refresh disabled |
| Temporary failure preserves session | PASSED (code audit) | typed temporary outcome; no credential clear |
| Invalid refresh opens Sign In | PASSED (code path) | shared invalidation handler |
| Post-auth Reels restoration | PASSED (code path) | `/pulse/reels` pending target |
| Logout / logout all | PASSED (code path) | server logout plus local credential clear |
| Clean signed-out login UI | PASSED (Xcode UI test) | identifier, password, Sign in, recovery, and signup controls |
| Password/email recovery navigation | PASSED (Xcode UI test) | recovery screen and return-to-login |
| Standalone launch without Metro | PASSED (Xcode Simulator) | Release artifact with embedded `main.jsbundle`, Metro stopped |
| Physical existing-user login | BLOCKED | owner must enter private credentials on device |
| Physical relaunch restoration | BLOCKED | follows physical login |
| Physical refresh/Reels | BLOCKED | follows physical login |

## Reels recovery

- Simulator Reels runtime: PASSED using the development QA environment; this is not counted as production-account proof.
- 401/session-expired state: routes to real Sign In and preserves Reels destination.
- 403/restricted state: remains authenticated and shows restricted-access recovery.
- Temporary service or network failure: does not clear Keychain state or claim that the session ended.
- Cached/offline Reels behavior remains intact.

## Build and device

- Xcode: 26.6 (17F113).
- Workspace: `mobile-native/ios/PulseSocNative.xcworkspace`.
- Scheme: `PulseSocNative`.
- Simulator configurations: Debug for UI automation and Release for standalone/Metro-off proof.
- Simulator: iPhone 17 Pro, iOS 26.5; the installed runtime has no iPhone 16 Pro simulator profile.
- Simulator build: PASSED.
- Simulator launch/runtime: PASSED with Metro development server.
- Physical device: iPhone 16 Pro, iOS 18.7.3, paired and available.
- Signed device build: PASSED in Release configuration with development-only identity overrides.
- Development bundle: `com.pulsesoc.nativeapp.dev`.
- Development display name: `PulseSoc Native Dev`.
- Installation: PASSED.
- Corrected standalone artifact installation: PASSED; it replaced only `com.pulsesoc.nativeapp.dev`.
- Corrected standalone artifact automated launch: PASSED; the embedded bundle, Metro-off simulator launch, and physical process presence are verified.
- Production app preserved: YES, `com.pulsesoc.app` remains installed.
- Side-by-side installation and runtime presence: YES; the production and development app processes were both observed without exposing device identifiers.

## Verification

- `npm ci --prefix mobile-native --no-audit --no-fund --progress=false`: PASSED.
- `npm run --prefix mobile-native typecheck`: PASSED.
- `EXPO_DOCTOR_ENABLE_DIRECTORY_CHECK=0 npx expo-doctor --verbose`: PASSED, 17/17.
- Existing-account authentication audit: PASSED.
- Canonical-ID and duplicate-user audit: PASSED.
- Refresh-rotation audit: PASSED.
- Shared-session, single-flight, replay, bootstrap, Keychain, deep-link, and release-boundary audit: PASSED.
- Xcode authentication UI test: PASSED, 1 test and 0 failures.
- Standalone Xcode Simulator build and Metro-off login launch: PASSED.
- iOS simulator build: PASSED.
- Signed iPhone 16 Pro build: PASSED.
- `git diff --check`: PASSED.

## Safe evidence

- `reports/screenshots/native-auth-session-reels-recovery-2026-07-14/simulator-reels-runtime.png`
- `reports/screenshots/native-auth-simulator-repair-2026-07-14/standalone-login-metro-off.png`
- `reports/screenshots/native-auth-simulator-repair-2026-07-14/xctest-login.png`
- `reports/screenshots/native-auth-simulator-repair-2026-07-14/xctest-recovery.png`
- `reports/screenshots/native-auth-simulator-repair-2026-07-14/xctest-return-login.png`

The initial simulator bundle-loader diagnostic was retained only during diagnosis and is not acceptance evidence. No passwords, tokens, cookies, device identifiers, private messages, or personal account fields are included.

## Remaining blocker and exact next test

On the now-running **PulseSoc Native Dev** app, confirm the existing-account login screen is visible, sign in with an existing production PulseSoc account, open **Reels**, force-close the dev app, reopen it, and confirm the same account and Reels restore without a second login. The owner must enter credentials privately; Codex will not request or store them.

Home stabilization, Messenger New Chat, and Profile V2 remain queued behind this physical authentication gate.
