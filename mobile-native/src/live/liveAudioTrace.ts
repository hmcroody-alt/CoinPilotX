import { hashIdentifier, redact } from "./liveAudioTelemetry";

export type LiveAudioTraceEventName =
  | "live_start_requested"
  | "live_authorization_succeeded"
  | "live_room_connect_started"
  | "live_room_connected"
  | "audio_owner_requested"
  | "audio_owner_acquired"
  | "audio_session_config_started"
  | "audio_session_config_completed"
  | "audio_session_activated"
  | "microphone_permission_checked"
  | "microphone_permission_granted"
  | "microphone_permission_denied"
  | "microphone_input_available"
  | "microphone_input_unavailable"
  | "local_audio_track_create_started"
  | "local_audio_track_created"
  | "local_audio_track_enabled"
  | "local_audio_publish_started"
  | "local_audio_published"
  | "local_audio_publication_sid_available"
  | "local_audio_unmuted"
  | "local_audio_energy_detected"
  | "viewer_room_connected"
  | "remote_participant_discovered"
  | "remote_audio_publication_discovered"
  | "remote_audio_subscribe_started"
  | "remote_audio_subscribed"
  | "remote_audio_track_unmuted"
  | "remote_audio_playback_expected"
  | "remote_audio_energy_detected"
  | "current_output_route_recorded"
  | "live_end_requested"
  | "local_audio_unpublish_started"
  | "local_audio_unpublished"
  | "local_audio_track_stopped"
  | "room_disconnect_started"
  | "room_disconnected"
  | "audio_owner_released"
  | "audio_session_deactivated_if_unowned"
  | "cleanup_completed"
  | "invariant_failed";

export type LiveAudioTraceEvent = {
  sequence: number;
  timestamp: string;
  event: LiveAudioTraceEventName;
  correlation_id: string;
  session_id: string;
  room_name: string;
  participant_identity: string;
  participant_role: string;
  room_state: string;
  audio_owner: string;
  track_sid: string;
  publication_sid: string;
  muted: boolean | null;
  enabled: boolean | null;
  subscription_state: string;
  output_route: string;
  error_category: string;
  audio_level: number | null;
  audio_profile: string;
  engine_state: string;
};

type TracePatch = Partial<Pick<
  LiveAudioTraceEvent,
  | "participant_role"
  | "room_state"
  | "muted"
  | "enabled"
  | "subscription_state"
  | "output_route"
  | "error_category"
  | "audio_profile"
  | "engine_state"
>> & {
  participantIdentity?: unknown;
  audioOwner?: unknown;
  trackSid?: unknown;
  publicationSid?: unknown;
  audioLevel?: unknown;
};

type TraceSink = (event: LiveAudioTraceEvent) => void;

const defaultSink: TraceSink = (event) => console.info("PulseSocLiveAudioTrace", event);
let sink: TraceSink = defaultSink;

export function setLiveAudioTraceSink(next?: TraceSink | null) {
  sink = next || defaultSink;
}

function safeId(value: unknown): string {
  return value ? `hash:${hashIdentifier(value)}` : "none";
}

function safeLevel(value: unknown): number | null {
  const level = Number(value);
  if (!Number.isFinite(level)) return null;
  return Math.max(0, Math.min(100, Math.round(level * 100)));
}

export function createLiveAudioTrace(options: {
  enabled: boolean;
  correlationId: string;
  room: unknown;
  participantIdentity: unknown;
  participantRole: string;
}) {
  let sequence = 0;
  const events: LiveAudioTraceEvent[] = [];
  const sessionId = safeId(`${options.room || "room"}:${options.correlationId}`);
  const roomName = safeId(options.room);
  const participantIdentity = safeId(options.participantIdentity);

  function emit(event: LiveAudioTraceEventName, patch: TracePatch = {}): LiveAudioTraceEvent | null {
    if (!options.enabled) return null;
    sequence += 1;
    const item: LiveAudioTraceEvent = {
      sequence,
      timestamp: new Date().toISOString(),
      event,
      correlation_id: redact(options.correlationId) || "none",
      session_id: sessionId,
      room_name: roomName,
      participant_identity: patch.participantIdentity ? safeId(patch.participantIdentity) : participantIdentity,
      participant_role: redact(patch.participant_role ?? options.participantRole) || "unknown",
      room_state: redact(patch.room_state) || "unknown",
      audio_owner: patch.audioOwner ? safeId(patch.audioOwner) : "none",
      track_sid: patch.trackSid ? safeId(patch.trackSid) : "none",
      publication_sid: patch.publicationSid ? safeId(patch.publicationSid) : "none",
      muted: typeof patch.muted === "boolean" ? patch.muted : null,
      enabled: typeof patch.enabled === "boolean" ? patch.enabled : null,
      subscription_state: redact(patch.subscription_state) || "unknown",
      output_route: redact(patch.output_route) || "unknown",
      error_category: redact(patch.error_category) || "none",
      audio_level: safeLevel(patch.audioLevel),
      audio_profile: redact(patch.audio_profile) || "unknown",
      engine_state: redact(patch.engine_state) || "unknown"
    };
    events.push(item);
    try { sink(item); } catch { /* tracing must not interrupt a broadcast */ }
    return item;
  }

  return {
    enabled: options.enabled,
    emit,
    snapshot: () => events.map((event) => ({ ...event }))
  };
}

export type LiveAudioTrace = ReturnType<typeof createLiveAudioTrace>;
