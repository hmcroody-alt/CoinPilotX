import * as SecureStore from "expo-secure-store";

const COOKIE_KEY = "pulsesoc.native.session.cookie";

export async function getSessionCookie() {
  return SecureStore.getItemAsync(COOKIE_KEY);
}

export async function setSessionCookie(cookie: string) {
  if (!cookie) {
    await SecureStore.deleteItemAsync(COOKIE_KEY);
    return;
  }
  await SecureStore.setItemAsync(COOKIE_KEY, cookie);
}
