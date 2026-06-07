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
    await setSessionCookie(responseCookie);
  }

  const text = await response.text();
  const data = parseJson(text);
  if (!response.ok || data.ok === false) {
    throw new PulseApiError(
      String(data.message || "PulseSoc request failed."),
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
    return { ok: false, message: "PulseSoc returned a non-JSON response." };
  }
}

export function isOfflineError(error: unknown) {
  if (error instanceof PulseApiError) return false;
  return error instanceof TypeError || String(error).toLowerCase().includes("network request failed");
}
