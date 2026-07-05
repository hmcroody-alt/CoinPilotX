import { Platform } from "react-native";
import { PULSE_API_BASE_URL } from "../api/config";
import { RootStackParamList } from "../navigation/types";
import { AuthState, signIn } from "./auth";

type CameraStudioParams = NonNullable<RootStackParamList["CameraStudio"]>;

export type QaSimulatorAuthResult = {
  handled: boolean;
  authState?: AuthState;
  cameraRoute?: RootStackParamList["CameraStudio"];
  reason?: string;
};

export function isQaSimulatorAuthEnabled() {
  return __DEV__ && Platform.OS !== "web" && isLocalApiBaseUrl(PULSE_API_BASE_URL);
}

export async function tryHandleQaSimulatorAuthUrl(url: string): Promise<QaSimulatorAuthResult> {
  if (!isQaSimulatorAuthEnabled()) return { handled: false, reason: "disabled" };

  const parsed = parseQaUrl(url);
  if (!parsed || parsed.hostname !== "qa" || parsed.pathname !== "/simulator-login") {
    return { handled: false, reason: "not_qa_login" };
  }

  const identifier = parsed.searchParams.get("identifier") || parsed.searchParams.get("email") || parsed.searchParams.get("username") || "";
  const password = parsed.searchParams.get("password") || "";
  if (!identifier.trim() || !password) return { handled: true, reason: "missing_credentials" };

  const authState = await signIn(identifier.trim(), password);
  return {
    handled: true,
    authState,
    cameraRoute: authState.status === "signedIn" ? cameraRouteFromQaUrl(parsed) : undefined
  };
}

function isLocalApiBaseUrl(value: string) {
  try {
    const parsed = new URL(value);
    return ["127.0.0.1", "localhost", "::1"].includes(parsed.hostname);
  } catch {
    return false;
  }
}

function parseQaUrl(url: string) {
  try {
    return new URL(url);
  } catch {
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

  return {
    target: target || "feed",
    mode: mode || "photo",
    captureMode,
    conversationId,
    title: "Camera"
  };
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
