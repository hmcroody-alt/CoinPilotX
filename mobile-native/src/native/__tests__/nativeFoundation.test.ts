/**
 * Native primitives foundation — registry, permission mapping, haptics
 * preference, clipboard confirmation, QR payload safety, document policy.
 */
import { allCapabilities, getCapability, isCapabilityUsable, undxVisibleCapabilities } from "../capabilityRegistry";
import { toPermissionState } from "../permissions";
import { haptic, hapticsEnabled, setHapticsEnabled } from "../haptics";
import { classifyScannedPayload, qrLink } from "../qr";
import { validateDocument } from "../documents";
import { listLocalReminderIds, scheduleLocalReminder } from "../localNotifications";
import { classifyDeviceAction, undxCapabilityManifest } from "../undxDeviceActions";

jest.mock("expo-haptics", () => ({
  impactAsync: jest.fn(() => Promise.resolve()),
  notificationAsync: jest.fn(() => Promise.resolve()),
  selectionAsync: jest.fn(() => Promise.resolve()),
  ImpactFeedbackStyle: { Light: "light", Medium: "medium" },
  NotificationFeedbackType: { Success: "success", Warning: "warning", Error: "error" }
}));
jest.mock("expo-camera", () => ({}));
jest.mock("expo-image-picker", () => ({}));
jest.mock("expo-media-library", () => ({}));
jest.mock("expo-notifications", () => ({
  getPermissionsAsync: jest.fn(() => Promise.resolve({ status: "granted", granted: true, canAskAgain: true })),
  requestPermissionsAsync: jest.fn(() => Promise.resolve({ status: "granted", granted: true, canAskAgain: true })),
  scheduleNotificationAsync: jest.fn(() => Promise.resolve("id")),
  cancelScheduledNotificationAsync: jest.fn(() => Promise.resolve()),
  getAllScheduledNotificationsAsync: jest.fn(() => Promise.resolve([{ identifier: "a" }])),
  SchedulableTriggerInputTypes: { DATE: "date" }
}));
jest.mock("expo-clipboard", () => ({ setStringAsync: jest.fn(() => Promise.resolve()) }));
jest.mock("expo-document-picker", () => ({}));

const ExpoHaptics = jest.requireMock("expo-haptics");

describe("capability registry (Phase 1)", () => {
  it("every record has an owner when usable, and device_verified is honest", () => {
    for (const record of allCapabilities()) {
      if (record.implementation_state === "IMPLEMENTED") {
        expect(record.owner_module).not.toBe("");
      }
      if (record.implementation_state === "NOT_IMPLEMENTED") {
        expect(record.device_verified).toBe(false);
      }
    }
  });

  it("protected capabilities are excluded from UNDX visibility", () => {
    const visible = undxVisibleCapabilities().map((r) => r.capability_id);
    expect(visible).not.toContain("audio_recording");
    expect(visible).not.toContain("device_motion");
  });

  it("usability reflects implementation state", () => {
    expect(isCapabilityUsable("deep_links")).toBe(true);
    expect(isCapabilityUsable("contacts")).toBe(false);
    expect(getCapability("audio_recording").implementation_state).toBe("PROTECTED");
  });
});

describe("permission state mapping (Phase 2)", () => {
  const base = { status: "granted", granted: true, canAskAgain: true };
  it("maps the full state machine", () => {
    expect(toPermissionState(base)).toBe("GRANTED");
    expect(toPermissionState({ ...base, accessPrivileges: "limited" })).toBe("LIMITED");
    expect(toPermissionState({ status: "undetermined", granted: false, canAskAgain: true })).toBe("NOT_REQUESTED");
    expect(toPermissionState({ status: "denied", granted: false, canAskAgain: true })).toBe("DENIED");
    expect(toPermissionState({ status: "denied", granted: false, canAskAgain: false })).toBe("BLOCKED");
  });
});

describe("haptics owner (Phase 3)", () => {
  beforeEach(() => {
    setHapticsEnabled(true);
    jest.clearAllMocks();
  });

  it("fires the mapped primitive per tone", () => {
    haptic("light");
    haptic("success");
    haptic("selection");
    expect(ExpoHaptics.impactAsync).toHaveBeenCalledTimes(1);
    expect(ExpoHaptics.notificationAsync).toHaveBeenCalledTimes(1);
    expect(ExpoHaptics.selectionAsync).toHaveBeenCalledTimes(1);
  });

  it("respects the accessibility preference", () => {
    setHapticsEnabled(false);
    expect(hapticsEnabled()).toBe(false);
    haptic("error");
    expect(ExpoHaptics.notificationAsync).not.toHaveBeenCalled();
  });
});

describe("QR payloads (Phases 4-5, 41)", () => {
  it("generates canonical PulseSoc links only", () => {
    expect(qrLink("profile", 123)).toBe("https://pulsesoc.com/profile/123");
    expect(qrLink("marketplace", "abc 1")).toBe("https://pulsesoc.com/marketplace/item/abc%201");
    expect(() => qrLink("profile", " ")).toThrow();
  });

  it("auto-routes only PulseSoc links", () => {
    expect(classifyScannedPayload("pulsesoc://profile/9").kind).toBe("pulsesoc_link");
    expect(classifyScannedPayload("https://pulsesoc.com/event/4").kind).toBe("pulsesoc_link");
    expect(classifyScannedPayload("https://evil.example.com/pulsesoc.com").kind).toBe("external_url");
  });

  it("rejects dangerous or malformed payloads outright", () => {
    expect(classifyScannedPayload("javascript:alert(1)")).toEqual({ kind: "rejected", reason: "dangerous_scheme" });
    expect(classifyScannedPayload("data:text/html,<b>x</b>")).toEqual({ kind: "rejected", reason: "dangerous_scheme" });
    expect(classifyScannedPayload("").kind).toBe("rejected");
    expect(classifyScannedPayload("pulsesoc://<script>").kind).toBe("rejected");
  });

  it("host suffix matching cannot be spoofed", () => {
    expect(classifyScannedPayload("https://notpulsesoc.com/profile/1").kind).toBe("external_url");
    expect(classifyScannedPayload("https://pulsesoc.com.evil.io/x").kind).toBe("external_url");
  });
});

describe("local reminders (Phase 12)", () => {
  const Notif = jest.requireMock("expo-notifications");

  it("rejects past dates without touching the scheduler", async () => {
    const result = await scheduleLocalReminder({ id: "x", title: "t", body: "b", date: new Date(Date.now() - 1000) });
    expect(result).toBe("invalid_date");
    expect(Notif.scheduleNotificationAsync).not.toHaveBeenCalled();
  });

  it("schedules with a stable identifier and pulsesoc deep link", async () => {
    const date = new Date(Date.now() + 60_000);
    const result = await scheduleLocalReminder({ id: "draft-1", title: "t", body: "b", date, path: "/drafts" });
    expect(result).toBe("scheduled");
    expect(Notif.scheduleNotificationAsync).toHaveBeenCalledWith(
      expect.objectContaining({
        identifier: "draft-1",
        content: expect.objectContaining({ data: { url: "pulsesoc://drafts" } })
      })
    );
  });

  it("lists scheduled ids", async () => {
    await expect(listLocalReminderIds()).resolves.toEqual(["a"]);
  });
});

describe("UNDX device actions (Phases 37-38)", () => {
  it("never exposes protected capabilities", () => {
    expect(classifyDeviceAction("audio_recording")).toEqual({ allowed: false, reason: "unknown_capability" });
    expect(classifyDeviceAction("device_motion")).toEqual({ allowed: false, reason: "unknown_capability" });
    expect(classifyDeviceAction("made_up_capability")).toEqual({ allowed: false, reason: "unknown_capability" });
    const manifest = undxCapabilityManifest().map((e) => e.capability_id);
    expect(manifest).not.toContain("audio_recording");
    expect(manifest).not.toContain("device_motion");
  });

  it("gates external writes and sensitive access behind confirmation", () => {
    expect(classifyDeviceAction("haptics")).toEqual({
      allowed: true,
      actionClass: "READ_ONLY_LOCAL",
      requiresUserConfirmation: false
    });
    expect(classifyDeviceAction("native_share")).toEqual({
      allowed: true,
      actionClass: "EXTERNAL_WRITE",
      requiresUserConfirmation: true
    });
    expect(classifyDeviceAction("qr_scan")).toEqual({
      allowed: true,
      actionClass: "SENSITIVE_DEVICE_ACCESS",
      requiresUserConfirmation: true
    });
  });

  it("rejects known-but-unimplemented capabilities", () => {
    expect(classifyDeviceAction("contacts")).toEqual({ allowed: false, reason: "not_usable" });
  });
});

describe("document validation (Phase 18)", () => {
  it("enforces size, extension, and MIME policy", () => {
    expect(validateDocument({ name: "a.pdf", size: 100, mimeType: "application/pdf" }, { extensions: ["pdf"] })).toBe("ok");
    expect(validateDocument({ name: "a.exe", size: 100, mimeType: "application/x-msdownload" }, { extensions: ["pdf"] })).toBe("bad_type");
    expect(validateDocument({ name: "a.pdf", size: 999, mimeType: "application/pdf" }, { maxBytes: 500 })).toBe("too_large");
    expect(validateDocument({ name: "p.png", size: 10, mimeType: "image/png" }, { mimeTypes: ["image/"] })).toBe("ok");
    expect(validateDocument({ name: "p.txt", size: 10, mimeType: "text/plain" }, { mimeTypes: ["image/"] })).toBe("bad_type");
  });
});
