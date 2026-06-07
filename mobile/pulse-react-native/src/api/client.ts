import * as SecureStore from "expo-secure-store";
import { PULSE_API_BASE_URL } from "../config";

const SESSION_COOKIE_KEY = "pulse.session.cookie";

export type PulseApiOptions = RequestInit & {
  skipJsonHeader?: boolean;
};

export class PulseApiError extends Error {
  status: number;
  code?: string;
  traceId?: string;

  constructor(message: string, status: number, code?: string, traceId?: string) {
    super(message);
    this.name = "PulseApiError";
    this.status = status;
    this.code = code;
    this.traceId = traceId;
  }
}

export async function pulseApi<T>(path: string, options: PulseApiOptions = {}): Promise<T> {
  const headers = new Headers(options.headers || {});
  const body = options.body;
  if (!options.skipJsonHeader && !(body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const cookie = await SecureStore.getItemAsync(SESSION_COOKIE_KEY);
  if (cookie) {
    headers.set("Cookie", cookie);
  }
  const response = await fetch(toApiUrl(path), {
    ...options,
    credentials: "include",
    headers
  });
  const setCookie = response.headers.get("set-cookie");
  if (setCookie) {
    await SecureStore.setItemAsync(SESSION_COOKIE_KEY, setCookie);
  }
  const text = await response.text();
  const data = parseJson(text);
  if (!response.ok || data.ok === false) {
    throw new PulseApiError(
      String(data.message || data.error || "PulseSoc request failed."),
      response.status,
      typeof data.error === "string" ? data.error : undefined,
      typeof data.trace_id === "string" ? data.trace_id : undefined
    );
  }
  return data as T;
}

export async function clearPulseSession() {
  await SecureStore.deleteItemAsync(SESSION_COOKIE_KEY);
}

function toApiUrl(path: string) {
  if (/^https?:\/\//.test(path)) return path;
  return `${PULSE_API_BASE_URL}${path.startsWith("/") ? path : `/${path}`}`;
}

function parseJson(text: string): Record<string, unknown> {
  if (!text) return {};
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return { ok: false, message: "PulseSoc returned a response the app could not read." };
  }
}
