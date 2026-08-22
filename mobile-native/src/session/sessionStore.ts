import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";
import { PULSE_API_BASE_URL } from "../api/config";

const COOKIE_KEY = "pulsesoc.native.session.cookie";
const SESSION_ENVELOPE_KEY = "pulsesoc.native.session.envelope.v1";
const CACHED_USER_KEY = "pulsesoc.native.session.user";
/**
 * Enrollment marker: which account, if any, has Face ID armed on this device.
 *
 * It is deliberately *not* protected by biometrics, because Settings and the
 * sign-in screen have to be able to ask "is Face ID on?" without putting a Face
 * ID sheet in front of the user. That is safe only because of an invariant the
 * rest of this module maintains: the marker is written **after** a credential
 * write has been confirmed, and removed **with** the credential. It therefore
 * answers "does a credential exist", never "may this caller have the token" —
 * no sign-in path consults it in place of the credential itself.
 */
export const BIOMETRIC_USER_KEY = "pulsesoc.native.session.biometric.userId";
// v1 of the Face-ID refresh token: correct in every respect except that the
// keychain handed it back to anyone who asked. Kept read-only so devices
// enrolled before v2 migrate on their next unlock instead of being silently
// un-enrolled and forced to set Face ID up again.
const LEGACY_BIOMETRIC_SESSION_KEY = "pulsesoc.native.session.biometric.envelope.v1";
// v2: the same refresh token, written with `requireAuthentication`, which makes
// expo-secure-store attach a `.biometryCurrentSet` SecAccessControl to the
// keychain item. That single flag is what makes Face ID here real rather than
// decorative:
//   * iOS — not our JavaScript — refuses to return the token without a live
//     face match, so patching out the app's own prompt gains an attacker
//     nothing; and
//   * iOS discards the item outright when the enrolled biometric set changes,
//     so adding a face to the device invalidates the saved sign-in for free.
// A refresh token stashed here survives an ordinary "Sign out" so Face ID can
// restore the session next time — but it is deliberately NOT the live envelope,
// so cold-start auto-refresh cannot silently resume the session without Face ID.
const BIOMETRIC_SESSION_KEY = "pulsesoc.native.session.biometric.envelope.v2";
const KEYCHAIN_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
  keychainService: __DEV__ ? "com.pulsesoc.nativeapp.dev.session" : "com.pulsesoc.app.session"
};
// The biometric credential does not share `KEYCHAIN_OPTIONS`. expo-secure-store
// files authenticated and unauthenticated items under different keychain
// services and documents that mixing the two on one service is unsupported, so
// the protected credential gets a service of its own.
const BIOMETRIC_KEYCHAIN_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
  keychainService: __DEV__ ? "com.pulsesoc.nativeapp.dev.biometric" : "com.pulsesoc.app.biometric",
  requireAuthentication: true
};

export type NativeSessionEnvelope = {
  version: 1;
  userId: number;
  accessToken: string;
  accessTokenExpiresAt: number;
  refreshToken: string;
  refreshTokenExpiresAt: number;
};

export async function getSessionCookie() {
  if (Platform.OS === "web") return AsyncStorage.getItem(COOKIE_KEY);
  try {
    return await SecureStore.getItemAsync(COOKIE_KEY, KEYCHAIN_OPTIONS);
  } catch (error) {
    if (isLocalQaSession()) return AsyncStorage.getItem(COOKIE_KEY);
    // Keychain unreadable (e.g. an adhoc/simulator build lacks the
    // keychain-access-groups entitlement → -34018, or a transient fault).
    // Degrade to signed-out instead of rejecting startup: a re-login is a far
    // better failure mode than a fatal "couldn't start PulseSoc" screen.
    return null;
  }
}

export async function setSessionCookie(cookie: string) {
  if (Platform.OS === "web") {
    if (!cookie) {
      await AsyncStorage.removeItem(COOKIE_KEY);
      return;
    }
    await AsyncStorage.setItem(COOKIE_KEY, cookie);
    return;
  }
  if (!cookie) {
    await SecureStore.deleteItemAsync(COOKIE_KEY, KEYCHAIN_OPTIONS).catch(async () => {
      if (isLocalQaSession()) await AsyncStorage.removeItem(COOKIE_KEY);
      // Off-QA: nothing persisted when the keychain is unavailable — swallow.
    });
    return;
  }
  await SecureStore.setItemAsync(COOKIE_KEY, cookie, KEYCHAIN_OPTIONS).catch(async () => {
    if (isLocalQaSession()) await AsyncStorage.setItem(COOKIE_KEY, cookie);
    // Off-QA: session won't persist across cold start; do not write plaintext.
  });
}

export async function getSessionEnvelope(): Promise<NativeSessionEnvelope | null> {
  const raw = await getSecureValue(SESSION_ENVELOPE_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<NativeSessionEnvelope>;
    if (value.version !== 1 || !value.refreshToken || Number(value.userId || 0) <= 0) return null;
    return {
      version: 1,
      userId: Number(value.userId),
      accessToken: String(value.accessToken || ""),
      accessTokenExpiresAt: Number(value.accessTokenExpiresAt || 0),
      refreshToken: String(value.refreshToken),
      refreshTokenExpiresAt: Number(value.refreshTokenExpiresAt || 0)
    };
  } catch {
    return null;
  }
}

export async function setSessionEnvelope(envelope: NativeSessionEnvelope | null) {
  if (!envelope) return deleteSecureValue(SESSION_ENVELOPE_KEY);
  await setSecureValue(SESSION_ENVELOPE_KEY, JSON.stringify(envelope));
}

export type BiometricSession = {
  userId: number;
  refreshToken: string;
  refreshTokenExpiresAt: number;
};

/**
 * Outcome of reading the biometric-protected credential.
 *
 * `denied` covers both "user cancelled" and "the face did not match". The
 * difference only changes which message we show; it never changes whether the
 * caller may continue. Collapsing them into one non-success branch means no
 * code path can mistake a classification miss for a pass.
 */
export type BiometricCredentialRead =
  | { status: "unlocked"; session: BiometricSession }
  | { status: "missing" }
  | { status: "denied"; cancelled: boolean };

function parseBiometricSession(raw: string | null): BiometricSession | null {
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as Partial<BiometricSession>;
    if (!value.refreshToken || Number(value.userId || 0) <= 0) return null;
    return {
      userId: Number(value.userId),
      refreshToken: String(value.refreshToken),
      refreshTokenExpiresAt: Number(value.refreshTokenExpiresAt || 0)
    };
  } catch {
    return null;
  }
}

/** Keychain rejections that mean "the user backed out", not "the face was wrong". */
function isCancelledKeychainError(error: unknown): boolean {
  const message = error instanceof Error ? error.message : String(error || "");
  return /cancel/i.test(message);
}

/**
 * Read the Face-ID-protected refresh token. **This call is itself the biometric
 * gate**: the item carries a `.biometryCurrentSet` access control, so iOS puts
 * up the authentication sheet and returns the token only on a match. There is
 * no separate JavaScript check for an attacker to skip.
 *
 * A `missing` result is iOS saying either "never stored" or "invalidated
 * because the enrolled biometrics changed" — indistinguishable through this
 * API, and identical in consequence: there is no credential to sign in with.
 */
export async function readBiometricCredential(authenticationPrompt: string): Promise<BiometricCredentialRead> {
  if (Platform.OS === "web") return { status: "missing" };
  try {
    const raw = await SecureStore.getItemAsync(BIOMETRIC_SESSION_KEY, {
      ...BIOMETRIC_KEYCHAIN_OPTIONS,
      authenticationPrompt
    });
    const session = parseBiometricSession(raw);
    return session ? { status: "unlocked", session } : { status: "missing" };
  } catch (error) {
    return { status: "denied", cancelled: isCancelledKeychainError(error) };
  }
}

/**
 * Store the credential behind the access control, reporting whether it landed.
 *
 * Returns `false` rather than throwing or silently continuing, because the
 * caller must not mark Face ID enabled for a credential that was never written.
 */
export async function writeBiometricCredential(session: BiometricSession): Promise<boolean> {
  if (Platform.OS === "web") return false;
  try {
    // Delete-then-add, not a plain set. expo-secure-store's write path falls
    // back to SecItemUpdate when the item already exists, and updating an
    // access-controlled item makes iOS prompt — which would put a *second* Face
    // ID sheet in front of the user immediately after the unlock that got us
    // here. SecItemAdd on a fresh item never prompts.
    await SecureStore.deleteItemAsync(BIOMETRIC_SESSION_KEY, BIOMETRIC_KEYCHAIN_OPTIONS).catch(() => undefined);
    await SecureStore.setItemAsync(BIOMETRIC_SESSION_KEY, JSON.stringify(session), BIOMETRIC_KEYCHAIN_OPTIONS);
  } catch {
    return false;
  }
  // The unprotected v1 copy is a standing bypass if it survives: expo-secure-store
  // searches unauthenticated items *first*, so leaving it behind would let every
  // later read succeed with no prompt at all.
  await deleteSecureValue(LEGACY_BIOMETRIC_SESSION_KEY);
  return true;
}

export async function deleteBiometricCredential() {
  if (Platform.OS === "web") {
    await Promise.all([AsyncStorage.removeItem(BIOMETRIC_SESSION_KEY), AsyncStorage.removeItem(LEGACY_BIOMETRIC_SESSION_KEY)]);
    return;
  }
  await SecureStore.deleteItemAsync(BIOMETRIC_SESSION_KEY, BIOMETRIC_KEYCHAIN_OPTIONS).catch(() => undefined);
  await deleteSecureValue(LEGACY_BIOMETRIC_SESSION_KEY);
}

/**
 * The pre-v2, unprotected credential. Reading it does not prompt, so it is only
 * ever honoured after the caller has run a biometric prompt of its own — see
 * the migration branch in `authenticateWithBiometrics`.
 */
export async function getLegacyBiometricSession(): Promise<BiometricSession | null> {
  return parseBiometricSession(await getSecureValue(LEGACY_BIOMETRIC_SESSION_KEY));
}

export async function getBiometricUserId(): Promise<number | null> {
  const raw = await getSecureValue(BIOMETRIC_USER_KEY);
  const userId = Number(raw || 0);
  return userId > 0 ? userId : null;
}

/** Arm the enrollment marker, reporting failure so enrollment can be rolled back. */
export async function setBiometricUserId(userId: number): Promise<boolean> {
  if (!Number.isFinite(userId) || userId <= 0) return false;
  if (Platform.OS === "web") {
    await AsyncStorage.setItem(BIOMETRIC_USER_KEY, String(userId));
    return true;
  }
  try {
    await SecureStore.setItemAsync(BIOMETRIC_USER_KEY, String(userId), KEYCHAIN_OPTIONS);
    return true;
  } catch {
    return false;
  }
}

export async function clearBiometricEnrollment() {
  await deleteSecureValue(BIOMETRIC_USER_KEY);
  await deleteBiometricCredential();
}

// Drops the active session (cookie + live envelope) but preserves the biometric
// enrollment key and the Face-ID-gated refresh token, so an ordinary sign-out
// still lets the user return via Face ID.
export async function clearActiveSessionKeepBiometric() {
  await Promise.all([setSessionCookie(""), setSessionEnvelope(null)]);
}

/**
 * Remove every PulseSoc credential on this device, biometric included.
 *
 * This is the account-deletion / hard-signout path, so it must leave nothing
 * behind that could resume the account: no cookie, no live refresh token, no
 * Face-ID-protected refresh token, and no enrollment marker claiming Face ID is
 * still armed. (It does not — and cannot — touch Apple's biometric template,
 * which PulseSoc never has access to in the first place.)
 */
export async function clearNativeSessionCredentials() {
  await Promise.all([setSessionCookie(""), setSessionEnvelope(null), clearBiometricEnrollment()]);
}

export async function getCachedSessionUser<T>() {
  try {
    const raw = await AsyncStorage.getItem(CACHED_USER_KEY);
    return raw ? (JSON.parse(raw) as T) : null;
  } catch {
    return null;
  }
}

export async function setCachedSessionUser(user: unknown) {
  if (!user) return AsyncStorage.removeItem(CACHED_USER_KEY);
  const input = user as Record<string, unknown>;
  const userId = Number(input.user_id ?? input.id ?? 0);
  const safeUser = {
    user_id: Number.isFinite(userId) && userId > 0 ? userId : 0,
    username: String(input.username || ""),
    display_name: String(input.display_name || input.full_name || input.username || ""),
    full_name: String(input.full_name || input.display_name || ""),
    avatar_url: String(input.avatar_url || input.avatar_thumbnail_url || ""),
    premium_status: String(input.premium_status || ""),
    account_status: String(input.account_status || "")
  };
  await AsyncStorage.setItem(CACHED_USER_KEY, JSON.stringify(safeUser));
}

function isLocalQaSession() {
  return /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/i.test(PULSE_API_BASE_URL);
}

async function getSecureValue(key: string) {
  if (Platform.OS === "web") return AsyncStorage.getItem(key);
  try {
    return await SecureStore.getItemAsync(key, KEYCHAIN_OPTIONS);
  } catch (error) {
    if (isLocalQaSession()) return AsyncStorage.getItem(key);
    // See getSessionCookie: an unreadable keychain degrades to signed-out
    // rather than throwing and taking down app startup.
    return null;
  }
}

async function setSecureValue(key: string, value: string) {
  if (Platform.OS === "web") return AsyncStorage.setItem(key, value);
  await SecureStore.setItemAsync(key, value, KEYCHAIN_OPTIONS).catch(async () => {
    if (isLocalQaSession()) {
      await AsyncStorage.setItem(key, value);
      return;
    }
    // Keychain unwritable (adhoc/simulator entitlement gap, or transient).
    // Swallow rather than crash: the session simply won't persist across a
    // cold start. We deliberately do NOT fall back to plaintext AsyncStorage
    // off-QA, to avoid writing tokens outside the keychain on a real device.
  });
}

async function deleteSecureValue(key: string) {
  if (Platform.OS === "web") return AsyncStorage.removeItem(key);
  await SecureStore.deleteItemAsync(key, KEYCHAIN_OPTIONS).catch(async () => {
    if (isLocalQaSession()) {
      await AsyncStorage.removeItem(key);
      return;
    }
    // Nothing was persisted off-QA when the keychain is unavailable, so there
    // is nothing to delete — swallow rather than crash sign-out.
  });
}
