import { hashIdentifier, redact } from "./liveAudioTelemetry";

export type LiveAudioTraceEventName =
  | "live_start_requested"
  | "live_session_created"
  | "live_audio_owner_requested"
  | "live_audio_owner_acquired"
  | "live_audio_generation_created"
  | "live_audio_policy_requested"
  | "live_audio_policy_applied"
  | "av_audio_session_activation_started"
  | "av_audio_session_activated"
  | "camera_initialization_started"
  | "camera_initialized"
  | "livekit_room_connect_started"
  | "livekit_room_connected"
  | "microphone_track_create_started"
  | "microphone_track_created"
  | "microphone_publish_started"
  | "microphone_published"
  | "live_audio_active_verification_started"
  | "live_audio_active_verification_passed"
  | "live_audio_active_verification_retrying"
  | "live_audio_active_verification_failed"
  | "audio_owner_release_requested"
  | "audio_session_deactivation_requested"
  | "audio_session_deactivated"
  | "audio_generation_replaced"
  | "stale_cleanup_rejected"
  | "cleanup_started"
  | "feature_flag_changed"
  | "quality_profile_changed"
  | "component_unmounted"
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
  audio_generation: number | null;
  current_owner: string;
  requested_owner: string;
  room_id: string;
  screen_instance_id: string;
  quality_profile: string;
  feature_flags: string;
  caller: string;
  reason: string;
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
  | "quality_profile"
  | "feature_flags"
  | "caller"
  | "reason"
>> & {
  participantIdentity?: unknown;
  audioOwner?: unknown;
  trackSid?: unknown;
  publicationSid?: unknown;
  audioLevel?: unknown;
  audioGeneration?: unknown;
  currentOwner?: unknown;
  requestedOwner?: unknown;
};

type TraceSink = (event: LiveAudioTraceEvent) => void;

// console.error (not info): iOS os_log/idevicesyslog drops info/debug in Release
// builds, so info-level traces never reach a physical device's syslog. error
// level survives, which is what makes the on-device audio failure observable.
const defaultSink: TraceSink = (event) => console.error("PulseSocLiveAudioTrace", event);
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
  screenInstanceId?: unknown;
}) {
  let sequence = 0;
  const events: LiveAudioTraceEvent[] = [];
  const sessionId = safeId(`${options.room || "room"}:${options.correlationId}`);
  const roomName = safeId(options.room);
  const participantIdentity = safeId(options.participantIdentity);
  const screenInstanceId = safeId(options.screenInstanceId || options.correlationId);

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
      engine_state: redact(patch.engine_state) || "unknown",
      audio_generation: Number.isFinite(Number(patch.audioGeneration)) ? Number(patch.audioGeneration) : null,
      current_owner: patch.currentOwner ? safeId(patch.currentOwner) : "none",
      requested_owner: patch.requestedOwner ? safeId(patch.requestedOwner) : "none",
      room_id: roomName,
      screen_instance_id: screenInstanceId,
      quality_profile: redact(patch.quality_profile) || "unknown",
      feature_flags: redact(patch.feature_flags) || "unknown",
      caller: redact(patch.caller) || "unknown",
      reason: redact(patch.reason) || "none"
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
