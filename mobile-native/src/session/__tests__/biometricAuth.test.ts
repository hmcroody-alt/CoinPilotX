import * as LocalAuthentication from "expo-local-authentication";
import * as SecureStore from "expo-secure-store";

jest.mock("expo-local-authentication", () => ({
  hasHardwareAsync: jest.fn(),
  isEnrolledAsync: jest.fn(),
  supportedAuthenticationTypesAsync: jest.fn(),
  authenticateAsync: jest.fn(),
  AuthenticationType: { FINGERPRINT: 1, FACIAL_RECOGNITION: 2, IRIS: 3 }
}));

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn(),
  AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY: "afterFirstUnlockThisDeviceOnly"
}));

jest.mock("../sessionStore", () => ({
  BIOMETRIC_USER_KEY: "pulsesoc.native.session.biometric.userId",
  getSessionEnvelope: jest.fn(),
  setSessionEnvelope: jest.fn(),
  getBiometricSession: jest.fn(),
  setBiometricSession: jest.fn()
}));

jest.mock("../auth", () => ({
  restoreSession: jest.fn()
}));

import { getBiometricSession, getSessionEnvelope } from "../sessionStore";
import { restoreSession } from "../auth";
import {
  authenticateWithBiometrics,
  confirmAndEnableBiometricLogin,
  getBiometricCapability,
  isBiometricEnabledForCurrentSession
} from "../biometricAuth";

const mockedLocalAuth = LocalAuthentication as jest.Mocked<typeof LocalAuthentication>;
const mockedSecureStore = SecureStore as jest.Mocked<typeof SecureStore>;
const mockedGetSessionEnvelope = getSessionEnvelope as jest.Mock;
const mockedGetBiometricSession = getBiometricSession as jest.Mock;
const mockedRestoreSession = restoreSession as jest.Mock;

beforeEach(() => mockedGetBiometricSession.mockResolvedValue(null));

describe("getBiometricCapability", () => {
  beforeEach(() => jest.clearAllMocks());

  it("reports no_hardware when the device has no biometric sensor", async () => {
    mockedLocalAuth.hasHardwareAsync.mockResolvedValue(false);
    mockedLocalAuth.supportedAuthenticationTypesAsync.mockResolvedValue([]);
    const result = await getBiometricCapability();
    expect(result).toEqual({ available: false, hasHardware: false, kind: "none", reason: "no_hardware" });
  });

  it("reports not_enrolled (but keeps hardware + kind) when nothing is enrolled in iOS", async () => {
    mockedLocalAuth.hasHardwareAsync.mockResolvedValue(true);
    mockedLocalAuth.isEnrolledAsync.mockResolvedValue(false);
    mockedLocalAuth.supportedAuthenticationTypesAsync.mockResolvedValue([2]);
    const result = await getBiometricCapability();
    expect(result).toEqual({ available: false, hasHardware: true, kind: "faceId", reason: "not_enrolled" });
  });

  it("reports faceId when facial recognition is supported and enrolled", async () => {
    mockedLocalAuth.hasHardwareAsync.mockResolvedValue(true);
    mockedLocalAuth.isEnrolledAsync.mockResolvedValue(true);
    mockedLocalAuth.supportedAuthenticationTypesAsync.mockResolvedValue([2]);
    const result = await getBiometricCapability();
    expect(result).toEqual({ available: true, hasHardware: true, kind: "faceId" });
  });
});

describe("isBiometricEnabledForCurrentSession", () => {
  beforeEach(() => jest.clearAllMocks());

  it("is false when no user has opted in", async () => {
    mockedSecureStore.getItemAsync.mockResolvedValue(null);
    mockedGetSessionEnvelope.mockResolvedValue({ userId: 5, refreshToken: "abc" });
    expect(await isBiometricEnabledForCurrentSession()).toBe(false);
  });

  it("is false when the enrolled biometric user does not match the current session (account-crossing protection)", async () => {
    mockedSecureStore.getItemAsync.mockResolvedValue("5");
    mockedGetSessionEnvelope.mockResolvedValue({ userId: 9, refreshToken: "abc" });
    expect(await isBiometricEnabledForCurrentSession()).toBe(false);
  });

  it("is true when the enrolled biometric user matches the current session", async () => {
    mockedSecureStore.getItemAsync.mockResolvedValue("5");
    mockedGetSessionEnvelope.mockResolvedValue({ userId: 5, refreshToken: "abc" });
    expect(await isBiometricEnabledForCurrentSession()).toBe(true);
  });
});

describe("authenticateWithBiometrics", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedLocalAuth.hasHardwareAsync.mockResolvedValue(true);
    mockedLocalAuth.isEnrolledAsync.mockResolvedValue(true);
    mockedLocalAuth.supportedAuthenticationTypesAsync.mockResolvedValue([2]);
  });

  it("returns not_available when there is no biometric hardware", async () => {
    mockedLocalAuth.hasHardwareAsync.mockResolvedValue(false);
    const result = await authenticateWithBiometrics();
    expect(result).toEqual({ outcome: "not_available" });
  });

  it("returns no_enrolled_account when no account has opted in on this device", async () => {
    mockedSecureStore.getItemAsync.mockResolvedValue(null);
    mockedGetSessionEnvelope.mockResolvedValue(null);
    const result = await authenticateWithBiometrics();
    expect(result).toEqual({ outcome: "no_enrolled_account" });
    expect(mockedLocalAuth.authenticateAsync).not.toHaveBeenCalled();
  });

  it("maps a user cancel to the cancelled outcome without touching the session", async () => {
    mockedSecureStore.getItemAsync.mockResolvedValue("5");
    mockedGetSessionEnvelope.mockResolvedValue({ userId: 5, refreshToken: "abc" });
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: false, error: "user_cancel" });
    const result = await authenticateWithBiometrics();
    expect(result).toEqual({ outcome: "cancelled" });
    expect(mockedRestoreSession).not.toHaveBeenCalled();
  });

  it("maps a lockout error to the lockout outcome", async () => {
    mockedSecureStore.getItemAsync.mockResolvedValue("5");
    mockedGetSessionEnvelope.mockResolvedValue({ userId: 5, refreshToken: "abc" });
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: false, error: "lockout" });
    const result = await authenticateWithBiometrics();
    expect(result).toEqual({ outcome: "lockout" });
  });

  it("validates the restored session server-side after a successful biometric unlock", async () => {
    mockedSecureStore.getItemAsync.mockResolvedValue("5");
    mockedGetSessionEnvelope.mockResolvedValue({ userId: 5, refreshToken: "abc" });
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: true });
    mockedRestoreSession.mockResolvedValue({ status: "signedIn", user: { user_id: 5 } });
    const result = await authenticateWithBiometrics();
    expect(result).toEqual({ outcome: "success", authState: { status: "signedIn", user: { user_id: 5 } } });
  });

  it("rejects and clears biometric enrollment when the restored session belongs to a different user", async () => {
    mockedSecureStore.getItemAsync.mockResolvedValue("5");
    mockedGetSessionEnvelope.mockResolvedValue({ userId: 5, refreshToken: "abc" });
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: true });
    mockedRestoreSession.mockResolvedValue({ status: "signedIn", user: { user_id: 9 } });
    const result = await authenticateWithBiometrics();
    expect(result).toEqual({ outcome: "session_invalid" });
    expect(mockedSecureStore.deleteItemAsync).toHaveBeenCalled();
  });

  it("treats a failed server-side session restore as session_invalid", async () => {
    mockedSecureStore.getItemAsync.mockResolvedValue("5");
    mockedGetSessionEnvelope.mockResolvedValue({ userId: 5, refreshToken: "abc" });
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: true });
    mockedRestoreSession.mockRejectedValue(new Error("network down"));
    const result = await authenticateWithBiometrics();
    expect(result).toEqual({ outcome: "session_invalid" });
  });
});

describe("confirmAndEnableBiometricLogin", () => {
  beforeEach(() => jest.clearAllMocks());

  it("does not persist enrollment when the device has no biometric capability", async () => {
    mockedLocalAuth.hasHardwareAsync.mockResolvedValue(false);
    const result = await confirmAndEnableBiometricLogin(5);
    expect(result).toBe(false);
    expect(mockedSecureStore.setItemAsync).not.toHaveBeenCalled();
  });

  it("does not persist enrollment when the user declines or fails the confirmation prompt", async () => {
    mockedLocalAuth.hasHardwareAsync.mockResolvedValue(true);
    mockedLocalAuth.isEnrolledAsync.mockResolvedValue(true);
    mockedLocalAuth.supportedAuthenticationTypesAsync.mockResolvedValue([2]);
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: false, error: "user_cancel" });
    const result = await confirmAndEnableBiometricLogin(5);
    expect(result).toBe(false);
    expect(mockedSecureStore.setItemAsync).not.toHaveBeenCalled();
  });

  it("persists enrollment for the confirmed user only after a successful prompt", async () => {
    mockedLocalAuth.hasHardwareAsync.mockResolvedValue(true);
    mockedLocalAuth.isEnrolledAsync.mockResolvedValue(true);
    mockedLocalAuth.supportedAuthenticationTypesAsync.mockResolvedValue([2]);
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: true });
    const result = await confirmAndEnableBiometricLogin(5);
    expect(result).toBe(true);
    expect(mockedSecureStore.setItemAsync).toHaveBeenCalledWith(
      "pulsesoc.native.session.biometric.userId",
      "5",
      expect.any(Object)
    );
  });
});
