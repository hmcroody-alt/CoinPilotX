/**
 * Permissions orchestrator (Phase 2) — the one shared permission layer.
 *
 * Rules:
 *  - `check*` never triggers an OS prompt.
 *  - `request*` may prompt and must only be called from a user-initiated
 *    action handler (button press, feature entry) — never on mount.
 *  - BLOCKED means the OS will not prompt again; callers should route the
 *    user to system settings via `openSystemSettings()`.
 *
 * Microphone is intentionally absent from the requestable set: microphone
 * acquisition is owned by the protected realtime-audio engine.
 */
import { Camera } from "expo-camera";
import * as ImagePicker from "expo-image-picker";
import * as Notifications from "expo-notifications";
import { Linking } from "react-native";
import { PermissionKey, PermissionSnapshot, PermissionState } from "./types";

type RawResponse = { status: string; granted: boolean; canAskAgain: boolean; accessPrivileges?: string };

export function toPermissionState(raw: RawResponse): PermissionState {
  if (raw.granted) return raw.accessPrivileges === "limited" ? "LIMITED" : "GRANTED";
  if (raw.status === "undetermined") return "NOT_REQUESTED";
  return raw.canAskAgain ? "DENIED" : "BLOCKED";
}

function snapshot(raw: RawResponse): PermissionSnapshot {
  return { state: toPermissionState(raw), canAskAgain: raw.canAskAgain };
}

type Handler = {
  check: () => Promise<PermissionSnapshot>;
  request: () => Promise<PermissionSnapshot>;
};

const HANDLERS: Partial<Record<PermissionKey, Handler>> = {
  CAMERA: {
    check: async () => snapshot(await Camera.getCameraPermissionsAsync()),
    request: async () => snapshot(await Camera.requestCameraPermissionsAsync())
  },
  PHOTOS_READ: {
    check: async () => snapshot(await ImagePicker.getMediaLibraryPermissionsAsync()),
    request: async () => snapshot(await ImagePicker.requestMediaLibraryPermissionsAsync())
  },
  // PHOTOS_ADD is deliberately absent: photo-library writes and their
  // permission flow are owned by media/mediaActions (media foundation).
  NOTIFICATIONS: {
    check: async () => {
      const r = await Notifications.getPermissionsAsync();
      return snapshot({ status: r.status, granted: r.granted, canAskAgain: r.canAskAgain });
    },
    request: async () => {
      const r = await Notifications.requestPermissionsAsync({
        ios: { allowAlert: true, allowBadge: true, allowSound: true }
      });
      return snapshot({ status: r.status, granted: r.granted, canAskAgain: r.canAskAgain });
    }
  }
};

const UNSUPPORTED: PermissionSnapshot = { state: "NOT_REQUESTED", canAskAgain: false };

/** Passive check — never prompts. */
export async function checkPermission(key: PermissionKey): Promise<PermissionSnapshot> {
  const handler = HANDLERS[key];
  if (!handler) return UNSUPPORTED;
  try {
    return await handler.check();
  } catch {
    return UNSUPPORTED;
  }
}

/**
 * Prompt-capable request. Call only from a user-initiated action.
 * Returns the post-request snapshot; a BLOCKED result means the caller
 * should offer `openSystemSettings()` instead of retrying.
 */
export async function requestPermission(key: PermissionKey): Promise<PermissionSnapshot> {
  const handler = HANDLERS[key];
  if (!handler) return UNSUPPORTED;
  const current = await checkPermission(key);
  if (current.state === "GRANTED" || current.state === "LIMITED" || current.state === "BLOCKED") {
    return current;
  }
  try {
    return await handler.request();
  } catch {
    return UNSUPPORTED;
  }
}

/** Keys this build can actually resolve (contacts/calendar/location need new native deps). */
export function supportedPermissionKeys(): PermissionKey[] {
  return Object.keys(HANDLERS) as PermissionKey[];
}

export function openSystemSettings(): Promise<void> {
  return Linking.openSettings();
}
