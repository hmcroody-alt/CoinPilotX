/**
 * PulseSoc native primitives foundation — public API.
 *
 * One owner per capability. Import from here; the ownership regression
 * guard fails if screens grow their own expo-haptics / expo-clipboard /
 * expo-document-picker call sites outside the legacy baseline.
 */
export { allCapabilities, getCapability, isCapabilityUsable, undxVisibleCapabilities } from "./capabilityRegistry";
export { checkPermission, requestPermission, supportedPermissionKeys, openSystemSettings, toPermissionState } from "./permissions";
export { haptic, hapticsEnabled, setHapticsEnabled } from "./haptics";
export type { HapticTone } from "./haptics";
export { copyToClipboard } from "./clipboard";
export type { CopyKind, CopyResult } from "./clipboard";
export { qrLink, classifyScannedPayload } from "./qr";
export { PulseQr } from "./PulseQr";
export { ScanSheet } from "./ScanSheet";
export type { QrEntityKind, ScannedPayload } from "./qr";
export { pickDocument, validateDocument } from "./documents";
export { scheduleLocalReminder, cancelLocalReminder, listLocalReminderIds, localNotificationsAllowed } from "./localNotifications";
export type { LocalReminder, ScheduleResult } from "./localNotifications";
export { startA11yMonitor, a11ySnapshot, onA11yChange, motionAllowed } from "./a11y";
export { classifyDeviceAction, undxCapabilityManifest } from "./undxDeviceActions";
export type { DeviceActionClass, DeviceActionVerdict } from "./undxDeviceActions";
export type { DocumentPolicy, PickedDocument, PickResult } from "./documents";
export type {
  CapabilityId,
  CapabilityRecord,
  ImplementationState,
  PermissionKey,
  PermissionSnapshot,
  PermissionState
} from "./types";
