# PulseSoc Native — CallKit + PushKit VoIP Integration (Stage 2)

- **Date:** 2026-07-20
- **Branch:** `release/undx-nexus-core-v4`
- **Goal:** Ring the iOS system call UI (CallKit) when a PulseSoc call arrives while the app is backgrounded or killed, delivered via a PushKit VoIP push.
- **Why it's needed:** Stage 1's in-app ringer (`callSignalMedia.ts` + `IncomingCallLayer.tsx`) only runs while the app is foregrounded and polling `getActiveCalls` every 4.2 s (`IncomingCallLayer.tsx:39`, gated on `appState.current === "active"`). Backgrounded/killed devices never ring today.

## What already landed in JS (this session, safe / flag-OFF)

| File | Purpose |
|---|---|
| `mobile-native/src/api/config.ts` | `NATIVE_CALLKIT_ENABLED` flag — `EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED === "1"`, **default OFF**. |
| `mobile-native/src/api/calls.ts` | `registerVoipPushToken(token, payload)` → `POST /api/calls/voip-token`; `unregisterVoipPushToken(token)` → `POST /api/calls/voip-token/revoke`. |
| `mobile-native/src/calls/callKitBridge.ts` | All orchestration: UUID↔callId map, flag gating, answer→`acceptCall`, reject→`declineCall`, hang-up→`endCall`, VoIP-token→backend. Native calls sit behind an injectable `NativeCallKitProvider` so the app **builds and unit-tests with zero new pods**. |
| `mobile-native/src/calls/__tests__/callKitBridge.test.ts` | No-op safety when disabled + full handler-routing coverage with an injected fake provider. |

Nothing in the current binary changes behavior: with the flag OFF and no provider registered, `isNativeCallKitEnabled()` returns `false` and every entry point is a no-op.

## Remaining work — NATIVE (needs a rebuild, no Apple account required)

1. **Add pods** to `package.json` and `pod install`:
   - `react-native-callkeep` (CallKit wrapper)
   - `react-native-voip-push-notification` (PushKit)
2. **`ios/PulseSocNative/Info.plist`** — add `voip` to `UIBackgroundModes` (currently only `audio`):
   ```xml
   <key>UIBackgroundModes</key>
   <array>
     <string>audio</string>
     <string>voip</string>
   </array>
   ```
3. **AppDelegate** — register the PushKit delegate and, on VoIP push, call `RNCallKeep.reportNewIncomingCall(...)` **synchronously in the push handler** (iOS 13+ kills the app if a VoIP push does not report a call to CallKit). Bridge the CallKit answer/end actions and the `didUpdatePushCredentials` token to JS via the callkeep/voip-push native events.
4. **Real provider** — author `src/calls/callKitNativeProvider.ts` implementing `NativeCallKitProvider` against `react-native-callkeep` + `react-native-voip-push-notification`, then `setNativeCallKitProvider(...)` + `initNativeCallKit({ onAnswered, onEnded })` at app startup (guarded by `isNativeCallKitEnabled()`), and call `reportIncomingCallKit(...)` / `markCallKitConnected(...)` / `endCallKitCall(...)` from the call lifecycle. Provider→CallKeep method mapping:
   - `setup` → `RNCallKeep.setup({ ios: { appName: "PulseSoc" } })`
   - `displayIncomingCall(uuid, i)` → `RNCallKeep.displayIncomingCall(uuid, i.handle, i.displayName, "generic", i.hasVideo)`
   - `setCallConnected(uuid)` → `RNCallKeep.setCurrentCallActive(uuid)`
   - `endCall(uuid)` → `RNCallKeep.endCall(uuid)`
   - `registerVoipToken()` → `VoipPushNotification.registerVoipToken()`
   - `onAnswer` → `RNCallKeep.addEventListener("answerCall", ...)`
   - `onEnd` → `RNCallKeep.addEventListener("endCall", ...)`
   - `onVoipToken` → `VoipPushNotification.addEventListener("register", ...)`

## Remaining work — ACCOUNT-SIDE (blocked on the COINPLOTXAI INC. org transfer / Mission 1)

1. **VoIP Services certificate** created under the account that owns the app's APNs — this must be the **COINPLOTXAI INC.** team after the App Transfer, so create it **after** the transfer to avoid re-issuing. VoIP certs/keys do **not** transfer with an app.
2. **Production push entitlement** — `aps-environment` is currently `development` (`PulseSocNative.entitlements`). TestFlight/App Store need `production` (see risk **R2** in `app_store_rejection_risks.json`). VoIP push shares this APNs environment.
3. **Backend** must implement `POST /api/calls/voip-token` (+ `/revoke`) to store the per-device VoIP token, and send a **VoIP-type** APNs push (topic `com.pulsesoc.nativeapp.voip`) carrying the call id + caller identity at ring time.

## Verification (two devices, after the above)

1. Kill the app on device B. Place a call from device A → device B's screen rings via the **iOS system call UI** even though PulseSoc is not running.
2. Answer from the CallKit UI → app launches into the connected call; decline → backend records a decline.
3. Confirm no "app terminated for not reporting a call after VoIP push" crashes.

## Sequencing note

Do the **App Transfer to COINPLOTXAI INC. first** (Mission 1), then create the VoIP cert + production push entitlement under the new team, then flip `EXPO_PUBLIC_NATIVE_CALLKIT_ENABLED=1` and ship. The JS scaffolding above is already in place and inert until then.
