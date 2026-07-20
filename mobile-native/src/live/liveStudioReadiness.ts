import { readJsonCache, writeJsonCache } from "../core/cache";

const DRAFT_CACHE_KEY = "pulsesoc.native.live.studio.draft";

export type ReadinessLevel = "ready" | "recommend" | "blocked";
export type NetworkQuality = "excellent" | "good" | "degraded" | "weak" | "offline";
export type ReadinessAction = "request-camera" | "request-mic" | "open-settings" | "retry-network";

export type ReadinessCheck = {
  key: "camera" | "microphone" | "network" | "battery" | "device";
  label: string;
  level: ReadinessLevel;
  detail: string;
  action?: ReadinessAction;
};

export type LiveTypeKey =
  | "solo"
  | "guest"
  | "interview"
  | "podcast"
  | "panel"
  | "music"
  | "gaming"
  | "shopping"
  | "class"
  | "community";

export type LiveAudience = "public" | "followers" | "subscribers" | "private";

export type LiveStudioDraft = {
  title: string;
  description: string;
  liveType: LiveTypeKey;
  audience: LiveAudience;
  allowComments: boolean;
  recordReplay: boolean;
  updatedAt: string;
};

export const LIVE_TYPE_OPTIONS: Array<{ key: LiveTypeKey; label: string; helper: string }> = [
  { key: "solo", label: "Solo", helper: "Just you on camera" },
  { key: "guest", label: "Multi-Guest", helper: "Invite co-hosts and guests" },
  { key: "interview", label: "Interview", helper: "One-on-one conversation" },
  { key: "podcast", label: "Podcast", helper: "Audio-forward session" },
  { key: "panel", label: "Panel", helper: "Group discussion" },
  { key: "music", label: "Music", helper: "Performance or listening" },
  { key: "gaming", label: "Gaming", helper: "Screen or gameplay share" },
  { key: "shopping", label: "Shopping", helper: "Feature products live" },
  { key: "class", label: "Class", helper: "Workshop or lesson" },
  { key: "community", label: "Community", helper: "Open community stage" }
];

export const AUDIENCE_OPTIONS: Array<{ key: LiveAudience; label: string; helper: string }> = [
  { key: "public", label: "Public", helper: "Anyone on PulseSoc" },
  { key: "followers", label: "Followers", helper: "People who follow you" },
  { key: "subscribers", label: "Subscribers", helper: "Paying subscribers only" },
  { key: "private", label: "Private", helper: "Only people you invite" }
];

const LIVE_TYPE_KEYS = new Set(LIVE_TYPE_OPTIONS.map((option) => option.key));
const AUDIENCE_KEYS = new Set(AUDIENCE_OPTIONS.map((option) => option.key));

export function emptyLiveStudioDraft(): LiveStudioDraft {
  return {
    title: "",
    description: "",
    liveType: "solo",
    audience: "public",
    allowComments: true,
    recordReplay: true,
    updatedAt: ""
  };
}

export function normalizeLiveStudioDraft(value: Partial<LiveStudioDraft> | null | undefined): LiveStudioDraft {
  const base = emptyLiveStudioDraft();
  if (!value) return base;
  const liveType = String(value.liveType || "") as LiveTypeKey;
  const audience = String(value.audience || "") as LiveAudience;
  return {
    title: String(value.title || "").slice(0, 120),
    description: String(value.description || "").slice(0, 500),
    liveType: LIVE_TYPE_KEYS.has(liveType) ? liveType : base.liveType,
    audience: AUDIENCE_KEYS.has(audience) ? audience : base.audience,
    allowComments: value.allowComments === undefined ? base.allowComments : Boolean(value.allowComments),
    recordReplay: value.recordReplay === undefined ? base.recordReplay : Boolean(value.recordReplay),
    updatedAt: String(value.updatedAt || "")
  };
}

export async function loadLiveStudioDraft(): Promise<LiveStudioDraft> {
  const cached = await readJsonCache<LiveStudioDraft>(DRAFT_CACHE_KEY, normalizeLiveStudioDraft);
  return cached || emptyLiveStudioDraft();
}

export async function saveLiveStudioDraft(draft: LiveStudioDraft): Promise<LiveStudioDraft> {
  const next = normalizeLiveStudioDraft({ ...draft, updatedAt: new Date().toISOString() });
  await writeJsonCache(DRAFT_CACHE_KEY, next).catch(() => undefined);
  return next;
}

export function mapPermissionToReadiness(
  kind: "camera" | "microphone",
  granted: boolean,
  canAskAgain: boolean
): ReadinessCheck {
  const label = kind === "camera" ? "Camera" : "Microphone";
  if (granted) {
    return { key: kind, label, level: "ready", detail: `${label} access granted.` };
  }
  if (canAskAgain) {
    return {
      key: kind,
      label,
      level: "blocked",
      detail: `${label} access is required to broadcast.`,
      action: kind === "camera" ? "request-camera" : "request-mic"
    };
  }
  return {
    key: kind,
    label,
    level: "blocked",
    detail: `Enable ${label} in Settings to broadcast.`,
    action: "open-settings"
  };
}

export function mapLatencyToNetwork(latencyMs: number | null): {
  quality: NetworkQuality;
  check: ReadinessCheck;
} {
  if (latencyMs === null || !Number.isFinite(latencyMs)) {
    return {
      quality: "offline",
      check: {
        key: "network",
        label: "Network",
        level: "blocked",
        detail: "No connection to PulseSoc. Broadcasting cannot start.",
        action: "retry-network"
      }
    };
  }
  const ms = Math.max(0, Math.round(latencyMs));
  if (ms <= 150) {
    return { quality: "excellent", check: { key: "network", label: "Network", level: "ready", detail: `Excellent connection (${ms}ms).` } };
  }
  if (ms <= 400) {
    return { quality: "good", check: { key: "network", label: "Network", level: "ready", detail: `Good connection (${ms}ms).` } };
  }
  if (ms <= 900) {
    return {
      quality: "degraded",
      check: { key: "network", label: "Network", level: "recommend", detail: `High latency (${ms}ms). Viewers may see buffering.`, action: "retry-network" }
    };
  }
  return {
    quality: "weak",
    check: { key: "network", label: "Network", level: "recommend", detail: `Very high latency (${ms}ms). Consider a stronger network.`, action: "retry-network" }
  };
}

export function mapBatteryToReadiness(level: number | null, lowPowerMode: boolean): ReadinessCheck {
  if (level === null || !Number.isFinite(level)) {
    return { key: "battery", label: "Battery", level: "ready", detail: "Battery status unavailable." };
  }
  const percent = Math.round(Math.max(0, Math.min(1, level)) * 100);
  if (percent < 15) {
    return { key: "battery", label: "Battery", level: "recommend", detail: `Battery low (${percent}%). Plug in before a long broadcast.` };
  }
  if (lowPowerMode) {
    return { key: "battery", label: "Battery", level: "recommend", detail: `Low Power Mode is on (${percent}%). It may reduce capture quality.` };
  }
  return { key: "battery", label: "Battery", level: "ready", detail: `Battery at ${percent}%.` };
}

export function mapDeviceToReadiness(isRealDevice: boolean): ReadinessCheck {
  if (isRealDevice) {
    return { key: "device", label: "Device", level: "ready", detail: "Running on a physical device." };
  }
  return {
    key: "device",
    label: "Device",
    level: "recommend",
    detail: "Simulator detected. Camera capture and broadcasting need a physical device."
  };
}

export function computeOverallReadiness(checks: ReadinessCheck[]): ReadinessLevel {
  if (checks.some((check) => check.level === "blocked")) return "blocked";
  if (checks.some((check) => check.level === "recommend")) return "recommend";
  return "ready";
}

export function readinessSummary(level: ReadinessLevel): { label: string; detail: string } {
  if (level === "blocked") {
    return { label: "Blocked", detail: "Resolve the blocked checks below before going live." };
  }
  if (level === "recommend") {
    return { label: "Ready with recommendations", detail: "You can go live, but review the recommendations below." };
  }
  return { label: "Ready", detail: "Your studio is ready to broadcast." };
}
