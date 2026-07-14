import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";
import { PULSE_API_BASE_URL } from "../api/config";

const COOKIE_KEY = "pulsesoc.native.session.cookie";
const CACHED_USER_KEY = "pulsesoc.native.session.user";
const KEYCHAIN_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.AFTER_FIRST_UNLOCK_THIS_DEVICE_ONLY
};

export async function getSessionCookie() {
  if (Platform.OS === "web") return AsyncStorage.getItem(COOKIE_KEY);
  try {
    return await SecureStore.getItemAsync(COOKIE_KEY, KEYCHAIN_OPTIONS);
  } catch (error) {
    if (!isLocalQaSession()) throw error;
    return AsyncStorage.getItem(COOKIE_KEY);
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
    await SecureStore.deleteItemAsync(COOKIE_KEY, KEYCHAIN_OPTIONS).catch(async (error) => {
      if (!isLocalQaSession()) throw error;
      await AsyncStorage.removeItem(COOKIE_KEY);
    });
    return;
  }
  await SecureStore.setItemAsync(COOKIE_KEY, cookie, KEYCHAIN_OPTIONS).catch(async (error) => {
    if (!isLocalQaSession()) throw error;
    await AsyncStorage.setItem(COOKIE_KEY, cookie);
  });
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
