import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";
import { PULSE_API_BASE_URL } from "../api/config";

const COOKIE_KEY = "pulsesoc.native.session.cookie";

export async function getSessionCookie() {
  if (Platform.OS === "web") return AsyncStorage.getItem(COOKIE_KEY);
  try {
    return await SecureStore.getItemAsync(COOKIE_KEY);
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
    await SecureStore.deleteItemAsync(COOKIE_KEY).catch(async (error) => {
      if (!isLocalQaSession()) throw error;
      await AsyncStorage.removeItem(COOKIE_KEY);
    });
    return;
  }
  await SecureStore.setItemAsync(COOKIE_KEY, cookie).catch(async (error) => {
    if (!isLocalQaSession()) throw error;
    await AsyncStorage.setItem(COOKIE_KEY, cookie);
  });
}

function isLocalQaSession() {
  return /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/i.test(PULSE_API_BASE_URL);
}
