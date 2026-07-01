import { PULSE_API_BASE_URL } from "./config";
import { getSessionCookie, setSessionCookie } from "./secureSession";

export type ApiRequestOptions = RequestInit & {
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

export async function pulseApi<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
  const headers = new Headers(options.headers || {});
  const body = options.body;
  if (!options.skipJsonHeader && !(body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const cookie = await getSessionCookie();
  if (cookie) headers.set("Cookie", cookie);

  const response = await fetch(toApiUrl(path), {
    ...options,
    headers,
    credentials: "include"
  });

  const responseCookie = response.headers.get("set-cookie");
  if (responseCookie) {
    await setSessionCookie(mergeSessionCookies(cookie, responseCookie));
  }

  const text = await response.text();
  const data = parseJson(text);
  if (!response.ok || data.ok === false) {
    throw new PulseApiError(
      String(data.message || "Pulse request failed."),
      response.status,
      typeof data.error === "string" ? data.error : undefined,
      typeof data.trace_id === "string" ? data.trace_id : undefined
    );
  }
  return data as T;
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
    return { ok: false, message: "Pulse returned a non-JSON response." };
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

export function isOfflineError(error: unknown) {
  if (error instanceof PulseApiError) return false;
  return error instanceof TypeError || String(error).toLowerCase().includes("network request failed");
}
