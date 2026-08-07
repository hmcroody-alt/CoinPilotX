export type RealtimeAudioTelemetryEventName =
  | "session_requested"
  | "audio_owner_requested"
  | "audio_owner_acquired"
  | "audio_owner_rejected"
  | "audio_session_activated"
  | "microphone_publish_started"
  | "microphone_published"
  | "microphone_publish_failed"
  | "microphone_reasserted"
  | "camera_publish_started"
  | "camera_published"
  | "remote_audio_subscribed"
  | "audio_engine_guard_started"
  | "audio_engine_guard_completed"
  | "audio_engine_guard_failed"
  // One per bounded recovery pass INSIDE a single guard invocation. A distinct
  // name so a repeated repair can never be misread as the guard having been
  // entered twice - which is exactly how the previous two-function recovery
  // (`recoverRealtimeRecordingEngine` then `stabilizeRealtimeAudioEngine`, both
  // emitting `audio_engine_guard_started` with an identical context) made every
  // guard line appear twice in the device log.
  | "audio_engine_recovery_attempt"
  // Emitted when enabling the ADM output path fails. `initPlayout` is the only
  // call that sets outputEnabled, and AVAudioEngine will not run without it - so
  // a non-zero status here is the difference between a host that broadcasts and
  // one that publishes a silent track. Carries the raw ADM status in outcome and
  // no identifiers.
  | "audio_engine_playout_init_failed"
  | "cleanup_started"
  | "cleanup_completed"
  // Emitted by realtimeAudioInvariants.ts when a state that should be
  // impossible is observed and rejected at runtime. Carries the invariant id in
  // failureCategory and what the owning module did in outcome.
  | "invariant_violation";

export type RealtimeAudioTelemetryEvent = {
  name: RealtimeAudioTelemetryEventName;
  correlationId: string;
  sessionHash: string;
  roomType: string;
  participantRole: string;
  outcome?: string;
  failureCategory?: string;
  durationMs?: number;
  audioTrackCount?: number;
  duplicatesRemoved?: number;
  /**
   * Native ADM state, rendered as enabled/running pairs.
   *
   * A separate field rather than more text appended to `outcome`: `outcome` is
   * capped at 96 characters, and the native reading was silently truncated away
   * when it was concatenated there - the diagnostic added to explain
   * `engine=false` never reached the log line that reported it.
   */
  engineState?: string;
  /** WebRTC's own reason the engine failed to start, e.g. "Failed to start engine: -10875". */
  nativeError?: string;
  /** Which pass of the bounded recovery produced this event. Absent outside recovery. */
  recoveryAttempt?: number;
  /** Where in the startup sequence the failure happened, for the user-facing message. */
  failureStage?: string;
  /** AVAudioSession interruption state at the time of the event. */
  interruption?: string;
};

const SECRET_PATTERNS = [
  /\beyJ[A-Za-z0-9_-]{8,}\b/g,
  /\bBearer\s+\S+/gi,
  /\b[A-Za-z0-9_-]{40,}\b/g,
  /\b(wss?|https?):\/\/\S+/gi
];

/**
 * Redaction runs before truncation on every field, so a longer cap never widens
 * what can leak - it only lets an already-redacted diagnostic survive intact.
 *
 * The default 96 is the historic cap for the short identity fields. Native
 * diagnostics pass a wider cap because they are the payload this incident is
 * about; clipping them is what silently destroyed the first attempt at
 * explaining `engine=false`.
 */
function sanitize(value: unknown, maxLength = 96): string {
  let text = String(value ?? "");
  for (const pattern of SECRET_PATTERNS) text = text.replace(pattern, "[redacted]");
  return text.replace(/\s+/g, " ").trim().slice(0, maxLength);
}

/** Native diagnostic fields carry the enabled/running split plus WebRTC's own error text. */
const NATIVE_FIELD_MAX_LENGTH = 480;

export function hashRealtimeAudioIdentifier(value: unknown): string {
  const input = String(value ?? "");
  if (!input) return "none";
  let hash = 2166136261;
  for (let index = 0; index < input.length; index += 1) {
    hash ^= input.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(36).padStart(7, "0").slice(0, 7);
}

type EventInput = Omit<RealtimeAudioTelemetryEvent, "sessionHash" | "correlationId" | "roomType" | "participantRole"> & {
  sessionId?: unknown;
  correlationId?: unknown;
  roomType?: unknown;
  participantRole?: unknown;
};

type Sink = (event: RealtimeAudioTelemetryEvent) => void;

/**
 * Events that describe a failure rather than a normal transition.
 *
 * Only these log at error level by default. Logging a successful
 * `microphone_published` at error severity trains every reader - human and
 * crash reporter alike - to ignore the channel, which is exactly what happened
 * during this incident: the real `audio_engine_guard_failed` line was
 * indistinguishable from the twenty healthy lines around it.
 */
const FAILURE_EVENTS: ReadonlySet<RealtimeAudioTelemetryEventName> = new Set([
  "audio_engine_guard_failed",
  "audio_engine_playout_init_failed",
  "audio_owner_rejected",
  "microphone_publish_failed",
  "invariant_violation"
]);

/**
 * Raise every event to error level.
 *
 * iOS os_log (and therefore `idevicesyslog`) drops info/debug in Release, so a
 * device capture would otherwise see only the failure lines and none of the
 * transitions that led to them. This is opt-in per capture session rather than
 * the default, so ordinary Release builds keep an honest severity mapping.
 */
let verbose = false;

export function setRealtimeAudioTelemetryVerbose(next: boolean): void {
  verbose = next === true;
}

export function isRealtimeAudioTelemetryVerbose(): boolean {
  return verbose;
}

const defaultSink: Sink = (event) => {
  if (verbose || FAILURE_EVENTS.has(event.name)) {
    console.error("PulseSocRealtimeAudio", event);
    return;
  }
  console.log("PulseSocRealtimeAudio", event);
};
let sink: Sink = defaultSink;
let correlationSequence = 0;

export function createRealtimeAudioCorrelationId(): string {
  correlationSequence += 1;
  return `rt-${Date.now().toString(36)}-${correlationSequence.toString(36)}`;
}

export function setRealtimeAudioTelemetrySink(next: Sink | null | undefined): void {
  sink = next || defaultSink;
}

export function emitRealtimeAudioEvent(input: EventInput): RealtimeAudioTelemetryEvent {
  const event: RealtimeAudioTelemetryEvent = {
    name: input.name,
    correlationId: sanitize(input.correlationId) || "none",
    sessionHash: hashRealtimeAudioIdentifier(input.sessionId),
    roomType: sanitize(input.roomType) || "unknown",
    participantRole: sanitize(input.participantRole) || "unknown"
  };
  if (input.outcome !== undefined) event.outcome = sanitize(input.outcome);
  if (input.failureCategory !== undefined) event.failureCategory = sanitize(input.failureCategory);
  if (Number.isFinite(input.durationMs)) event.durationMs = Math.round(input.durationMs as number);
  if (Number.isFinite(input.audioTrackCount)) event.audioTrackCount = Math.round(input.audioTrackCount as number);
  if (Number.isFinite(input.duplicatesRemoved)) event.duplicatesRemoved = Math.round(input.duplicatesRemoved as number);
  // Native readings get their own fields at a wider cap. Appending them to
  // `outcome` truncated them away entirely, which is how the diagnostic added to
  // explain `engine=false` never reached the log line reporting it.
  if (input.engineState !== undefined) event.engineState = sanitize(input.engineState, NATIVE_FIELD_MAX_LENGTH);
  if (input.nativeError !== undefined) event.nativeError = sanitize(input.nativeError, NATIVE_FIELD_MAX_LENGTH);
  if (input.failureStage !== undefined) event.failureStage = sanitize(input.failureStage);
  if (input.interruption !== undefined) event.interruption = sanitize(input.interruption);
  if (Number.isFinite(input.recoveryAttempt)) event.recoveryAttempt = Math.round(input.recoveryAttempt as number);
  try {
    sink(event);
  } catch {
    // Diagnostics must never alter call or Live behavior.
  }
  return event;
}
