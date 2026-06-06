import * as SecureStore from "expo-secure-store";
import { PULSE_API_BASE_URL } from "../config";

const SESSION_KEY = "pulse.session.cookie";

export type ApiOptions = RequestInit & { auth?: boolean };

export async function getStoredSession() {
  return SecureStore.getItemAsync(SESSION_KEY);
}

export async function setStoredSession(value: string) {
  await SecureStore.setItemAsync(SESSION_KEY, value);
}

export async function clearStoredSession() {
  await SecureStore.deleteItemAsync(SESSION_KEY);
}

export async function pulseApi<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (!(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const session = await getStoredSession();
  if (session) headers.set("Cookie", session);
  const response = await fetch(`${PULSE_API_BASE_URL}${path}`, {
    ...options,
    headers,
    credentials: "include",
  });
  const setCookie = response.headers.get("set-cookie");
  if (setCookie) await setStoredSession(setCookie);
  const text = await response.text();
  let data: any = {};
  try {
    data = text ? JSON.parse(text) : {};
  } catch {
    data = { ok: false, message: "Pulse returned an unreadable response." };
  }
  if (!response.ok || data.ok === false) {
    throw new Error(data.message || data.error || "Pulse request failed.");
  }
  return data as T;
}
