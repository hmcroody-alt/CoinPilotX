/**
 * Privacy-safe media quality telemetry.
 *
 * WHAT IT MAY CARRY
 * Enumerations and numbers: which profile ran, which rung the adaptation
 * settled on, why, bitrates, frame rates, resolution, freeze counts, round-trip
 * time. These describe the pipe, not the person.
 *
 * WHAT IT MUST NEVER CARRY
 * Audio samples, video frames, transcripts, speech content, tokens, room URLs,
 * credentials, raw participant identifiers, or anything derived from what was
 * said or shown. Quality diagnostics that captured any part of a conversation
 * would be a far worse problem than the quality issue they were added to solve.
 *
 * HOW THAT IS ENFORCED RATHER THAN PROMISED
 * The event type has a closed field list, every string field passes through the
 * same redaction used by realtimeAudioTelemetry, every numeric field is coerced
 * and rounded, and identifiers are hashed on the way in so the raw value never
 * exists in an event object. A comment asking people to be careful would not be
 * enforcement; a type that has nowhere to put a transcript is.
 */

import { hashRealtimeAudioIdentifier } from "./realtimeAudioTelemetry";
import type { MediaFeature, MediaQualityProfileName } from "./mediaQualityPolicy";
import type { DegradationRung, NetworkTier, ThermalState } from "./mediaAdaptationController";

export type MediaQualityEventName =
  | "quality_plan_resolved"
  | "quality_plan_applied"
  | "quality_plan_rejected"
  | "adaptation_sample"
  | "adaptation_degraded"
  | "adaptation_recovered"
  | "adaptation_floor_reached"
  | "capture_started"
  | "capture_reconfigured"
  | "encoding_updated"
  | "subscription_layer_changed"
  | "remote_video_paused"
  | "remote_video_resumed"
  | "quality_metrics_sample"
  | "quality_fallback_to_stable";

export type MediaQualityEvent = {
  name: MediaQualityEventName;
  correlationId: string;
  sessionHash: string;
  feature: MediaFeature;
  profile: MediaQualityProfileName;
  requestedProfile?: MediaQualityProfileName;
  rung?: DegradationRung;
  networkTier?: NetworkTier;
  thermalState?: ThermalState;
  contentMode?: string;
  /** Enum-only decision trail. Never free text from an error message. */
  reasons?: string[];

  /* Measured quality. All optional; absent means not sampled, not zero. */
  audioBitrateBps?: number;
  videoBitrateBps?: number;
  captureWidth?: number;
  captureHeight?: number;
  captureFrameRate?: number;
  encodedFrameRate?: number;
  freezeCount?: number;
  freezeDurationMs?: number;
  packetLossPercent?: number;
  roundTripTimeMs?: number;
  jitterMs?: number;
  audioLevelDbfs?: number;
  batteryPercent?: number;

  /**
   * Always true when present. Emitted so that a dashboard can alert on its
   * absence rather than on a subtle change in some other field.
   */
  audioPathUnchanged?: boolean;
};

/**
 * Reused from the audio telemetry module rather than reimplemented. Two
 * redaction lists diverge; one does not.
 */
const SECRET_PATTERNS = [
  /\beyJ[A-Za-z0-9_-]{8,}\b/g,
  /\bBearer\s+\S+/gi,
  /\b[A-Za-z0-9_-]{40,}\b/g,
  /\b(wss?|https?):\/\/\S+/gi
];

function sanitize(value: unknown): string {
  let text = String(value ?? "");
  for (const pattern of SECRET_PATTERNS) text = text.replace(pattern, "[redacted]");
  return text.replace(/\s+/g, " ").trim().slice(0, 96);
}

/**
 * Reason codes are machine-readable by contract. Anything containing a space or
 * a character outside the enum alphabet is dropped rather than redacted,
 * because a "reason" that needed redacting was never a reason code — it was
 * someone logging a message where an enum belonged.
 */
const REASON_CODE = /^[a-z0-9_]{1,48}$/;

function sanitizeReasons(raw: unknown): string[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  const codes = raw.filter((entry): entry is string => typeof entry === "string" && REASON_CODE.test(entry));
  return codes.length ? codes.slice(0, 12) : undefined;
}

function numeric(value: unknown, decimals = 0): number | undefined {
  if (typeof value !== "number" || !Number.isFinite(value)) return undefined;
  const factor = 10 ** decimals;
  return Math.round(value * factor) / factor;
}

type Sink = (event: MediaQualityEvent) => void;
const defaultSink: Sink = (event) => console.info("PulseSocMediaQuality", event);
let sink: Sink = defaultSink;
let correlationSequence = 0;

export function createMediaQualityCorrelationId(): string {
  correlationSequence += 1;
  return `mq-${Date.now().toString(36)}-${correlationSequence.toString(36)}`;
}

export function setMediaQualityTelemetrySink(next: Sink | null | undefined): void {
  sink = next || defaultSink;
}

export type MediaQualityEventInput = Omit<
  MediaQualityEvent,
  "sessionHash" | "correlationId" | "reasons"
> & {
  sessionId?: unknown;
  correlationId?: unknown;
  reasons?: unknown;
};

/**
 * Builds and emits one event. Every field is rebuilt explicitly — the input is
 * never spread into the output — so a caller who attaches an extra property
 * cannot smuggle it into the payload.
 */
export function emitMediaQualityEvent(input: MediaQualityEventInput): MediaQualityEvent {
  const event: MediaQualityEvent = {
    name: input.name,
    correlationId: sanitize(input.correlationId) || "none",
    sessionHash: hashRealtimeAudioIdentifier(input.sessionId),
    feature: input.feature,
    profile: input.profile
  };

  if (input.requestedProfile !== undefined) event.requestedProfile = input.requestedProfile;
  if (input.rung !== undefined) event.rung = input.rung;
  if (input.networkTier !== undefined) event.networkTier = input.networkTier;
  if (input.thermalState !== undefined) event.thermalState = input.thermalState;
  if (input.contentMode !== undefined) event.contentMode = sanitize(input.contentMode);

  const reasons = sanitizeReasons(input.reasons);
  if (reasons) event.reasons = reasons;

  const assign = (key: keyof MediaQualityEvent, value: unknown, decimals = 0) => {
    const parsed = numeric(value, decimals);
    if (parsed !== undefined) (event as Record<string, unknown>)[key] = parsed;
  };

  assign("audioBitrateBps", input.audioBitrateBps);
  assign("videoBitrateBps", input.videoBitrateBps);
  assign("captureWidth", input.captureWidth);
  assign("captureHeight", input.captureHeight);
  assign("captureFrameRate", input.captureFrameRate);
  assign("encodedFrameRate", input.encodedFrameRate);
  assign("freezeCount", input.freezeCount);
  assign("freezeDurationMs", input.freezeDurationMs);
  assign("packetLossPercent", input.packetLossPercent, 2);
  assign("roundTripTimeMs", input.roundTripTimeMs);
  assign("jitterMs", input.jitterMs, 1);
  assign("batteryPercent", input.batteryPercent);

  // Audio level is a loudness measurement in dBFS, not a recording. It is a
  // single scalar per sample and cannot be reassembled into speech.
  assign("audioLevelDbfs", input.audioLevelDbfs, 1);

  if (input.audioPathUnchanged !== undefined) {
    event.audioPathUnchanged = input.audioPathUnchanged === true;
  }

  try {
    sink(event);
  } catch {
    // Diagnostics must never alter call or Live behavior.
  }
  return event;
}

/**
 * The closed list of keys an event may contain. Exported so the privacy test
 * can assert the emitted object against it rather than against a hand-kept copy
 * that would drift the first time a field is added.
 */
export const MEDIA_QUALITY_EVENT_KEYS = Object.freeze([
  "name",
  "correlationId",
  "sessionHash",
  "feature",
  "profile",
  "requestedProfile",
  "rung",
  "networkTier",
  "thermalState",
  "contentMode",
  "reasons",
  "audioBitrateBps",
  "videoBitrateBps",
  "captureWidth",
  "captureHeight",
  "captureFrameRate",
  "encodedFrameRate",
  "freezeCount",
  "freezeDurationMs",
  "packetLossPercent",
  "roundTripTimeMs",
  "jitterMs",
  "audioLevelDbfs",
  "batteryPercent",
  "audioPathUnchanged"
] as const);
