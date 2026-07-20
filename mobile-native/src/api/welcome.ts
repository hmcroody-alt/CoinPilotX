import { pulseApi, PulseApiError } from "./pulseApi";

export type WelcomeType =
  | "first_login"
  | "welcome_back"
  | "session_return"
  | "version_update"
  | "manual"
  | "generic";

export type WelcomeReducedMotion = "system" | "true" | "false";

export type WelcomeSettings = {
  welcomeExperience: boolean;
  welcomeSound: boolean;
  welcomeHaptics: boolean;
  reducedMotion: WelcomeReducedMotion;
};

export type WelcomeState =
  | { shouldShow: false; reason?: string }
  | {
      shouldShow: true;
      welcomeType: WelcomeType;
      eventId: number;
      name: string;
      title: string;
      body: string;
      subtext: string;
      cta: string;
      animation: string;
      appVersion: string;
      settings: WelcomeSettings;
    };

type WelcomeStateResponse = {
  ok?: boolean;
  should_show?: boolean;
  reason?: string;
  welcome_type?: string;
  event_id?: number;
  name?: string;
  title?: string;
  body?: string;
  subtext?: string;
  cta?: string;
  animation?: string;
  app_version?: string;
  settings?: {
    welcome_experience?: boolean;
    welcome_sound?: boolean;
    welcome_haptics?: boolean;
    reduced_motion?: string;
  };
};

const WELCOME_TYPES: WelcomeType[] = [
  "first_login",
  "welcome_back",
  "session_return",
  "version_update",
  "manual",
  "generic"
];

function normalizeWelcomeType(value?: string): WelcomeType {
  return (WELCOME_TYPES as string[]).includes(value || "") ? (value as WelcomeType) : "generic";
}

function normalizeReducedMotion(value?: string): WelcomeReducedMotion {
  return value === "true" || value === "false" ? value : "system";
}

function normalizeSettings(raw?: WelcomeStateResponse["settings"]): WelcomeSettings {
  return {
    welcomeExperience: raw?.welcome_experience !== false,
    welcomeSound: raw?.welcome_sound === true,
    welcomeHaptics: raw?.welcome_haptics !== false,
    reducedMotion: normalizeReducedMotion(raw?.reduced_motion)
  };
}

// GET recording the impression server-side (the backend claims the event on read).
// Callers must present the returned copy only when shouldShow is true, otherwise
// the impression is wasted against the cooldown window.
export async function fetchWelcomeState(): Promise<WelcomeState> {
  let response: WelcomeStateResponse;
  try {
    response = await pulseApi<WelcomeStateResponse>("/api/pulse/welcome-state", { method: "GET" });
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 401) {
      return { shouldShow: false, reason: "unauthenticated" };
    }
    return { shouldShow: false, reason: "welcome_unavailable" };
  }
  if (!response?.should_show) {
    return { shouldShow: false, reason: response?.reason };
  }
  return {
    shouldShow: true,
    welcomeType: normalizeWelcomeType(response.welcome_type),
    eventId: Number(response.event_id) || 0,
    name: response.name || "",
    title: response.title || "",
    body: response.body || "",
    subtext: response.subtext || "",
    cta: response.cta || "",
    animation: response.animation || "ufo",
    appVersion: response.app_version || "",
    settings: normalizeSettings(response.settings)
  };
}

export async function dismissWelcome(welcomeType: WelcomeType, eventId: number): Promise<boolean> {
  try {
    const result = await pulseApi<{ ok?: boolean; dismissed?: boolean }>("/api/pulse/welcome-dismiss", {
      method: "POST",
      body: JSON.stringify({ welcome_type: welcomeType, event_id: eventId })
    });
    return result?.dismissed !== false;
  } catch {
    return false;
  }
}
