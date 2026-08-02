import { Platform } from "react-native";
import {
  RealtimeAudioOwnershipError,
  resolveOwnershipDecision,
  type OwnershipDecision
} from "./audioOwnershipPolicy";
import { emitRealtimeAudioEvent } from "./realtimeAudioTelemetry";

export type RealtimeAudioMode =
  | "none"
  | "audio_call"
  | "video_call"
  | "live_host"
  | "live_guest"
  | "live_viewer"
  | "voice_message"
  | "music_playback";

export type AppleAudioConfiguration = {
  audioCategory: string;
  audioMode: string;
  audioCategoryOptions: string[];
};

export type RealtimeAudioOwner = {
  ownerId: string;
  leaseId: number;
  mode: RealtimeAudioMode;
  startedAt: number;
  publishesMicrophone: boolean;
};

export type RealtimeAudioLease = Pick<RealtimeAudioOwner, "ownerId" | "leaseId" | "mode">;

type LiveKitAudioSession = {
  setAppleAudioConfiguration?: (config: any) => Promise<void>;
  configureAudio?: (config: Record<string, unknown>) => Promise<void>;
  startAudioSession?: () => Promise<void>;
  stopAudioSession?: () => Promise<void>;
  selectAudioOutput?: (deviceId: string) => Promise<void>;
  showAudioRoutePicker?: () => Promise<void>;
};

type RealtimeAudioDeviceModule = {
  isEngineRunning?: () => boolean;
  isPlaying?: () => boolean;
  isRecording?: () => boolean;
  startPlayout?: () => Promise<void>;
  startRecording?: () => Promise<void>;
  startLocalRecording?: () => Promise<void>;
};

export type RealtimeAudioEngineStatus = {
  engineRunning: boolean | null;
  playoutRunning: boolean | null;
  recordingRunning: boolean | null;
};

let activeRealtimeAudioOwner: RealtimeAudioOwner | null = null;
let nextRealtimeAudioLeaseId = 0;

/**
 * Teardown callbacks keyed by ownerId. When a higher-priority feature takes the
 * audio session, the incumbent is invoked here so it can stop its own media
 * instead of silently believing it still owns a session it has lost.
 */
const displacementHandlers = new Map<string, () => void>();

export type ClaimOptions = {
  /** Invoked when a higher-priority owner takes the session from this owner. */
  onDisplaced?: () => void;
  correlationId?: string;
  participantRole?: string;
};

/** Last arbitration outcome, exposed for telemetry and tests. */
let lastOwnershipDecision: OwnershipDecision | null = null;

export function getLastOwnershipDecision(): OwnershipDecision | null {
  return lastOwnershipDecision;
}

function notifyDisplaced(ownerId: string) {
  const handler = displacementHandlers.get(ownerId);
  displacementHandlers.delete(ownerId);
  if (!handler) return;
  try {
    handler();
  } catch {
    // A failing teardown handler must never block the incoming owner.
  }
}

/**
 * Canonical PulseSoc realtime audio profile.
 *
 * Publishers share the known-good call path. Listen-only viewers use playback:
 * subscribing to Live must not acquire a microphone-oriented recording profile.
 */
export function resolveRealtimeAudioConfiguration(mode: RealtimeAudioMode | boolean): AppleAudioConfiguration {
  const normalizedMode: RealtimeAudioMode = typeof mode === "boolean" ? (mode ? "live_host" : "live_viewer") : mode;
  if (
    normalizedMode === "audio_call" ||
    normalizedMode === "video_call" ||
    normalizedMode === "live_host" ||
    normalizedMode === "live_guest" ||
    normalizedMode === "voice_message"
  ) {
    return {
      audioCategory: "playAndRecord",
      audioMode: "videoChat",
      audioCategoryOptions: ["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay", "defaultToSpeaker"]
    };
  }
  return {
    audioCategory: "playback",
    audioMode: "default",
    audioCategoryOptions: ["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay"]
  };
}

export function getActiveRealtimeAudioOwner(): RealtimeAudioOwner | null {
  return activeRealtimeAudioOwner ? { ...activeRealtimeAudioOwner } : null;
}

export function getActiveRealtimeMicrophoneOwner(): RealtimeAudioOwner | null {
  const owner = getActiveRealtimeAudioOwner();
  return owner?.publishesMicrophone ? owner : null;
}

export function modePublishesMicrophone(mode: RealtimeAudioMode): boolean {
  return ["audio_call", "video_call", "live_host", "live_guest", "voice_message"].includes(mode);
}

/**
 * Claim the single device audio session.
 *
 * Arbitration is delegated to the pure policy module. A claim that loses
 * arbitration throws `RealtimeAudioOwnershipError` rather than silently
 * stealing the session - this is what stops a livestream from cutting the
 * audio out from under an active call.
 *
 * @throws {RealtimeAudioOwnershipError} when a higher-priority owner holds the session.
 */
export function claimRealtimeAudioSession(
  mode: RealtimeAudioMode,
  ownerId: string,
  options: ClaimOptions = {}
): RealtimeAudioOwner {
  emitRealtimeAudioEvent({
    name: "audio_owner_requested",
    sessionId: ownerId,
    correlationId: options.correlationId,
    roomType: mode,
    participantRole: options.participantRole
  });
  const decision = resolveOwnershipDecision(activeRealtimeAudioOwner, { ownerId, mode });
  lastOwnershipDecision = decision;

  if (decision.outcome === "denied") {
    emitRealtimeAudioEvent({
      name: "audio_owner_rejected",
      sessionId: ownerId,
      correlationId: options.correlationId,
      roomType: mode,
      participantRole: options.participantRole,
      outcome: decision.outcome,
      failureCategory: "higher_priority_owner"
    });
    throw new RealtimeAudioOwnershipError(decision.blockedBy, decision.blockedByMode);
  }
  if (decision.outcome === "displaced") {
    notifyDisplaced(decision.displaces);
  }

  if (options.onDisplaced) displacementHandlers.set(ownerId, options.onDisplaced);
  else displacementHandlers.delete(ownerId);

  // Re-acquiring preserves the original startedAt so session duration telemetry
  // measures the real session, not the latest reconnect.
  const startedAt =
    decision.outcome === "reacquired" && activeRealtimeAudioOwner
      ? activeRealtimeAudioOwner.startedAt
      : Date.now();

  // Every acquisition rotates the lease, even when the semantic ownerId is the
  // same. A delayed cleanup holding the previous lease can therefore never
  // stop the newer room/session instance.
  nextRealtimeAudioLeaseId += 1;
  activeRealtimeAudioOwner = {
    ownerId,
    leaseId: nextRealtimeAudioLeaseId,
    mode,
    startedAt,
    publishesMicrophone: modePublishesMicrophone(mode)
  };
  emitRealtimeAudioEvent({
    name: "audio_owner_acquired",
    sessionId: ownerId,
    correlationId: options.correlationId,
    roomType: mode,
    participantRole: options.participantRole,
    outcome: decision.outcome
  });
  return getActiveRealtimeAudioOwner() as RealtimeAudioOwner;
}

export async function activateRealtimeAudioSession(
  audioSession: LiveKitAudioSession,
  mode: RealtimeAudioMode,
  ownerId: string,
  options: { speaker?: boolean; onDisplaced?: () => void; correlationId?: string; participantRole?: string } = {}
): Promise<RealtimeAudioOwner> {
  // Throws RealtimeAudioOwnershipError if a higher-priority owner holds the
  // session. Callers surface this as a user-facing "audio is busy" state.
  const owner = claimRealtimeAudioSession(mode, ownerId, {
    onDisplaced: options.onDisplaced,
    correlationId: options.correlationId,
    participantRole: options.participantRole
  });
  const reacquired = lastOwnershipDecision?.outcome === "reacquired";

  const config = resolveRealtimeAudioConfiguration(mode);
  if (Platform.OS === "ios" && typeof audioSession.setAppleAudioConfiguration === "function") {
    await audioSession.setAppleAudioConfiguration(config).catch(() => undefined);
  }
  if (typeof audioSession.configureAudio === "function") {
    await audioSession.configureAudio({ ios: { defaultOutput: options.speaker === false ? "default" : "speaker" } }).catch(() => undefined);
  }
  // Idempotent: re-activating an owner that already holds the session must not
  // start a second session. Unbalanced start/stop pairs leak the mic indicator
  // and leave the route stuck after the feature exits.
  if (!reacquired && typeof audioSession.startAudioSession === "function") {
    await audioSession.startAudioSession();
  }
  if (options.speaker !== false) {
    await selectRealtimeAudioOutput(audioSession, true).catch(() => undefined);
  }
  emitRealtimeAudioEvent({
    name: "audio_session_activated",
    sessionId: ownerId,
    correlationId: options.correlationId,
    roomType: mode,
    participantRole: options.participantRole
  });
  return owner;
}

export async function releaseRealtimeAudioSession(
  audioSession: LiveKitAudioSession | null | undefined,
  lease: string | RealtimeAudioLease
): Promise<boolean> {
  const ownerId = typeof lease === "string" ? lease : lease.ownerId;
  if (!activeRealtimeAudioOwner || activeRealtimeAudioOwner.ownerId !== ownerId) return false;
  if (typeof lease !== "string" && activeRealtimeAudioOwner.leaseId !== lease.leaseId) return false;
  displacementHandlers.delete(ownerId);
  const released = activeRealtimeAudioOwner;
  emitRealtimeAudioEvent({ name: "cleanup_started", sessionId: ownerId, roomType: released.mode });
  activeRealtimeAudioOwner = null;
  lastOwnershipDecision = null;
  await audioSession?.stopAudioSession?.().catch(() => undefined);
  emitRealtimeAudioEvent({ name: "cleanup_completed", sessionId: ownerId, roomType: released.mode });
  return true;
}

/**
 * Force-clear ownership regardless of holder. Reserved for logout, fatal
 * teardown, and test setup - never for ordinary feature exit, which must use
 * the owner-scoped release above.
 */
export async function resetRealtimeAudioOwnership(audioSession?: LiveKitAudioSession | null): Promise<void> {
  const owner = activeRealtimeAudioOwner;
  activeRealtimeAudioOwner = null;
  lastOwnershipDecision = null;
  if (owner) notifyDisplaced(owner.ownerId);
  displacementHandlers.clear();
  await audioSession?.stopAudioSession?.().catch(() => undefined);
}

export async function selectRealtimeAudioOutput(audioSession: LiveKitAudioSession, speakerEnabled: boolean): Promise<void> {
  const output = Platform.OS === "ios" ? (speakerEnabled ? "force_speaker" : "default") : speakerEnabled ? "speaker" : "earpiece";
  await audioSession.selectAudioOutput?.(output);
}

export async function showRealtimeAudioRoutePicker(audioSession: LiveKitAudioSession): Promise<void> {
  if (Platform.OS === "ios") await audioSession.showAudioRoutePicker?.();
}

function readAudioEngineBoolean(reader: (() => boolean) | undefined): boolean | null {
  if (typeof reader !== "function") return null;
  try {
    return Boolean(reader());
  } catch {
    return null;
  }
}

export function inspectRealtimeAudioEngine(audioDeviceModule: RealtimeAudioDeviceModule | null | undefined): RealtimeAudioEngineStatus {
  return {
    engineRunning: readAudioEngineBoolean(audioDeviceModule?.isEngineRunning?.bind(audioDeviceModule)),
    playoutRunning: readAudioEngineBoolean(audioDeviceModule?.isPlaying?.bind(audioDeviceModule)),
    recordingRunning: readAudioEngineBoolean(audioDeviceModule?.isRecording?.bind(audioDeviceModule))
  };
}

/**
 * Reassert the native WebRTC engine after camera startup.
 *
 * Production CoreAudio evidence showed the failing video path stopping its
 * RemoteIO engine less than half a second after camera startup while LiveKit
 * still reported both microphone publications. A published SID is therefore
 * necessary but not sufficient: the adapter must also prove that the native
 * playout/recording engine is running after the camera transition settles.
 */
export async function stabilizeRealtimeAudioEngine(
  audioDeviceModule: RealtimeAudioDeviceModule | null | undefined,
  options: {
    playout: boolean;
    recording: boolean;
    settleMs?: number;
    context?: { sessionId?: string; correlationId?: string; roomType?: string; participantRole?: string };
  }
): Promise<RealtimeAudioEngineStatus> {
  const context = options.context || {};
  emitRealtimeAudioEvent({ name: "audio_engine_guard_started", ...context });
  if (!audioDeviceModule || Platform.OS !== "ios") {
    const status = inspectRealtimeAudioEngine(audioDeviceModule);
    emitRealtimeAudioEvent({ name: "audio_engine_guard_completed", ...context, outcome: "not_required" });
    return status;
  }

  const enforce = async () => {
    const before = inspectRealtimeAudioEngine(audioDeviceModule);
    const engineStopped = before.engineRunning === false;

    // `startRecording` only resumes an already-initialized WebRTC recorder.
    // Camera startup can tear the underlying ADM down completely, which is the
    // physical Live failure captured on iPhone. In that state the SDK's
    // init-and-start operation must run first; attempting playout or ordinary
    // recording against the uninitialized engine leaves every status false.
    if (options.recording && (engineStopped || before.recordingRunning === false)) {
      if (engineStopped && typeof audioDeviceModule.startLocalRecording === "function") {
        await audioDeviceModule.startLocalRecording().catch(() => undefined);
      } else {
        await audioDeviceModule.startRecording?.().catch(() => undefined);
      }
    }

    const afterRecording = inspectRealtimeAudioEngine(audioDeviceModule);
    if (options.playout && (afterRecording.engineRunning === false || afterRecording.playoutRunning === false)) {
      await audioDeviceModule.startPlayout?.().catch(() => undefined);
    }
  };

  await enforce();
  const settleMs = Math.max(0, Math.min(Number(options.settleMs ?? 650), 1500));
  if (settleMs > 0) {
    await new Promise((resolve) => setTimeout(resolve, settleMs));
    // Camera initialization can stop RemoteIO asynchronously after its promise
    // resolves, so the second pass is the one that catches the observed race.
    await enforce();
  }
  const status = inspectRealtimeAudioEngine(audioDeviceModule);
  const failed =
    (options.playout && status.playoutRunning === false) ||
    (options.recording && status.recordingRunning === false) ||
    ((options.playout || options.recording) && status.engineRunning === false);
  emitRealtimeAudioEvent({
    name: failed ? "audio_engine_guard_failed" : "audio_engine_guard_completed",
    ...context,
    outcome: `engine=${status.engineRunning};playout=${status.playoutRunning};recording=${status.recordingRunning}`,
    failureCategory: failed ? "native_engine_not_running" : undefined
  });
  if (failed) {
    const error = new Error("The native real-time audio engine did not remain active.");
    Object.assign(error, { code: "REALTIME_AUDIO_ENGINE_INACTIVE", status });
    throw error;
  }
  return status;
}

export const PULSE_LIVE_PORTRAIT_VIDEO_RESOLUTION = {
  width: 720,
  height: 1280,
  frameRate: 30,
  aspectRatio: 9 / 16
};

export const PULSE_LIVE_VIDEO_CAPTURE_OPTIONS = {
  facingMode: "user" as const,
  frameRate: 30,
  resolution: PULSE_LIVE_PORTRAIT_VIDEO_RESOLUTION
};

export const PULSE_LIVE_VIDEO_PUBLISH_OPTIONS = {
  videoEncoding: {
    maxBitrate: 2_300_000,
    maxFramerate: 30,
    priority: "medium" as const
  },
  simulcast: true
};

export function audioPublications(participant: any): any[] {
  return Array.from(participant?.audioTrackPublications?.values?.() || []) as any[];
}

export function videoPublications(participant: any): any[] {
  return Array.from(participant?.videoTrackPublications?.values?.() || []) as any[];
}

export function publicationHasTrack(publication: any): boolean {
  return Boolean(publication?.track && publication?.isSubscribed !== false);
}

export function countPublishedAudioTracks(participant: any): number {
  return audioPublications(participant).filter(publicationHasTrack).length;
}

export function countSubscribedRemoteAudioTracks(room: any): number {
  return Array.from(room?.remoteParticipants?.values?.() || []).reduce(
    (total: number, participant: any) => total + countPublishedAudioTracks(participant),
    0
  );
}

export async function applyRemoteAudioEnabled(room: any, enabled: boolean): Promise<number> {
  let touched = 0;
  const tasks: Promise<unknown>[] = [];
  for (const remote of Array.from(room?.remoteParticipants?.values?.() || []) as any[]) {
    for (const publication of audioPublications(remote)) {
      const track = publication?.track;
      if (!track || publication?.isSubscribed === false) continue;
      if (typeof track.setEnabled === "function") {
        tasks.push(Promise.resolve(track.setEnabled(enabled)));
        touched += 1;
      } else if (track.mediaStreamTrack) {
        track.mediaStreamTrack.enabled = enabled;
        touched += 1;
      }
    }
  }
  await Promise.all(tasks).catch(() => undefined);
  return touched;
}

/**
 * Reassert an already-published microphone after camera or route transitions.
 * This never unpublishes or creates a second track: LiveKit resolves an enabled
 * source to the existing publication, while the explicit track enable repairs
 * a native media track left disabled by a camera transition.
 */
export async function reassertRealtimeMicrophone(
  room: any,
  context: { sessionId?: string; correlationId?: string; roomType?: string; participantRole?: string } = {}
): Promise<number> {
  const participant = room?.localParticipant;
  if (!participant) return 0;
  await participant.setMicrophoneEnabled?.(true);
  const publications = audioPublications(participant).filter(publicationHasTrack);
  for (const publication of publications) {
    const track = publication?.track;
    if (typeof track?.setEnabled === "function") await Promise.resolve(track.setEnabled(true)).catch(() => undefined);
    else if (track?.mediaStreamTrack) track.mediaStreamTrack.enabled = true;
  }
  emitRealtimeAudioEvent({
    name: "microphone_reasserted",
    ...context,
    outcome: publications.length > 0 ? "published" : "missing",
    audioTrackCount: publications.length
  });
  return publications.length;
}

export async function ensureMicrophonePublished(room: any): Promise<number> {
  const localParticipant = room?.localParticipant;
  if (!localParticipant) return 0;
  await localParticipant.setMicrophoneEnabled(true);
  let count = countPublishedAudioTracks(localParticipant);
  if (count > 0) return count;
  await new Promise((resolve) => setTimeout(resolve, 150));
  count = countPublishedAudioTracks(localParticipant);
  if (count > 0) return count;
  await localParticipant.setMicrophoneEnabled(false).catch(() => undefined);
  await localParticipant.setMicrophoneEnabled(true);
  await new Promise((resolve) => setTimeout(resolve, 150));
  return countPublishedAudioTracks(localParticipant);
}
