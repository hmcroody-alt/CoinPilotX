/**
 * Where Face ID stops being a JavaScript convention and becomes an OS rule.
 *
 * Everything above this layer can be patched, hooked or stepped over by anyone
 * who controls the binary. The one thing that survives that is the keychain
 * access control: `requireAuthentication` makes expo-secure-store attach
 * `.biometryCurrentSet` to the item, so iOS itself refuses to return the refresh
 * token without a live face match and destroys the item when the enrolled
 * biometrics change.
 *
 * These tests assert the options we hand the keychain, because those options —
 * not our control flow — are the security boundary. They also pin the two
 * details that silently un-protect it: leaving the old unauthenticated copy in
 * place (expo-secure-store searches unauthenticated items first, so it would
 * shadow the protected one), and letting a write fall through to SecItemUpdate
 * (which prompts, producing a second Face ID sheet right after the first).
 */
import * as SecureStore from "expo-secure-store";

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(async () => null),
  setItemAsync: jest.fn(async () => undefined),
  deleteItemAsync: jest.fn(async () => undefined),
  AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY: "afterFirstUnlockThisDeviceOnly"
}));

import {
  BIOMETRIC_USER_KEY,
  clearBiometricEnrollment,
  clearNativeSessionCredentials,
  deleteBiometricCredential,
  getLegacyBiometricSession,
  readBiometricCredential,
  writeBiometricCredential
} from "../sessionStore";

const mockedSecureStore = SecureStore as jest.Mocked<typeof SecureStore>;

const CREDENTIAL_KEY = "pulsesoc.native.session.biometric.envelope.v2";
const LEGACY_CREDENTIAL_KEY = "pulsesoc.native.session.biometric.envelope.v1";
const SESSION = { userId: 5, refreshToken: "refresh-1", refreshTokenExpiresAt: 2 };

/** Keys touched by a SecureStore mock call, in order. */
function keysPassedTo(mock: jest.Mock) {
  return mock.mock.calls.map((call) => String(call[0]));
}

function optionsFor(mock: jest.Mock, key: string) {
  const call = mock.mock.calls.find((entry) => entry[0] === key);
  return (call?.[call.length - 1] ?? {}) as SecureStore.SecureStoreOptions;
}

beforeEach(() => {
  jest.clearAllMocks();
  mockedSecureStore.getItemAsync.mockResolvedValue(null);
  mockedSecureStore.setItemAsync.mockResolvedValue(undefined);
  mockedSecureStore.deleteItemAsync.mockResolvedValue(undefined);
});

describe("writeBiometricCredential", () => {
  it("stores the credential behind a biometric access control", async () => {
    expect(await writeBiometricCredential(SESSION)).toBe(true);
    const options = optionsFor(mockedSecureStore.setItemAsync as jest.Mock, CREDENTIAL_KEY);
    // This flag is the entire security claim: without it iOS hands the token to
    // any caller, and our own prompt is the only thing in the way.
    expect(options.requireAuthentication).toBe(true);
    // Device-only, so a restored backup on another phone cannot carry it over.
    expect(options.keychainAccessible).toBe(SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY);
  });

  it("keeps the protected credential on its own keychain service", async () => {
    await writeBiometricCredential(SESSION);
    const credentialService = optionsFor(mockedSecureStore.setItemAsync as jest.Mock, CREDENTIAL_KEY).keychainService;
    // expo-secure-store files authenticated and unauthenticated items under
    // different services and documents that sharing one service between the two
    // is unsupported — so the ordinary session must not sit on this service.
    expect(credentialService).toBeTruthy();
    expect(credentialService).not.toBe(optionsFor(mockedSecureStore.setItemAsync as jest.Mock, BIOMETRIC_USER_KEY).keychainService);
  });

  it("deletes before writing so the keychain never takes the prompting update path", async () => {
    await writeBiometricCredential(SESSION);
    const deletedFirst = (mockedSecureStore.deleteItemAsync as jest.Mock).mock.invocationCallOrder[0];
    const wroteAfter = (mockedSecureStore.setItemAsync as jest.Mock).mock.invocationCallOrder[0];
    expect(deletedFirst).toBeLessThan(wroteAfter);
  });

  it("purges the unprotected predecessor, which would otherwise shadow it on every read", async () => {
    await writeBiometricCredential(SESSION);
    expect(keysPassedTo(mockedSecureStore.deleteItemAsync as jest.Mock)).toContain(LEGACY_CREDENTIAL_KEY);
  });

  it("reports failure instead of swallowing it when the keychain refuses the write", async () => {
    mockedSecureStore.setItemAsync.mockRejectedValue(new Error("keychain unavailable"));
    // Callers use this to decide whether to mark Face ID enabled, so a silent
    // success here is how you end up with a toggle that protects nothing.
    expect(await writeBiometricCredential(SESSION)).toBe(false);
  });
});

describe("readBiometricCredential", () => {
  it("asks the keychain to authenticate, with the prompt the caller supplied", async () => {
    mockedSecureStore.getItemAsync.mockResolvedValue(JSON.stringify(SESSION));
    await readBiometricCredential("Unlock PulseSoc");
    const options = optionsFor(mockedSecureStore.getItemAsync as jest.Mock, CREDENTIAL_KEY);
    expect(options.requireAuthentication).toBe(true);
    expect(options.authenticationPrompt).toBe("Unlock PulseSoc");
  });

  it("returns the credential when the biometric match succeeds", async () => {
    mockedSecureStore.getItemAsync.mockResolvedValue(JSON.stringify(SESSION));
    expect(await readBiometricCredential("Unlock PulseSoc")).toEqual({ status: "unlocked", session: SESSION });
  });

  it("reports missing when iOS has invalidated or never stored the item", async () => {
    // A biometric change makes iOS discard the item; the API surfaces that as a
    // plain null, identical to "never stored". Both mean there is nothing to
    // sign in with, so both must land on the same non-authenticating branch.
    mockedSecureStore.getItemAsync.mockResolvedValue(null);
    expect(await readBiometricCredential("Unlock PulseSoc")).toEqual({ status: "missing" });
  });

  it("reports a cancel as denied-by-cancel rather than as a missing credential", async () => {
    mockedSecureStore.getItemAsync.mockRejectedValue(new Error("User canceled the operation."));
    expect(await readBiometricCredential("Unlock PulseSoc")).toEqual({ status: "denied", cancelled: true });
  });

  it("reports a failed match as denied, never as success", async () => {
    mockedSecureStore.getItemAsync.mockRejectedValue(new Error("Authentication failed."));
    expect(await readBiometricCredential("Unlock PulseSoc")).toEqual({ status: "denied", cancelled: false });
  });

  it("rejects a stored blob that carries no usable token", async () => {
    mockedSecureStore.getItemAsync.mockResolvedValue(JSON.stringify({ userId: 5 }));
    expect(await readBiometricCredential("Unlock PulseSoc")).toEqual({ status: "missing" });
  });

  it("does not fall over on a corrupted keychain payload", async () => {
    mockedSecureStore.getItemAsync.mockResolvedValue("{not json");
    expect(await readBiometricCredential("Unlock PulseSoc")).toEqual({ status: "missing" });
  });
});

describe("getLegacyBiometricSession", () => {
  it("reads the pre-migration item without requesting authentication", async () => {
    mockedSecureStore.getItemAsync.mockResolvedValue(JSON.stringify(SESSION));
    expect(await getLegacyBiometricSession()).toEqual(SESSION);
    // Reading it must not prompt — that is exactly why the caller has to run a
    // biometric prompt of its own before honouring the result.
    expect(optionsFor(mockedSecureStore.getItemAsync as jest.Mock, LEGACY_CREDENTIAL_KEY).requireAuthentication).toBeUndefined();
  });
});

describe("removing Face ID from the device", () => {
  it("deletes both the protected credential and its unprotected predecessor", async () => {
    await deleteBiometricCredential();
    const deleted = keysPassedTo(mockedSecureStore.deleteItemAsync as jest.Mock);
    expect(deleted).toContain(CREDENTIAL_KEY);
    expect(deleted).toContain(LEGACY_CREDENTIAL_KEY);
  });

  it("clears the enrollment marker alongside the credential", async () => {
    await clearBiometricEnrollment();
    const deleted = keysPassedTo(mockedSecureStore.deleteItemAsync as jest.Mock);
    expect(deleted).toContain(BIOMETRIC_USER_KEY);
    expect(deleted).toContain(CREDENTIAL_KEY);
  });

  // Account deletion. A surviving biometric refresh token here would let a face
  // scan resume — and thereby cancel — a deletion the user asked for.
  it("leaves no credential of any kind behind on a full credential wipe", async () => {
    await clearNativeSessionCredentials();
    const deleted = keysPassedTo(mockedSecureStore.deleteItemAsync as jest.Mock);
    expect(deleted).toContain(BIOMETRIC_USER_KEY);
    expect(deleted).toContain(CREDENTIAL_KEY);
    expect(deleted).toContain(LEGACY_CREDENTIAL_KEY);
    expect(deleted).toContain("pulsesoc.native.session.cookie");
    expect(deleted).toContain("pulsesoc.native.session.envelope.v1");
  });

  it("does not leave a readable credential after the wipe", async () => {
    await clearNativeSessionCredentials();
    mockedSecureStore.getItemAsync.mockResolvedValue(null);
    expect(await readBiometricCredential("Unlock PulseSoc")).toEqual({ status: "missing" });
    expect(await getLegacyBiometricSession()).toBeNull();
  });
});
