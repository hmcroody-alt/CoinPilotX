/**
 * Shared native capability registry (Phase 1).
 *
 * Single source of truth for what PulseSoc can do locally. UNDX and any
 * feature tile must consult this registry rather than guessing.
 * `device_verified` flips to true only after physical-device QA — never
 * from simulator or static analysis.
 */
import { CapabilityId, CapabilityRecord } from "./types";

const RECORDS: CapabilityRecord[] = [
  { capability_id: "haptics", platform: "all", implementation_state: "IMPLEMENTED", permission_required: null, native_dependency: "expo-haptics", owner_module: "native/haptics", device_verified: false },
  { capability_id: "qr_generate", platform: "all", implementation_state: "IMPLEMENTED", permission_required: null, native_dependency: "react-native-qrcode-svg", owner_module: "native/PulseQr", device_verified: false },
  { capability_id: "qr_scan", platform: "all", implementation_state: "DEVICE_REQUIRED", permission_required: "CAMERA", native_dependency: "expo-camera", owner_module: "native/ScanSheet", device_verified: false },
  { capability_id: "deep_links", platform: "all", implementation_state: "IMPLEMENTED", permission_required: null, native_dependency: "expo-linking", owner_module: "navigation/linking", device_verified: true },
  { capability_id: "native_share", platform: "all", implementation_state: "IMPLEMENTED", permission_required: null, native_dependency: null, owner_module: "sharing/nativeShare", device_verified: true },
  { capability_id: "clipboard", platform: "all", implementation_state: "IMPLEMENTED", permission_required: null, native_dependency: "expo-clipboard", owner_module: "native/clipboard", device_verified: false },
  { capability_id: "biometrics", platform: "ios", implementation_state: "IMPLEMENTED", permission_required: null, native_dependency: "expo-local-authentication", owner_module: "session/biometricAuth", device_verified: true },
  { capability_id: "push_notifications", platform: "all", implementation_state: "IMPLEMENTED", permission_required: "NOTIFICATIONS", native_dependency: "expo-notifications", owner_module: "api/push", device_verified: true },
  { capability_id: "local_notifications", platform: "all", implementation_state: "DEVICE_REQUIRED", permission_required: "NOTIFICATIONS", native_dependency: "expo-notifications", owner_module: "native/localNotifications", device_verified: false },
  { capability_id: "contacts", platform: "all", implementation_state: "NOT_IMPLEMENTED", permission_required: "CONTACTS", native_dependency: null, owner_module: "", device_verified: false },
  { capability_id: "calendar", platform: "all", implementation_state: "NOT_IMPLEMENTED", permission_required: "CALENDAR", native_dependency: null, owner_module: "", device_verified: false },
  { capability_id: "location", platform: "all", implementation_state: "NOT_IMPLEMENTED", permission_required: "LOCATION", native_dependency: null, owner_module: "", device_verified: false },
  { capability_id: "maps", platform: "all", implementation_state: "NOT_IMPLEMENTED", permission_required: null, native_dependency: null, owner_module: "", device_verified: false },
  { capability_id: "speech_to_text", platform: "ios", implementation_state: "PARTIAL", permission_required: "MICROPHONE", native_dependency: null, owner_module: "system-keyboard-dictation", device_verified: true },
  { capability_id: "text_to_speech", platform: "all", implementation_state: "NOT_IMPLEMENTED", permission_required: null, native_dependency: null, owner_module: "", device_verified: false },
  { capability_id: "document_picker", platform: "all", implementation_state: "IMPLEMENTED", permission_required: null, native_dependency: "expo-document-picker", owner_module: "native/documents", device_verified: true },
  { capability_id: "document_scanner", platform: "all", implementation_state: "NOT_IMPLEMENTED", permission_required: "CAMERA", native_dependency: null, owner_module: "", device_verified: false },
  { capability_id: "camera_utility", platform: "all", implementation_state: "IMPLEMENTED", permission_required: "CAMERA", native_dependency: "expo-camera", owner_module: "screens/CameraStudioScreen", device_verified: true },
  { capability_id: "device_motion", platform: "all", implementation_state: "PROTECTED", permission_required: null, native_dependency: "expo-sensors", owner_module: "spatial/motion", device_verified: true },
  { capability_id: "network_state", platform: "all", implementation_state: "PARTIAL", permission_required: null, native_dependency: null, owner_module: "", device_verified: false },
  { capability_id: "battery_state", platform: "all", implementation_state: "IMPLEMENTED", permission_required: null, native_dependency: "expo-battery", owner_module: "live/liveStudioReadiness", device_verified: true },
  { capability_id: "secure_storage", platform: "all", implementation_state: "IMPLEMENTED", permission_required: null, native_dependency: "expo-secure-store", owner_module: "session/sessionStore", device_verified: true },
  { capability_id: "offline_drafts", platform: "all", implementation_state: "PARTIAL", permission_required: null, native_dependency: "@react-native-async-storage/async-storage", owner_module: "marketplace/listingDraftStore", device_verified: true },
  { capability_id: "pending_send_queue", platform: "all", implementation_state: "NOT_IMPLEMENTED", permission_required: null, native_dependency: null, owner_module: "", device_verified: false },
  { capability_id: "image_manipulation", platform: "all", implementation_state: "NOT_IMPLEMENTED", permission_required: null, native_dependency: null, owner_module: "", device_verified: false },
  { capability_id: "video_thumbnails", platform: "all", implementation_state: "NOT_IMPLEMENTED", permission_required: null, native_dependency: null, owner_module: "", device_verified: false },
  { capability_id: "hashing", platform: "all", implementation_state: "NOT_IMPLEMENTED", permission_required: null, native_dependency: null, owner_module: "", device_verified: false },
  { capability_id: "compression", platform: "all", implementation_state: "PARTIAL", permission_required: null, native_dependency: "expo-image-picker", owner_module: "media/MediaUploadManager", device_verified: true },
  { capability_id: "audio_recording", platform: "all", implementation_state: "PROTECTED", permission_required: "MICROPHONE", native_dependency: "expo-av", owner_module: "core/realtimeAudioEngine", device_verified: true }
];

const INDEX = new Map<CapabilityId, CapabilityRecord>(RECORDS.map((r) => [r.capability_id, r]));

export function allCapabilities(): readonly CapabilityRecord[] {
  return RECORDS;
}

export function getCapability(id: CapabilityId): CapabilityRecord {
  const record = INDEX.get(id);
  if (!record) throw new Error(`Unknown capability: ${id}`);
  return record;
}

/** True only for capabilities a feature surface may present as usable. */
export function isCapabilityUsable(id: CapabilityId): boolean {
  const s = getCapability(id).implementation_state;
  return s === "IMPLEMENTED" || s === "PARTIAL" || s === "DEVICE_REQUIRED";
}

/** Capabilities UNDX may reference. PROTECTED ones are excluded outright. */
export function undxVisibleCapabilities(): readonly CapabilityRecord[] {
  return RECORDS.filter((r) => r.implementation_state !== "PROTECTED" && isCapabilityUsable(r.capability_id));
}
