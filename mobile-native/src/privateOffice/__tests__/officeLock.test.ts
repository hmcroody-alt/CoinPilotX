/**
 * The office grant lives in memory and nowhere else.
 *
 * Every case here defends one of three claims the module's header makes:
 * the grant token never touches disk (so a backup or the next signer-in can't
 * carry it), the relock preference is a ceiling the local clock enforces (the
 * server's TTL is the floor), and Face ID is a convenience credential whose
 * write ordering can never leave the settings toggle ON with nothing behind it.
 */

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
  AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY: "afterFirstUnlockThisDeviceOnly"
}));

import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import {
  OFFICE_DEVICE_HEADER,
  OFFICE_GRANT_HEADER,
  __resetOfficeLockForTests,
  currentOfficeGrantToken,
  disableOfficeBiometric,
  enableOfficeBiometric,
  getOfficeBiometricUserId,
  getOfficeLockSnapshot,
  getOfficeRelockTiming,
  isOfficeUnlocked,
  lockOfficeLocally,
  noteOfficeBackgrounded,
  noteOfficeForegrounded,
  officeRequestHeaders,
  readOfficeBiometricPasscode,
  reconcileOfficeOwner,
  setOfficeRelockTiming,
  setOfficeUnlocked,
  subscribeOfficeLock
} from "../officeLock";

const getItemAsync = SecureStore.getItemAsync as jest.Mock;
const setItemAsync = SecureStore.setItemAsync as jest.Mock;
const deleteItemAsync = SecureStore.deleteItemAsync as jest.Mock;

const USER = 9401;
const TOKEN = "grant-token-a1b2c3d4e5f6";
const FUTURE_ISO = () => new Date(Date.now() + 900_000).toISOString();

beforeEach(async () => {
  jest.clearAllMocks();
  getItemAsync.mockImplementation(async () => null);
  setItemAsync.mockImplementation(async () => undefined);
  deleteItemAsync.mockImplementation(async () => undefined);
  await AsyncStorage.clear();
  __resetOfficeLockForTests();
});

afterEach(() => {
  jest.restoreAllMocks();
});

describe("grant lifecycle in memory", () => {
  it("starts locked, with no token to offer", () => {
    expect(isOfficeUnlocked()).toBe(false);
    expect(currentOfficeGrantToken()).toBe("");
  });

  it("ignores an empty grant token — a blank unlock must not unlock", () => {
    setOfficeUnlocked("", FUTURE_ISO(), USER);
    expect(isOfficeUnlocked()).toBe(false);
  });

  it("unlocks for the owning user and refuses a different one", () => {
    setOfficeUnlocked(TOKEN, FUTURE_ISO(), USER);
    expect(isOfficeUnlocked()).toBe(true);
    expect(isOfficeUnlocked(USER)).toBe(true);
    expect(isOfficeUnlocked(USER + 1)).toBe(false);
    expect(currentOfficeGrantToken()).toBe(TOKEN);
  });

  it("stops offering the token once the server's expiry passes", () => {
    const base = Date.now();
    setOfficeUnlocked(TOKEN, new Date(base + 10_000).toISOString(), USER);
    expect(isOfficeUnlocked()).toBe(true);
    jest.spyOn(Date, "now").mockReturnValue(base + 10_001);
    expect(isOfficeUnlocked()).toBe(false);
    expect(currentOfficeGrantToken()).toBe("");
  });

  it("degrades an unparseable expiry to 'trust the server per request'", () => {
    setOfficeUnlocked(TOKEN, "not-a-date", USER);
    // expiresAt 0 means no local expiry check; the server still enforces its own.
    expect(isOfficeUnlocked()).toBe(true);
  });

  it("locks locally and notifies subscribers with a fresh snapshot", () => {
    const seen: boolean[] = [];
    const unsubscribe = subscribeOfficeLock(() => seen.push(getOfficeLockSnapshot().unlocked));
    setOfficeUnlocked(TOKEN, FUTURE_ISO(), USER);
    const unlockedSnapshot = getOfficeLockSnapshot();
    lockOfficeLocally();
    unsubscribe();
    expect(seen).toEqual([true, false]);
    // Snapshot identity changes with state — required by useSyncExternalStore.
    expect(getOfficeLockSnapshot()).not.toBe(unlockedSnapshot);
    expect(getOfficeLockSnapshot().grantToken).toBe("");
  });
});

describe("request headers", () => {
  it("always carries the device header, and the grant only while unlocked", async () => {
    const lockedHeaders = await officeRequestHeaders();
    expect(lockedHeaders[OFFICE_DEVICE_HEADER]).toBeTruthy();
    expect(lockedHeaders[OFFICE_GRANT_HEADER]).toBeUndefined();

    setOfficeUnlocked(TOKEN, FUTURE_ISO(), USER);
    const unlockedHeaders = await officeRequestHeaders();
    expect(unlockedHeaders[OFFICE_GRANT_HEADER]).toBe(TOKEN);
    // Same install, same device id.
    expect(unlockedHeaders[OFFICE_DEVICE_HEADER]).toBe(lockedHeaders[OFFICE_DEVICE_HEADER]);
  });
});

describe("relock lifecycle", () => {
  it("defaults to immediate and locks the moment the app leaves the foreground", async () => {
    expect(await getOfficeRelockTiming()).toBe("immediate");
    setOfficeUnlocked(TOKEN, FUTURE_ISO(), USER);
    await noteOfficeBackgrounded();
    expect(isOfficeUnlocked()).toBe(false);
  });

  it("keeps the grant across a short background under a timed preference", async () => {
    await setOfficeRelockTiming("5m");
    setOfficeUnlocked(TOKEN, FUTURE_ISO(), USER);
    const base = Date.now();
    const now = jest.spyOn(Date, "now");
    now.mockReturnValue(base);
    await noteOfficeBackgrounded();
    expect(isOfficeUnlocked()).toBe(true);
    now.mockReturnValue(base + 299_000);
    await noteOfficeForegrounded();
    expect(isOfficeUnlocked()).toBe(true);
  });

  it("locks when the background stay reaches the preference's ceiling", async () => {
    await setOfficeRelockTiming("5m");
    setOfficeUnlocked(TOKEN, FUTURE_ISO(), USER);
    const base = Date.now();
    const now = jest.spyOn(Date, "now");
    now.mockReturnValue(base);
    await noteOfficeBackgrounded();
    now.mockReturnValue(base + 300_000);
    await noteOfficeForegrounded();
    expect(isOfficeUnlocked()).toBe(false);
  });

  it("rejects a timing outside the vocabulary", async () => {
    await setOfficeRelockTiming("forever" as never);
    expect(await getOfficeRelockTiming()).toBe("immediate");
  });
});

describe("owner reconciliation", () => {
  it("relocks on account switch and on signed-out", () => {
    setOfficeUnlocked(TOKEN, FUTURE_ISO(), USER);
    reconcileOfficeOwner(USER);
    expect(isOfficeUnlocked()).toBe(true);
    reconcileOfficeOwner(USER + 1);
    expect(isOfficeUnlocked()).toBe(false);

    setOfficeUnlocked(TOKEN, FUTURE_ISO(), USER);
    reconcileOfficeOwner(0);
    expect(isOfficeUnlocked()).toBe(false);
  });
});

describe("Face ID convenience credential", () => {
  it("arms in the safe order: credential behind biometrics first, marker second", async () => {
    expect(await enableOfficeBiometric(USER, "824913")).toBe(true);
    const credentialWrite = setItemAsync.mock.calls.find(
      (call) => call[0] === "pulsesoc.native.office.passcode.v1"
    );
    const markerWrite = setItemAsync.mock.calls.find(
      (call) => call[0] === "pulsesoc.native.office.biometric.userId"
    );
    expect(credentialWrite).toBeTruthy();
    expect(markerWrite).toBeTruthy();
    // The passcode item demands a live biometric match to read back.
    expect(credentialWrite?.[2]?.requireAuthentication).toBe(true);
    // The marker does not — it is a display predicate, never an unlock.
    expect(markerWrite?.[2]?.requireAuthentication).toBeUndefined();
    // Credential before marker: settings must never read ON with nothing behind it.
    expect(setItemAsync.mock.calls.indexOf(credentialWrite!)).toBeLessThan(
      setItemAsync.mock.calls.indexOf(markerWrite!)
    );
  });

  it("rolls the credential back when the marker write fails", async () => {
    setItemAsync.mockImplementation(async (key: string) => {
      if (key === "pulsesoc.native.office.biometric.userId") throw new Error("keychain full");
    });
    expect(await enableOfficeBiometric(USER, "824913")).toBe(false);
    // The rollback removed the credential the marker could not vouch for.
    expect(
      deleteItemAsync.mock.calls.some((call) => call[0] === "pulsesoc.native.office.passcode.v1")
    ).toBe(true);
  });

  it("refuses to arm without a user or a passcode", async () => {
    expect(await enableOfficeBiometric(0, "824913")).toBe(false);
    expect(await enableOfficeBiometric(USER, "")).toBe(false);
    expect(setItemAsync).not.toHaveBeenCalled();
  });

  it("reads the passcode back only for the owning user", async () => {
    getItemAsync.mockImplementation(async (key: string) =>
      key === "pulsesoc.native.office.passcode.v1"
        ? JSON.stringify({ userId: USER, passcode: "824913" })
        : null
    );
    expect(await readOfficeBiometricPasscode(USER, "Unlock")).toEqual({
      status: "unlocked",
      passcode: "824913"
    });
  });

  it("treats a credential for someone else as stale and clears it", async () => {
    getItemAsync.mockImplementation(async (key: string) =>
      key === "pulsesoc.native.office.passcode.v1"
        ? JSON.stringify({ userId: USER + 1, passcode: "605827" })
        : null
    );
    expect(await readOfficeBiometricPasscode(USER, "Unlock")).toEqual({ status: "missing" });
    expect(
      deleteItemAsync.mock.calls.some((call) => call[0] === "pulsesoc.native.office.passcode.v1")
    ).toBe(true);
  });

  it("maps a cancelled biometric sheet and a hard refusal to distinct denials", async () => {
    getItemAsync.mockImplementation(async () => {
      throw new Error("User canceled the operation.");
    });
    expect(await readOfficeBiometricPasscode(USER, "Unlock")).toEqual({
      status: "denied",
      cancelled: true
    });
    getItemAsync.mockImplementation(async () => {
      throw new Error("Biometry is locked out.");
    });
    expect(await readOfficeBiometricPasscode(USER, "Unlock")).toEqual({
      status: "denied",
      cancelled: false
    });
  });

  it("reports no credential as missing, and disables idempotently", async () => {
    expect(await readOfficeBiometricPasscode(USER, "Unlock")).toEqual({ status: "missing" });
    expect(await getOfficeBiometricUserId()).toBeNull();
    await disableOfficeBiometric();
    expect(deleteItemAsync).toHaveBeenCalled();
  });
});

describe("the token never touches disk", () => {
  it("no persistence layer ever sees the grant token", async () => {
    setOfficeUnlocked(TOKEN, FUTURE_ISO(), USER);
    await officeRequestHeaders(); // mints + persists the device id
    await setOfficeRelockTiming("15m");
    await noteOfficeBackgrounded();
    await noteOfficeForegrounded();
    await enableOfficeBiometric(USER, "824913");

    const persistedValues = [
      ...setItemAsync.mock.calls.map((call) => String(call[1])),
      ...(AsyncStorage.setItem as jest.Mock).mock.calls.map((call) => String(call[1]))
    ];
    expect(persistedValues.length).toBeGreaterThan(0);
    for (const value of persistedValues) {
      expect(value).not.toContain(TOKEN);
    }
  });
});
