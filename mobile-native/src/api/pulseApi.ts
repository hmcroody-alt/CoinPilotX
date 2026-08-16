import { PULSE_API_BASE_URL } from "./config";
import {
  clearNativeSessionCredentials,
  getSessionCookie,
  getSessionEnvelope,
  NativeSessionEnvelope,
  setCachedSessionUser,
  setSessionCookie,
  setSessionEnvelope
} from "../session/sessionStore";
import { shouldRejectTemporaryQaUser } from "../session/qaTemporaryAccount";
import { Platform } from "react-native";
import { startSpan } from "../core/perfTrace";


/**
 * Normalize a request path into a low-cardinality route label for perf aggregation:
 * drop the query string and collapse numeric / long-hash id segments to ":id".
 * Keeps traces free of per-user identifiers while grouping the same endpoint.
 */
function perfRouteLabel(path: string): string {
  const withoutQuery = path.split("?")[0];
  return withoutQuery
    .split("/")
    .map((segment) => (/^\d+$/.test(segment) || /^[0-9a-f]{16,}$/i.test(segment) ? ":id" : segment))
    .join("/")
    .slice(0, 120);
}

export class PulseApiError extends Error {
  status: number;
  code?: string;
  /**
   * Parsed error body. Some endpoints answer a rejection with structured data
   * the caller still needs — a lockout countdown, a retry hint — which would
   * otherwise be lost when the response is turned into a thrown error.
   */
  details?: Record<string, unknown>;

  constructor(message: string, status: number, code?: string, details?: Record<string, unknown>) {
    super(message);
    this.name = "PulseApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

type SessionInvalidation = { path: string; code: string };
export type RefreshResult = "refreshed" | "invalid" | "temporary" | "unavailable";
let refreshPromise: Promise<RefreshResult> | null = null;
let sessionInvalidationHandler: ((event: SessionInvalidation) => void) | null = null;
const inFlightReads = new Map<string, Promise<unknown>>();

export function registerSessionInvalidationHandler(handler: ((event: SessionInvalidation) => void) | null) {
  sessionInvalidationHandler = handler;
  return () => {
    if (sessionInvalidationHandler === handler) sessionInvalidationHandler = null;
  };
}

export async function pulseApi<T>(path: string, options: RequestInit = {}): Promise<T> {
  const method = String(options.method || "GET").toUpperCase();
  const span = startSpan("api.request", { route: perfRouteLabel(path), method });
  try {
    const coalescingKey = readCoalescingKey(path, options, method);
    let request = coalescingKey ? inFlightReads.get(coalescingKey) as Promise<T> | undefined : undefined;
    if (!request) {
      request = pulseApiRequest<T>(path, options, true);
      if (coalescingKey) {
        inFlightReads.set(coalescingKey, request);
        request.then(
          () => { if (inFlightReads.get(coalescingKey) === request) inFlightReads.delete(coalescingKey); },
          () => { if (inFlightReads.get(coalescingKey) === request) inFlightReads.delete(coalescingKey); }
        );
      }
    }
    const result = await request;
    span.end({ ok: true });
    return result;
  } catch (error) {
    span.end({ ok: false, status: error instanceof PulseApiError ? error.status : 0 });
    throw error;
  }
}

/**
 * Components that enter focus together often ask for the same canonical GET in
 * the same frame. Share only the in-flight work; never cache the response here,
 * so server authority, refresh semantics, and explicit reloads stay intact.
 */
function readCoalescingKey(path: string, options: RequestInit, method: string) {
  if (method !== "GET" || options.body || options.signal || options.headers) return "";
  if (options.cache === "no-store" || options.cache === "reload") return "";
  return path;
}

async function pulseApiRequest<T>(path: string, options: RequestInit, allowRefresh: boolean): Promise<T> {
  const headers = new Headers(options.headers || {});
  const body = options.body;
  if (!(body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  let [cookie, envelope] = await Promise.all([getSessionCookie(), getSessionEnvelope()]);
  if (allowRefresh && shouldRefresh(path) && envelope?.refreshToken && (!envelope.accessToken || envelope.accessTokenExpiresAt <= Date.now() + 5000)) {
    const refreshResult = await refreshNativeSession(cookie || "");
    if (refreshResult === "refreshed") {
      [cookie, envelope] = await Promise.all([getSessionCookie(), getSessionEnvelope()]);
    }
  }
  if (envelope?.accessToken && envelope.accessTokenExpiresAt > Date.now() + 5000 && !headers.has("Authorization")) {
    headers.set("Authorization", `Bearer ${envelope.accessToken}`);
  }
  if (cookie && Platform.OS !== "web") headers.set("Cookie", cookie);

  let response: Response;
  const timeout = requestTimeout(options);
  try {
    response = await fetch(`${PULSE_API_BASE_URL}${path}`, {
      ...options,
      headers,
      credentials: "include",
      signal: timeout.signal
    });
  } catch (fetchError) {
    // A silently stalled socket is indistinguishable from a slow one to the
    // user: both render as a spinner that never resolves. Report the timeout as
    // the same reachability failure every caller already handles, so screens
    // fall back to cache and offer retry instead of hanging forever.
    //
    // Only the budget expiring counts as a timeout. An `AbortError` on its own
    // does not: we forward the caller's `signal` into our controller, so a
    // screen teardown or a superseded search query also surfaces as one. Reading
    // the name here told a user who had just navigated away that PulseSoc "took
    // too long to respond", and reclassified every caller cancellation. The
    // expiry flag is set synchronously in the timer before it aborts, so it is
    // the reliable discriminator, and caller cancellation keeps the exact
    // classification it had before the budget existed.
    const timedOut = timeout.timedOut();
    throw new PulseApiError(
      timedOut
        ? "PulseSoc took too long to respond. Check your connection and try again."
        : "PulseSoc could not be reached. Check your connection and try again.",
      503,
      timedOut ? "request_timeout" : "request_unreachable"
    );
  } finally {
    timeout.clear();
  }

  const responseCookie = response.headers.get("set-cookie");
  if (responseCookie) await setSessionCookie(mergeSessionCookies(cookie || "", responseCookie));

  if (response.status === 401 && allowRefresh && shouldRefresh(path)) {
    const refreshResult = await refreshNativeSession(cookie || "");
    if (refreshResult === "refreshed") return pulseApiRequest<T>(path, options, false);
    if (refreshResult === "temporary") {
      console.warn("PULSESOC_SESSION_REFRESH_TEMPORARY", { path, status: response.status });
      throw new PulseApiError("PulseSoc could not restore your session yet. Your secure sign-in was preserved.", 503, "session_refresh_temporary");
    }
    if (refreshResult === "invalid" || refreshResult === "unavailable") {
      await setCachedSessionUser(null);
      console.warn("PULSESOC_SESSION_INVALID", { path, status: response.status });
      sessionInvalidationHandler?.({ path, code: "session_expired" });
    }
  }

  const text = await response.text();
  const data = parseJson(text);
  if (!response.ok || data.ok === false) {
    throw new PulseApiError(
      String(data.message || data.error || "PulseSoc request failed."),
      response.status,
      typeof data.error_code === "string" ? data.error_code : typeof data.error === "string" ? data.error : undefined,
      data && typeof data === "object" ? (data as Record<string, unknown>) : undefined
    );
  }

  return data as T;
}

/** Ordinary JSON call. Generous enough to survive a slow cellular handshake. */
const REQUEST_TIMEOUT_MS = 20000;
/**
 * Uploads are bounded too, but on a budget that reflects the payload: a short
 * timeout here would cancel a legitimate video upload on a slow connection,
 * which is a data-loss bug wearing a performance costume.
 */
const UPLOAD_TIMEOUT_MS = 180000;

/**
 * Bounds a request so a stalled socket cannot hold a screen in a permanent
 * loading state. A caller-supplied `signal` still wins — its abort is forwarded
 * — so explicit cancellation (screen teardown, superseded search query) keeps
 * working and is not replaced by this budget.
 */
function requestTimeout(options: RequestInit) {
  const controller = new AbortController();
  const budget = options.body instanceof FormData ? UPLOAD_TIMEOUT_MS : REQUEST_TIMEOUT_MS;
  let expired = false;
  const timer = setTimeout(() => {
    expired = true;
    controller.abort();
  }, budget);

  const caller = options.signal;
  const forwardAbort = () => controller.abort();
  if (caller) {
    if (caller.aborted) controller.abort();
    else caller.addEventListener?.("abort", forwardAbort);
  }

  return {
    signal: controller.signal,
    timedOut: () => expired,
    clear: () => {
      clearTimeout(timer);
      caller?.removeEventListener?.("abort", forwardAbort);
    }
  };
}

function shouldRefresh(path: string) {
  return !path.startsWith("/api/mobile/auth/") && !path.startsWith("/api/pulse/mobile/auth/");
}

export async function recoverNativeSession(): Promise<RefreshResult> {
  const cookie = await getSessionCookie();
  return refreshNativeSession(cookie || "");
}

async function refreshNativeSession(cookie: string): Promise<RefreshResult> {
  if (refreshPromise) return refreshPromise;
  refreshPromise = performNativeSessionRefresh(cookie).finally(() => {
    refreshPromise = null;
  });
  return refreshPromise;
}

async function performNativeSessionRefresh(cookie: string): Promise<RefreshResult> {
  try {
    const envelope = await getSessionEnvelope();
    if (!envelope?.refreshToken && !cookie) return "unavailable";
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (Platform.OS !== "web") headers.Cookie = cookie;
    const response = await fetch(`${PULSE_API_BASE_URL}/api/mobile/auth/refresh`, {
      method: "POST",
      headers,
      credentials: "include",
      body: JSON.stringify({ refresh_token: envelope?.refreshToken || undefined, source: "native_automatic_refresh" })
    });
    if (!response.ok) {
      if (response.status === 401 || response.status === 403) {
        await clearNativeSessionCredentials();
        await setCachedSessionUser(null);
        return "invalid";
      }
      return "temporary";
    }
    const data = parseJson(await response.text());
    const user = data.user as Record<string, unknown> | undefined;
    const userId = Number(user?.user_id ?? user?.id ?? 0);
    if (data.authenticated !== true || userId <= 0 || !data.refresh_token) return "temporary";
    if (shouldRejectTemporaryQaUser(user)) {
      await clearNativeSessionCredentials();
      await setCachedSessionUser(null);
      return "invalid";
    }
    if (envelope?.userId && envelope.userId !== userId) {
      await clearNativeSessionCredentials();
      await setCachedSessionUser(null);
      return "invalid";
    }
    const now = Date.now();
    const nextEnvelope: NativeSessionEnvelope = {
      version: 1,
      userId,
      accessToken: String(data.access_token || ""),
      accessTokenExpiresAt: now + Number(data.access_token_expires_in || 0) * 1000,
      refreshToken: String(data.refresh_token),
      refreshTokenExpiresAt: now + Number(data.refresh_token_expires_in || 0) * 1000
    };
    const next = response.headers.get("set-cookie");
    const merged = next ? mergeSessionCookies(cookie, next) : cookie;
    await setSessionEnvelope(nextEnvelope);
    await Promise.all([merged ? setSessionCookie(merged) : Promise.resolve(), setCachedSessionUser(user)]);
    return "refreshed";
  } catch {
    return "temporary";
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
