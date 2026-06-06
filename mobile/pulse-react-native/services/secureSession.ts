import * as SecureStore from "expo-secure-store";

const SESSION_COOKIE_KEY = "pulse.session.cookie";
const REFRESH_TOKEN_KEY = "pulse.refresh.token";
let runtimeSessionCookie = "";

export async function getSessionCookie() {
  return (await SecureStore.getItemAsync(SESSION_COOKIE_KEY)) || runtimeSessionCookie;
}

export async function setSessionCookie(cookie: string) {
  runtimeSessionCookie = cookie;
  await SecureStore.setItemAsync(SESSION_COOKIE_KEY, cookie);
}

export async function clearSessionCookie() {
  runtimeSessionCookie = "";
  await SecureStore.deleteItemAsync(SESSION_COOKIE_KEY);
}

export async function clearPersistedSessionCookie() {
  await SecureStore.deleteItemAsync(SESSION_COOKIE_KEY);
}

export async function getRefreshToken() {
  return SecureStore.getItemAsync(REFRESH_TOKEN_KEY);
}

export async function setRefreshToken(token: string) {
  await SecureStore.setItemAsync(REFRESH_TOKEN_KEY, token);
}

export async function clearRefreshToken() {
  await SecureStore.deleteItemAsync(REFRESH_TOKEN_KEY);
}

export async function clearSecureSession() {
  runtimeSessionCookie = "";
  await Promise.all([clearSessionCookie(), clearRefreshToken()]);
}
