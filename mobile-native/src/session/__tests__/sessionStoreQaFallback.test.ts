/**
 * The plaintext-fallback boundary.
 *
 * An adhoc or simulator build without the `keychain-access-groups` entitlement
 * makes every `expo-secure-store` call reject with `-34018`. Left alone that
 * takes down startup, so `sessionStore` catches it — and for local QA it falls
 * back to AsyncStorage so a simulator can hold a session at all.
 *
 * That fallback is a hole in the only place tokens are supposed to live. It is
 * safe solely because of the condition on it, and the condition is one line:
 * `isLocalQaSession()`, true only when `PULSE_API_BASE_URL` points at
 * 127.0.0.1 or localhost. Widen it — to `__DEV__`, to a device check, to a
 * bare `catch` — and a build talking to production starts writing refresh
 * tokens to unencrypted storage. Nothing about that failure is visible: sign-in
 * keeps working, the app looks identical, and no test that only exercises the
 * happy keychain path would notice.
 *
 * These cases are the two halves of that condition, asserted against a
 * keychain that fails exactly the way a real entitlement gap does.
 *
 * Ported from `codex/governed-realtime-audio` (`sessionStoreSimulatorFallback`),
 * whose gate was `Device.isDevice`. That gate is gone; the invariant it was
 * defending is not, so the assertions were rewritten against the gate that
 * actually ships. The current gate is the stricter of the two — a simulator
 * pointed at production no longer falls back, where the device check would have
 * let it.
 */

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

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

beforeEach(async () => {
  jest.clearAllMocks();
  await AsyncStorage.clear();
  secureStore.getItemAsync.mockRejectedValue(KEYCHAIN_DENIED);
  secureStore.setItemAsync.mockRejectedValue(KEYCHAIN_DENIED);
  secureStore.deleteItemAsync.mockRejectedValue(KEYCHAIN_DENIED);
});

describe("with the keychain refusing and the app pointed at a local QA server", () => {
  beforeEach(() => {
    mockApiBaseUrl = "http://127.0.0.1:5000";
  });

  it("reads a cookie back out of QA-only storage", async () => {
    await AsyncStorage.setItem(COOKIE_KEY, "session=simulator");

    await expect(getSessionCookie()).resolves.toBe("session=simulator");
  });

  it("persists and clears a cookie without surfacing the entitlement error", async () => {
    await setSessionCookie("session=simulator");
    await expect(AsyncStorage.getItem(COOKIE_KEY)).resolves.toBe("session=simulator");

    await setSessionCookie("");
    await expect(AsyncStorage.getItem(COOKIE_KEY)).resolves.toBeNull();
  });

  it("round-trips the refresh envelope so a simulator can stay signed in", async () => {
    await setSessionEnvelope(ENVELOPE);

    await expect(getSessionEnvelope()).resolves.toEqual(ENVELOPE);
  });
});

describe("with the keychain refusing and the app pointed at production", () => {
  beforeEach(() => {
    mockApiBaseUrl = "https://pulsesoc.com";
  });

  /**
   * The case the whole gate exists for. A refresh token is a bearer credential;
   * writing one to AsyncStorage puts it in an unencrypted file readable by
   * anything that can read the container.
   */
  it("never writes the refresh envelope outside the keychain", async () => {
    await setSessionEnvelope(ENVELOPE);

    await expect(AsyncStorage.getItem(ENVELOPE_KEY)).resolves.toBeNull();
    expect(secureStore.setItemAsync).toHaveBeenCalled();
  });

  it("never writes the session cookie outside the keychain", async () => {
    await setSessionCookie("session=production");

    await expect(AsyncStorage.getItem(COOKIE_KEY)).resolves.toBeNull();
  });

  /**
   * Degrading to signed-out is the intended failure, and it has to be silent:
   * throwing here happens during startup, before there is a screen to show an
   * error on.
   */
  it("degrades to signed-out rather than throwing on startup", async () => {
    await expect(getSessionCookie()).resolves.toBeNull();
    await expect(getSessionEnvelope()).resolves.toBeNull();
  });

  /**
   * A value already sitting in AsyncStorage — left by an earlier QA build, or
   * planted — must not be honoured once the app is talking to production. The
   * read gate matters as much as the write gate, and only this direction can
   * catch a fallback that reads without writing.
   */
  it("ignores a plaintext value left behind by an earlier QA build", async () => {
    await AsyncStorage.setItem(COOKIE_KEY, "session=stale-qa");
    await AsyncStorage.setItem(ENVELOPE_KEY, JSON.stringify(ENVELOPE));

    await expect(getSessionCookie()).resolves.toBeNull();
    await expect(getSessionEnvelope()).resolves.toBeNull();
  });
});

/**
 * The gate is a regex over the base URL, so its edges are worth stating: a
 * hostname that merely contains "localhost" is not local QA.
 */
describe("the boundary of what counts as local QA", () => {
  it.each([
    ["https://pulsesoc.com", false],
    ["https://localhost.pulsesoc.com", false],
    ["https://staging.pulsesoc.com", false],
    ["http://127.0.0.1:5000", true],
    ["http://localhost:8081", true],
    ["http://localhost", true]
  ])("%s falls back: %s", async (baseUrl, fallsBack) => {
    mockApiBaseUrl = baseUrl;

    await setSessionCookie("session=probe");

    await expect(AsyncStorage.getItem(COOKIE_KEY)).resolves.toBe(
      fallsBack ? "session=probe" : null
    );
  });
});
