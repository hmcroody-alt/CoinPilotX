# Native Capability Cost Register (Phase 39)

Every capability in `mobile-native/src/native/capabilityRegistry.ts`, with its recurring cost.
Rule applied: local OS/Expo primitive → mature open source → owned logic → only then a paid API.

| Capability | Provider | Recurring cost | Paid-API alternative avoided |
|---|---|---|---|
| haptics | expo-haptics (local) | $0 | — |
| qr_generate | react-native-qrcode-svg (OSS, local render) | $0 | hosted QR APIs (goqr, qrserver) |
| qr_scan | expo-camera barcode (local) | $0 | commercial scanner SDKs (Scandit et al.) |
| deep_links | expo-linking + universal links (local) | $0 | Branch/AppsFlyer link services |
| native_share | RN Share (OS sheet) | $0 | — |
| clipboard | expo-clipboard (local) | $0 | — |
| biometrics | expo-local-authentication (local) | $0 | 3rd-party auth SDKs |
| push_notifications | FCM/APNs (existing infra) | $0 marginal | paid push vendors (OneSignal paid tiers) |
| local_notifications | expo-notifications scheduling (local) | $0 | — |
| contacts | expo-contacts (not yet installed) | $0 when added | contact-enrichment APIs |
| calendar | expo-calendar (not yet installed) | $0 when added | scheduling SaaS |
| location / maps | expo-location + Apple/OS maps (not yet installed) | $0 when added | Google Maps API metering |
| speech_to_text | iOS SFSpeech via expo-speech-recognition (not installed) | $0 when added | Whisper/Deepgram APIs |
| text_to_speech | expo-speech / AVSpeechSynthesizer (not installed) | $0 when added | ElevenLabs/Polly |
| document_picker | expo-document-picker (local) | $0 | — |
| document_scanner | VisionKit via camera (DEVICE_REQUIRED) | $0 | scanning SDKs |
| camera_utility | expo-camera / expo-image-picker (local) | $0 | — |
| network_state | expo-network (not installed; fetch-probe fallback) | $0 | — |
| battery_state | expo-battery (local) | $0 | — |
| secure_storage | expo-secure-store (local Keychain) | $0 | — |
| offline_drafts / pending_send_queue | AsyncStorage (local) | $0 | sync SaaS |
| image_manipulation | expo-image-manipulator (not installed) | $0 when added | image-processing APIs |
| video_thumbnails | expo-video-thumbnails (not installed) | $0 when added | Mux thumbnail metering |
| hashing | expo-crypto (not installed) | $0 when added | — |
| compression | existing media foundation (local) | $0 | — |
| audio_recording / device_motion | PROTECTED — existing owners | $0 | — |

## Verdict
No new paid API was introduced by this mission. Every capability is a local OS/Expo
primitive or mature OSS. Recurring cost added: **$0**.

## Privacy & security review (Phases 40-41)
- QR/scanned payloads: validated by `classifyScannedPayload` — javascript:/data:/file:/vbscript:/blob: rejected; only pulsesoc.com (exact or subdomain, URL-parsed) auto-routes; external URLs need explicit confirmation; host-suffix spoofing tested (`pulsesoc.com.evil.io` → external).
- QR generation encodes canonical PulseSoc links only — never vendor links.
- Permissions: single orchestrator; `check*` never prompts; `request*` documented user-action-only; BLOCKED routes to system settings. Microphone deliberately not requestable (protected realtime-audio owner).
- Clipboard: write-only owner; the app never reads the clipboard.
- Camera: active only while ScanSheet is visible; no background capture.
- UNDX: protected capabilities invisible; external writes and sensitive device access require explicit user confirmation (`classifyDeviceAction`).
- Local reminders carry only pulsesoc:// deep links, no PII in payloads.
