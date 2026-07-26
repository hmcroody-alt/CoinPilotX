import { pulseApi } from "./pulseApi";

/**
 * Client for the unified, server-authoritative presence service
 * (services/presence_service.py exposed at /api/pulse/presence/*).
 *
 * The single rule this module enforces on the client: presence is whatever the
 * server says it is. Nothing here ever synthesises an "online" from a local
 * socket flag, a navigation-param snapshot, or an optimistic assumption. If the
 * server has not confirmed a live session, the user is offline. That is why
 * every normaliser below defaults to offline and every display helper reads
 * `online`/`status`/`activity` straight from the server payload.
 */

export type PresenceStatus = "online" | "away" | "offline";

export type PresenceActivity =
  | "idle"
  | "typing"
  | "recording_voice"
  | "uploading_media"
  | "sending_files"
  | "in_audio_call"
  | "in_video_call"
  | "live_hosting"
  | "live_guest"
  | "live_watching";

/** The viewer-facing payload returned by presence_for on the server. */
export type PulsePresence = {
  user_id: number;
  status: PresenceStatus;
  online: boolean;
  activity: PresenceActivity;
  activity_context: string;
  last_seen_at: string;
  last_seen_text: string;
  devices: number;
  available: boolean;
  self: boolean;
  // Present only when the payload describes the caller's own presence.
  invisible?: boolean;
  hide_last_seen?: boolean;
};

export type PresenceDevice = {
  session_id: string;
  device_id: string;
  device_label: string;
  platform: string;
  activity: PresenceActivity;
  connected_at: string;
  last_heartbeat_at: string;
};

export type PresencePrivacy = {
  user_id?: number;
  hide_last_seen: boolean;
  invisible_mode: boolean;
};

export type PresenceSession = {
  ok: boolean;
  session_id: string;
  device_id?: string;
  heartbeat_interval_seconds: number;
  grace_period_seconds: number;
  expires_at?: string;
  reconnected?: boolean;
};

const VALID_STATUS: ReadonlySet<PresenceStatus> = new Set<PresenceStatus>(["online", "away", "offline"]);

const VALID_ACTIVITY: ReadonlySet<PresenceActivity> = new Set<PresenceActivity>([
  "idle",
  "typing",
  "recording_voice",
  "uploading_media",
  "sending_files",
  "in_audio_call",
  "in_video_call",
  "live_hosting",
  "live_guest",
  "live_watching"
]);

/**
 * Coerce an arbitrary server value into a trustworthy PulsePresence.
 *
 * Anything we cannot positively confirm as online collapses to offline. This
 * is deliberate: an unparseable or partial payload must never render as online.
 */
export function normalizePresence(raw: unknown, fallbackUserId = 0): PulsePresence {
  const source = (raw && typeof raw === "object" ? (raw as Record<string, unknown>) : {}) as Record<string, unknown>;

  const userId = toInt(source.user_id, fallbackUserId);
  // Keep "was the status token recognised?" separate from "what is the status?".
  // Collapsing the two loses the distinction between a server that said
  // "offline" and a payload where the field was missing or unparseable -- and
  // those must be treated differently when deciding whether to trust `online`.
  const recognisedStatus = VALID_STATUS.has(source.status as PresenceStatus);
  const status = recognisedStatus ? (source.status as PresenceStatus) : "offline";
  // Both signals must agree before anyone is shown as online.
  //
  // `online` alone is not enough: a payload of `{online: true}` with a missing
  // or unrecognised status used to produce status="offline" alongside
  // online=true, so the avatar lit up while the text beside it said offline.
  // Requiring a recognised, non-offline status closes that fail-open. The
  // asymmetry is the point -- an ambiguous payload must resolve to offline,
  // because the cost of a false "online" is a user who looks reachable and
  // is not, and nothing on screen reveals the error.
  const claimsOnline = typeof source.online === "boolean" ? source.online : true;
  const online = recognisedStatus && status !== "offline" && claimsOnline;
  const activity = VALID_ACTIVITY.has(source.activity as PresenceActivity)
    ? (source.activity as PresenceActivity)
    : "idle";

  const presence: PulsePresence = {
    user_id: userId,
    // A status that claims online while online is false is not trusted.
    status: online ? status : status === "away" ? "away" : "offline",
    online,
    activity: online ? activity : "idle",
    activity_context: toStr(source.activity_context),
    last_seen_at: toStr(source.last_seen_at),
    last_seen_text: toStr(source.last_seen_text),
    devices: Math.max(0, toInt(source.devices, 0)),
    available: source.available === undefined ? true : Boolean(source.available),
    self: Boolean(source.self)
  };

  if (presence.self) {
    presence.invisible = Boolean(source.invisible);
    presence.hide_last_seen = Boolean(source.hide_last_seen);
  }
  return presence;
}

/** A guaranteed-offline placeholder, identical in shape to a hidden user. */
export function offlinePresence(userId = 0): PulsePresence {
  return {
    user_id: toInt(userId, 0),
    status: "offline",
    online: false,
    activity: "idle",
    activity_context: "",
    last_seen_at: "",
    last_seen_text: "",
    devices: 0,
    available: true,
    self: false
  };
}

/**
 * The ONE predicate UI should use to decide whether to show an online dot.
 * It reads the server's boolean and nothing else — never a local connection
 * flag or a stale route param.
 */
export function isPresenceOnline(presence: PulsePresence | null | undefined): boolean {
  return Boolean(presence && presence.online === true);
}

export function isPresenceActive(presence: PulsePresence | null | undefined): boolean {
  // Retained name for call sites that previously derived "active" from strings.
  // Now strictly server-authoritative.
  return isPresenceOnline(presence);
}

/** Human-readable activity label, or "" for idle/none. */
export function presenceActivityText(activity: PresenceActivity | string | undefined): string {
  switch (activity) {
    case "typing":
      return "Typing…";
    case "recording_voice":
      return "Recording voice…";
    case "uploading_media":
      return "Uploading media…";
    case "sending_files":
      return "Sending files…";
    case "in_audio_call":
      return "In audio call";
    case "in_video_call":
      return "In video call";
    case "live_hosting":
      return "Hosting Live";
    case "live_guest":
      return "Guest in Live";
    case "live_watching":
      return "Watching Live";
    default:
      return "";
  }
}

/**
 * The single string a header/subtitle should show for a user's presence.
 * Priority: live activity > online/away > last-seen > offline. Never fabricates
 * "Online" — an offline user with no last-seen simply reads "Offline".
 */
export function presenceStatusLabel(
  presence: PulsePresence | null | undefined,
  options: { locale?: string; now?: Date } = {}
): string {
  if (!presence) return "Offline";
  if (presence.online) {
    const activity = presenceActivityText(presence.activity);
    if (activity) return activity;
    return presence.status === "away" ? "Away" : "Online";
  }
  const lastSeen = formatLastSeen(presence, options);
  return lastSeen || "Offline";
}

/**
 * Locale-aware last-seen. Prefers a device-local rendering via Intl, and falls
 * back to the server-provided last_seen_text when the raw timestamp is absent
 * (e.g. the viewer is not permitted to see it, so the server sent "").
 */
export function formatLastSeen(
  presence: PulsePresence | null | undefined,
  options: { locale?: string; now?: Date } = {}
): string {
  if (!presence) return "";
  const iso = presence.last_seen_at;
  if (!iso) return presence.last_seen_text || "";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return presence.last_seen_text || "";

  const now = options.now instanceof Date ? options.now : new Date();
  const seconds = Math.max(0, Math.round((now.getTime() - parsed.getTime()) / 1000));
  if (seconds < 60) return "Last seen just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `Last seen ${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24 && sameDay(parsed, now)) return `Last seen ${hours} ${hours === 1 ? "hour" : "hours"} ago`;

  const locale = options.locale || undefined;
  const time = formatLocalTime(parsed, locale);
  if (isYesterday(parsed, now)) return `Last seen yesterday at ${time}`;
  const day = formatLocalDate(parsed, now, locale);
  return `Last seen ${day} at ${time}`;
}

// --------------------------------------------------------------------------
// Network client
// --------------------------------------------------------------------------

const BASE = "/api/pulse/presence";

export async function connectPresence(input: {
  deviceId?: string;
  deviceLabel?: string;
  platform?: string;
  invisible?: boolean;
} = {}): Promise<PresenceSession> {
  return pulseApi<PresenceSession>(`${BASE}/connect`, {
    method: "POST",
    body: JSON.stringify({
      device_id: input.deviceId || "",
      device_label: input.deviceLabel || "",
      platform: input.platform || "",
      invisible: Boolean(input.invisible)
    })
  });
}

export async function heartbeatPresence(input: {
  sessionId?: string;
  activity?: PresenceActivity;
  activityContext?: string;
  deviceId?: string;
} = {}): Promise<PresenceSession> {
  return pulseApi<PresenceSession>(`${BASE}/heartbeat`, {
    method: "POST",
    body: JSON.stringify({
      session_id: input.sessionId || "",
      activity: input.activity,
      activity_context: input.activityContext || "",
      device_id: input.deviceId || ""
    })
  });
}

export async function setPresenceActivity(input: {
  sessionId?: string;
  activity: PresenceActivity;
  activityContext?: string;
}): Promise<PresenceSession> {
  return pulseApi<PresenceSession>(`${BASE}/activity`, {
    method: "POST",
    body: JSON.stringify({
      session_id: input.sessionId || "",
      activity: input.activity,
      activity_context: input.activityContext || ""
    })
  });
}

export async function disconnectPresence(input: { sessionId?: string; all?: boolean } = {}): Promise<{ ok: boolean }> {
  return pulseApi<{ ok: boolean }>(`${BASE}/disconnect`, {
    method: "POST",
    body: JSON.stringify({ session_id: input.sessionId || "", all: Boolean(input.all) })
  });
}

export async function queryPresence(userIds: number[]): Promise<Map<number, PulsePresence>> {
  const ids = Array.from(new Set((userIds || []).map((id) => toInt(id, 0)).filter((id) => id > 0)));
  const out = new Map<number, PulsePresence>();
  if (!ids.length) return out;
  const response = await pulseApi<{ ok?: boolean; presence?: unknown[] }>(
    `${BASE}/query?user_ids=${encodeURIComponent(ids.join(","))}`
  );
  for (const item of response?.presence || []) {
    const presence = normalizePresence(item);
    if (presence.user_id > 0) out.set(presence.user_id, presence);
  }
  // Any id the server did not return is offline, never assumed online.
  for (const id of ids) {
    if (!out.has(id)) out.set(id, offlinePresence(id));
  }
  return out;
}

export async function presenceForUser(userId: number): Promise<PulsePresence> {
  const id = toInt(userId, 0);
  if (id <= 0) return offlinePresence(0);
  const response = await pulseApi<{ ok?: boolean; presence?: unknown }>(`${BASE}/user/${id}`);
  return normalizePresence(response?.presence, id);
}

export async function fetchMyPresence(): Promise<{
  presence: PulsePresence;
  devices: PresenceDevice[];
  privacy: PresencePrivacy;
  heartbeatIntervalSeconds: number;
  gracePeriodSeconds: number;
}> {
  const response = await pulseApi<{
    presence?: unknown;
    devices?: PresenceDevice[];
    privacy?: PresencePrivacy;
    heartbeat_interval_seconds?: number;
    grace_period_seconds?: number;
  }>(`${BASE}/me`);
  return {
    presence: normalizePresence(response?.presence),
    devices: Array.isArray(response?.devices) ? response!.devices : [],
    privacy: {
      hide_last_seen: Boolean(response?.privacy?.hide_last_seen),
      invisible_mode: Boolean(response?.privacy?.invisible_mode)
    },
    heartbeatIntervalSeconds: toInt(response?.heartbeat_interval_seconds, 45),
    gracePeriodSeconds: toInt(response?.grace_period_seconds, 90)
  };
}

export async function setPresencePrivacy(input: {
  hideLastSeen?: boolean;
  invisibleMode?: boolean;
}): Promise<PresencePrivacy> {
  const body: Record<string, boolean> = {};
  if (input.hideLastSeen !== undefined) body.hide_last_seen = Boolean(input.hideLastSeen);
  if (input.invisibleMode !== undefined) body.invisible_mode = Boolean(input.invisibleMode);
  const response = await pulseApi<PresencePrivacy>(`${BASE}/privacy`, {
    method: "POST",
    body: JSON.stringify(body)
  });
  return {
    hide_last_seen: Boolean(response?.hide_last_seen),
    invisible_mode: Boolean(response?.invisible_mode)
  };
}

// --------------------------------------------------------------------------
// Internal helpers
// --------------------------------------------------------------------------

function toInt(value: unknown, fallback: number): number {
  const parsed = typeof value === "number" ? value : parseInt(String(value ?? ""), 10);
  return Number.isFinite(parsed) ? Math.trunc(parsed) : fallback;
}

function toStr(value: unknown): string {
  return value === undefined || value === null ? "" : String(value);
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

function isYesterday(value: Date, now: Date): boolean {
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  return sameDay(value, yesterday);
}

function formatLocalTime(value: Date, locale?: string): string {
  try {
    return new Intl.DateTimeFormat(locale, { hour: "numeric", minute: "2-digit" }).format(value);
  } catch {
    return value.toISOString().slice(11, 16);
  }
}

function formatLocalDate(value: Date, now: Date, locale?: string): string {
  const withinWeek = now.getTime() - value.getTime() < 7 * 24 * 60 * 60 * 1000;
  try {
    if (withinWeek) return new Intl.DateTimeFormat(locale, { weekday: "long" }).format(value);
    const opts: Intl.DateTimeFormatOptions =
      value.getFullYear() === now.getFullYear()
        ? { month: "long", day: "numeric" }
        : { month: "long", day: "numeric", year: "numeric" };
    return new Intl.DateTimeFormat(locale, opts).format(value);
  } catch {
    return value.toISOString().slice(0, 10);
  }
}
