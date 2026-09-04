# PulseSoc Native Primitives Foundation

Consolidated documentation for the native primitives mission (registry, permissions,
haptics, QR, clipboard, documents, local notifications, a11y, UNDX device actions).
Companion docs: `NATIVE_PRIMITIVES_FOUNDATION_MAP.md` (recon + dependency ledger),
`NATIVE_CAPABILITY_COST_REGISTER.md` (cost + privacy review).

## Ownership model
One owner per native capability. Import from `src/native` (barrel `src/native/index.ts`).
The regression guard (`src/native/__tests__/nativeOwnershipGuard.test.ts`) fails CI when a
NEW direct import of expo-haptics / expo-clipboard / expo-document-picker /
expo-local-authentication / expo-secure-store appears outside its owner, or when a second
permissions orchestrator appears. Legacy call sites are baselined; never add to a baseline.

| Area | Owner |
|---|---|
| Capability truth | `native/capabilityRegistry.ts` |
| Permissions | `native/permissions.ts` |
| Haptics | `native/haptics.ts` (preference-wired from settings store) |
| QR render | `native/PulseQr.tsx` |
| QR scan | `native/ScanSheet.tsx` (modal, not a nav screen) |
| Payload safety | `native/qr.ts` |
| Clipboard (write-only) | `native/clipboard.ts` |
| Documents | `native/documents.ts` |
| Local reminders | `native/localNotifications.ts` |
| A11y snapshot | `native/a11y.ts` |
| UNDX device actions | `native/undxDeviceActions.ts` |
| Deep links | `navigation/linking.ts` (pre-existing) |
| Share | `sharing/nativeShare.ts` (pre-existing) |
| Biometrics | `session/biometricAuth.ts` (pre-existing) |
| Secure storage | `session/sessionStore.ts` (pre-existing) |
| Realtime audio / motion | PROTECTED — existing owners, untouchable |

## Permission policy
States: NOT_REQUESTED → GRANTED / LIMITED / DENIED / BLOCKED.
`checkPermission` never prompts. `requestPermission` may prompt and must only run from a
user-initiated action. BLOCKED → offer `openSystemSettings()`. Microphone is not
requestable through this layer — it belongs to the protected realtime-audio engine.

## Haptics
Tones: light / medium / success / warning / error / selection. Fire-and-forget, never
throws, disabled on web and when `accessibility.hapticFeedback` is off (mirrored into the
owner by `settings/store.tsx`).

## QR policy
Generated QR codes contain only canonical `https://pulsesoc.com/...` links (`qrLink`).
Scanned payloads run through `classifyScannedPayload`: PulseSoc links may auto-route;
external URLs require explicit confirmation; dangerous schemes are rejected outright.

## UNDX device actions
UNDX sees only `undxVisibleCapabilities()` (PROTECTED excluded). Every proposed action is
classified: READ_ONLY_LOCAL (silent) / REVERSIBLE_LOCAL_WRITE (logged) / EXTERNAL_WRITE and
SENSITIVE_DEVICE_ACCESS (explicit user confirmation). Unknown ids are rejected.

## Device verification matrix
`device_verified` in the registry is honest: it is true only for capabilities proven on a
physical device by their existing owners (deep links, share, biometrics, push). Everything
new in this mission is `device_verified: false` until owner QA on iPhone:
haptics tones, ScanSheet camera flow (grant/deny/blocked), PulseQr scanability from Camera
app, clipboard confirmation, document picker policy, scheduled local reminder delivery + tap
deep link.

## Blocked expansion (needs owner action)
The sandbox npm registry returns 403 for new packages. To unlock contacts, calendar,
location, STT/TTS, image manipulation, video thumbnails, hashing, network:
`npx expo install` the modules listed in the foundation map ledger, add the iOS purpose
strings, cut a new EAS dev build, then flip registry states and build owners in `src/native`.
