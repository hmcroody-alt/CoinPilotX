/**
 * Privacy-safe structured telemetry for the livestream audio route.
 *
 * The point of this module is that it is IMPOSSIBLE to log a token by accident.
 * Callers do not hand it a free-form object that gets spread into a log line;
 * they hand it a typed event, and every string that leaves here has been through
 * `redact`, which drops anything that looks like a JWT, a bearer token, a wss
 * URL with a query string, or a long opaque blob.
 *
 * Room and participant identifiers are hashed, not emitted raw, so two events
 * from the same broadcast can be correlated in aggregate without the log itself
 * naming the user or the room.
 *
 * Pure and dependency-free so the redaction rules can be unit tested.
 */

export type LiveAudioEventName =
  | "live_audio_session_claimed"
  | "live_audio_session_denied"
  | "live_audio_session_displaced"
  | "live_audio_session_released"
  | "live_audio_publish_started"
  | "live_audio_publish_settled"
  | "live_audio_publish_timeout"
  | "live_audio_duplicate_reconciled"
  | "live_audio_route_reapplied"
  | "live_audio_interruption_began"
  | "live_audio_interruption_ended"
  | "live_audio_disconnect_classified"
  | "live_audio_reconnect_scheduled"
  | "live_audio_reconnect_exhausted"
  | "live_audio_token_refresh_scheduled"
  | "live_audio_token_refreshed"
  | "live_audio_token_refresh_failed"
  | "live_audio_path_selected"
  | "live_audio_fallback_to_legacy";

export type LiveAudioEvent = {
  name: LiveAudioEventName;
  path: "v2_isolated" | "v1_legacy";
  role: "host" | "guest" | "cohost" | "viewer" | "unknown";
  roomHash: string;
  attempt?: number;
  durationMs?: number;
  audioTrackCount?: number;
  duplicatesRemoved?: number;
  outcome?: string;
  reason?: string;
  detail?: string;
};

/** Anything matching these is never emitted, regardless of which field it lands in. */
const SECRET_PATTERNS: RegExp[] = [
  /\beyJ[A-Za-z0-9_-]{8,}\b/g, // JWT-ish (a base64url-encoded '{"' header)
  /\bBearer\s+\S+/gi,
  /\b[A-Za-z0-9_-]{40,}\b/g, // long opaque blobs: API keys, signatures, raw tokens
  /\b(wss?|https?):\/\/\S+/gi // endpoints can carry credentials in the query string
];

const MAX_FIELD_LENGTH = 120;

/**
 * Reduce an arbitrary value to a short, secret-free string. Applied to every
 * string field, so a caller cannot leak a token by putting it in `detail`.
 */
export function redact(value: unknown): string {
  let text = typeof value === "string" ? value : value === undefined || value === null ? "" : String(value);
  for (const pattern of SECRET_PATTERNS) {
    text = text.replace(pattern, "[redacted]");
  }
  return text.replace(/\s+/g, " ").trim().slice(0, MAX_FIELD_LENGTH);
}

/**
 * Stable, non-reversible short hash. Correlates events from one broadcast
 * without putting the room name or user identity in the log.
 */
export function hashIdentifier(value: unknown): string {
  const text = typeof value === "string" ? value : String(value ?? "");
  if (!text) return "none";
  let hash = 2166136261;
  for (let index = 0; index < text.length; index += 1) {
    hash ^= text.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36).padStart(7, "0").slice(0, 7);
}

const KNOWN_ROLES = new Set(["host", "guest", "cohost", "viewer"]);

export function normalizeRole(role: unknown): LiveAudioEvent["role"] {
  const text = String(role ?? "").trim().toLowerCase();
  if (text === "co-host") return "cohost";
  return (KNOWN_ROLES.has(text) ? text : "unknown") as LiveAudioEvent["role"];
}

function finiteNumber(value: unknown): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
  return Math.round(value);
}

export type LiveAudioEventInput = {
  name: LiveAudioEventName;
  path?: "v2_isolated" | "v1_legacy";
  role?: unknown;
  room?: unknown;
  attempt?: unknown;
  durationMs?: unknown;
  audioTrackCount?: unknown;
  duplicatesRemoved?: unknown;
  outcome?: unknown;
  reason?: unknown;
  detail?: unknown;
};

/**
 * Build the event. Optional fields are omitted entirely rather than emitted as
 * undefined, so log lines stay small and a missing value is unambiguous.
 */
export function buildLiveAudioEvent(input: LiveAudioEventInput): LiveAudioEvent {
  const event: LiveAudioEvent = {
    name: input.name,
    path: input.path === "v2_isolated" ? "v2_isolated" : "v1_legacy",
    role: normalizeRole(input.role),
    roomHash: hashIdentifier(input.room)
  };
  const attempt = finiteNumber(input.attempt);
  const durationMs = finiteNumber(input.durationMs);
  const audioTrackCount = finiteNumber(input.audioTrackCount);
  const duplicatesRemoved = finiteNumber(input.duplicatesRemoved);
  if (attempt !== undefined) event.attempt = attempt;
  if (durationMs !== undefined) event.durationMs = durationMs;
  if (audioTrackCount !== undefined) event.audioTrackCount = audioTrackCount;
  if (duplicatesRemoved !== undefined) event.duplicatesRemoved = duplicatesRemoved;
  if (input.outcome !== undefined && input.outcome !== null) event.outcome = redact(input.outcome);
  if (input.reason !== undefined && input.reason !== null) event.reason = redact(input.reason);
  if (input.detail !== undefined && input.detail !== null) event.detail = redact(input.detail);
  return event;
}

type LiveAudioSink = (event: LiveAudioEvent) => void;

const defaultSink: LiveAudioSink = (event) => {
  // console.info keeps this visible in Metro/device logs without adding a
  // dependency. Swap via setLiveAudioTelemetrySink to forward to an analytics
  // pipeline later; the redaction happens before the sink either way.
  console.info("PulseSocLiveAudio", event);
};

let sink: LiveAudioSink = defaultSink;

export function setLiveAudioTelemetrySink(next: LiveAudioSink | null | undefined): void {
  sink = next || defaultSink;
}

export function emitLiveAudioEvent(input: LiveAudioEventInput): LiveAudioEvent {
  const event = buildLiveAudioEvent(input);
  try {
    sink(event);
  } catch {
    // Telemetry must never break a live broadcast.
  }
  return event;
}
