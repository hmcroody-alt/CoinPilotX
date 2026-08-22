/**
 * Face ID durability and bypass-resistance.
 *
 * Two properties are load-bearing here and neither is obvious from the code:
 *
 *  1. Enrollment survives everything except a deliberate teardown. Closing the
 *     app, signing out, restarting the phone and rotating the refresh token all
 *     have to leave Face ID working; only "turn it off", "delete the account"
 *     and iOS invalidating the credential may end it.
 *  2. The *only* thing that releases the refresh token is a live biometric
 *     match. The enrollment marker is a display flag and must never, on its
 *     own, get anybody signed in — which is exactly what these tests pin down,
 *     because in the source the two live one line apart.
 *
 * The storage layer that actually enforces (2) — the keychain access control —
 * is covered by `sessionStore.biometric.test.ts`; this file covers the flow
 * that sits on top of it.
 */
import * as LocalAuthentication from "expo-local-authentication";

jest.mock("expo-local-authentication", () => ({
  hasHardwareAsync: jest.fn(),
  isEnrolledAsync: jest.fn(),
  supportedAuthenticationTypesAsync: jest.fn(),
  authenticateAsync: jest.fn(),
  AuthenticationType: { FINGERPRINT: 1, FACIAL_RECOGNITION: 2, IRIS: 3 }
}));

jest.mock("../sessionStore", () => ({
  getBiometricUserId: jest.fn(),
  setBiometricUserId: jest.fn(async () => true),
  getSessionEnvelope: jest.fn(),
  setSessionEnvelope: jest.fn(),
  readBiometricCredential: jest.fn(async () => ({ status: "missing" })),
  writeBiometricCredential: jest.fn(async () => true),
  getLegacyBiometricSession: jest.fn(async () => null),
  clearBiometricEnrollment: jest.fn(async () => undefined)
}));

jest.mock("../auth", () => ({
  restoreSession: jest.fn()
}));

import {
  clearBiometricEnrollment,
  getBiometricUserId,
  getLegacyBiometricSession,
  readBiometricCredential,
  setBiometricUserId,
  setSessionEnvelope,
  getSessionEnvelope,
  writeBiometricCredential
} from "../sessionStore";
import { restoreSession } from "../auth";
import {
  authenticateWithBiometrics,
  confirmAndEnableBiometricLogin,
  disableBiometricLogin,
  getBiometricCapability,
  isBiometricEnabledForCurrentSession
} from "../biometricAuth";

const mockedLocalAuth = LocalAuthentication as jest.Mocked<typeof LocalAuthentication>;
const mockedGetBiometricUserId = getBiometricUserId as jest.Mock;
const mockedSetBiometricUserId = setBiometricUserId as jest.Mock;
const mockedGetSessionEnvelope = getSessionEnvelope as jest.Mock;
const mockedSetSessionEnvelope = setSessionEnvelope as jest.Mock;
const mockedReadCredential = readBiometricCredential as jest.Mock;
const mockedWriteCredential = writeBiometricCredential as jest.Mock;
const mockedGetLegacy = getLegacyBiometricSession as jest.Mock;
const mockedClearEnrollment = clearBiometricEnrollment as jest.Mock;
const mockedRestoreSession = restoreSession as jest.Mock;

const USER = 5;
const OTHER_USER = 9;

/** A signed-in session envelope for `userId`, i.e. a token Face ID could bind. */
function envelopeFor(userId: number, refreshToken = "refresh-1") {
  return { version: 1, userId, accessToken: "a", accessTokenExpiresAt: 1, refreshToken, refreshTokenExpiresAt: 2 };
}

function credentialFor(userId: number, refreshToken = "refresh-1") {
  return { userId, refreshToken, refreshTokenExpiresAt: 2 };
}

/** Hardware present, a face enrolled in iOS — the precondition for every unlock test. */
function deviceHasFaceId() {
  mockedLocalAuth.hasHardwareAsync.mockResolvedValue(true);
  mockedLocalAuth.isEnrolledAsync.mockResolvedValue(true);
  mockedLocalAuth.supportedAuthenticationTypesAsync.mockResolvedValue([2]);
}

beforeEach(() => {
  jest.clearAllMocks();
  mockedSetBiometricUserId.mockResolvedValue(true);
  mockedWriteCredential.mockResolvedValue(true);
  mockedReadCredential.mockResolvedValue({ status: "missing" });
  mockedGetLegacy.mockResolvedValue(null);
  mockedGetSessionEnvelope.mockResolvedValue(null);
  mockedGetBiometricUserId.mockResolvedValue(null);
});

describe("getBiometricCapability", () => {
  it("reports no_hardware when the device has no biometric sensor", async () => {
    mockedLocalAuth.hasHardwareAsync.mockResolvedValue(false);
    mockedLocalAuth.supportedAuthenticationTypesAsync.mockResolvedValue([]);
    expect(await getBiometricCapability()).toEqual({ available: false, hasHardware: false, kind: "none", reason: "no_hardware" });
  });

  it("reports not_enrolled (but keeps hardware + kind) when nothing is enrolled in iOS", async () => {
    mockedLocalAuth.hasHardwareAsync.mockResolvedValue(true);
    mockedLocalAuth.isEnrolledAsync.mockResolvedValue(false);
    mockedLocalAuth.supportedAuthenticationTypesAsync.mockResolvedValue([2]);
    expect(await getBiometricCapability()).toEqual({ available: false, hasHardware: true, kind: "faceId", reason: "not_enrolled" });
  });

  it("reports faceId when facial recognition is supported and enrolled", async () => {
    deviceHasFaceId();
    expect(await getBiometricCapability()).toEqual({ available: true, hasHardware: true, kind: "faceId" });
  });
});

describe("enabling Face ID", () => {
  beforeEach(deviceHasFaceId);

  // Required test 1.
  it("enables Face ID for the confirmed user after a successful prompt", async () => {
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: true } as never);
    mockedGetSessionEnvelope.mockResolvedValue(envelopeFor(USER));
    expect(await confirmAndEnableBiometricLogin(USER)).toBe(true);
    expect(mockedSetBiometricUserId).toHaveBeenCalledWith(USER);
  });

  // Required test 2: the thing Face ID protects is a real credential, and it is
  // written to protected storage — not a flag flipped in memory.
  it("stores the account's refresh token as the protected credential", async () => {
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: true } as never);
    mockedGetSessionEnvelope.mockResolvedValue(envelopeFor(USER, "refresh-abc"));
    await confirmAndEnableBiometricLogin(USER);
    expect(mockedWriteCredential).toHaveBeenCalledWith({
      userId: USER,
      refreshToken: "refresh-abc",
      refreshTokenExpiresAt: 2
    });
  });

  it("does not enable Face ID when the device has no biometric capability", async () => {
    mockedLocalAuth.hasHardwareAsync.mockResolvedValue(false);
    expect(await confirmAndEnableBiometricLogin(USER)).toBe(false);
    expect(mockedWriteCredential).not.toHaveBeenCalled();
    expect(mockedSetBiometricUserId).not.toHaveBeenCalled();
  });

  it("does not enable Face ID when the user declines or fails the confirmation prompt", async () => {
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: false, error: "user_cancel" } as never);
    mockedGetSessionEnvelope.mockResolvedValue(envelopeFor(USER));
    expect(await confirmAndEnableBiometricLogin(USER)).toBe(false);
    expect(mockedWriteCredential).not.toHaveBeenCalled();
    expect(mockedSetBiometricUserId).not.toHaveBeenCalled();
  });

  it("refuses to arm Face ID when the credential write fails, rather than claiming it is on", async () => {
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: true } as never);
    mockedGetSessionEnvelope.mockResolvedValue(envelopeFor(USER));
    mockedWriteCredential.mockResolvedValue(false);
    expect(await confirmAndEnableBiometricLogin(USER)).toBe(false);
    // The marker is what Settings reads. Setting it here would produce a switch
    // that says ON over a keychain with nothing in it.
    expect(mockedSetBiometricUserId).not.toHaveBeenCalled();
  });

  it("refuses to arm Face ID when there is no refresh token to protect", async () => {
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: true } as never);
    mockedGetSessionEnvelope.mockResolvedValue(null);
    expect(await confirmAndEnableBiometricLogin(USER)).toBe(false);
    expect(mockedSetBiometricUserId).not.toHaveBeenCalled();
  });

  it("rolls the credential back if the enrollment marker cannot be written", async () => {
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: true } as never);
    mockedGetSessionEnvelope.mockResolvedValue(envelopeFor(USER));
    mockedSetBiometricUserId.mockResolvedValue(false);
    expect(await confirmAndEnableBiometricLogin(USER)).toBe(false);
    expect(mockedClearEnrollment).toHaveBeenCalled();
  });
});

describe("durability", () => {
  // Required test 3: a cold start reads persisted state and writes nothing.
  it("still reports Face ID as enabled on a cold start with no live session", async () => {
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedGetSessionEnvelope.mockResolvedValue(null);
    expect(await isBiometricEnabledForCurrentSession()).toBe(true);
    expect(mockedClearEnrollment).not.toHaveBeenCalled();
  });

  // Required test 4: refresh rotates the token; enrollment is not collateral.
  it("survives an ordinary token rotation for the same account", async () => {
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedGetSessionEnvelope.mockResolvedValue(envelopeFor(USER, "rotated-token"));
    expect(await isBiometricEnabledForCurrentSession()).toBe(true);
    expect(mockedClearEnrollment).not.toHaveBeenCalled();
  });

  it("re-stores the rotated token so the next sign-out keeps a usable credential", async () => {
    deviceHasFaceId();
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedReadCredential.mockResolvedValue({ status: "unlocked", session: credentialFor(USER, "old-token") });
    mockedGetSessionEnvelope
      .mockResolvedValueOnce(null)
      .mockResolvedValue(envelopeFor(USER, "rotated-token"));
    mockedRestoreSession.mockResolvedValue({ status: "signedIn", user: { user_id: USER } });
    await authenticateWithBiometrics();
    expect(mockedWriteCredential).toHaveBeenLastCalledWith({
      userId: USER,
      refreshToken: "rotated-token",
      refreshTokenExpiresAt: 2
    });
  });

  it("reports Face ID as off once a different account is signed in on this device", async () => {
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedGetSessionEnvelope.mockResolvedValue(envelopeFor(OTHER_USER));
    expect(await isBiometricEnabledForCurrentSession()).toBe(false);
  });
});

describe("unlocking with Face ID", () => {
  beforeEach(deviceHasFaceId);

  it("returns not_available when there is no biometric hardware", async () => {
    mockedLocalAuth.hasHardwareAsync.mockResolvedValue(false);
    expect(await authenticateWithBiometrics()).toEqual({ outcome: "not_available" });
  });

  it("returns no_enrolled_account when no account has opted in on this device", async () => {
    mockedGetBiometricUserId.mockResolvedValue(null);
    expect(await authenticateWithBiometrics()).toEqual({ outcome: "no_enrolled_account" });
    expect(mockedReadCredential).not.toHaveBeenCalled();
  });

  // Required test 5.
  it("releases the credential into the live session only after the biometric read succeeds", async () => {
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedReadCredential.mockResolvedValue({ status: "unlocked", session: credentialFor(USER, "gated-token") });
    mockedGetSessionEnvelope.mockResolvedValueOnce(null).mockResolvedValue(envelopeFor(USER));
    mockedRestoreSession.mockResolvedValue({ status: "signedIn", user: { user_id: USER } });
    const result = await authenticateWithBiometrics();
    expect(result).toEqual({ outcome: "success", authState: { status: "signedIn", user: { user_id: USER } } });
    expect(mockedSetSessionEnvelope).toHaveBeenCalledWith(expect.objectContaining({ userId: USER, refreshToken: "gated-token" }));
  });

  // Required test 6.
  it("does not authenticate when the user cancels the biometric prompt", async () => {
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedReadCredential.mockResolvedValue({ status: "denied", cancelled: true });
    expect(await authenticateWithBiometrics()).toEqual({ outcome: "cancelled" });
    expect(mockedSetSessionEnvelope).not.toHaveBeenCalled();
    expect(mockedRestoreSession).not.toHaveBeenCalled();
  });

  // Required test 7.
  it("does not authenticate when the biometric match fails", async () => {
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedReadCredential.mockResolvedValue({ status: "denied", cancelled: false });
    expect(await authenticateWithBiometrics()).toEqual({ outcome: "failed" });
    expect(mockedSetSessionEnvelope).not.toHaveBeenCalled();
    expect(mockedRestoreSession).not.toHaveBeenCalled();
  });

  // Required test 8.
  it("does not authenticate when the protected credential is gone", async () => {
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedReadCredential.mockResolvedValue({ status: "missing" });
    mockedGetLegacy.mockResolvedValue(null);
    expect(await authenticateWithBiometrics()).toEqual({ outcome: "no_enrolled_account" });
    expect(mockedRestoreSession).not.toHaveBeenCalled();
  });

  // Required test 12: the credential is bound to an account, not to the device.
  it("refuses a stored credential that belongs to a different account", async () => {
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedReadCredential.mockResolvedValue({ status: "unlocked", session: credentialFor(OTHER_USER) });
    expect(await authenticateWithBiometrics()).toEqual({ outcome: "no_enrolled_account" });
    expect(mockedSetSessionEnvelope).not.toHaveBeenCalled();
    expect(mockedRestoreSession).not.toHaveBeenCalled();
    expect(mockedClearEnrollment).toHaveBeenCalled();
  });

  it("clears enrollment when the restored session turns out to belong to another user", async () => {
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedReadCredential.mockResolvedValue({ status: "unlocked", session: credentialFor(USER) });
    mockedGetSessionEnvelope.mockResolvedValue(null);
    mockedRestoreSession.mockResolvedValue({ status: "signedIn", user: { user_id: OTHER_USER } });
    expect(await authenticateWithBiometrics()).toEqual({ outcome: "session_invalid" });
    expect(mockedClearEnrollment).toHaveBeenCalled();
  });

  it("treats a failed server-side session restore as session_invalid", async () => {
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedReadCredential.mockResolvedValue({ status: "unlocked", session: credentialFor(USER) });
    mockedGetSessionEnvelope.mockResolvedValue(null);
    mockedRestoreSession.mockRejectedValue(new Error("network down"));
    expect(await authenticateWithBiometrics()).toEqual({ outcome: "session_invalid" });
  });

  // Required test 13.
  it("falls back to normal sign-in, without crashing, when iOS has invalidated the credential", async () => {
    // A biometric change makes iOS discard the item; the read then reports it
    // simply as absent, which is the only signal we get.
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedReadCredential.mockResolvedValue({ status: "missing" });
    mockedGetLegacy.mockResolvedValue(null);
    const result = await authenticateWithBiometrics();
    expect(result).toEqual({ outcome: "no_enrolled_account" });
    // Self-heals the UI: Settings must stop advertising Face ID for a credential
    // the OS has thrown away.
    expect(mockedClearEnrollment).toHaveBeenCalled();
  });

  // Required test 14: this is the whole security claim in one assertion.
  it("cannot sign in on the enrollment marker alone", async () => {
    mockedGetBiometricUserId.mockResolvedValue(USER); // "biometricEnabled = true"
    mockedReadCredential.mockResolvedValue({ status: "missing" });
    mockedGetLegacy.mockResolvedValue(null);
    mockedGetSessionEnvelope.mockResolvedValue(null);
    mockedRestoreSession.mockResolvedValue({ status: "signedIn", user: { user_id: USER } });
    expect(await authenticateWithBiometrics()).toEqual({ outcome: "no_enrolled_account" });
    expect(mockedRestoreSession).not.toHaveBeenCalled();
  });

  it("migrates a pre-existing unprotected credential, but only behind a live prompt", async () => {
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedReadCredential.mockResolvedValue({ status: "missing" });
    mockedGetLegacy.mockResolvedValue(credentialFor(USER, "legacy-token"));
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: true } as never);
    mockedGetSessionEnvelope.mockResolvedValueOnce(null).mockResolvedValue(envelopeFor(USER, "rotated-token"));
    mockedRestoreSession.mockResolvedValue({ status: "signedIn", user: { user_id: USER } });
    const result = await authenticateWithBiometrics();
    expect(result).toEqual({ outcome: "success", authState: { status: "signedIn", user: { user_id: USER } } });
    expect(mockedLocalAuth.authenticateAsync).toHaveBeenCalled();
    // Re-stored through the protected path, which also purges the legacy copy.
    expect(mockedWriteCredential).toHaveBeenLastCalledWith({
      userId: USER,
      refreshToken: "rotated-token",
      refreshTokenExpiresAt: 2
    });
  });

  it("does not honour an unprotected legacy credential when the prompt is refused", async () => {
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedReadCredential.mockResolvedValue({ status: "missing" });
    mockedGetLegacy.mockResolvedValue(credentialFor(USER, "legacy-token"));
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: false, error: "user_cancel" } as never);
    expect(await authenticateWithBiometrics()).toEqual({ outcome: "cancelled" });
    expect(mockedSetSessionEnvelope).not.toHaveBeenCalled();
    expect(mockedRestoreSession).not.toHaveBeenCalled();
  });

  it("maps a biometric lockout to its own outcome so the UI can explain it", async () => {
    mockedGetBiometricUserId.mockResolvedValue(USER);
    mockedReadCredential.mockResolvedValue({ status: "missing" });
    mockedGetLegacy.mockResolvedValue(credentialFor(USER));
    mockedLocalAuth.authenticateAsync.mockResolvedValue({ success: false, error: "lockout" } as never);
    expect(await authenticateWithBiometrics()).toEqual({ outcome: "lockout" });
  });
});

describe("turning Face ID off", () => {
  // Required test 9.
  it("removes the credential and the enrollment together", async () => {
    await disableBiometricLogin();
    expect(mockedClearEnrollment).toHaveBeenCalled();
  });

  it("leaves nothing that a later unlock attempt could use", async () => {
    deviceHasFaceId();
    await disableBiometricLogin();
    mockedGetBiometricUserId.mockResolvedValue(null);
    expect(await authenticateWithBiometrics()).toEqual({ outcome: "no_enrolled_account" });
    expect(await isBiometricEnabledForCurrentSession()).toBe(false);
  });
});
