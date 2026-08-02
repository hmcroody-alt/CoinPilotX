export type RealtimeAudioTelemetryEventName =
  | "session_requested"
  | "audio_owner_requested"
  | "audio_owner_acquired"
  | "audio_owner_rejected"
  | "audio_session_activated"
  | "microphone_publish_started"
  | "microphone_published"
  | "microphone_publish_failed"
  | "cleanup_started"
  | "cleanup_completed";

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
};

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
const defaultSink: Sink = (event) => console.info("PulseSocRealtimeAudio", event);
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
  try {
    sink(event);
  } catch {
    // Diagnostics must never alter call or Live behavior.
  }
  return event;
}
