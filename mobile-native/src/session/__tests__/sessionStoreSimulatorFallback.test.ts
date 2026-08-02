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

import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import {
  getSessionCookie,
  getSessionEnvelope,
  setSessionCookie,
  setSessionEnvelope
} from "../sessionStore";

const secureStore = SecureStore as jest.Mocked<typeof SecureStore>;

beforeEach(async () => {
  jest.clearAllMocks();
  mockIsPhysicalDevice = false;
  await AsyncStorage.clear();
  secureStore.getItemAsync.mockRejectedValue(new Error("Keychain access failed: -34018"));
  secureStore.setItemAsync.mockRejectedValue(new Error("Keychain access failed: -34018"));
  secureStore.deleteItemAsync.mockRejectedValue(new Error("Keychain access failed: -34018"));
});

describe("iOS Simulator secure-session fallback", () => {
  it("restores a cookie from QA-only AsyncStorage when Keychain is unavailable", async () => {
    await AsyncStorage.setItem("pulsesoc.native.session.cookie", "session=simulator");

    await expect(getSessionCookie()).resolves.toBe("session=simulator");
  });

  it("persists and clears a cookie without surfacing the missing-entitlement error", async () => {
    await setSessionCookie("session=simulator");
    await expect(AsyncStorage.getItem("pulsesoc.native.session.cookie")).resolves.toBe("session=simulator");

    await setSessionCookie("");
    await expect(AsyncStorage.getItem("pulsesoc.native.session.cookie")).resolves.toBeNull();
  });

  it("persists and restores the refresh envelope for simulator QA", async () => {
    const envelope = {
      version: 1 as const,
      userId: 42,
      accessToken: "access-token",
      accessTokenExpiresAt: 1_900_000_000,
      refreshToken: "refresh-token",
      refreshTokenExpiresAt: 1_900_003_600
    };

    await setSessionEnvelope(envelope);
    await expect(getSessionEnvelope()).resolves.toEqual(envelope);
  });

  it("never falls back to unencrypted storage on a physical iPhone", async () => {
    mockIsPhysicalDevice = true;

    await expect(setSessionCookie("session=physical")).rejects.toThrow("-34018");
    await expect(AsyncStorage.getItem("pulsesoc.native.session.cookie")).resolves.toBeNull();
  });
});
