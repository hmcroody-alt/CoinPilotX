/**
 * Native primitives foundation — shared types.
 *
 * One registry describes what PulseSoc can do locally; one orchestrator
 * owns permission state. No screen may independently guess availability
 * or spray its own permission prompts.
 */

export type CapabilityId =
  | "haptics"
  | "qr_generate"
  | "qr_scan"
  | "deep_links"
  | "native_share"
  | "clipboard"
  | "biometrics"
  | "push_notifications"
  | "local_notifications"
  | "contacts"
  | "calendar"
  | "location"
  | "maps"
  | "speech_to_text"
  | "text_to_speech"
  | "document_picker"
  | "document_scanner"
  | "camera_utility"
  | "device_motion"
  | "network_state"
  | "battery_state"
  | "secure_storage"
  | "offline_drafts"
  | "pending_send_queue"
  | "image_manipulation"
  | "video_thumbnails"
  | "hashing"
  | "compression"
  | "audio_recording";

export type ImplementationState =
  | "IMPLEMENTED"
  | "PARTIAL"
  | "NOT_IMPLEMENTED"
  | "DEVICE_REQUIRED"
  | "UNSUPPORTED"
  | "PROTECTED";

export type PermissionKey =
  | "CAMERA"
  | "MICROPHONE"
  | "PHOTOS_READ"
  | "PHOTOS_ADD"
  | "CONTACTS"
  | "LOCATION"
  | "CALENDAR"
  | "NOTIFICATIONS";

export type PermissionState =
  | "NOT_REQUESTED"
  | "GRANTED"
  | "LIMITED"
  | "DENIED"
  | "BLOCKED";

export type PermissionSnapshot = {
  state: PermissionState;
  /** false once the OS will no longer show the prompt (state BLOCKED). */
  canAskAgain: boolean;
};

export type CapabilityRecord = {
  capability_id: CapabilityId;
  platform: "ios" | "android" | "all";
  implementation_state: ImplementationState;
  permission_required: PermissionKey | null;
  native_dependency: string | null;
  owner_module: string;
  device_verified: boolean;
};
