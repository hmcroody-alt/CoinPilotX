import AsyncStorage from "@react-native-async-storage/async-storage";
import * as SecureStore from "expo-secure-store";
import { Platform } from "react-native";

const COOKIE_KEY = "pulsesoc.native.session.cookie";

export async function getSessionCookie() {
  if (Platform.OS === "web") return AsyncStorage.getItem(COOKIE_KEY);
  return SecureStore.getItemAsync(COOKIE_KEY);
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
    await SecureStore.deleteItemAsync(COOKIE_KEY);
    return;
  }
  await SecureStore.setItemAsync(COOKIE_KEY, cookie);
}
