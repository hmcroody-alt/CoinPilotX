import { Platform } from "react-native";
import { PULSE_API_BASE_URL } from "../api/config";
import { RootStackParamList } from "../navigation/types";
import { AuthState, signIn } from "./auth";
import { setSessionCookie } from "./sessionStore";

type CameraStudioParams = NonNullable<RootStackParamList["CameraStudio"]>;

export type QaSimulatorAuthResult = {
  handled: boolean;
  authState?: AuthState;
  cameraRoute?: RootStackParamList["CameraStudio"];
  redirectTarget?: string;
  reason?: string;
};

export function isQaSimulatorAuthEnabled() {
  return __DEV__ && isLocalApiBaseUrl(PULSE_API_BASE_URL);
}

export async function tryHandleQaSimulatorAuthUrl(url: string): Promise<QaSimulatorAuthResult> {
  if (!__DEV__) return { handled: false, reason: "disabled" };

  const parsed = parseQaUrl(url);
  if (!parsed || !isQaLoginUrl(parsed)) {
    return { handled: false, reason: "not_qa_login" };
  }
  const qaApiBase = qaApiBaseFromUrl(parsed);
  if (!isQaSimulatorAuthEnabled() && !qaApiBase) return { handled: false, reason: "disabled" };

  const runtimeCredentials = runtimeWebCredentials();
  const identifier =
    parsed.searchParams.get("identifier") ||
    parsed.searchParams.get("email") ||
    parsed.searchParams.get("username") ||
    runtimeCredentials.identifier ||
    "";
  const password = parsed.searchParams.get("password") || runtimeCredentials.password || "";
  if (!identifier.trim() || !password) return { handled: true, reason: "missing_credentials" };

  const authState = qaApiBase
    ? await signInWithQaApiBase(qaApiBase, identifier.trim(), password)
    : await signIn(identifier.trim(), password);
  return {
    handled: true,
    authState,
    cameraRoute: authState.status === "signedIn" ? cameraRouteFromQaUrl(parsed) : undefined,
    redirectTarget: authState.status === "signedIn" ? safeRedirectTarget(parsed) : undefined
  };
}

async function signInWithQaApiBase(apiBase: string, identifier: string, password: string): Promise<AuthState> {
  const response = await fetch(`${apiBase}/api/mobile/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ identifier, email: identifier, password }),
    credentials: "include"
  });
  const cookie = response.headers.get("set-cookie");
  if (cookie) await setSessionCookie(cookie.split(";")[0] || cookie);
  const data = await response.json().catch(() => ({}));
  if (!response.ok || !data?.authenticated || !data?.user) return { status: "signedOut", user: null };
  return { status: "signedIn", user: data.user };
}

function runtimeWebCredentials() {
  if (Platform.OS !== "web" || typeof window === "undefined") return { identifier: "", password: "" };
  try {
    const identifier = window.sessionStorage.getItem("pulsesoc.qa.identifier") || "";
    const password = window.sessionStorage.getItem("pulsesoc.qa.password") || "";
    window.sessionStorage.removeItem("pulsesoc.qa.identifier");
    window.sessionStorage.removeItem("pulsesoc.qa.password");
    return { identifier, password };
  } catch {
    return { identifier: "", password: "" };
  }
}

function isQaLoginUrl(parsed: URL) {
  if (Platform.OS === "web") {
    return isLocalWebHost(parsed.hostname) && parsed.pathname === "/qa/simulator-login";
  }
  return parsed.hostname === "qa" && parsed.pathname === "/simulator-login";
}

function isLocalApiBaseUrl(value: string) {
  try {
    const parsed = new URL(value);
    return ["127.0.0.1", "localhost", "::1"].includes(parsed.hostname);
  } catch {
    return false;
  }
}

function qaApiBaseFromUrl(parsed: URL) {
  const value = parsed.searchParams.get("api_base") || parsed.searchParams.get("apiBase") || "";
  if (!value || !isLocalApiBaseUrl(value)) return "";
  return value.trim().replace(/\/+$/, "");
}

function isLocalWebHost(hostname: string) {
  return ["127.0.0.1", "localhost", "::1"].includes(hostname);
}

function parseQaUrl(url: string) {
  try {
    return new URL(url);
  } catch {
    if (url.startsWith("pulsesoc://qa/simulator-login")) {
      try {
        return new URL(url.replace(/^pulsesoc:\/\/qa/, "https://qa"));
      } catch {
        return null;
      }
    }
    return null;
  }
}

function cameraRouteFromQaUrl(parsed: URL): RootStackParamList["CameraStudio"] | undefined {
  const redirect = parsed.searchParams.get("redirect") || "";
  if (redirect && !redirect.startsWith("/pulse/camera")) return undefined;

  const modeFromRedirect = redirect.match(/\/pulse\/camera\/([^/?#]+)/)?.[1];
  const target = normalizeTarget(parsed.searchParams.get("target"));
  const mode = normalizeMode(parsed.searchParams.get("mode") || modeFromRedirect || null);
  const captureMode = normalizeCaptureMode(parsed.searchParams.get("captureMode") || parsed.searchParams.get("capture_mode"));
  const conversationId = Number(parsed.searchParams.get("conversationId") || parsed.searchParams.get("conversation_id") || 0) || undefined;
  const qaMedia = parsed.searchParams.get("qaMedia") === "image" ? "image" : undefined;
  const qaAutoPublish = ["1", "true", "yes"].includes(String(parsed.searchParams.get("qaAutoPublish") || "").toLowerCase());
  const qaCaption = parsed.searchParams.get("qaCaption") || undefined;

  return {
    target: target || "feed",
    mode: mode || "photo",
    captureMode,
    conversationId,
    title: "Camera",
    qaMedia,
    qaAutoPublish,
    qaCaption
  };
}

function safeRedirectTarget(parsed: URL) {
  const redirect = parsed.searchParams.get("redirect") || "";
  if (!redirect || !redirect.startsWith("/") || redirect.startsWith("//") || redirect.includes("\\") || redirect.startsWith("/api/") || redirect.startsWith("/admin/")) {
    return "";
  }
  return redirect.slice(0, 240);
}

function normalizeTarget(value: string | null): CameraStudioParams["target"] | undefined {
  if (
    value === "feed" ||
    value === "post" ||
    value === "status" ||
    value === "reel" ||
    value === "message" ||
    value === "avatar" ||
    value === "cover" ||
    value === "creator" ||
    value === "marketplace"
  ) {
    return value;
  }
  return undefined;
}

function normalizeMode(value: string | null): CameraStudioParams["mode"] | undefined {
  if (value === "photo" || value === "video" || value === "status" || value === "reel") return value;
  return undefined;
}

function normalizeCaptureMode(value: string | null): CameraStudioParams["captureMode"] | undefined {
  if (value === "photo" || value === "video") return value;
  return undefined;
}
