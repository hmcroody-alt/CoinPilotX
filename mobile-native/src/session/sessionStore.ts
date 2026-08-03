import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";
import { PULSE_API_BASE_URL } from "../api/config";

const COOKIE_KEY = "pulsesoc.native.session.cookie";
const SESSION_ENVELOPE_KEY = "pulsesoc.native.session.envelope.v1";
const CACHED_USER_KEY = "pulsesoc.native.session.user";
export const BIOMETRIC_USER_KEY = "pulsesoc.native.session.biometric.userId";
// A refresh token stashed here survives an ordinary "Sign out" so Face ID can
// restore the session next time — but it is deliberately NOT the live envelope,
// so cold-start auto-refresh cannot silently resume the session without Face ID.
const BIOMETRIC_SESSION_KEY = "pulsesoc.native.session.biometric.envelope.v1";
const KEYCHAIN_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY,
  keychainService: __DEV__ ? "com.pulsesoc.nativeapp.dev.session" : "com.pulsesoc.app.session"
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

export async function getBiometricSession(): Promise<BiometricSession | null> {
  const raw = await getSecureValue(BIOMETRIC_SESSION_KEY);
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

export async function setBiometricSession(session: BiometricSession | null) {
  if (!session) return deleteSecureValue(BIOMETRIC_SESSION_KEY);
  await setSecureValue(BIOMETRIC_SESSION_KEY, JSON.stringify(session));
}

export async function getBiometricUserId(): Promise<number | null> {
  const raw = await getSecureValue(BIOMETRIC_USER_KEY);
  const userId = Number(raw || 0);
  return userId > 0 ? userId : null;
}

// Drops the active session (cookie + live envelope) but preserves the biometric
// enrollment key and the Face-ID-gated refresh token, so an ordinary sign-out
// still lets the user return via Face ID.
export async function clearActiveSessionKeepBiometric() {
  await Promise.all([setSessionCookie(""), setSessionEnvelope(null)]);
}

export async function clearNativeSessionCredentials() {
  await Promise.all([
    setSessionCookie(""),
    setSessionEnvelope(null),
    deleteSecureValue(BIOMETRIC_USER_KEY),
    deleteSecureValue(BIOMETRIC_SESSION_KEY)
  ]);
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
  const safeUser = {
    user_id: Number(input.user_id || 0),
    username: String(input.username || ""),
    display_name: String(input.display_name || ""),
    full_name: String(input.full_name || ""),
    avatar_url: String(input.avatar_url || ""),
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
