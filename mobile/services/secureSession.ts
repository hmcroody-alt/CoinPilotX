import * as SecureStore from "expo-secure-store";

const SESSION_COOKIE_KEY = "pulse.session.cookie";

export async function getSessionCookie() {
  return SecureStore.getItemAsync(SESSION_COOKIE_KEY);
}

export async function setSessionCookie(cookie: string) {
  await SecureStore.setItemAsync(SESSION_COOKIE_KEY, cookie);
}

export async function clearSessionCookie() {
  await SecureStore.deleteItemAsync(SESSION_COOKIE_KEY);
}
