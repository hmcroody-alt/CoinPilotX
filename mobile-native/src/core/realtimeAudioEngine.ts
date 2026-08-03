import { Platform } from "react-native";
import {
  RealtimeAudioOwnershipError,
  resolveOwnershipDecision,
  type OwnershipDecision
} from "./audioOwnershipPolicy";
import { emitRealtimeAudioEvent } from "./realtimeAudioTelemetry";
import { reportRealtimeAudioInvariant } from "./realtimeAudioInvariants";

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
  // Native ADM lever: keeps the WebRTC record engine "prepared" so it can be
  // resumed after an AVAudioSession interruption instead of being fully torn
  // down. Enabling this for publishers hardens the recorder against the
  // camera-start interruption that otherwise silences the Live host.
  setRecordingAlwaysPreparedMode?: (enabled: boolean) => void | Promise<void>;
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
 * LiveKit real-time media shares the known-good call path. Listen-only viewers
 * still do not publish or start microphone capture (`modePublishesMicrophone`
 * remains false for `live_viewer`), but iOS remote WebRTC audio is rendered
 * through the same communication session profile as calls. A playback-only
 * profile can show host video while leaving subscribed host audio silent.
 */
export function resolveRealtimeAudioConfiguration(mode: RealtimeAudioMode | boolean): AppleAudioConfiguration {
  const normalizedMode: RealtimeAudioMode = typeof mode === "boolean" ? (mode ? "live_host" : "live_viewer") : mode;
  if (
    normalizedMode === "audio_call" ||
    normalizedMode === "video_call" ||
    normalizedMode === "live_host" ||
    normalizedMode === "live_guest" ||
    normalizedMode === "live_viewer" ||
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
  if (typeof lease !== "string" && activeRealtimeAudioOwner.leaseId !== lease.leaseId) {
    // The rejection itself is unchanged - this is the lease generation doing
    // exactly its job. It is recorded because a stale cleanup firing in
    // production means some caller is holding a lease past its session, and
    // that is invisible unless it is counted.
    reportRealtimeAudioInvariant({
      id: "stale_cleanup_of_newer_session",
      action: "rejected",
      detail: activeRealtimeAudioOwner.mode,
      sessionId: ownerId,
      roomType: activeRealtimeAudioOwner.mode
    });
    return false;
  }
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

/**
 * Re-apply a publisher's Apple audio configuration onto the shared session.
 *
 * Enabling the camera on iOS routes through the same AVAudioSession the WebRTC
 * engine records from. Camera capture can reconfigure that session's category or
 * mode away from `playAndRecord`/`videoChat`, which is a state the record engine
 * cannot restart into — the physical "engine did not remain active" failure.
 * Re-asserting our owner-chosen configuration restores a record-capable session
 * before the engine guard tries to restart the ADM. It is idempotent: when the
 * session already holds this configuration, the native call is a no-op.
 */
export async function reapplyRealtimeAudioConfiguration(
  audioSession: LiveKitAudioSession | null | undefined,
  mode: RealtimeAudioMode
): Promise<void> {
  if (Platform.OS !== "ios" || !audioSession) return;
  const config = resolveRealtimeAudioConfiguration(mode);
  if (typeof audioSession.setAppleAudioConfiguration === "function") {
    await audioSession.setAppleAudioConfiguration(config).catch(() => undefined);
  }
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
    /**
     * Re-establish the owner's AVAudioSession configuration when the engine is
     * found stopped, BEFORE the ADM restart. Camera startup can leave the shared
     * session in a non-record-capable state; restarting the recorder against it
     * silently no-ops. Supplied by the publisher path only.
     */
    reactivateSession?: () => Promise<void>;
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

    // The camera can reconfigure the shared AVAudioSession out from under the
    // WebRTC recorder. Restore the record-capable session first, otherwise the
    // ADM restart below runs against a session it cannot start into.
    if (engineStopped && options.reactivateSession) {
      await options.reactivateSession().catch(() => undefined);
    }

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

/**
 * Ask the native ADM to keep the WebRTC recorder permanently prepared.
 *
 * On iPhone, starting the camera fires an AVAudioSession interruption that stops
 * AURemoteIO (the record engine); iOS never delivers an interruption-*ended*
 * event while the camera holds the session, so nothing restarts the recorder and
 * the Live host goes silent. "Always prepared" mode makes the ADM keep the
 * record graph resumable across that interruption so a later start resumes it
 * rather than finding it fully torn down. Best-effort and iOS-only; a missing
 * native method or failure must never block going Live.
 */
export async function enableRealtimeRecordingAlwaysPrepared(
  audioDeviceModule: RealtimeAudioDeviceModule | null | undefined
): Promise<boolean> {
  if (!audioDeviceModule || Platform.OS !== "ios") return false;
  if (typeof audioDeviceModule.setRecordingAlwaysPreparedMode !== "function") return false;
  try {
    await audioDeviceModule.setRecordingAlwaysPreparedMode(true);
    return true;
  } catch {
    return false;
  }
}

/**
 * Proactively restart a torn-down WebRTC record engine WITHOUT touching the
 * shared AVAudioSession.
 *
 * This is the recovery for the camera-start interruption described above: after
 * the camera settles, re-init and start the recorder ourselves because iOS will
 * never signal that the interruption ended. Unlike stabilizeRealtimeAudioEngine
 * this never reconfigures the audio session (reconfiguring mid-broadcast was
 * observed to disrupt the running WebRTC video pipeline on the legacy publisher
 * path) and never throws - a recovery attempt must not fail a healthy broadcast
 * closed. Returns the observed engine status for logging.
 */
export async function recoverRealtimeRecordingEngine(
  audioDeviceModule: RealtimeAudioDeviceModule | null | undefined,
  options?: {
    /**
     * Re-activate the shared AVAudioSession when the engine is found stopped.
     * Device syslog proves the camera transition leaves the session INACTIVE
     * (`cmsSetIsActive ... going inactive` right after camera startup), so the
     * recorder cannot be started until the session is activated again. This must
     * be a plain setActive(true) (LiveKit `startAudioSession`) - NOT a category
     * reassert, which is what disrupts the running video pipeline.
     */
    reactivateSession?: () => Promise<void>;
    settleMs?: number;
    passes?: number;
    context?: { sessionId?: string; correlationId?: string; roomType?: string; participantRole?: string };
  }
): Promise<RealtimeAudioEngineStatus> {
  const context = options?.context || {};
  emitRealtimeAudioEvent({ name: "audio_engine_guard_started", ...context, outcome: "legacy_recover" });
  if (!audioDeviceModule || Platform.OS !== "ios") {
    return inspectRealtimeAudioEngine(audioDeviceModule);
  }

  const enforce = async () => {
    const before = inspectRealtimeAudioEngine(audioDeviceModule);
    if (before.engineRunning === false || before.recordingRunning === false) {
      // Restore the (now inactive) session before touching the ADM, otherwise
      // the restart below runs against a session it cannot start into.
      if (before.engineRunning === false && options?.reactivateSession) {
        await options.reactivateSession().catch(() => undefined);
      }
      // A completely torn-down engine must be re-initialized (init-and-start);
      // startRecording alone only resumes an already-initialized recorder.
      if (before.engineRunning === false && typeof audioDeviceModule.startLocalRecording === "function") {
        await audioDeviceModule.startLocalRecording().catch(() => undefined);
      } else {
        await audioDeviceModule.startRecording?.().catch(() => undefined);
      }
    }
    const after = inspectRealtimeAudioEngine(audioDeviceModule);
    if (after.engineRunning === false || after.playoutRunning === false) {
      await audioDeviceModule.startPlayout?.().catch(() => undefined);
    }
  };

  // Camera startup stops RemoteIO asynchronously (~1s after the camera promise
  // resolves on iPhone) and the exact moment varies run-to-run, so a single pass
  // can fire before the teardown. Sweep several passes across the window and stop
  // as soon as the recorder is confirmed back up.
  const settleMs = Math.max(0, Math.min(Number(options?.settleMs ?? 400), 1500));
  const maxPasses = Math.max(1, Math.floor(options?.passes ?? 4));
  let status = inspectRealtimeAudioEngine(audioDeviceModule);
  for (let pass = 0; pass < maxPasses; pass += 1) {
    await enforce();
    status = inspectRealtimeAudioEngine(audioDeviceModule);
    if (status.engineRunning !== false && status.recordingRunning !== false && status.playoutRunning !== false) break;
    if (pass < maxPasses - 1 && settleMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, settleMs));
    }
  }
  emitRealtimeAudioEvent({
    name: "audio_engine_guard_completed",
    ...context,
    outcome: `legacy_recover;engine=${status.engineRunning};playout=${status.playoutRunning};recording=${status.recordingRunning}`
  });
  return status;
}

// Internal. Consumed only by PULSE_LIVE_VIDEO_CAPTURE_OPTIONS below; exporting
// it invited a feature to build its own capture options from the same numbers
// instead of using the shared ones.
const PULSE_LIVE_PORTRAIT_VIDEO_RESOLUTION = {
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
      if (enabled && publication?.isSubscribed === false && typeof publication?.setSubscribed === "function") {
        tasks.push(Promise.resolve(publication.setSubscribed(true)));
      }
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
