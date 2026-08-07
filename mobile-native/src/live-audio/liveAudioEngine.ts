import { Platform } from "react-native";
import {
  RealtimeAudioOwnershipError,
  resolveOwnershipDecision,
  type OwnershipDecision
} from "../core/audioOwnershipPolicy";
import { emitRealtimeAudioEvent } from "../core/realtimeAudioTelemetry";
import { reportRealtimeAudioInvariant } from "../core/realtimeAudioInvariants";
import {
  describeNativeAudioEngineState,
  drainNativeAudioEngineLogs,
  isStaleRecordingWithoutEngine,
  readNativeAudioEngineState,
  summarizeNativeAudioEngineLogs,
  type NativeAudioEngineState
} from "./liveAudioNative";

export type LiveAudioMode =
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

export type LiveAudioOwner = {
  ownerId: string;
  leaseId: number;
  mode: LiveAudioMode;
  startedAt: number;
  publishesMicrophone: boolean;
};

export type LiveAudioLease = Pick<LiveAudioOwner, "ownerId" | "leaseId" | "mode">;

type LiveKitAudioSession = {
  setAppleAudioConfiguration?: (config: any) => Promise<void>;
  configureAudio?: (config: Record<string, unknown>) => Promise<void>;
  startAudioSession?: () => Promise<void>;
  stopAudioSession?: () => Promise<void>;
  selectAudioOutput?: (deviceId: string) => Promise<void>;
  showAudioRoutePicker?: () => Promise<void>;
};

type LiveAudioDeviceModule = {
  isEngineRunning?: () => boolean;
  isPlaying?: () => boolean;
  isRecording?: () => boolean;
  startPlayout?: () => Promise<void>;
  // Receive-only repair. `startPlayout` alone only resumes an already-initialized
  // output path, so an audience whose ADM never initialized has nothing to resume.
  // An explicit stop clears the stale enable that makes the next start a no-op.
  stopPlayout?: () => Promise<void>;
  // Tells the ADM an output sink is wanted and no input is, so it does not decline
  // to start playout on a participant that will never record.
  setEngineAvailability?: (availability: {
    isInputAvailable: boolean;
    isOutputAvailable: boolean;
  }) => void | Promise<void>;
  startRecording?: () => Promise<void>;
  startLocalRecording?: () => Promise<void>;
  // Required to clear a capture path left ENABLED over a dead engine. Without an
  // explicit stop the ADM answers "already recording" and every subsequent start
  // short-circuits, which is why the observed six restart attempts changed
  // nothing. Optional because it is absent from older SDK builds.
  stopRecording?: () => Promise<void>;
  // Native ADM lever: keeps the WebRTC record engine "prepared" so it can be
  // resumed after an AVAudioSession interruption instead of being fully torn
  // down. Enabling this for publishers hardens the recorder against the
  // camera-start interruption that otherwise silences the Live host.
  setRecordingAlwaysPreparedMode?: (enabled: boolean) => void | Promise<void>;
  // Reads the lever above. Needed because the repair has to restore whatever the
  // connect path chose rather than assume it, and because a build without the
  // lever must be told apart from one where it is simply off.
  isRecordingAlwaysPreparedMode?: () => boolean;
  // Diagnostics only. Never used to decide pass/fail - they exist so a device
  // log can distinguish "the ADM declined to start output because iOS says
  // there is no output" from "the engine died", which the three booleans above
  // cannot tell apart.
  isMicrophoneMuted?: () => boolean;
  getEngineAvailability?: () => { isInputAvailable?: boolean; isOutputAvailable?: boolean } | null;
};

export type LiveAudioEngineStatus = {
  engineRunning: boolean | null;
  playoutRunning: boolean | null;
  recordingRunning: boolean | null;
};

let activeLiveAudioOwner: LiveAudioOwner | null = null;
let nextLiveAudioLeaseId = 0;

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
 * Canonical PulseSoc live audio profile.
 *
 * LiveKit real-time media shares the known-good call path. Listen-only viewers
 * still do not publish or start microphone capture (`modePublishesMicrophone`
 * remains false for `live_viewer`), but iOS remote WebRTC audio is rendered
 * through the same communication session profile as calls. A playback-only
 * profile can show host video while leaving subscribed host audio silent.
 */
export function resolveLiveAudioConfiguration(mode: LiveAudioMode | boolean): AppleAudioConfiguration {
  const normalizedMode: LiveAudioMode = typeof mode === "boolean" ? (mode ? "live_host" : "live_viewer") : mode;
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

export function getActiveLiveAudioOwner(): LiveAudioOwner | null {
  return activeLiveAudioOwner ? { ...activeLiveAudioOwner } : null;
}

export function getActiveLiveMicrophoneOwner(): LiveAudioOwner | null {
  const owner = getActiveLiveAudioOwner();
  return owner?.publishesMicrophone ? owner : null;
}

export function modePublishesMicrophone(mode: LiveAudioMode): boolean {
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
export function claimLiveAudioSession(
  mode: LiveAudioMode,
  ownerId: string,
  options: ClaimOptions = {}
): LiveAudioOwner {
  emitRealtimeAudioEvent({
    name: "audio_owner_requested",
    sessionId: ownerId,
    correlationId: options.correlationId,
    roomType: mode,
    participantRole: options.participantRole
  });
  const decision = resolveOwnershipDecision(activeLiveAudioOwner, { ownerId, mode });
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
    decision.outcome === "reacquired" && activeLiveAudioOwner
      ? activeLiveAudioOwner.startedAt
      : Date.now();

  // Every acquisition rotates the lease, even when the semantic ownerId is the
  // same. A delayed cleanup holding the previous lease can therefore never
  // stop the newer room/session instance.
  nextLiveAudioLeaseId += 1;
  activeLiveAudioOwner = {
    ownerId,
    leaseId: nextLiveAudioLeaseId,
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
  return getActiveLiveAudioOwner() as LiveAudioOwner;
}

export async function activateLiveAudioSession(
  audioSession: LiveKitAudioSession,
  mode: LiveAudioMode,
  ownerId: string,
  options: { speaker?: boolean; onDisplaced?: () => void; correlationId?: string; participantRole?: string } = {}
): Promise<LiveAudioOwner> {
  // Throws RealtimeAudioOwnershipError if a higher-priority owner holds the
  // session. Callers surface this as a user-facing "audio is busy" state.
  const owner = claimLiveAudioSession(mode, ownerId, {
    onDisplaced: options.onDisplaced,
    correlationId: options.correlationId,
    participantRole: options.participantRole
  });
  const reacquired = lastOwnershipDecision?.outcome === "reacquired";

  const config = resolveLiveAudioConfiguration(mode);
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
    await selectLiveAudioOutput(audioSession, true).catch(() => undefined);
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

export async function releaseLiveAudioSession(
  audioSession: LiveKitAudioSession | null | undefined,
  lease: string | LiveAudioLease
): Promise<boolean> {
  const ownerId = typeof lease === "string" ? lease : lease.ownerId;
  if (!activeLiveAudioOwner || activeLiveAudioOwner.ownerId !== ownerId) return false;
  if (typeof lease !== "string" && activeLiveAudioOwner.leaseId !== lease.leaseId) {
    // The rejection itself is unchanged - this is the lease generation doing
    // exactly its job. It is recorded because a stale cleanup firing in
    // production means some caller is holding a lease past its session, and
    // that is invisible unless it is counted.
    reportRealtimeAudioInvariant({
      id: "stale_cleanup_of_newer_session",
      action: "rejected",
      detail: activeLiveAudioOwner.mode,
      sessionId: ownerId,
      roomType: activeLiveAudioOwner.mode
    });
    return false;
  }
  displacementHandlers.delete(ownerId);
  const released = activeLiveAudioOwner;
  emitRealtimeAudioEvent({ name: "cleanup_started", sessionId: ownerId, roomType: released.mode });
  activeLiveAudioOwner = null;
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
export async function resetLiveAudioOwnership(audioSession?: LiveKitAudioSession | null): Promise<void> {
  const owner = activeLiveAudioOwner;
  activeLiveAudioOwner = null;
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
export async function reapplyLiveAudioConfiguration(
  audioSession: LiveKitAudioSession | null | undefined,
  mode: LiveAudioMode
): Promise<void> {
  if (Platform.OS !== "ios" || !audioSession) return;
  const config = resolveLiveAudioConfiguration(mode);
  if (typeof audioSession.setAppleAudioConfiguration === "function") {
    await audioSession.setAppleAudioConfiguration(config).catch(() => undefined);
  }
}

export async function selectLiveAudioOutput(audioSession: LiveKitAudioSession, speakerEnabled: boolean): Promise<void> {
  const output = Platform.OS === "ios" ? (speakerEnabled ? "force_speaker" : "default") : speakerEnabled ? "speaker" : "earpiece";
  await audioSession.selectAudioOutput?.(output);
}

export async function showLiveAudioRoutePicker(audioSession: LiveKitAudioSession): Promise<void> {
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

export function inspectLiveAudioEngine(audioDeviceModule: LiveAudioDeviceModule | null | undefined): LiveAudioEngineStatus {
  return {
    engineRunning: readAudioEngineBoolean(audioDeviceModule?.isEngineRunning?.bind(audioDeviceModule)),
    playoutRunning: readAudioEngineBoolean(audioDeviceModule?.isPlaying?.bind(audioDeviceModule)),
    recordingRunning: readAudioEngineBoolean(audioDeviceModule?.isRecording?.bind(audioDeviceModule))
  };
}

/**
 * Extra ADM readings for the telemetry line only.
 *
 * Deliberately NOT part of `LiveAudioEngineStatus`: nothing branches on
 * these. They exist because `engine=false;recording=true` was observed on
 * device and the three status booleans give no way to explain it. Availability
 * is the ADM's own view of whether iOS is offering an input and an output; a
 * publisher with no remote audio subscribed is expected to report output
 * unavailable, and that is the reading that tells us a stopped engine is
 * normal rather than broken.
 */
export function describeLiveAudioDiagnostics(
  audioDeviceModule: LiveAudioDeviceModule | null | undefined
): string {
  const muted = readAudioEngineBoolean(audioDeviceModule?.isMicrophoneMuted?.bind(audioDeviceModule));
  let inputAvailable: boolean | null = null;
  let outputAvailable: boolean | null = null;
  try {
    const availability = audioDeviceModule?.getEngineAvailability?.();
    if (availability) {
      inputAvailable = availability.isInputAvailable ?? null;
      outputAvailable = availability.isOutputAvailable ?? null;
    }
  } catch {
    // A diagnostic read must never affect the outcome it is describing.
  }
  return `micMuted=${muted};inputAvail=${inputAvailable};outputAvail=${outputAvailable}`;
}

/**
 * The native readings, as their own telemetry fields.
 *
 * Deliberately not appended to `outcome`: that field is truncated at 96
 * characters, and the first attempt at this concatenated the native block onto
 * it, so the diagnostic added to explain `engine=false` was clipped away before
 * it ever reached the log line reporting the failure.
 *
 * The patched native bridge is the only place the ADM's enabled-vs-running
 * split and WebRTC's own engine-start error are visible. It is absent on
 * Android and on any binary older than the patch, where this degrades to
 * `native=unavailable` rather than failing.
 *
 * Draining is destructive, so this must be called exactly once per emitted
 * event or a later event will report an earlier event's native errors.
 */
export function readLiveAudioNativeFields(): { engineState: string; nativeError: string } {
  return {
    engineState: describeNativeAudioEngineState(readNativeAudioEngineState()),
    nativeError: summarizeNativeAudioEngineLogs(drainNativeAudioEngineLogs())
  };
}

/**
 * Repair passes inside one guard invocation. A CONSTANT, not an option.
 *
 * Callers must not be able to buy a green light with more attempts. Camera
 * startup stops RemoteIO asynchronously and the exact moment varies run to run,
 * so more than one pass is needed to cover the window - but an engine that is
 * still dead after three bounded passes is broken, and sweeping it for longer
 * only converts a fast honest failure into a slow one.
 */
const LIVE_AUDIO_RECOVERY_PASSES = 3;

/** Milliseconds between recovery passes when the caller gives no settle time. */
const LIVE_AUDIO_DEFAULT_SETTLE_MS = 400;

/** Where in the Live/call startup sequence a guard invocation was made. */
export type LiveAudioGuardStage =
  | "session_start"
  | "room_connected"
  | "camera_start"
  | "track_subscribed"
  | "app_foreground"
  | "route_change"
  | "manual_retry"
  | "unspecified";

export type LiveAudioGuardContext = {
  sessionId?: string;
  correlationId?: string;
  roomType?: string;
  participantRole?: string;
};

/**
 * Who this participant is, in terms of what a healthy engine means for them.
 *
 * Before this existed, each of the sixteen guard call sites hand-assembled its
 * own `{ playout, recording, requirePlayout }` triple. That is how the incident's
 * predecessor happened: one site was relaxed to unblock a host, and because the
 * relaxation lived at a call site rather than in a named role, nothing said
 * whether it also applied to viewers or to callers. A role has exactly one
 * profile, and it is written down once, here.
 */
export type LiveAudioRole =
  /** Live host publishing microphone only - no camera, no remote audio yet. */
  | "HOST_AUDIO_ONLY"
  /** Live host publishing microphone and camera. */
  | "HOST_AUDIO_VIDEO"
  /** Live viewer: renders remote audio, publishes nothing. */
  | "AUDIENCE"
  /** One- or two-way call participant: publishes and renders. */
  | "CALL_PARTICIPANT";

export type LiveAudioHealthProfile = {
  role: LiveAudioRole;
  /** Try to start the render path. */
  playout: boolean;
  /** Try to start the capture path. */
  recording: boolean;
  /** Whether a down render path is a FAILURE rather than merely not-started. */
  requirePlayout: boolean;
  /** Why `requirePlayout` is what it is. Read this before changing one. */
  rationale: string;
};

/**
 * The only place a role's audio health requirements are defined.
 *
 * `engineRunning` is required for every profile and is deliberately not a field:
 * it is the one signal that reflects whether AVAudioEngine is actually running,
 * and making it configurable would let a silent broadcast be declared healthy.
 */
export const LIVE_AUDIO_HEALTH_PROFILES: Readonly<Record<LiveAudioRole, LiveAudioHealthProfile>> =
  Object.freeze({
    HOST_AUDIO_ONLY: {
      role: "HOST_AUDIO_ONLY",
      playout: true,
      recording: true,
      requirePlayout: false,
      rationale:
        "A host publishes before anyone subscribes, so iOS has no sink to render and the output side is " +
        "legitimately down. Requiring playout here is a red light no healthy host can turn green. Recording " +
        "stays required, so a mic that is denied or revoked still fails closed."
    },
    HOST_AUDIO_VIDEO: {
      role: "HOST_AUDIO_VIDEO",
      playout: true,
      recording: true,
      requirePlayout: false,
      rationale:
        "Same as HOST_AUDIO_ONLY. Adding the camera does not create a remote audio sink, and camera startup is " +
        "the transition that stops the record engine - which the required engine and recording checks catch."
    },
    AUDIENCE: {
      role: "AUDIENCE",
      playout: true,
      recording: false,
      requirePlayout: true,
      rationale:
        "A viewer exists to hear the broadcast. Playout down means silence, which is the whole failure. A viewer " +
        "must never be asked to record - that would open a second capture path and steal the session."
    },
    CALL_PARTICIPANT: {
      role: "CALL_PARTICIPANT",
      playout: true,
      recording: true,
      requirePlayout: true,
      rationale:
        "In a call there is a remote party from the first moment, so playout down means the user hears nothing. " +
        "Fail closed on both directions."
    }
  });

/**
 * The guard options for a role.
 *
 * Callers pass a role and a stage; they do not get to assemble their own health
 * requirements. `settleMs`, `reactivateSession` and `context` remain per-call
 * because they describe the situation, not the definition of health.
 */
export function liveAudioProfileFor(role: LiveAudioRole): LiveAudioHealthProfile {
  return LIVE_AUDIO_HEALTH_PROFILES[role];
}

/**
 * Reassert the native WebRTC engine after camera startup.
 *
 * Production CoreAudio evidence showed the failing video path stopping its
 * RemoteIO engine less than half a second after camera startup while LiveKit
 * still reported both microphone publications. A published SID is therefore
 * necessary but not sufficient: the adapter must also prove that the native
 * playout/recording engine is running after the camera transition settles.
 *
 * This is the ONLY recovery path. A second, non-throwing "legacy recover"
 * function used to run immediately before it with the same telemetry context,
 * which both duplicated every guard line in the device log and split the repair
 * logic across two places that then disagreed about when to restart a recorder.
 */
export async function stabilizeLiveAudioEngine(
  audioDeviceModule: LiveAudioDeviceModule | null | undefined,
  options: {
    /**
     * The participant's role. When given it is AUTHORITATIVE: the profile's
     * `playout`, `recording` and `requirePlayout` replace whatever the caller
     * passed, and the role name is carried on every event.
     *
     * This exists so a relaxation can never again be made at one call site and
     * silently not apply, or wrongly apply, to another. If a role's requirements
     * need to change, they change in `LIVE_AUDIO_HEALTH_PROFILES` where the
     * rationale sits next to them - not in whichever screen happens to be failing.
     */
    role?: LiveAudioRole;
    playout: boolean;
    recording: boolean;
    /**
     * Whether a down playout path is a FAILURE, as opposed to something we merely
     * try to start. Defaults to `playout` so calls keep their fail-closed
     * behaviour: in a call, playout down means the user cannot hear the other
     * party, which is a broken call.
     *
     * A Live HOST at startup is the case that must set this false. It publishes
     * before any remote participant is subscribed, so iOS has nothing to render
     * and AURemoteIO's output side is legitimately not running - `startPlayout()`
     * is a no-op with no sink. Measured on iPhone (P3r7or, 2026-08-05), three
     * consecutive broadcasts reported `engine=true;recording=true;playout=false`
     * and were killed by this guard even though the microphone was live and the
     * broadcast would have been audible. Requiring playout there is a red light
     * that no healthy host can ever turn green.
     *
     * Recording stays required for publishers, so a genuinely silent broadcast
     * (mic denied or revoked) still fails closed.
     */
    requirePlayout?: boolean;
    // A `requireEngineRunning` escape hatch was drafted here and deliberately
    // REMOVED. `engineRunning` is the only signal that reflects whether
    // AVAudioEngine is actually rendering; `recordingRunning` is an ADM-level
    // flag that survives the engine dying underneath it. Allowing a publisher
    // to pass on `recording=true` while `engine=false` would let a silent
    // broadcast report healthy, which is the exact failure this guard exists
    // to catch. The engine is never optional. Fix the engine, not the check.
    settleMs?: number;
    /**
     * Re-establish the owner's AVAudioSession configuration when the engine is
     * found stopped, BEFORE the ADM restart. Camera startup can leave the shared
     * session in a non-record-capable state; restarting the recorder against it
     * silently no-ops. Supplied by the publisher path only.
     */
    reactivateSession?: () => Promise<void>;
    /**
     * Where this invocation sits in the startup sequence. Carried on every event
     * this call emits so two guard runs in one session are distinguishable in a
     * log; without it the camera-start guard and the room-connected guard were
     * byte-identical lines and read as one event logged twice.
     */
    stage?: LiveAudioGuardStage;
    context?: LiveAudioGuardContext;
  }
): Promise<LiveAudioEngineStatus> {
  // A role, when supplied, replaces the caller's hand-assembled triple outright.
  // Merging the two would reintroduce exactly what the profiles exist to prevent:
  // a call site quietly holding a different definition of "healthy" than the role
  // it claims to be.
  const profile = options.role ? liveAudioProfileFor(options.role) : null;
  const wantPlayout = profile ? profile.playout : options.playout;
  const wantRecording = profile ? profile.recording : options.recording;
  const requirePlayout = profile ? profile.requirePlayout : options.requirePlayout ?? options.playout;
  const context = options.context || {};
  const failureStage = options.stage || "unspecified";
  emitRealtimeAudioEvent({ name: "audio_engine_guard_started", ...context, failureStage });
  if (!audioDeviceModule || Platform.OS !== "ios") {
    const status = inspectLiveAudioEngine(audioDeviceModule);
    emitRealtimeAudioEvent({
      name: "audio_engine_guard_completed",
      ...context,
      failureStage,
      outcome: "not_required"
    });
    return status;
  }

  const enforce = async () => {
    const before = inspectLiveAudioEngine(audioDeviceModule);
    const engineStopped = before.engineRunning === false;
    const native = readNativeAudioEngineState();

    // The camera can reconfigure the shared AVAudioSession out from under the
    // WebRTC recorder. Restore the record-capable session first, otherwise the
    // ADM restart below runs against a session it cannot start into.
    if (engineStopped && options.reactivateSession) {
      await options.reactivateSession().catch(() => undefined);
    }

    // THE FAILING STATE. `recording=true` over `engine=false` is the ADM's
    // capture path left ENABLED across a dead AVAudioEngine - the flag outlives
    // the engine, so `isRecording` answers true while nothing is captured and
    // the broadcast publishes silence.
    //
    // Nothing repaired this before: both recovery paths gated on
    // `recordingRunning === false`, which this state never satisfies, so the
    // observed six passes performed no work at all. `startRecording()` would
    // not have helped either - it short-circuits on an ADM that already reports
    // itself recording. Only an explicit stop, clearing the stale enable, lets
    // the following init-and-start rebuild the engine.
    //
    // The tear-down that must NOT happen is restarting a recorder that is
    // genuinely capturing. `engineRunning` is what separates the two, and it can
    // only be answered by the native bridge. `inputRunning` does NOT separate
    // them: with AVAudioEngine stopped it is as stale as `inputEnabled`, and
    // requiring it to be false is what made this branch unreachable on the very
    // device the incident was reported from.
    const staleRecorder = wantRecording && isStaleRecordingWithoutEngine(native);

    // THE SAME STATE, SEEN WITHOUT THE BRIDGE.
    //
    // `isStaleRecordingWithoutEngine(null)` is false, by design - with no native
    // reading there is no honest way to say the recorder is stale. But the
    // consequence was that the one repair written for this incident became
    // unreachable on any binary without the patched bridge, and the `else if`
    // below could not pick it up either, because that branch requires
    // `recordingRunning === false` and this state reports `true`. Both repairs
    // declined, the guard threw anyway, and a host was told the broadcast could
    // not start after the code had attempted nothing at all.
    //
    // `engineStopped` is what makes the fallback safe without the bridge. If the
    // engine is not running, no capture is in flight, so there is no live
    // recorder for the stop below to tear down - the tear-down the bridge was
    // introduced to prevent cannot happen here. When the bridge IS present its
    // reading wins, and this never fires.
    const blindStaleRecorder =
      wantRecording && native === null && engineStopped && before.recordingRunning !== false;

    if (staleRecorder || blindStaleRecorder) {
      // Always-prepared mode (native `SetInitRecordingPersistentMode`) is what
      // makes the stop below a no-op: its entire purpose is to keep the record
      // path INITIALIZED across a stop so it can be resumed. Leave it on and
      // `stopRecording()` returns without clearing `inputEnabled`, the next
      // start short-circuits on an ADM that still answers "already recording",
      // and the engine is never rebuilt - which is this incident.
      //
      // So the lever is lowered for the duration of the repair and put back
      // exactly as it was found. It is not disabled outright: it is a real
      // mitigation for the camera-start interruption, and turning it off here
      // would trade one wedge for the silence it was added to prevent.
      const alwaysPrepared = readRecordingAlwaysPrepared(audioDeviceModule, native);
      if (alwaysPrepared === true) {
        await setRecordingAlwaysPrepared(audioDeviceModule, false);
      }
      await audioDeviceModule.stopRecording?.().catch(() => undefined);
      if (typeof audioDeviceModule.startLocalRecording === "function") {
        await audioDeviceModule.startLocalRecording().catch(() => undefined);
      } else {
        await audioDeviceModule.startRecording?.().catch(() => undefined);
      }
      if (alwaysPrepared === true) {
        await setRecordingAlwaysPrepared(audioDeviceModule, true);
      }
    } else if (wantRecording && before.recordingRunning === false) {
      // `startRecording` only resumes an already-initialized WebRTC recorder.
      // Camera startup can tear the underlying ADM down completely; in that
      // state the SDK's init-and-start operation must run first, or playout and
      // ordinary recording both run against an uninitialized engine and leave
      // every status false.
      if (engineStopped && typeof audioDeviceModule.startLocalRecording === "function") {
        await audioDeviceModule.startLocalRecording().catch(() => undefined);
      } else {
        await audioDeviceModule.startRecording?.().catch(() => undefined);
      }
    }

    const afterRecording = inspectLiveAudioEngine(audioDeviceModule);
    if (wantPlayout && (afterRecording.engineRunning === false || afterRecording.playoutRunning === false)) {
      // `startPlayout` asks the ADM for outputRunning WITHOUT outputEnabled, and
      // ModifyEngineState rejects that pair outright: "Output must be enabled if
      // running". It is not the harmless no-op a host profile assumes. A Live
      // host subscribes to nobody, so output stays disabled for the whole
      // broadcast and every pass of this guard spent its only native call on a
      // transition the engine refuses - captured on P3r7or (2026-08-07) as the
      // sole nativeLogs entry of a pass that repaired nothing.
      const output = readNativeAudioEngineState();
      if (!output || output.outputEnabled || output.playoutInitialized) {
        await audioDeviceModule.startPlayout?.().catch(() => undefined);
      }
    }

    // RECEIVE-ONLY REPAIR. This is the branch the call path never needed and so
    // never had, and its absence is why a Live viewer could not be repaired.
    //
    // Every ADM restart above is gated on `wantRecording`. That is correct for a
    // call, where the local participant always records, so the init-and-start
    // operation is always reachable. A Live audience must never record - a second
    // capture path would steal the session - so on the audience profile
    // (`recording:false`, `requirePlayout:true`) the gate excludes the viewer from
    // every repair the guard owns. All a viewer could ever do was call
    // `startPlayout()`, which only resumes an already-initialized engine. Against
    // an uninitialized ADM it is a no-op, `engineRunning` and `playoutRunning`
    // stay false, and the guard then fails on a condition it has no branch to fix.
    // That is a required check with nothing behind it: the viewer is told to prove
    // playout is up and given no way to bring it up.
    //
    // The repair is the same shape as the stale-recorder branch above, applied to
    // the output side. An explicit `stopPlayout` clears a playout path left
    // ENABLED over a dead engine - the state in which the next `startPlayout`
    // short-circuits on an ADM that already answers "playing" - and the following
    // start rebuilds it. Availability is declared first so the ADM is not asked to
    // start an output it has been told does not exist.
    //
    // Deliberately narrow: it runs only when recording is not wanted, so it can
    // never fire on a host or on a call participant, and only after the ordinary
    // `startPlayout` above has already been tried and left the engine down.
    if (wantPlayout && !wantRecording) {
      const afterPlayout = inspectLiveAudioEngine(audioDeviceModule);
      if (afterPlayout.engineRunning === false || afterPlayout.playoutRunning === false) {
        if (typeof audioDeviceModule.setEngineAvailability === "function") {
          await Promise.resolve(
            audioDeviceModule.setEngineAvailability({ isInputAvailable: false, isOutputAvailable: true })
          ).catch(() => undefined);
        }
        await audioDeviceModule.stopPlayout?.().catch(() => undefined);
        await audioDeviceModule.startPlayout?.().catch(() => undefined);
      }
    }
  };

  // Bounded, fixed, and not caller-tunable. `settleMs` only spaces the passes;
  // it cannot add one. A guard that could be given more attempts would let a
  // failing broadcast be retried into a green light, which is the failure mode
  // this whole guard exists to prevent.
  const settleMs = Math.max(0, Math.min(Number(options.settleMs ?? LIVE_AUDIO_DEFAULT_SETTLE_MS), 1500));
  let status = inspectLiveAudioEngine(audioDeviceModule);
  for (let pass = 1; pass <= LIVE_AUDIO_RECOVERY_PASSES; pass += 1) {
    await enforce();
    status = inspectLiveAudioEngine(audioDeviceModule);
    const satisfied =
      status.engineRunning !== false &&
      (!wantRecording || status.recordingRunning !== false) &&
      (!requirePlayout || status.playoutRunning !== false);
    emitRealtimeAudioEvent({
      name: "audio_engine_recovery_attempt",
      ...context,
      failureStage,
      recoveryAttempt: pass,
      outcome:
        `engine=${status.engineRunning};playout=${status.playoutRunning};` +
        `recording=${status.recordingRunning};satisfied=${satisfied}`,
      ...readLiveAudioNativeFields()
    });
    if (satisfied) break;
    if (pass < LIVE_AUDIO_RECOVERY_PASSES && settleMs > 0) {
      await new Promise((resolve) => setTimeout(resolve, settleMs));
    }
  }
  const failed =
    (requirePlayout && status.playoutRunning === false) ||
    (wantRecording && status.recordingRunning === false) ||
    ((requirePlayout || wantRecording) && status.engineRunning === false);
  emitRealtimeAudioEvent({
    name: failed ? "audio_engine_guard_failed" : "audio_engine_guard_completed",
    ...context,
    failureStage,
    // `required=` records what this call was actually willing to fail on, so a
    // log line is enough to tell "playout was down and that was fine" apart from
    // "playout was down and we shipped a silent broadcast anyway". The trailing
    // availability fields come from the SDK's own view of what iOS is offering,
    // so a capture can distinguish "no output exists to run" from "the engine
    // died" - which the three booleans alone cannot.
    outcome:
      `engine=${status.engineRunning};playout=${status.playoutRunning};recording=${status.recordingRunning};` +
      `role=${options.role || "adhoc"};required=playout:${requirePlayout},recording:${wantRecording},engine:true;` +
      describeLiveAudioDiagnostics(audioDeviceModule),
    ...readLiveAudioNativeFields(),
    failureCategory: failed ? "native_engine_not_running" : undefined
  });
  if (failed) {
    const error = new Error("The native real-time audio engine did not remain active.");
    // `stage` and `role` travel with the error so the screen can say what actually
    // failed. The Live copy previously said "while the camera started" for every
    // stage, including failures that happened before the camera was ever touched.
    Object.assign(error, {
      code: "LIVE_AUDIO_ENGINE_INACTIVE",
      status,
      stage: failureStage,
      role: options.role || null
    });
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
export async function enableLiveRecordingAlwaysPrepared(
  audioDeviceModule: LiveAudioDeviceModule | null | undefined
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
 * Whether always-prepared mode is currently on.
 *
 * Returns `null`, not `false`, when the answer is unknown - a build without the
 * lever and a build with it switched off must not be conflated, because only the
 * first means "there is nothing here to restore". The native state snapshot is
 * preferred over the ADM getter: it is the same read the stale-recorder verdict
 * was made from, so the repair cannot act on a newer or older reading than the
 * decision that triggered it.
 */
export function readRecordingAlwaysPrepared(
  audioDeviceModule: LiveAudioDeviceModule | null | undefined,
  native: NativeAudioEngineState | null
): boolean | null {
  if (native && typeof native.recordingAlwaysPrepared === "boolean") return native.recordingAlwaysPrepared;
  if (!audioDeviceModule || typeof audioDeviceModule.isRecordingAlwaysPreparedMode !== "function") return null;
  try {
    const value = audioDeviceModule.isRecordingAlwaysPreparedMode();
    return typeof value === "boolean" ? value : null;
  } catch {
    return null;
  }
}

/**
 * Move the lever, swallowing every failure.
 *
 * Deliberately not `Promise<boolean>`: no caller may branch on whether the lever
 * moved. Making the repair conditional on it would let a build that lacks the
 * native method skip the stop-and-restart entirely, and that build is exactly
 * the one where the stop already works.
 */
async function setRecordingAlwaysPrepared(
  audioDeviceModule: LiveAudioDeviceModule,
  enabled: boolean
): Promise<void> {
  if (typeof audioDeviceModule.setRecordingAlwaysPreparedMode !== "function") return;
  try {
    await audioDeviceModule.setRecordingAlwaysPreparedMode(enabled);
  } catch {
    // The repair proceeds regardless. A lever that will not move is not a
    // reason to leave a dead engine in place.
  }
}

// `recoverLiveRecordingEngine` lived here and was DELETED.
//
// It ran immediately before `stabilizeLiveAudioEngine` on the Live
// publisher path with an identical telemetry context, so every guard line in
// the device log appeared twice with no field to tell the two apart - the
// reported duplicate-event symptom. Worse, the two copies of the repair had
// drifted: both gated the recorder restart on `recordingRunning === false`, a
// condition the observed `recording=true;engine=false` failure never meets, so
// neither of them attempted any repair at all.
//
// Both jobs now live in the single bounded loop inside
// `stabilizeLiveAudioEngine`: it emits exactly one guard start and one
// terminal event per invocation, one `audio_engine_recovery_attempt` per pass,
// and repairs the stale-recorder state with an explicit stop before restart.

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
export async function reassertLiveMicrophone(
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
