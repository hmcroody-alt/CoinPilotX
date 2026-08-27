import { readJsonCache, writeJsonCache } from "../core/cache";

const DRAFT_CACHE_KEY = "pulsesoc.native.live.studio.draft";

export type ReadinessLevel = "ready" | "recommend" | "blocked";
export type NetworkQuality = "excellent" | "good" | "degraded" | "weak" | "offline";
export type ReadinessAction = "request-camera" | "request-mic" | "open-settings" | "retry-network" | "sign-in";

export type ReadinessCheck = {
  key: "camera" | "microphone" | "network" | "battery" | "device" | "account";
  label: string;
  level: ReadinessLevel;
  detail: string;
  action?: ReadinessAction;
};

/**
 * The one word the Live Studio dashboard leads with.
 *
 * Deliberately three states and not five: the readiness *levels* are an
 * engineering vocabulary (`recommend` means "you can broadcast, but read this"),
 * and a creator glancing at the top of the screen only ever needs to know
 * whether they can press the button. `recommend` therefore collapses into
 * `READY` — the recommendations are still spelled out in the readiness card
 * below, where there is room to say what they are.
 */
export type LiveStudioStatus = "READY" | "BLOCKED" | "LIVE";

/**
 * A broadcast already running outranks everything else. If the creator is on
 * air, the dashboard must not tell them their battery is a bit low — it must
 * tell them they are live.
 */
export function deriveLiveStudioStatus(level: ReadinessLevel, hostingLive: boolean): LiveStudioStatus {
  if (hostingLive) return "LIVE";
  return level === "blocked" ? "BLOCKED" : "READY";
}

/**
 * Whether a media-playback owner id belongs to *this* user hosting, as opposed
 * to watching someone else's broadcast. Both claim the coordinator with kind
 * "live", so the kind alone cannot tell the dashboard "you are on air" from
 * "you are in the audience".
 *
 * The prefix is owned by `live/livePlaybackOwnership.ts`; that file is a
 * protected real-time path, so rather than edit it this reads its output. The
 * test pins this against `livePlaybackOwnerId("host", …)` so the two cannot
 * drift apart silently.
 */
export function isLiveHostPlaybackId(id: string | null | undefined): boolean {
  return typeof id === "string" && id.startsWith("live-host:");
}

/**
 * Creator tools that are named on the dashboard but not built yet.
 *
 * They are listed rather than hidden because a control center that shows only
 * what exists reads as finished, and a creator then goes looking elsewhere for
 * scheduling or analytics that are simply not written. Each blurb says what the
 * tool will do, so a row is never just a word with "Soon" next to it.
 *
 * Note the wording on the three that sound like the setup form below: the form
 * already sets audience, comments and replay *for the broadcast you are about
 * to start*. These rows are the durable, cross-broadcast versions — defaults,
 * moderation during a stream, replay retention. Nothing that already works is
 * labelled "Coming soon".
 */
export const LIVE_STUDIO_UPCOMING: ReadonlyArray<{ key: string; label: string; blurb: string }> = Object.freeze([
  { key: "schedule", label: "Schedule Live", blurb: "Announce a broadcast ahead of time and let followers set a reminder." },
  { key: "settings", label: "Live settings", blurb: "Save default title, type and quality so every broadcast starts the same way." },
  { key: "audience", label: "Audience controls", blurb: "Ban, mute and restrict individual viewers while you are on air." },
  { key: "moderation", label: "Moderation tools", blurb: "Word filters, trusted moderators and a queue for reported comments." },
  { key: "analytics", label: "Analytics", blurb: "Peak viewers, watch time and retention for each broadcast you finish." },
  { key: "replay", label: "Replay settings", blurb: "Choose how long replays are kept and who can watch them afterwards." }
]);

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
  /*
   * `recommend`, never `blocked`. A simulator genuinely cannot capture video,
   * but saying so as a blocker turns the whole studio into an error page for
   * every developer and reviewer who opens it — everything else on this screen
   * (setup, drafts, the other checks) works perfectly well here.
   */
  return {
    key: "device",
    label: "Device",
    level: "recommend",
    detail: "Camera capture requires a physical device. Everything else in the studio works here."
  };
}

/**
 * Account eligibility.
 *
 * `signedOut` is the only thing this can honestly assert as a hard blocker
 * today: there is no `can_go_live` flag on the session, and inventing a
 * client-side eligibility rule would either lie to eligible creators or let
 * ineligible ones through to a server rejection. `account_status` is the one
 * real signal the session already carries, so a suspended account is surfaced
 * with the status the backend gave rather than a guess.
 *
 * `loading` is deliberately not a blocker. Bootstrap resolves in well under a
 * second, and treating it as blocked would flash BLOCKED across the top of the
 * dashboard on every cold open.
 */
export function mapAccountToReadiness(
  status: "loading" | "signedIn" | "signedOut",
  accountStatus?: string | null
): ReadinessCheck {
  if (status === "loading") {
    return { key: "account", label: "Account", level: "recommend", detail: "Checking your account…" };
  }
  if (status === "signedOut") {
    return {
      key: "account",
      label: "Account",
      level: "blocked",
      detail: "Sign in to PulseSoc to broadcast.",
      action: "sign-in"
    };
  }
  const normalized = String(accountStatus || "active").trim().toLowerCase();
  if (normalized && normalized !== "active") {
    return {
      key: "account",
      label: "Account",
      level: "blocked",
      detail: `Your account is ${normalized} and cannot broadcast. Contact support to restore it.`
    };
  }
  return { key: "account", label: "Account", level: "ready", detail: "Your account can broadcast." };
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
