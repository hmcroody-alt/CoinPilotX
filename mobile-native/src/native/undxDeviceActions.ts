/**
 * UNDX device-action layer (Phases 37-38).
 *
 * UNDX may only see capabilities via `undxVisibleCapabilities()` (protected
 * capabilities are invisible) and every proposed device action is classified
 * before execution:
 *
 *  - READ_ONLY_LOCAL          → allowed silently
 *  - REVERSIBLE_LOCAL_WRITE   → allowed, logged
 *  - EXTERNAL_WRITE           → requires explicit user confirmation
 *  - SENSITIVE_DEVICE_ACCESS  → requires explicit user confirmation +
 *                               user-initiated permission flow
 *
 * Unknown capabilities are always rejected.
 */
import { isCapabilityUsable, undxVisibleCapabilities } from "./capabilityRegistry";
import { CapabilityId } from "./types";

export type DeviceActionClass =
  | "READ_ONLY_LOCAL"
  | "REVERSIBLE_LOCAL_WRITE"
  | "EXTERNAL_WRITE"
  | "SENSITIVE_DEVICE_ACCESS";

const CLASSIFICATION: Partial<Record<CapabilityId, DeviceActionClass>> = {
  haptics: "READ_ONLY_LOCAL",
  qr_generate: "READ_ONLY_LOCAL",
  deep_links: "READ_ONLY_LOCAL",
  network_state: "READ_ONLY_LOCAL",
  battery_state: "READ_ONLY_LOCAL",
  clipboard: "REVERSIBLE_LOCAL_WRITE",
  local_notifications: "REVERSIBLE_LOCAL_WRITE",
  offline_drafts: "REVERSIBLE_LOCAL_WRITE",
  pending_send_queue: "REVERSIBLE_LOCAL_WRITE",
  native_share: "EXTERNAL_WRITE",
  push_notifications: "EXTERNAL_WRITE",
  qr_scan: "SENSITIVE_DEVICE_ACCESS",
  camera_utility: "SENSITIVE_DEVICE_ACCESS",
  document_picker: "SENSITIVE_DEVICE_ACCESS",
  document_scanner: "SENSITIVE_DEVICE_ACCESS",
  biometrics: "SENSITIVE_DEVICE_ACCESS",
  contacts: "SENSITIVE_DEVICE_ACCESS",
  calendar: "SENSITIVE_DEVICE_ACCESS",
  location: "SENSITIVE_DEVICE_ACCESS",
  speech_to_text: "SENSITIVE_DEVICE_ACCESS",
  text_to_speech: "READ_ONLY_LOCAL",
  secure_storage: "SENSITIVE_DEVICE_ACCESS"
};

export type DeviceActionVerdict =
  | { allowed: true; actionClass: DeviceActionClass; requiresUserConfirmation: boolean }
  | { allowed: false; reason: "unknown_capability" | "protected_or_hidden" | "not_usable" | "unclassified" };

/** Classify an UNDX-proposed device action against the capability registry. */
export function classifyDeviceAction(capabilityId: string): DeviceActionVerdict {
  const visible = undxVisibleCapabilities().some((r) => r.capability_id === capabilityId);
  const known = capabilityId in CLASSIFICATION;
  if (!known) {
    // Either a made-up id or a capability UNDX must never touch (protected
    // realtime audio / motion are deliberately absent from CLASSIFICATION).
    return { allowed: false, reason: "unknown_capability" };
  }
  if (!visible) {
    return isCapabilityUsable(capabilityId as CapabilityId)
      ? { allowed: false, reason: "protected_or_hidden" }
      : { allowed: false, reason: "not_usable" };
  }
  const actionClass = CLASSIFICATION[capabilityId as CapabilityId];
  if (!actionClass) return { allowed: false, reason: "unclassified" };
  return {
    allowed: true,
    actionClass,
    requiresUserConfirmation: actionClass === "EXTERNAL_WRITE" || actionClass === "SENSITIVE_DEVICE_ACCESS"
  };
}

/** Capability ids UNDX is allowed to know about, for prompt construction. */
export function undxCapabilityManifest(): { capability_id: CapabilityId; actionClass: DeviceActionClass }[] {
  return undxVisibleCapabilities()
    .map((r) => ({ capability_id: r.capability_id, actionClass: CLASSIFICATION[r.capability_id] }))
    .filter((e): e is { capability_id: CapabilityId; actionClass: DeviceActionClass } => Boolean(e.actionClass));
}
