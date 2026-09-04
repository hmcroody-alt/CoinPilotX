# NATIVE PRIMITIVES FOUNDATION MAP
Mission: Native Primitives Super Mission (P1) — Phase 0 recon
Date: 2026-09-03 · Branch: codex/emergency-live-audio-recovery · App: mobile-native (Expo SDK 54, RN 0.81.5)

States: PRESENT / PARTIAL / MISSING / DUPLICATE / PROTECTED

| Capability | State | Evidence | Owner today | Notes |
|---|---|---|---|---|
| Haptics | PARTIAL + DUPLICATE | `expo-haptics` imported directly in 20 files (EmojiPicker, PostCard, GlobalNavigation, calls/callSignalMedia, spatial/motion, auth screens…) | none — every call site self-owns | Needs one `core/haptics.ts` owner honoring accessibility/system settings |
| Contacts | MISSING | no `expo-contacts` dependency, zero references | — | Find Friends requires new native dep + EAS build |
| QR generation | PARTIAL | `react-native-qrcode-svg` used only in `screens/PulseShareScreen.tsx` (canonical link QR, testID pulse-share-qr) | PulseShareScreen | Extract shared `<PulseQr>`; already encodes canonical pulsesoc links ✓ |
| QR/barcode scanning | MISSING | `expo-camera` installed but no scanner surface anywhere | — | Build on existing expo-camera (`CameraView` barcode API), no new dep |
| Deep links | PRESENT | `navigation/linking.ts`: prefixes `pulsesoc://` + `https://pulsesoc.com`, ~71 mapped paths, registry-driven settings links | navigation/linking.ts | Canonical foundation exists; extend for new surfaces only |
| Universal links | PRESENT | `associatedDomains: ["applinks:pulsesoc.com"]` in app.json | app config | |
| Share sheet | PRESENT | `sharing/nativeShare.ts` canonical owner (typed PulseShareKind × canonical URL); `expo-sharing` only in `media/mediaActions.ts` (file share, media foundation) | sharing/nativeShare.ts + media foundation | Mission Phase 7 already satisfied structurally; audit stray Share.share call sites |
| Clipboard | PARTIAL + DUPLICATE | `expo-clipboard` called directly in 3 screens (AdsReports, PulseShare, ProgressCenter) | none | Needs shared `core/clipboard.ts` with "Copied" confirmation |
| Biometrics | PRESENT | `session/biometricAuth.ts` (capability probe, faceId/touchId kinds, SecureStore-wrapped credential, server session authoritative) + Settings toggle | session/biometricAuth.ts | Reuse for Private Office gate; do NOT create a second wrapper |
| Push notifications | PRESENT | `expo-notifications` + `api/push.ts` (FCM/APNs token flow), routing in `navigation/notificationRouting.ts` | api/push.ts + navigation | |
| Local notifications | PARTIAL | expo-notifications installed; no `scheduleNotificationAsync` usage found | — | Add local scheduling channel distinct from server push |
| Calendar | MISSING | no `expo-calendar` | — | New native dep required (EAS build) |
| Location | MISSING | no `expo-location`, zero geolocation refs | — | New native dep required |
| Maps | MISSING | no map library | — | Defer: needs product surface + provider decision (prefer Apple Maps via `MapKit`/expo-maps) |
| Speech-to-text | MISSING | no speech recognition refs | — | iOS system keyboard dictation already works in every TextInput today (zero-code path); dedicated STT needs new dep |
| Text-to-speech | MISSING | no `expo-speech` | — | expo-speech is JS-API over native AVSpeechSynthesizer; pure-Expo dep |
| Camera utility | PRESENT (fragmented) | `expo-camera` in CameraStudioScreen + LiveStudioScreen; `expo-image-picker` for profile/chat/status | CameraStudioScreen | Live/call camera pipeline PROTECTED — do not touch |
| Document picker | PARTIAL + DUPLICATE | `expo-document-picker` direct in ChatScreen, MusicScreen, SellerListingComposer + api/sellerApplication, api/verification | none | Centralize validation (MIME/size/extension) |
| Document scanner | MISSING | none | — | Build capture+crop on camera utility; OCR = PROVIDER_REQUIRED (no fake OCR) |
| Device motion | PRESENT + PROTECTED | `expo-sensors` via `spatial/motion/motionAvailability.ts`; Reels motion machine protected | spatial/motion | Preserve Reels behavior |
| Network state | PARTIAL | ad-hoc online checks scattered (ReelsScreen, MessengerScreen, Signup…), no NetInfo dep | none | Add shared connectivity service (expo-network or NetInfo) |
| Battery state | PRESENT | `expo-battery` in LiveStudio readiness, Home, background atmospheres | live/liveStudioReadiness.ts | Extend to low-power prefetch deferral |
| Secure storage | PRESENT | `expo-secure-store` centralized in `session/sessionStore.ts` (+ api/push.ts token) | session/sessionStore.ts | Good owner; route new secrets through it |
| SQLite / offline DB | MISSING | no expo-sqlite; AsyncStorage used for drafts/caches | — | AsyncStorage stores exist; decide SQLite only if real need |
| Offline drafts | PARTIAL | `marketplace/listingDraftStore.ts` (durable listing drafts), create/ composer handoff; no post/comment/messenger drafts | marketplace store only | Generalize draft persistence |
| Pending send queue | MISSING | no offline queue | — | |
| Image manipulation | MISSING | no expo-image-manipulator; CameraStudio uses picker quality flags only | — | Pure-Expo dep, no config plugin |
| Video thumbnails | MISSING | no expo-video-thumbnails | — | Pure-Expo dep |
| System icons | PRESENT | `@expo/vector-icons` | — | Brand icons stay custom |
| Localization | PRESENT | i18n engine, 11 locales, `i18n/format.ts` (date/number), CI-gated | i18n/ | Currency formatting partially screen-local — audit |
| Accessibility | PARTIAL | `__tests__/accessibilityBaseline.test.ts`, emoji a11y labels; no systematic Dynamic Type/Reduce Motion audit outside spatial | — | Reduce Motion honored in spatial/motion |
| Audio recording | PROTECTED | realtime audio policy; expo-av allowlist capped at 6 files | core/realtimeAudio* | DO NOT TOUCH without approval |
| Background tasks | MISSING | no expo-task-manager / background-fetch | — | Only bounded local work if added |
| Hashing/checksums | MISSING | no expo-crypto | — | Pure-Expo dep |
| Compression | PARTIAL | picker-level image quality; no shared original/optimized/thumbnail policy | — | Fold into image manipulation owner |

## Duplicate-ownership hotspots (Phase 46 guard targets)
1. Haptics — 20 direct import sites, no owner.
2. Clipboard — 3 direct sites.
3. Document picker — 5 direct sites.
4. Network checks — per-screen ad hoc logic.

## Protected (untouchable without explicit approval)
- Realtime audio/live/call paths (`config/realtime-audio-protected-paths.json`), expo-av allowlist, LiveKit/Agora pipeline, Reels motion machine.

## New-native-dependency ledger (requires EAS build; sandbox cannot pod-install)
- expo-contacts (Find Friends), expo-calendar, expo-location, expo-speech (TTS — JS-only API, still a new package), expo-image-manipulator, expo-video-thumbnails, expo-crypto, expo-network. All are free Expo SDK modules — zero recurring API cost.
- **BLOCKED in this session (2026-09-03):** the sandbox npm registry policy returns 403 for all of the above packages, so they cannot be added here. Owner action: run `npx expo install expo-contacts expo-calendar expo-location expo-speech expo-crypto expo-network expo-image-manipulator expo-video-thumbnails` on a dev machine, add the iOS purpose strings (NSContactsUsageDescription, NSCalendarsFullAccessUsageDescription, NSLocationWhenInUseUsageDescription, NSSpeechRecognitionUsageDescription) to app.json, and cut a new EAS dev build. Until then these capabilities stay NOT_IMPLEMENTED in the registry and no UI claims them.

## Zero-new-dependency wins available now
- Haptics owner, clipboard service, shared QR component, QR scanning (expo-camera CameraView barcode), deep-link extension, document-picker centralization, battery-aware prefetch policy, Face ID Private Office gate (reuse biometricAuth), local notifications (expo-notifications scheduling), offline drafts generalization (AsyncStorage), share audit.
