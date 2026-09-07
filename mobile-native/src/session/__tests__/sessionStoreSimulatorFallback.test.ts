/**
 * What the iOS Simulator does *not* get.
 *
 * An unprovisioned simulator has no `keychain-access-groups` entitlement, so
 * every `expo-secure-store` call rejects with -34018 and a session cannot
 * survive a cold start. The obvious fix is to let the simulator fall back to
 * AsyncStorage, and this repo shipped exactly that once: a
 * `Platform.OS === "ios" && !Device.isDevice` gate on the fallback.
 *
 * It was withdrawn, and this file exists to keep it withdrawn. The gate is
 * wider than it looks: `Device.isDevice` distinguishes simulator from phone,
 * not QA backend from production, so a simulator pointed at pulsesoc.com —
 * holding a real production refresh token — would write that token to
 * unencrypted storage. The surviving gate, `isLocalQaSession()`, keys on the
 * backend instead, which is the property that actually decides whether a
 * leaked credential matters.
 *
 * So the assertions below vary `Device.isDevice` across both values and assert
 * the behaviour is *identical* either way, determined only by the base URL.
 * The `expo-device` mock is deliberately inert — `sessionStore` no longer
 * imports it, and this file is what fails if someone wires it back in.
 *
 * The positive contract (that the localhost fallback works, its regex edges,
 * the read direction) lives in `sessionStoreQaFallback.test.ts`.
 */

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

let mockIsPhysicalDevice = false;
jest.mock("expo-device", () => ({
  get isDevice() {
    return mockIsPhysicalDevice;
  }
}));

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn(),
  AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY: "afterFirstUnlockThisDeviceOnly"
}));

let mockApiBaseUrl = "https://pulsesoc.com";
jest.mock("../../api/config", () => ({
  get PULSE_API_BASE_URL() {
    return mockApiBaseUrl;
  }
}));

import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";

import {
  getSessionCookie,
  getSessionEnvelope,
  setSessionCookie,
  setSessionEnvelope
} from "../sessionStore";

const secureStore = SecureStore as jest.Mocked<typeof SecureStore>;

const COOKIE_KEY = "pulsesoc.native.session.cookie";
const ENVELOPE_KEY = "pulsesoc.native.session.envelope.v1";

const ENVELOPE = {
  version: 1 as const,
  userId: 42,
  accessToken: "access-token",
  accessTokenExpiresAt: 1_900_000_000,
  refreshToken: "refresh-token",
  refreshTokenExpiresAt: 1_900_003_600
};

/** What a missing keychain-access-groups entitlement actually looks like. */
const KEYCHAIN_DENIED = new Error("Keychain access failed: -34018");

/** Both sides of the check the withdrawn gate used to make. */
const HARDWARE = [
  ["an iOS Simulator", false],
  ["a physical iPhone", true]
] as const;

beforeEach(async () => {
  jest.clearAllMocks();
  mockIsPhysicalDevice = false;
  mockApiBaseUrl = "https://pulsesoc.com";
  await AsyncStorage.clear();
  secureStore.getItemAsync.mockRejectedValue(KEYCHAIN_DENIED);
  secureStore.setItemAsync.mockRejectedValue(KEYCHAIN_DENIED);
  secureStore.deleteItemAsync.mockRejectedValue(KEYCHAIN_DENIED);
});

describe.each(HARDWARE)("on %s with the keychain refusing", (_label, isPhysicalDevice) => {
  beforeEach(() => {
    mockIsPhysicalDevice = isPhysicalDevice;
  });

  describe("pointed at production", () => {
    it("never writes the session cookie outside the keychain", async () => {
      await setSessionCookie("session=production");

      await expect(AsyncStorage.getItem(COOKIE_KEY)).resolves.toBeNull();
      expect(secureStore.setItemAsync).toHaveBeenCalled();
    });

    it("never writes the refresh envelope outside the keychain", async () => {
      await setSessionEnvelope(ENVELOPE);

      await expect(AsyncStorage.getItem(ENVELOPE_KEY)).resolves.toBeNull();
    });

    /**
     * The reason the old assertion here (`rejects.toThrow("-34018")`) was
     * wrong rather than unmet. This read happens in `hasStoredCredentials()`
     * during bootstrap, outside `restoreSession()`'s try/catch, so throwing
     * rendered a fatal "couldn't start PulseSoc" screen instead of a sign-in
     * screen. Degrading to signed-out is the intended failure, and it has to
     * be silent because there is no UI yet to show an error on.
     */
    it("degrades to signed-out instead of surfacing the entitlement error", async () => {
      await expect(getSessionCookie()).resolves.toBeNull();
      await expect(getSessionEnvelope()).resolves.toBeNull();
    });

    it("swallows the entitlement error on write and on sign-out", async () => {
      await expect(setSessionCookie("session=production")).resolves.toBeUndefined();
      await expect(setSessionEnvelope(ENVELOPE)).resolves.toBeUndefined();
      await expect(setSessionCookie("")).resolves.toBeUndefined();
      await expect(setSessionEnvelope(null)).resolves.toBeUndefined();
    });
  });

  describe("pointed at a local QA server", () => {
    beforeEach(() => {
      mockApiBaseUrl = "http://127.0.0.1:5000";
    });

    it("falls back to QA-only storage", async () => {
      await setSessionCookie("session=qa");

      await expect(AsyncStorage.getItem(COOKIE_KEY)).resolves.toBe("session=qa");
    });
  });
});
