import { PULSE_API_BASE_URL } from "./config";
import { getSessionCookie, setSessionCookie } from "../session/sessionStore";
import { Platform } from "react-native";

export class PulseApiError extends Error {
  status: number;
  code?: string;

  constructor(message: string, status: number, code?: string) {
    super(message);
    this.name = "PulseApiError";
    this.status = status;
    this.code = code;
  }
}

export async function pulseApi<T>(path: string, options: RequestInit = {}): Promise<T> {
  return pulseApiRequest<T>(path, options, true);
}

async function pulseApiRequest<T>(path: string, options: RequestInit, allowRefresh: boolean): Promise<T> {
  const headers = new Headers(options.headers || {});
  const body = options.body;
  if (!(body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const cookie = await getSessionCookie();
  if (cookie && Platform.OS !== "web") headers.set("Cookie", cookie);

  let response: Response;
  try {
    response = await fetch(`${PULSE_API_BASE_URL}${path}`, {
      ...options,
      headers,
      credentials: "include"
    });
  } catch {
    throw new PulseApiError("PulseSoc could not be reached. Check your connection and try again.", 503, "request_unreachable");
  }

  const responseCookie = response.headers.get("set-cookie");
  if (responseCookie) await setSessionCookie(mergeSessionCookies(cookie || "", responseCookie));

  if (response.status === 401 && allowRefresh && cookie && shouldRefresh(path)) {
    const refreshedCookie = await refreshNativeSession(cookie);
    if (refreshedCookie) return pulseApiRequest<T>(path, options, false);
  }

  const text = await response.text();
  const data = parseJson(text);
  if (!response.ok || data.ok === false) {
    throw new PulseApiError(
      String(data.message || data.error || "PulseSoc request failed."),
      response.status,
      typeof data.error_code === "string" ? data.error_code : undefined
    );
  }

  return data as T;
}

function shouldRefresh(path: string) {
  return !path.startsWith("/api/mobile/auth/") && !path.startsWith("/api/pulse/mobile/auth/");
}

async function refreshNativeSession(cookie: string) {
  try {
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (Platform.OS !== "web") headers.Cookie = cookie;
    const response = await fetch(`${PULSE_API_BASE_URL}/api/mobile/auth/refresh`, {
      method: "POST",
      headers,
      credentials: "include",
      body: JSON.stringify({ source: "native_automatic_refresh" })
    });
    if (!response.ok) {
      await setSessionCookie("");
      return "";
    }
    const next = response.headers.get("set-cookie");
    const merged = next ? mergeSessionCookies(cookie, next) : cookie;
    await setSessionCookie(merged);
    return merged;
  } catch {
    return "";
  }
}

function parseJson(text: string): Record<string, unknown> {
  if (!text) return {};
  try {
    return JSON.parse(text) as Record<string, unknown>;
  } catch {
    return { ok: false, message: "PulseSoc returned a non-JSON response." };
  }
}

function mergeSessionCookies(existingCookie: string, setCookieHeader: string) {
  const cookies = new Map<string, string>();
  existingCookie
    .split(";")
    .map((part) => part.trim())
    .filter(Boolean)
    .forEach((part) => {
      const eq = part.indexOf("=");
      if (eq > 0) cookies.set(part.slice(0, eq), part.slice(eq + 1));
    });

  setCookieHeader
    .split(/,(?=\s*[^=;,\s]+=)/)
    .map((part) => part.trim())
    .filter(Boolean)
    .forEach((part) => {
      const pair = part.split(";")[0]?.trim() || "";
      const eq = pair.indexOf("=");
      if (eq <= 0) return;
      const name = pair.slice(0, eq);
      const value = pair.slice(eq + 1);
      if (/max-age=0/i.test(part) || /expires=thu,\s*01\s+jan\s+1970/i.test(part)) {
        cookies.delete(name);
      } else {
        cookies.set(name, value);
      }
    });

  return Array.from(cookies.entries())
    .map(([name, value]) => `${name}=${value}`)
    .join("; ");
}
