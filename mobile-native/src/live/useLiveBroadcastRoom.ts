import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, Platform } from "react-native";
import { RealtimeAudioOwnershipError, ownershipDenialMessage } from "../core/audioOwnershipPolicy";
import {
  activateRealtimeAudioSession,
  applyRemoteAudioEnabled as driveRemoteAudioEnabled,
  audioPublications,
  enableRealtimeRecordingAlwaysPrepared,
  ensureMicrophonePublished,
  recoverRealtimeRecordingEngine,
  PULSE_LIVE_VIDEO_CAPTURE_OPTIONS,
  PULSE_LIVE_VIDEO_PUBLISH_OPTIONS,
  publicationHasTrack,
  inspectRealtimeAudioEngine,
  reapplyRealtimeAudioConfiguration,
  reassertRealtimeMicrophone,
  releaseRealtimeAudioSession,
  resolveRealtimeAudioConfiguration,
  selectRealtimeAudioOutput,
  showRealtimeAudioRoutePicker,
  stabilizeRealtimeAudioEngine,
  type RealtimeAudioEngineStatus,
  type RealtimeAudioLease,
  type RealtimeAudioMode,
  videoPublications
} from "../core/realtimeAudioEngine";
import { setRealtimeMicrophoneEnabled } from "../core/realtimeMicrophonePublisher";
import { RealtimeAudioStateMachine } from "../core/realtimeAudioStateMachine";
import { createRealtimeAudioCorrelationId } from "../core/realtimeAudioTelemetry";
import { describeMediaQualityFlags, parseMediaQualityFlags } from "../core/mediaQualityFlags";
import {
  buildRoomQualityOptions,
  resolveMediaQualityPlan,
  type MediaQualityPlan
} from "../core/mediaQualityPolicy";
import { emitMediaQualityEvent } from "../core/mediaQualityTelemetry";
import { getLiveRuntime } from "./liveRuntime";
import { isLiveAudioV2EnabledForSession, resolveLiveAudioPath, resolveLiveAudioPathForSession } from "./liveAudioFlags";
import { publishLiveMicrophone } from "./liveAudioPublisher";
import {
  classifyDisconnect,
  millisecondsUntilRefresh,
  nextReconnectDelayMs,
  shouldAttemptReconnect
} from "./liveAudioRecovery";
import { emitLiveAudioEvent } from "./liveAudioTelemetry";
import { createLiveAudioTrace, type LiveAudioTrace } from "./liveAudioTrace";
import type { LiveKitCredentials } from "./liveSession";

/**
 * LiveKit room hook for native live broadcasting. Unlike the 1:1 call hook this
 * tracks ALL participants as an array (host + co-host guests + the local
 * publisher) so the Reels/host UI can render a real multi-guest stage. It reuses
 * the same dynamic-import + registerGlobals pattern as `useNativeCallRoom` so no
 * native rebuild is required — the LiveKit pods are already in the binary.
 */

/**
 * Token refresh retry budget. The refresh is scheduled TOKEN_REFRESH_MARGIN_MS
 * (5 min) before the token expires, so retrying every 45s gives several honest
 * attempts inside that margin without turning a revoked guest slot into a
 * request loop against the token endpoint.
 */
const TOKEN_REFRESH_RETRY_MS = 45_000;
const TOKEN_REFRESH_MAX_FAILURES = 4;

export type LiveParticipant = {
  identity: string;
  name: string;
  isLocal: boolean;
  isHost: boolean;
  videoTrack: any | null;
  audioTrack: any | null;
  hasVideo: boolean;
  hasAudio: boolean;
  audioMuted: boolean;
  speaking: boolean;
};

type LiveBroadcastState = {
  supported: boolean;
  connecting: boolean;
  connected: boolean;
  reconnecting: boolean;
  connectionState: string;
  connectionQuality: string;
  error: string;
  canPublish: boolean;
  audioEnabled: boolean;
  videoEnabled: boolean;
  speakerEnabled: boolean;
  remoteAudioEnabled: boolean;
  localVideoTrack: any | null;
  localAudioTrackCount: number;
  remoteAudioTrackCount: number;
  remoteVideoTrackCount: number;
  participants: LiveParticipant[];
  reconnectCount: number;
  disconnectReason: string;
  diagnosticCode: string;
  /** Which audio route this broadcast is actually running, for the QA overlay. */
  audioPath: "v2_isolated" | "v1_legacy";
  /** True when another feature (an active call) holds the audio session. */
  audioBusy: boolean;
  /** True while a bounded automatic reconnect is pending. */
  recovering: boolean;
};

const initialState: LiveBroadcastState = {
  supported: Platform.OS !== "web",
  connecting: false,
  connected: false,
  reconnecting: false,
  connectionState: "disconnected",
  connectionQuality: "unknown",
  error: "",
  canPublish: false,
  audioEnabled: false,
  videoEnabled: false,
  speakerEnabled: true,
  remoteAudioEnabled: true,
  localVideoTrack: null,
  localAudioTrackCount: 0,
  remoteAudioTrackCount: 0,
  remoteVideoTrackCount: 0,
  participants: [],
  reconnectCount: 0,
  disconnectReason: "",
  diagnosticCode: "",
  audioPath: "v1_legacy",
  audioBusy: false,
  recovering: false
};

let globalsRegistered = false;

export const applyRemoteAudioEnabled = driveRemoteAudioEnabled;
export const ensureLiveMicrophonePublished = ensureMicrophonePublished;
export const resolveLiveAudioConfiguration = resolveRealtimeAudioConfiguration;

/**
 * Camera startup can stop iOS RemoteIO after LiveKit has already reported a
 * successful microphone publication. Reassert the existing publication, then
 * restore capture/playout without creating a second microphone track.
 */
export async function stabilizeLivePublisherAudio(
  room: any,
  audioDeviceModule: any,
  audioSession: any,
  options: {
    settleMs?: number;
    context?: { sessionId?: string; correlationId?: string; roomType?: string; participantRole?: string };
  } = {}
): Promise<RealtimeAudioEngineStatus & { audioTrackCount: number }> {
  const audioTrackCount = await reassertRealtimeMicrophone(room, options.context);
  const status = await stabilizeRealtimeAudioEngine(audioDeviceModule, {
    playout: true,
    recording: true,
    settleMs: options.settleMs,
    // Publishers share one record-capable config (live_host === live_guest), so
    // restoring the host configuration is correct for any on-stage participant.
    reactivateSession: () => reapplyRealtimeAudioConfiguration(audioSession, "live_host"),
    context: options.context
  });
  await selectRealtimeAudioOutput(audioSession, true).catch(() => undefined);
  return { ...status, audioTrackCount };
}

/** A viewer must restore playout only; it must never acquire microphone input. */
export async function stabilizeLiveViewerAudio(
  audioDeviceModule: any,
  audioSession: any,
  options: {
    settleMs?: number;
    context?: { sessionId?: string; correlationId?: string; roomType?: string; participantRole?: string };
  } = {}
): Promise<RealtimeAudioEngineStatus> {
  await reapplyRealtimeAudioConfiguration(audioSession, "live_viewer");
  const status = await stabilizeRealtimeAudioEngine(audioDeviceModule, {
    playout: true,
    recording: false,
    settleMs: options.settleMs,
    context: options.context
  });
  await selectRealtimeAudioOutput(audioSession, true).catch(() => undefined);
  return status;
}

export async function stabilizeLiveRemotePlayback(
  room: any,
  audioDeviceModule: any,
  audioSession: any,
  enabled: boolean,
  options: {
    settleMs?: number;
    context?: { sessionId?: string; correlationId?: string; roomType?: string; participantRole?: string };
  } = {}
): Promise<RealtimeAudioEngineStatus & { remoteAudioTrackCount: number }> {
  const remoteAudioTrackCount = await applyRemoteAudioEnabled(room, enabled);
  const status = await stabilizeLiveViewerAudio(audioDeviceModule, audioSession, options);
  return { ...status, remoteAudioTrackCount };
}

function readableError(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function firstVideoTrack(participant: any): any | null {
  const publications = videoPublications(participant);
  return publications.find((publication) => publication?.track && publication?.isSubscribed !== false)?.track || null;
}

function firstAudioTrack(participant: any): any | null {
  return audioPublications(participant).find((publication) => publication?.track && publication?.isSubscribed !== false)?.track || null;
}

function publicationSid(publication: any): string {
  return String(publication?.trackSid || publication?.sid || publication?.track?.sid || "");
}

function trackSid(track: any): string {
  return String(track?.sid || track?.mediaStreamTrack?.id || "");
}

function isAudioMuted(participant: any): boolean {
  const publications = audioPublications(participant);
  if (!publications.length) return true;
  return publications.every((publication) => publication?.isMuted !== false);
}

function participantName(participant: any): string {
  return String(participant?.name || participant?.identity || "Guest");
}

function readRole(participant: any): string {
  try {
    const metadata = participant?.metadata ? JSON.parse(participant.metadata) : {};
    return String(metadata?.role || "");
  } catch {
    return "";
  }
}

/**
 * Return type narrowed to the three livestream surfaces rather than the full
 * RealtimeAudioMode union. The narrower type is what it always returned; naming
 * it means the same value can drive both the audio lease and the quality policy
 * without a cast, so there is no way for the two to end up classifying one
 * participant differently.
 */
function liveAudioMode(
  credentials: LiveKitCredentials,
  publish: boolean
): Extract<RealtimeAudioMode, "live_host" | "live_guest" | "live_viewer"> {
  if (!publish) return "live_viewer";
  return credentials.role === "cohost" || credentials.guestId > 0 ? "live_guest" : "live_host";
}

/**
 * Live is a foreground real-time media experience, so every role should start
 * on an audible output route. `liveAudioMode` controls whether the session is
 * record-capable; this helper controls output only. Keeping it separate avoids
 * regressing viewers into a silent default/earpiece route when publishers are
 * the only roles that need microphone input.
 */
export function shouldForceLiveSpeakerRoute() {
  return true;
}

/**
 * The single place the V2 and legacy publish paths diverge.
 *
 * V2 (`publishLiveMicrophone`) waits on LiveKit's own `localTrackPublished`
 * event and reconciles duplicates. The legacy helper polled for 150ms and, if it
 * had not seen a publication yet, toggled the microphone off and on - which
 * against any publish slower than 150ms produced TWO audio publications for one
 * speaker. That is the duplicate-audio defect. The legacy branch is preserved
 * byte-for-byte so a flag flip is a true A/B, not a rewrite.
 */
async function publishMicrophoneForPath(
  room: any,
  useV2: boolean,
  context: { role: string; room: string; correlationId?: string; canPublishMicrophone?: boolean }
): Promise<number> {
  if (!useV2) return ensureMicrophonePublished(room);
  emitLiveAudioEvent({ name: "live_audio_publish_started", path: "v2_isolated", role: context.role, room: context.room });
  const result = await publishLiveMicrophone(room, {
    context: {
      sessionId: context.room,
      correlationId: context.correlationId,
      roomType: "livestream",
      participantRole: context.role,
      canPublishMicrophone: context.canPublishMicrophone
    }
  });
  emitLiveAudioEvent({
    name: result.outcome === "timeout" ? "live_audio_publish_timeout" : "live_audio_publish_settled",
    path: "v2_isolated",
    role: context.role,
    room: context.room,
    outcome: result.outcome,
    audioTrackCount: result.audioTrackCount,
    duplicatesRemoved: result.duplicatesRemoved,
    durationMs: result.durationMs
  });
  if (result.duplicatesRemoved > 0) {
    emitLiveAudioEvent({
      name: "live_audio_duplicate_reconciled",
      path: "v2_isolated",
      role: context.role,
      duplicatesRemoved: result.duplicatesRemoved
    });
  }
  return result.audioTrackCount;
}

/**
 * Order the host media transition around the camera/audio race observed on iOS.
 *
 * The engine guard is deliberately last. Running it before camera publication
 * can inspect WebRTC while the newly-published microphone is still starting,
 * fail closed, and disconnect a healthy room before the camera is ever added.
 * The guard exists to verify the state *after* camera startup settles.
 */
export async function initializeLivePublisherMedia(options: {
  useV2: boolean;
  publishMicrophone: () => Promise<number>;
  enableCamera: () => Promise<void>;
  stabilizeAudio: () => Promise<number>;
  wait?: (milliseconds: number) => Promise<void>;
  /**
   * Bounded retry budget for the post-camera engine guard. Camera startup can
   * stop iOS RemoteIO *asynchronously*, after `stabilizeAudio`'s own settle
   * window has closed, so a single guard pass can still observe (and fail on) an
   * engine the SDK is about to restart. A temporary post-camera engine stop is a
   * recoverable audio interruption, not a terminal failure: reassert the
   * microphone and re-run the guard before failing the whole broadcast closed.
   */
  stabilizeAttempts?: number;
  stabilizeRetryWaitMs?: number;
  /**
   * Read-only observation of the native audio engine at labelled points in the
   * startup sequence. Must NOT reconfigure the session or restart the engine -
   * it exists purely to make the post-camera recorder teardown visible in
   * device logs without perturbing the running media pipeline.
   */
  probeAudio?: (phase: "after_microphone" | "after_camera") => Promise<void> | void;
  /**
   * Legacy-path recovery for the camera-start audio interruption. Starting the
   * camera fires an AVAudioSession interruption that stops the native WebRTC
   * record engine, and iOS never delivers an interruption-ended event while the
   * camera holds the session - so the recorder must be restarted proactively.
   * Unlike `stabilizeAudio` (V2), this must NOT reconfigure the shared audio
   * session and must NOT throw: it restarts the torn-down recorder in place so
   * the legacy host is heard, without perturbing the running video pipeline.
   */
  recoverRecordingEngine?: () => Promise<void>;
  trace?: (event: "microphone_track_create_started" | "microphone_track_created" | "microphone_publish_started" | "microphone_published" | "camera_initialization_started" | "camera_initialized" | "live_audio_active_verification_started" | "live_audio_active_verification_passed" | "live_audio_active_verification_retrying" | "live_audio_active_verification_failed") => void;
}): Promise<number> {
  const wait = options.wait || ((milliseconds) => new Promise((resolve) => setTimeout(resolve, milliseconds)));
  options.trace?.("microphone_track_create_started");
  options.trace?.("microphone_publish_started");
  let audioTrackCount = await options.publishMicrophone();
  options.trace?.("microphone_track_created");
  options.trace?.("microphone_published");
  await options.probeAudio?.("after_microphone");
  options.trace?.("camera_initialization_started");
  await options.enableCamera();
  options.trace?.("camera_initialized");
  audioTrackCount = await options.publishMicrophone();
  await options.probeAudio?.("after_camera");
  if (options.useV2) {
    const maxAttempts = Math.max(1, Math.floor(options.stabilizeAttempts ?? 3));
    const retryWaitMs = Math.max(0, options.stabilizeRetryWaitMs ?? 250);
    for (let attempt = 1; attempt <= maxAttempts; attempt += 1) {
      options.trace?.("live_audio_active_verification_started");
      try {
        audioTrackCount = Math.max(audioTrackCount, await options.stabilizeAudio());
        options.trace?.("live_audio_active_verification_passed");
        return audioTrackCount;
      } catch (error) {
        if (attempt >= maxAttempts) {
          options.trace?.("live_audio_active_verification_failed");
          throw error;
        }
        options.trace?.("live_audio_active_verification_retrying");
        // Reassert the microphone the camera transition may have torn, then let
        // the async RemoteIO teardown settle before the next guard pass. The V2
        // publisher reconciles duplicates, so this reassert is idempotent.
        audioTrackCount = Math.max(audioTrackCount, await options.publishMicrophone().catch(() => audioTrackCount));
        await wait(retryWaitMs);
      }
    }
  } else {
    // Legacy publisher path does NOT reconfigure the shared AVAudioSession
    // mid-broadcast: reasserting the Apple audio configuration here was observed
    // to disrupt the running WebRTC video pipeline (degraded quality / slow
    // reload / capture-session restart). But settling alone left the host
    // silent, because the camera-start interruption tears the native recorder
    // down and iOS never signals that it ended. Restart the recorder in place -
    // no session reconfiguration - so the legacy host is actually heard.
    await wait(150);
    await options.recoverRecordingEngine?.();
  }
  return audioTrackCount;
}

export type LiveConnectOptions = {
  publish?: boolean;
  /**
   * Re-mint LiveKit credentials for this broadcast. LiveKit reuses the ORIGINAL
   * join token on reconnect, and guest tokens are minted with a 30 minute TTL,
   * so a guest in a longer broadcast who hits a network blip would otherwise
   * reconnect with an expired token and never rejoin. The hook schedules a
   * refresh ahead of expiry when this is supplied.
   *
   * Supplied by the caller (which owns the live id) rather than imported here,
   * so this hook stays free of the API layer. Refreshing re-hits the server,
   * which re-checks that the guest is still accepted - which is exactly why the
   * short TTL must stay short rather than be widened.
   */
  refreshCredentials?: () => Promise<LiveKitCredentials | null>;
};

export function useLiveBroadcastRoom() {
  const runtimeRef = useRef(getLiveRuntime());
  const existingResources = runtimeRef.current.getResources();
  const roomRef = useRef<any>(existingResources.room || null);
  const audioSessionRef = useRef<any>(null);
  const audioDeviceModuleRef = useRef<any>(null);
  const audioLeaseRef = useRef<RealtimeAudioLease | null>((existingResources.audioLease as RealtimeAudioLease) || null);
  const lifecycleRef = useRef(new RealtimeAudioStateMachine());
  const correlationIdRef = useRef("");
  const lastConnectErrorRef = useRef("");
  const traceRef = useRef<LiveAudioTrace | null>(null);
  const activeSpeakersRef = useRef<Set<string>>(new Set());
  const localEnergySeenRef = useRef(false);
  const remoteEnergySeenRef = useRef(false);
  // Desired viewer remote-audio state; reapplied to tracks that subscribe after
  // the user toggled sound off (co-host join, host republish, reconnect).
  const remoteAudioEnabledRef = useRef(true);

  // --- V2 route state -------------------------------------------------------
  // All of this is inert while the server flag is off: `useV2Ref` gates every
  // read, so the legacy path behaves exactly as it did before.
  const useV2Ref = useRef(false);
  const roleRef = useRef("");
  const roomNameRef = useRef("");
  const publishRef = useRef(false);
  // Resolved once per connection. Read by setCameraEnabled so a mid-session
  // camera toggle reuses the configuration the session was published with.
  const qualityPlanRef = useRef<MediaQualityPlan | null>(null);
  const credentialsRef = useRef<LiveKitCredentials | null>(null);
  const refreshCredentialsRef = useRef<LiveConnectOptions["refreshCredentials"]>(undefined);
  const reconnectAttemptRef = useRef(0);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const tokenRefreshTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Consecutive refresh failures. Reset on every success so a long broadcast
  // that survives one bad minute still gets a full retry budget hours later.
  const tokenRefreshFailuresRef = useRef(0);
  const appStateSubscriptionRef = useRef<{ remove: () => void } | null>(null);
  // Set while the caller is deliberately tearing the room down, so the
  // Disconnected event does not treat an intentional stop as a network drop.
  const intentionalTeardownRef = useRef(false);
  const connectRef = useRef<((credentials: LiveKitCredentials, options?: LiveConnectOptions) => Promise<boolean>) | null>(null);

  const [state, setState] = useState<LiveBroadcastState>(initialState);

  const clearRecoveryTimers = useCallback(() => {
    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    if (tokenRefreshTimerRef.current) clearTimeout(tokenRefreshTimerRef.current);
    reconnectTimerRef.current = null;
    tokenRefreshTimerRef.current = null;
    tokenRefreshFailuresRef.current = 0;
    appStateSubscriptionRef.current?.remove?.();
    appStateSubscriptionRef.current = null;
  }, []);

  const refreshParticipants = useCallback((room = roomRef.current) => {
    if (!room) return;
    const speaking = activeSpeakersRef.current;
    const local = room.localParticipant;
    const localVideoTrack = firstVideoTrack(local);
    const localAudioTrack = firstAudioTrack(local);
    const localAudioTrackCount = audioPublications(local).filter(publicationHasTrack).length;
    let remoteAudioTrackCount = 0;
    let remoteVideoTrackCount = 0;
    const participants: LiveParticipant[] = [];
    if (local) {
      participants.push({
        identity: String(local.identity || "local"),
        name: participantName(local),
        isLocal: true,
        isHost: readRole(local) === "host" || Boolean(local.permissions?.canPublish),
        videoTrack: localVideoTrack,
        audioTrack: localAudioTrack,
        hasVideo: Boolean(localVideoTrack),
        hasAudio: Boolean(localAudioTrack),
        audioMuted: local.isMicrophoneEnabled === false || !localAudioTrackCount,
        speaking: speaking.has(String(local.identity || "local"))
      });
    }
    for (const remote of Array.from(room.remoteParticipants?.values?.() || []) as any[]) {
      const videoTrack = firstVideoTrack(remote);
      const audioTrack = firstAudioTrack(remote);
      remoteAudioTrackCount += audioPublications(remote).filter(publicationHasTrack).length;
      remoteVideoTrackCount += videoPublications(remote).filter(publicationHasTrack).length;
      const role = readRole(remote);
      participants.push({
        identity: String(remote.identity || ""),
        name: participantName(remote),
        isLocal: false,
        isHost: role === "host",
        videoTrack,
        audioTrack,
        hasVideo: Boolean(videoTrack),
        hasAudio: Boolean(audioTrack),
        audioMuted: isAudioMuted(remote),
        speaking: speaking.has(String(remote.identity || ""))
      });
    }
    setState((current) => ({
      ...current,
      localVideoTrack,
      localAudioTrackCount,
      remoteAudioTrackCount,
      remoteVideoTrackCount,
      participants
    }));
  }, []);

  const disconnect = useCallback(async (reason = "local_disconnect") => {
    const trace = traceRef.current;
    const wasPublishing = publishRef.current;
    const currentRoom = roomRef.current;
    const localPublication = audioPublications(currentRoom?.localParticipant).find(publicationHasTrack);
    if (wasPublishing) {
      trace?.emit("live_end_requested", { room_state: String(currentRoom?.state || "disconnecting") });
      trace?.emit("local_audio_unpublish_started", {
        room_state: String(currentRoom?.state || "disconnecting"),
        trackSid: trackSid(localPublication?.track),
        publicationSid: publicationSid(localPublication),
        muted: localPublication?.isMuted === true,
        enabled: localPublication?.track?.isEnabled !== false
      });
    }
    trace?.emit("room_disconnect_started", { room_state: String(currentRoom?.state || "disconnecting") });
    lifecycleRef.current.markTerminal();
    lifecycleRef.current.tryTransition("room", "disconnecting");
    lifecycleRef.current.tryTransition("local", "unpublishing");
    const room = roomRef.current;
    const lease = audioLeaseRef.current;
    audioLeaseRef.current = null;
    intentionalTeardownRef.current = true;
    clearRecoveryTimers();
    reconnectAttemptRef.current = 0;
    roomRef.current = null;
    activeSpeakersRef.current = new Set();
    remoteAudioEnabledRef.current = true;
    if (room?.disconnect) await room.disconnect().catch(() => undefined);
    if (wasPublishing) {
      trace?.emit("local_audio_unpublished", { room_state: "disconnected", publicationSid: publicationSid(localPublication) });
      trace?.emit("local_audio_track_stopped", { room_state: "disconnected", trackSid: trackSid(localPublication?.track) });
    }
    trace?.emit("room_disconnected", { room_state: "disconnected" });
    // Only release when we actually hold the session. The previous
    // `ownerId || reason` fallback passed a REASON STRING as the owner id, which
    // can never match the real owner, so the release silently no-opped and the
    // AVAudioSession leaked - blocking the next call or broadcast.
    if (lease) {
      trace?.emit("audio_owner_release_requested", { room_state: "disconnecting", currentOwner: lease.ownerId, audioGeneration: lease.leaseId, caller: "useLiveBroadcastRoom.disconnect", reason });
      trace?.emit("audio_session_deactivation_requested", { room_state: "disconnecting", currentOwner: lease.ownerId, audioGeneration: lease.leaseId, caller: "realtimeAudioEngine.release", reason });
      await releaseRealtimeAudioSession(audioSessionRef.current, lease).catch(() => undefined);
      trace?.emit("audio_owner_released", { room_state: "disconnected", audioOwner: lease.ownerId });
      trace?.emit("audio_session_deactivated_if_unowned", { room_state: "disconnected", audioOwner: lease.ownerId });
      trace?.emit("audio_session_deactivated", { room_state: "disconnected", currentOwner: lease.ownerId, audioGeneration: lease.leaseId, caller: "realtimeAudioEngine.release", reason });
      emitLiveAudioEvent({
        name: "live_audio_session_released",
        path: resolveLiveAudioPath({ audioV2Enabled: useV2Ref.current }),
        role: roleRef.current,
        room: roomNameRef.current,
        reason
      });
    }
    lifecycleRef.current.tryTransition("local", "released");
    lifecycleRef.current.tryTransition("remote", "ended");
    lifecycleRef.current.tryTransition("room", "disconnected");
    credentialsRef.current = null;
    refreshCredentialsRef.current = undefined;
    audioDeviceModuleRef.current = null;
    trace?.emit("cleanup_completed", { room_state: "disconnected", error_category: reason });
    const runtimeSession = runtimeRef.current.getSnapshot().session;
    if (runtimeSession) {
      await runtimeRef.current.cleanup(runtimeSession.generation, async () => undefined, reason);
    }
    setState((current) => ({
      ...current,
      connecting: false,
      connected: false,
      reconnecting: false,
      recovering: false,
      connectionState: "disconnected",
      localVideoTrack: null,
      localAudioTrackCount: 0,
      remoteAudioTrackCount: 0,
      remoteVideoTrackCount: 0,
      participants: [],
      disconnectReason: reason,
      diagnosticCode: reason
    }));
  }, [clearRecoveryTimers]);

  /**
   * Reassert the output route we chose. iOS silently moves output to the
   * receiver when a Bluetooth device disappears, which is how Live audio went
   * quiet with no error and no event the app was listening for. LiveKit's
   * device-change events are the portable signal available without adding a
   * native AVAudioSession observer, so they drive this.
   */
  const reapplyAudioRoute = useCallback(async (reason: string) => {
    if (!useV2Ref.current) return;
    const audioSession = audioSessionRef.current;
    if (!audioSession) return;
    const applied = await selectRealtimeAudioOutput(audioSession, true).then(
      () => true,
      () => false
    );
    emitLiveAudioEvent({
      name: "live_audio_route_reapplied",
      path: "v2_isolated",
      role: roleRef.current,
      room: roomNameRef.current,
      reason,
      outcome: applied ? "applied" : "failed"
    });
  }, []);

  /**
   * Refresh the join token ahead of expiry. LiveKit reuses the ORIGINAL token on
   * reconnect, so without this a guest whose 30 minute token lapsed can never
   * rejoin after a blip. Refreshing re-hits the server, which re-checks that the
   * guest is still accepted - so the short TTL stays short and stays enforced.
   */
  const scheduleTokenRefresh = useCallback(() => {
    if (tokenRefreshTimerRef.current) clearTimeout(tokenRefreshTimerRef.current);
    tokenRefreshTimerRef.current = null;
    if (!useV2Ref.current) return;
    const refresh = refreshCredentialsRef.current;
    const credentials = credentialsRef.current;
    if (!refresh || !credentials) return;

    const waitMs = millisecondsUntilRefresh(credentials.expiresAt);
    emitLiveAudioEvent({
      name: "live_audio_token_refresh_scheduled",
      path: "v2_isolated",
      role: roleRef.current,
      room: roomNameRef.current,
      durationMs: waitMs
    });
    tokenRefreshTimerRef.current = setTimeout(() => {
      tokenRefreshTimerRef.current = null;
      refresh()
        .then((next) => {
          if (!next?.token) throw new Error("refresh returned no token");
          // Only the credentials are swapped; the room stays connected. The new
          // token is what a subsequent LiveKit reconnect will carry.
          credentialsRef.current = next;
          tokenRefreshFailuresRef.current = 0;
          emitLiveAudioEvent({
            name: "live_audio_token_refreshed",
            path: "v2_isolated",
            role: roleRef.current,
            room: roomNameRef.current
          });
          scheduleTokenRefreshRef.current?.();
        })
        .catch((error: unknown) => {
          // Never surface the failure body - it can contain the token.
          emitLiveAudioEvent({
            name: "live_audio_token_refresh_failed",
            path: "v2_isolated",
            role: roleRef.current,
            room: roomNameRef.current,
            reason: error instanceof Error ? error.name : "unknown",
            attempt: tokenRefreshFailuresRef.current + 1
          });
          // The refresh fires TOKEN_REFRESH_MARGIN_MS before expiry, so one
          // transient network blip still leaves room to try again before the
          // token actually dies. Retry on a fixed short interval, bounded, and
          // only while this connection is still the live one - an unbounded
          // retry against a revoked guest slot would just hammer the endpoint.
          tokenRefreshFailuresRef.current += 1;
          if (tokenRefreshFailuresRef.current > TOKEN_REFRESH_MAX_FAILURES) return;
          if (!roomRef.current) return;
          tokenRefreshTimerRef.current = setTimeout(() => {
            tokenRefreshTimerRef.current = null;
            scheduleTokenRefreshRef.current?.();
          }, TOKEN_REFRESH_RETRY_MS);
        });
    }, waitMs);
  }, []);
  const scheduleTokenRefreshRef = useRef<(() => void) | null>(null);
  scheduleTokenRefreshRef.current = scheduleTokenRefresh;

  /**
   * Bounded automatic reconnect. The previous hook set state on `Disconnected`
   * and stopped: a network blip and a host-ended room were indistinguishable, so
   * a recoverable drop was never retried. Retrying a TERMINAL state forever is
   * equally wrong, hence the classification plus a hard attempt budget.
   */
  const scheduleReconnect = useCallback((reason: string) => {
    if (!useV2Ref.current || intentionalTeardownRef.current || !lifecycleRef.current.mayReconnect()) return false;
    const credentials = credentialsRef.current;
    if (!credentials) return false;

    const attempt = reconnectAttemptRef.current + 1;
    if (!shouldAttemptReconnect(reason, attempt)) {
      emitLiveAudioEvent({
        name: "live_audio_reconnect_exhausted",
        path: "v2_isolated",
        role: roleRef.current,
        room: roomNameRef.current,
        reason,
        attempt
      });
      setState((current) => ({
        ...current,
        recovering: false,
        diagnosticCode: classifyDisconnect(reason) === "terminal" ? "LIVE_DISCONNECT_TERMINAL" : "LIVE_RECONNECT_EXHAUSTED"
      }));
      return false;
    }

    const delayMs = nextReconnectDelayMs(attempt) ?? 0;
    reconnectAttemptRef.current = attempt;
    emitLiveAudioEvent({
      name: "live_audio_reconnect_scheduled",
      path: "v2_isolated",
      role: roleRef.current,
      room: roomNameRef.current,
      reason,
      attempt,
      durationMs: delayMs
    });
    setState((current) => ({ ...current, recovering: true, reconnectCount: current.reconnectCount + 1 }));

    if (reconnectTimerRef.current) clearTimeout(reconnectTimerRef.current);
    reconnectTimerRef.current = setTimeout(() => {
      reconnectTimerRef.current = null;
      const latest = credentialsRef.current;
      if (!latest || intentionalTeardownRef.current) return;
      void connectRef.current?.(latest, {
        publish: publishRef.current,
        refreshCredentials: refreshCredentialsRef.current
      });
    }, delayMs);
    return true;
  }, []);

  const connectTransaction = useCallback(
    async (credentials: LiveKitCredentials, options: LiveConnectOptions = {}) => {
      lastConnectErrorRef.current = "";
      if (Platform.OS === "web") {
        lastConnectErrorRef.current = "Native LiveKit broadcasting requires an installed iOS or Android build.";
        setState((current) => ({ ...current, supported: false, error: lastConnectErrorRef.current }));
        return false;
      }
      if (!credentials.token || !credentials.url) {
        lastConnectErrorRef.current = "PulseSoc did not return a usable LiveKit token for this broadcast.";
        setState((current) => ({ ...current, error: lastConnectErrorRef.current }));
        return false;
      }
      const publishSources = credentials.canPublishSources?.length
        ? credentials.canPublishSources
        : credentials.canPublish
          ? ["microphone", "camera"]
          : [];
      const canPublishMicrophone = publishSources.includes("microphone");
      const publish = Boolean(options.publish && credentials.canPublish && canPublishMicrophone);

      // SERVER-AUTHORITATIVE GATE. The decision arrives on the token response the
      // client already fetches for every broadcast, so flipping the backend flag
      // takes effect on the next token fetch with no app release. There is no
      // local override on purpose - a client-side flag is not a kill switch.
      const useV2 = isLiveAudioV2EnabledForSession(credentials, publish);
      const audioPath = resolveLiveAudioPathForSession(credentials, publish);
      const telemetryRole = credentials.role || (publish ? "host" : "viewer");

      if (roomRef.current) await disconnect("replaced_room");
      clearRecoveryTimers();
      lifecycleRef.current = new RealtimeAudioStateMachine();
      lifecycleRef.current.transition("room", "connecting");
      intentionalTeardownRef.current = false;
      useV2Ref.current = useV2;
      roleRef.current = telemetryRole;
      correlationIdRef.current = createRealtimeAudioCorrelationId();
      roomNameRef.current = credentials.room || credentials.identity || "";
      publishRef.current = publish;
      localEnergySeenRef.current = false;
      remoteEnergySeenRef.current = false;
      traceRef.current = createLiveAudioTrace({
        enabled: credentials.audioTraceEnabled === true,
        correlationId: correlationIdRef.current,
        room: roomNameRef.current,
        participantIdentity: credentials.identity,
        participantRole: telemetryRole
      });
      const trace = traceRef.current;
      trace.emit("live_start_requested", { room_state: "new", enabled: publish });
      trace.emit("live_session_created", { room_state: "new", caller: "useLiveBroadcastRoom.connect" });
      if (publish && !credentials.canPublish) {
        trace.emit("invariant_failed", { room_state: "authorization_failed", error_category: "publish_not_authorized" });
      } else if (options.publish && credentials.canPublish && !canPublishMicrophone) {
        trace.emit("invariant_failed", { room_state: "authorization_failed", error_category: "microphone_source_not_authorized" });
      } else {
        trace.emit("live_authorization_succeeded", {
          room_state: "authorized",
          enabled: publish ? credentials.canPublish : credentials.canSubscribe,
          subscription_state: publish ? "publisher" : "subscriber"
        });
      }
      trace.emit("live_room_connect_started", { room_state: "connecting" });
      lifecycleRef.current.transition("local", publish ? "acquiringSession" : "released");
      credentialsRef.current = credentials;
      if (options.refreshCredentials) refreshCredentialsRef.current = options.refreshCredentials;
      emitLiveAudioEvent({
        name: "live_audio_path_selected",
        path: audioPath,
        role: telemetryRole,
        room: roomNameRef.current,
        outcome: publish ? "publisher" : "subscriber"
      });

      remoteAudioEnabledRef.current = true;
      setState((current) => ({
        ...initialState,
        supported: current.supported,
        connecting: true,
        connectionState: "connecting",
        canPublish: credentials.canPublish,
        audioPath
      }));
      try {
        const livekitNative = await import("@livekit/react-native");
        const livekitClient = await import("livekit-client");
        if (!globalsRegistered) {
          livekitNative.registerGlobals({ autoConfigureAudioSession: false });
          globalsRegistered = true;
        }
        audioSessionRef.current = livekitNative.AudioSession;
        audioDeviceModuleRef.current = livekitNative.AudioDeviceModule;
        if (publish) {
          const permission = await import("expo-av")
            .then(({ Audio }) => Audio.getPermissionsAsync())
            .catch(() => null);
          trace.emit("microphone_permission_checked", {
            room_state: "connecting",
            enabled: permission?.granted === true,
            error_category: permission ? "none" : "permission_check_failed"
          });
          if (permission?.granted !== true) {
            trace.emit("microphone_permission_denied", {
              room_state: "failed",
              enabled: false,
              error_category: permission?.status || "permission_unavailable"
            });
            setState((current) => ({
              ...current,
              connecting: false,
              connectionState: "failed",
              error: "Microphone access is required before starting a Live broadcast.",
              diagnosticCode: "LIVE_MICROPHONE_PERMISSION_REQUIRED"
            }));
            return false;
          }
          trace.emit("microphone_permission_granted", { room_state: "connecting", enabled: true });
        }
        // AUDIO SESSION OWNERSHIP: use the canonical call-grade media engine.
        // LiveKit auto audio configuration is disabled, so PulseSoc must claim
        // one owner-controlled AVAudioSession before connecting or publishing.
        const mediaMode = liveAudioMode(credentials, publish);
        trace.emit("live_audio_policy_requested", { room_state: "connecting", caller: "mediaQualityPolicy.resolve" });
        const qualityFlags = parseMediaQualityFlags(credentials.mediaQuality);
        const qualityPlan = resolveMediaQualityPlan({ feature: mediaMode, flags: qualityFlags });
        qualityPlanRef.current = qualityPlan;
        const runtime = runtimeRef.current;
        runtime.createSession({
          sessionId: correlationIdRef.current,
          broadcastId: credentials.broadcastId,
          roomName: roomNameRef.current,
          hostUserId: credentials.hostUserId,
          authorizationVersion: credentials.authorizationVersion,
          featureFlags: {
            audioV2: useV2,
            publisherAudioV2: credentials.publisherAudioV2Enabled,
            qualityV2: qualityFlags.realtimeMediaQualityV2Enabled === true
          },
          qualityProfile: qualityPlan.profile
        });
        runtime.transition("authorizing", "LiveAuthorizationController", "token_validation_started");
        runtime.update({ authorized: true }, "authorization_succeeded", "LiveAuthorizationController", "server_token_valid");
        runtime.transition("authorized", "LiveAuthorizationController", "server_authorized");
        runtime.transition("acquiringMedia", "LiveMediaCoordinator", "ownership_requested");
        const qualityTrace = {
          quality_profile: qualityPlan.profile,
          feature_flags: describeMediaQualityFlags(qualityFlags),
          caller: "mediaQualityPolicy.resolve"
        };
        trace.emit("live_audio_policy_applied", { room_state: "connecting", ...qualityTrace });
        const appleAudioConfiguration = resolveLiveAudioConfiguration(mediaMode);
        const ownerId = `live:${mediaMode}:${credentials.room || credentials.identity || Date.now()}`;
        const audioProfile = `${appleAudioConfiguration.audioCategory}/${appleAudioConfiguration.audioMode}`;
        trace.emit("audio_owner_requested", { room_state: "connecting", audioOwner: ownerId, audio_profile: audioProfile });
        trace.emit("live_audio_owner_requested", { room_state: "connecting", requestedOwner: ownerId, caller: "realtimeAudioEngine.claim" });
        trace.emit("av_audio_session_activation_started", { room_state: "connecting", requestedOwner: ownerId, caller: "realtimeAudioEngine.activate" });
        trace.emit("audio_session_config_started", { room_state: "connecting", audioOwner: ownerId, audio_profile: audioProfile });
        try {
          audioLeaseRef.current = await activateRealtimeAudioSession(livekitNative.AudioSession, mediaMode, ownerId, {
            // Live viewers still need an audible route. The mode decides whether
            // the session records (`live_host`/`live_guest`) or only plays back
            // (`live_viewer`); the speaker flag only selects output. Keep it on
            // for every Live role so native Live follows the working call route
            // and does not render host video silently through an unavailable
            // default/earpiece path.
            speaker: shouldForceLiveSpeakerRoute(),
            participantRole: telemetryRole,
            correlationId: correlationIdRef.current,
            // A higher-priority owner (an incoming call) taking the session must
            // tear this broadcast down rather than leave it silently muted.
            onDisplaced: () => {
              emitLiveAudioEvent({
                name: "live_audio_session_displaced",
                path: audioPath,
                role: telemetryRole,
                room: roomNameRef.current
              });
              setState((current) => ({
                ...current,
                audioBusy: true,
                error: "Live audio stopped because a call took over the microphone.",
                diagnosticCode: "LIVE_AUDIO_SESSION_DISPLACED"
              }));
              void disconnect("audio_session_displaced");
            }
          });
          trace.emit("audio_owner_acquired", { room_state: "connecting", audioOwner: ownerId, audio_profile: audioProfile });
          trace.emit("live_audio_owner_acquired", { room_state: "connecting", currentOwner: ownerId, audioGeneration: audioLeaseRef.current.leaseId, caller: "realtimeAudioEngine.claim" });
          trace.emit("live_audio_generation_created", { room_state: "connecting", currentOwner: ownerId, audioGeneration: audioLeaseRef.current.leaseId, caller: "realtimeAudioEngine.claim" });
          trace.emit("audio_session_config_completed", { room_state: "connecting", audioOwner: ownerId, audio_profile: audioProfile });
          trace.emit("audio_session_activated", { room_state: "connecting", audioOwner: ownerId, audio_profile: audioProfile });
          trace.emit("av_audio_session_activated", { room_state: "connecting", currentOwner: ownerId, audioGeneration: audioLeaseRef.current.leaseId, caller: "realtimeAudioEngine.activate" });
          runtime.attachResources({ audioLease: audioLeaseRef.current });
          runtime.update({ audio: "active", audioOwnerActive: true }, "audio_activated", "AudioCoordinator", "lease_active");
          if (publish) {
            lifecycleRef.current.transition("local", "publishing");
            // Harden the recorder against the camera-start AVAudioSession
            // interruption before any media is published, so the record engine
            // can be resumed rather than found fully torn down. Best-effort.
            await enableRealtimeRecordingAlwaysPrepared(livekitNative.AudioDeviceModule);
          }
        } catch (ownershipError) {
          // A call already owns the audio session. Report it honestly instead of
          // stealing the session and cutting the user's call off mid-sentence.
          if (ownershipError instanceof RealtimeAudioOwnershipError) {
            audioLeaseRef.current = null;
            emitLiveAudioEvent({
              name: "live_audio_session_denied",
              path: audioPath,
              role: telemetryRole,
              room: roomNameRef.current,
              reason: ownershipError.blockedByMode
            });
            setState((current) => ({
              ...current,
              connecting: false,
              connected: false,
              connectionState: "failed",
              audioBusy: true,
              error: ownershipDenialMessage(ownershipError.blockedByMode),
              diagnosticCode: "LIVE_AUDIO_SESSION_BUSY"
            }));
            return false;
          }
          throw ownershipError;
        }
        if (Platform.OS === "ios") {
          const availability = (() => {
            try { return livekitNative.AudioDeviceModule?.getEngineAvailability?.(); } catch { return null; }
          })();
          const engineRunning = (() => {
            try { return Boolean(livekitNative.AudioDeviceModule?.isEngineRunning?.()); } catch { return false; }
          })();
          const inputAvailable = availability?.isInputAvailable === true;
          const engineState = `running=${engineRunning};input=${Boolean(availability?.isInputAvailable)};output=${Boolean(availability?.isOutputAvailable)}`;
          if (publish) {
            trace.emit(inputAvailable ? "microphone_input_available" : "microphone_input_unavailable", {
              room_state: "connecting",
              enabled: inputAvailable,
              engine_state: engineState,
              error_category: inputAvailable ? "none" : "physical_input_unavailable"
            });
            if (!inputAvailable) {
              const lease = audioLeaseRef.current;
              audioLeaseRef.current = null;
              if (lease) await releaseRealtimeAudioSession(livekitNative.AudioSession, lease).catch(() => undefined);
              setState((current) => ({
                ...current,
                connecting: false,
                connectionState: "failed",
                error: "No microphone input is available for this Live broadcast.",
                diagnosticCode: "LIVE_MICROPHONE_INPUT_UNAVAILABLE"
              }));
              return false;
            }
          }
        }
        emitLiveAudioEvent({
          name: "live_audio_session_claimed",
          path: audioPath,
          role: telemetryRole,
          room: roomNameRef.current,
          outcome: mediaMode
        });

        // Resolved once, before the Room exists, and never recomputed for the
        // life of the session. `mediaMode` is already exactly "live_host",
        // "live_guest" or "live_viewer", so the surface the policy sees is the
        // same one the audio lease sees — there is no second classification to
        // disagree with the first.
        //
        // With every flag off, buildRoomQualityOptions returns exactly the
        // literal that used to be written here, including
        // videoCaptureDefaults: PULSE_LIVE_VIDEO_CAPTURE_OPTIONS and the
        // 2.3 Mbps encoding. mediaQualityPolicy.test.ts asserts that.
        emitMediaQualityEvent({
          name: "quality_plan_resolved",
          sessionId: roomNameRef.current,
          feature: qualityPlan.feature,
          profile: qualityPlan.profile,
          requestedProfile: qualityPlan.requestedProfile,
          contentMode: qualityPlan.contentMode,
          reasons: qualityPlan.reasons,
          audioPathUnchanged: true
        });

        const room = new livekitClient.Room(buildRoomQualityOptions(qualityPlan));
        roomRef.current = room;
        runtime.attachResources({ room });
        runtime.update({ room: "creating", camera: publish ? "acquiring" : "idle" }, "room_created", "LiveRoomController", "one_room_for_generation");

        const refresh = () => refreshParticipants(room);
        room.on(livekitClient.RoomEvent.ConnectionStateChanged, (connectionState: string) => {
          setState((current) => ({
            ...current,
            connectionState,
            connected: connectionState === "connected",
            connecting: connectionState === "connecting",
            reconnecting: connectionState === "reconnecting"
          }));
        });
        room.on(livekitClient.RoomEvent.Reconnecting, () => {
          lifecycleRef.current.tryTransition("room", "reconnecting");
          if (publish) lifecycleRef.current.tryTransition("local", "recovering");
          lifecycleRef.current.tryTransition("remote", "recovering");
          setState((current) => ({ ...current, connected: false, reconnecting: true, connectionState: "reconnecting", reconnectCount: current.reconnectCount + 1 }));
        });
        room.on(livekitClient.RoomEvent.Reconnected, () => {
          lifecycleRef.current.tryTransition("room", "connected");
          if (publish) lifecycleRef.current.tryTransition("local", "publishing");
          reconnectAttemptRef.current = 0;
          setState((current) => ({ ...current, connected: true, reconnecting: false, recovering: false, connectionState: "connected", error: "" }));
            const audioTasks: Promise<unknown>[] = [];
            if (publish) {
          audioTasks.push(publishMicrophoneForPath(room, useV2, {
            role: telemetryRole,
            room: roomNameRef.current,
            correlationId: correlationIdRef.current,
            canPublishMicrophone
          }));
          }
          if (useV2 || !publish) {
            const engineContext = {
              sessionId: roomNameRef.current,
              correlationId: correlationIdRef.current,
              roomType: "livestream",
              participantRole: telemetryRole
            };
            // Reconnect can replace both the native engine and the output
            // route. Publishers restore capture/playout; viewers restore only
            // playout and never request microphone input.
            if (publish && useV2) {
              audioTasks.push(stabilizeLivePublisherAudio(room, livekitNative.AudioDeviceModule, livekitNative.AudioSession, {
                settleMs: 250,
                context: engineContext
              }));
            } else if (!publish) {
              audioTasks.push(stabilizeLiveRemotePlayback(room, livekitNative.AudioDeviceModule, livekitNative.AudioSession, remoteAudioEnabledRef.current, {
                settleMs: 250,
                context: engineContext
              }));
            }
          } else {
            audioTasks.push(applyRemoteAudioEnabled(room, remoteAudioEnabledRef.current));
          }
          Promise.all(audioTasks)
            .then(() => {
              if (publish) lifecycleRef.current.tryTransition("local", "published");
            })
            .catch(() => lifecycleRef.current.tryTransition("local", "failed"));
          refresh();
        });
        room.on(livekitClient.RoomEvent.ConnectionQualityChanged, (quality: unknown, participant: any) => {
          if (participant?.isLocal !== false) {
            setState((current) => ({ ...current, connectionQuality: String(quality || "unknown").toLowerCase() }));
          }
        });
        room.on(livekitClient.RoomEvent.ActiveSpeakersChanged, (speakers: any[]) => {
          activeSpeakersRef.current = new Set((speakers || []).map((speaker) => String(speaker?.identity || "")));
          for (const speaker of speakers || []) {
            const level = Number(speaker?.audioLevel || 0);
            if (level <= 0) continue;
            if (speaker?.isLocal !== false && publish && !localEnergySeenRef.current) {
              localEnergySeenRef.current = true;
              trace.emit("local_audio_energy_detected", {
                room_state: String(room?.state || "connected"),
                participantIdentity: speaker?.identity,
                audioLevel: level,
                muted: false,
                enabled: true
              });
            } else if (speaker?.isLocal === false && !remoteEnergySeenRef.current) {
              remoteEnergySeenRef.current = true;
              trace.emit("remote_audio_energy_detected", {
                room_state: String(room?.state || "connected"),
                participantIdentity: speaker?.identity,
                audioLevel: level,
                muted: false,
                enabled: true,
                subscription_state: "subscribed"
              });
            }
          }
          refresh();
        });
        room.on(livekitClient.RoomEvent.MediaDevicesError, (mediaError: unknown) => {
          setState((current) => ({ ...current, error: readableError(mediaError, "Camera or microphone access failed.") }));
        });
        room.on(livekitClient.RoomEvent.ParticipantConnected, (participant: any) => {
          trace.emit("remote_participant_discovered", {
            room_state: String(room?.state || "connected"),
            participantIdentity: participant?.identity,
            subscription_state: "participant_connected"
          });
          refresh();
        });
        room.on(livekitClient.RoomEvent.ParticipantDisconnected, refresh);
        room.on(livekitClient.RoomEvent.TrackPublished, (publication: any, participant: any) => {
          if (String(publication?.kind || publication?.track?.kind || "") !== "audio") return;
          trace.emit("remote_audio_publication_discovered", {
            room_state: String(room?.state || "connected"),
            participantIdentity: participant?.identity,
            publicationSid: publicationSid(publication),
            muted: publication?.isMuted === true,
            subscription_state: String(publication?.subscriptionStatus || "available")
          });
          trace.emit("remote_audio_subscribe_started", {
            room_state: String(room?.state || "connected"),
            participantIdentity: participant?.identity,
            publicationSid: publicationSid(publication),
            subscription_state: "auto_subscribe"
          });
        });
        room.on(livekitClient.RoomEvent.TrackSubscribed, (track: any, publication: any, participant: any) => {
          // A newly subscribed audio track must follow the viewer's current sound
          // choice immediately. When sound is on, this mirrors the call path and
          // force-enables remote host/co-host audio instead of trusting defaults.
          if (String(track?.kind || "") === "audio") {
            lifecycleRef.current.tryTransition("remote", "publicationAvailable");
            lifecycleRef.current.tryTransition("remote", "subscribing");
            lifecycleRef.current.tryTransition("remote", "subscribed");
            lifecycleRef.current.tryTransition("remote", "playing");
            trace.emit("remote_audio_subscribed", {
              room_state: String(room?.state || "connected"),
              participantIdentity: participant?.identity,
              trackSid: trackSid(track),
              publicationSid: publicationSid(publication),
              muted: publication?.isMuted === true,
              enabled: track?.isEnabled !== false,
              subscription_state: "subscribed"
            });
            if (publication?.isMuted !== true) {
              trace.emit("remote_audio_track_unmuted", {
                room_state: String(room?.state || "connected"),
                participantIdentity: participant?.identity,
                trackSid: trackSid(track),
                publicationSid: publicationSid(publication),
                muted: false,
                enabled: track?.isEnabled !== false,
                subscription_state: "subscribed"
              });
            }
            trace.emit("remote_audio_playback_expected", {
              room_state: String(room?.state || "connected"),
              participantIdentity: participant?.identity,
              trackSid: trackSid(track),
              publicationSid: publicationSid(publication),
              muted: publication?.isMuted === true,
              enabled: remoteAudioEnabledRef.current,
              subscription_state: "playing"
            });
            stabilizeLiveRemotePlayback(room, livekitNative.AudioDeviceModule, livekitNative.AudioSession, remoteAudioEnabledRef.current, {
              settleMs: 0,
              context: {
                sessionId: roomNameRef.current,
                correlationId: correlationIdRef.current,
                roomType: "livestream",
                participantRole: telemetryRole
              }
            })
              .then(() => {
                if (publish && useV2) {
                  return stabilizeLivePublisherAudio(room, livekitNative.AudioDeviceModule, livekitNative.AudioSession, {
                    settleMs: 0,
                    context: {
                      sessionId: roomNameRef.current,
                      correlationId: correlationIdRef.current,
                      roomType: "livestream",
                      participantRole: telemetryRole
                    }
                  });
                }
                return undefined;
              })
              .catch(() => undefined);
          }
          refresh();
        });
        room.on(livekitClient.RoomEvent.TrackUnsubscribed, refresh);
        room.on(livekitClient.RoomEvent.TrackMuted, refresh);
        room.on(livekitClient.RoomEvent.TrackUnmuted, refresh);
        room.on(livekitClient.RoomEvent.LocalTrackPublished, refresh);
        room.on(livekitClient.RoomEvent.LocalTrackUnpublished, refresh);
        if (useV2) {
          // Route-change surface. `@livekit/react-native`'s AudioSession exposes
          // no AVAudioSession route-change or interruption listener, so these
          // LiveKit device events plus the AppState foreground transition are the
          // portable signals available without shipping a new native module.
          const onDeviceChange = () => {
            void reapplyAudioRoute("media_devices_changed");
          };
          room.on(livekitClient.RoomEvent.MediaDevicesChanged, onDeviceChange);
          room.on(livekitClient.RoomEvent.ActiveDeviceChanged, onDeviceChange);
          room.on(livekitClient.RoomEvent.AudioPlaybackStatusChanged, () => {
            void reapplyAudioRoute("audio_playback_status_changed");
          });
          appStateSubscriptionRef.current?.remove?.();
          appStateSubscriptionRef.current = AppState.addEventListener("change", (nextState) => {
            // Returning to the foreground is the observable end of an audio
            // interruption (a phone call, Siri, an alarm). iOS may have moved
            // output while we were backgrounded.
            if (nextState !== "active") return;
            emitLiveAudioEvent({
              name: "live_audio_interruption_ended",
              path: "v2_isolated",
              role: telemetryRole,
              room: roomNameRef.current,
              reason: "app_state_active"
            });
            void reapplyAudioRoute("app_state_active");
            if (publish && roomRef.current) {
              void publishMicrophoneForPath(roomRef.current, true, {
                role: telemetryRole,
                room: roomNameRef.current,
                correlationId: correlationIdRef.current,
                canPublishMicrophone
              }).then(() => stabilizeLivePublisherAudio(
                roomRef.current,
                livekitNative.AudioDeviceModule,
                livekitNative.AudioSession,
                {
                  settleMs: 250,
                  context: {
                    sessionId: roomNameRef.current,
                    correlationId: correlationIdRef.current,
                    roomType: "livestream",
                    participantRole: telemetryRole
                  }
                }
              )).catch(() => undefined);
            } else if (!publish) {
              void stabilizeLiveViewerAudio(livekitNative.AudioDeviceModule, livekitNative.AudioSession, {
                settleMs: 250,
                context: {
                  sessionId: roomNameRef.current,
                  correlationId: correlationIdRef.current,
                  roomType: "livestream",
                  participantRole: telemetryRole
                }
              }).catch(() => undefined);
            }
          });
        }

        room.on(livekitClient.RoomEvent.Disconnected, (reason: unknown) => {
          const reasonText = String(reason || "provider_disconnected");
          const classification = classifyDisconnect(reasonText);
          if (classification === "terminal") lifecycleRef.current.markTerminal();
          lifecycleRef.current.tryTransition("room", classification === "terminal" ? "disconnected" : "reconnecting");
          if (classification === "terminal") lifecycleRef.current.tryTransition("remote", "ended");
          if (useV2) {
            emitLiveAudioEvent({
              name: "live_audio_disconnect_classified",
              path: "v2_isolated",
              role: telemetryRole,
              room: roomNameRef.current,
              reason: reasonText,
              outcome: classification
            });
          }
          setState((current) => ({
            ...current,
            connected: false,
            connecting: false,
            reconnecting: false,
            connectionState: "disconnected",
            localVideoTrack: null,
            participants: [],
            disconnectReason: reasonText
          }));
          // Only a recoverable drop is retried, and only within the attempt
          // budget. A terminal reason (host ended, removed, token expired) is an
          // authorization or lifecycle decision that retrying cannot reverse.
          if (classification !== "terminal") scheduleReconnect(reasonText);
        });

        runtime.transition("connecting", "LiveRoomController", "connect_requested");
        runtime.update({ room: "connecting" }, "room_connect_started", "LiveRoomController", "connect_requested");
        trace.emit("livekit_room_connect_started", { room_state: "connecting", caller: "LiveKit.Room.connect" });
        await room.connect(credentials.url, credentials.token, { autoSubscribe: true });
        lifecycleRef.current.tryTransition("room", "connected");
        trace.emit("live_room_connected", { room_state: String(room?.state || "connected") });
        trace.emit("livekit_room_connected", { room_state: String(room?.state || "connected"), caller: "LiveKit.Room.connect" });
        runtime.update({ room: "connected" }, "room_connected", "LiveRoomController", "provider_connected");
        if (!publish) trace.emit("viewer_room_connected", { room_state: String(room?.state || "connected"), subscription_state: "auto_subscribe" });
        if (publish) {
          runtime.transition("publishing", "LiveMediaCoordinator", "publish_required_media");
          trace.emit("local_audio_track_create_started", { room_state: String(room?.state || "connected"), enabled: true });
          trace.emit("local_audio_publish_started", { room_state: String(room?.state || "connected"), enabled: true });
          await initializeLivePublisherMedia({
            useV2,
            publishMicrophone: () => publishMicrophoneForPath(room, useV2, {
              role: telemetryRole,
              room: roomNameRef.current,
              correlationId: correlationIdRef.current,
              canPublishMicrophone
            }),
            enableCamera: async () => {
              // At `stable` these resolve to PULSE_LIVE_VIDEO_CAPTURE_OPTIONS
              // and PULSE_LIVE_VIDEO_PUBLISH_OPTIONS, so the call made here is
              // the same call the verified baseline made.
              await room.localParticipant.setCameraEnabled(
                true,
                qualityPlan.videoCaptureDefaults || PULSE_LIVE_VIDEO_CAPTURE_OPTIONS,
                qualityPlan.videoPublishDefaults || PULSE_LIVE_VIDEO_PUBLISH_OPTIONS
              );
            },
            stabilizeAudio: async () => (await stabilizeLivePublisherAudio(
              room,
              livekitNative.AudioDeviceModule,
              livekitNative.AudioSession,
              {
                settleMs: 650,
                context: {
                  sessionId: roomNameRef.current,
                  correlationId: correlationIdRef.current,
                  roomType: "livestream",
                  participantRole: telemetryRole
                }
              }
            )).audioTrackCount,
            // Legacy-path recovery: restart the native recorder the camera
            // interruption tore down, WITHOUT reconfiguring the shared audio
            // session (that disrupts the running video pipeline). This is what
            // actually makes the legacy host audible again.
            recoverRecordingEngine: async () => {
              await recoverRealtimeRecordingEngine(livekitNative.AudioDeviceModule, {
                // Plain setActive(true) - re-activates the session the camera
                // transition left inactive, WITHOUT reasserting the audio
                // category (which would disrupt the running video pipeline).
                reactivateSession: async () => { await livekitNative.AudioSession.startAudioSession?.(); },
                context: {
                  sessionId: roomNameRef.current,
                  correlationId: correlationIdRef.current,
                  roomType: "livestream",
                  participantRole: telemetryRole
                }
              });
            },
            // Read-only engine probe. Surfaces the native record/playout state
            // around the camera transition at error level (visible in Release
            // device syslog) without reconfiguring the session - the missing
            // signal that made the legacy silent-mic failure impossible to
            // diagnose on hardware.
            probeAudio: (phase) => {
              const status = inspectRealtimeAudioEngine(livekitNative.AudioDeviceModule);
              console.error("PulseSocLiveAudioProbe", {
                phase,
                useV2,
                engineRunning: status.engineRunning,
                playoutRunning: status.playoutRunning,
                recordingRunning: status.recordingRunning,
                localAudioTracks: audioPublications(room.localParticipant).filter(publicationHasTrack).length
              });
            },
            trace: (event) => trace.emit(event, {
              room_state: String(room?.state || "connected"),
              audioGeneration: audioLeaseRef.current?.leaseId,
              currentOwner: audioLeaseRef.current?.ownerId,
              quality_profile: qualityPlan.profile,
              feature_flags: describeMediaQualityFlags(qualityFlags),
              caller: event.startsWith("camera_") ? "LiveKit.setCameraEnabled" : event.startsWith("live_audio_active_") ? "realtimeAudioEngine.guard" : "realtimeMicrophonePublisher"
            })
          });
          runtime.update({
            audio: "published",
            microphoneTrackCreated: true,
            microphonePublished: true,
            camera: "published",
            cameraOwnerActive: true,
            cameraTrackCreated: true,
            cameraPublished: true
          }, "required_media_published", "PublicationController", "audio_and_camera_confirmed");
          runtime.assertReady("LiveReadinessController");
        }
        if (publish) await selectRealtimeAudioOutput(livekitNative.AudioSession, true).catch(() => undefined);
        if (!publish) {
          await stabilizeLiveRemotePlayback(room, livekitNative.AudioDeviceModule, livekitNative.AudioSession, remoteAudioEnabledRef.current, {
            settleMs: 0,
            context: {
              sessionId: roomNameRef.current,
              correlationId: correlationIdRef.current,
              roomType: "livestream",
              participantRole: telemetryRole
            }
          });
        }
        const outputs = await livekitNative.AudioSession.getAudioOutputs?.().catch(() => []) || [];
        trace.emit("current_output_route_recorded", {
          room_state: String(room?.state || "connected"),
          output_route: outputs.length ? outputs.join(",") : "unavailable",
          error_category: outputs.length ? "none" : "output_route_unavailable"
        });
        refresh();
        const publishedAudioCount = audioPublications(room.localParticipant).filter(publicationHasTrack).length;
        if (publish && publishedAudioCount <= 0) {
          lifecycleRef.current.tryTransition("local", "failed");
          const message = "Microphone connected, but PulseSoc could not verify a published audio track.";
          lastConnectErrorRef.current = message;
          await room.disconnect?.().catch(() => undefined);
          const lease = audioLeaseRef.current;
          audioLeaseRef.current = null;
          if (lease) await releaseRealtimeAudioSession(livekitNative.AudioSession, lease).catch(() => undefined);
          roomRef.current = null;
          setState((current) => ({
            ...current,
            connecting: false,
            connected: false,
            reconnecting: false,
            connectionState: "failed",
            error: message,
            diagnosticCode: "LIVE_LOCAL_AUDIO_NOT_PUBLISHED",
            audioEnabled: false,
            localAudioTrackCount: 0
          }));
          return false;
        }
        if (publish) {
          const localPublication = audioPublications(room.localParticipant).find(publicationHasTrack);
          const localTrack = localPublication?.track;
          trace.emit("local_audio_track_created", {
            room_state: String(room?.state || "connected"),
            trackSid: trackSid(localTrack),
            publicationSid: publicationSid(localPublication),
            muted: localPublication?.isMuted === true,
            enabled: localTrack?.isEnabled !== false
          });
          trace.emit("local_audio_track_enabled", {
            room_state: String(room?.state || "connected"),
            trackSid: trackSid(localTrack),
            publicationSid: publicationSid(localPublication),
            muted: localPublication?.isMuted === true,
            enabled: localTrack?.isEnabled !== false
          });
          trace.emit("local_audio_published", {
            room_state: String(room?.state || "connected"),
            trackSid: trackSid(localTrack),
            publicationSid: publicationSid(localPublication),
            muted: localPublication?.isMuted === true,
            enabled: localTrack?.isEnabled !== false
          });
          trace.emit("local_audio_publication_sid_available", {
            room_state: String(room?.state || "connected"),
            trackSid: trackSid(localTrack),
            publicationSid: publicationSid(localPublication),
            enabled: Boolean(publicationSid(localPublication))
          });
          if (localPublication?.isMuted !== true) {
            trace.emit("local_audio_unmuted", {
              room_state: String(room?.state || "connected"),
              trackSid: trackSid(localTrack),
              publicationSid: publicationSid(localPublication),
              muted: false,
              enabled: localTrack?.isEnabled !== false
            });
          }
        }
        if (publish) lifecycleRef.current.tryTransition("local", "published");
        reconnectAttemptRef.current = 0;
        if (useV2) scheduleTokenRefresh();
        setState((current) => ({
          ...current,
          connecting: false,
          connected: true,
          reconnecting: false,
          recovering: false,
          connectionState: "connected",
          canPublish: credentials.canPublish,
          audioEnabled: publish,
          videoEnabled: publish,
          speakerEnabled: true,
          remoteAudioEnabled: true,
          audioBusy: false,
          audioPath,
          localAudioTrackCount: publishedAudioCount,
          error: "",
          diagnosticCode: ""
        }));
        const remoteAudioAtConnect = Array.from(room.remoteParticipants?.values?.() || []).reduce(
          (total: number, remote: any) => total + audioPublications(remote).filter(publicationHasTrack).length,
          0
        );
        console.error("PulseSoc Live media connected", {
          role: credentials.role || (publish ? "host" : "viewer"),
          room: credentials.room || "unknown",
          canPublish: credentials.canPublish,
          publish,
          audioProfile: `${appleAudioConfiguration.audioCategory}/${appleAudioConfiguration.audioMode}`,
          localAudioTrackCount: publishedAudioCount,
          remoteAudioTrackCount: remoteAudioAtConnect
        });
        return true;
      } catch (error) {
        // The exact internal reason (e.g. "native real-time audio engine did not
        // remain active", REALTIME_AUDIO_ENGINE_INACTIVE) is already in the audio
        // trace/telemetry. Do not surface raw engine internals as the only
        // user-facing diagnostic (mission Error Model); map the known audio-startup
        // failure to a typed code and a plain, actionable message instead.
        const engineInactive =
          (error as { code?: string } | null)?.code === "REALTIME_AUDIO_ENGINE_INACTIVE" ||
          /engine did not remain active/i.test(readableError(error, ""));
        const message = engineInactive
          ? "Broadcast audio could not stay active while the camera started. Please try going live again."
          : readableError(error, "Native LiveKit broadcast connection failed.");
        lastConnectErrorRef.current = message;
        await disconnect("connect_failed").catch(() => undefined);
        setState((current) => ({
          ...current,
          connectionState: "failed",
          error: message,
          disconnectReason: "connect_failed",
          diagnosticCode: engineInactive ? "LIVE_AUDIO_PUBLICATION_FAILED" : "LIVEKIT_CONNECT_FAILED"
        }));
        return false;
      }
    },
    [clearRecoveryTimers, disconnect, reapplyAudioRoute, refreshParticipants, scheduleReconnect, scheduleTokenRefresh]
  );
  const connect = useCallback(
    (credentials: LiveKitCredentials, options: LiveConnectOptions = {}) =>
      runtimeRef.current.runStart(() => connectTransaction(credentials, options)),
    [connectTransaction]
  );
  connectRef.current = connect;

  const setMicrophoneEnabled = useCallback(async (enabled: boolean) => {
    const room = roomRef.current;
    if (!room) throw new Error("Broadcast media is not connected.");
    if (!publishRef.current) throw new Error("Viewers cannot publish microphone audio.");
    await setRealtimeMicrophoneEnabled(room, enabled);
    lifecycleRef.current.tryTransition("local", enabled ? "published" : "muted");
    refreshParticipants(room);
    setState((current) => ({ ...current, audioEnabled: enabled, error: "" }));
  }, [refreshParticipants]);

  const setCameraEnabled = useCallback(async (enabled: boolean) => {
    const room = roomRef.current;
    if (!room) throw new Error("Broadcast media is not connected.");
    // Same options the session connected with. Re-enabling a camera mid-session
    // with a different configuration than it was published with is how a stream
    // silently changes resolution partway through.
    const plan = qualityPlanRef.current;
    await room.localParticipant.setCameraEnabled(
      enabled,
      plan?.videoCaptureDefaults || PULSE_LIVE_VIDEO_CAPTURE_OPTIONS,
      plan?.videoPublishDefaults || PULSE_LIVE_VIDEO_PUBLISH_OPTIONS
    );
    const localAudioTrackCount = await publishMicrophoneForPath(room, useV2Ref.current, {
      role: roleRef.current,
      room: roomNameRef.current,
      correlationId: correlationIdRef.current
    });
    if (localAudioTrackCount <= 0) throw new Error("Camera changed, but microphone audio is no longer published.");
    if (useV2Ref.current) {
      await stabilizeLivePublisherAudio(room, audioDeviceModuleRef.current, audioSessionRef.current, {
        settleMs: 450,
        context: {
          sessionId: roomNameRef.current,
          correlationId: correlationIdRef.current,
          roomType: "livestream",
          participantRole: roleRef.current
        }
      });
    }
    refreshParticipants(room);
    setState((current) => ({ ...current, videoEnabled: enabled, audioEnabled: true, localAudioTrackCount, error: "", diagnosticCode: "" }));
  }, [refreshParticipants]);

  const setSpeakerEnabled = useCallback(async (enabled: boolean) => {
    const audioSession = audioSessionRef.current;
    if (!audioSession) throw new Error("Broadcast audio session is not available.");
    await selectRealtimeAudioOutput(audioSession, enabled);
    setState((current) => ({ ...current, speakerEnabled: enabled, error: "" }));
  }, []);

  const setRemoteAudioEnabled = useCallback(async (enabled: boolean) => {
    const room = roomRef.current;
    if (!room) throw new Error("Broadcast media is not connected.");
    remoteAudioEnabledRef.current = enabled;
    await stabilizeLiveRemotePlayback(room, audioDeviceModuleRef.current, audioSessionRef.current, enabled, {
      settleMs: 0,
      context: {
        sessionId: roomNameRef.current,
        correlationId: correlationIdRef.current,
        roomType: "livestream",
        participantRole: roleRef.current
      }
    });
    setState((current) => ({ ...current, remoteAudioEnabled: enabled, error: "" }));
  }, []);

  const showAudioRoutePicker = useCallback(async () => {
    const audioSession = audioSessionRef.current;
    if (!audioSession) throw new Error("Broadcast audio session is not available.");
    await showRealtimeAudioRoutePicker(audioSession);
  }, []);

  const switchCamera = useCallback(async () => {
    const localParticipant = roomRef.current?.localParticipant;
    const publications = Array.from(localParticipant?.videoTrackPublications?.values?.() || []) as any[];
    const publication = publications.find((item) => item?.track);
    if (!publication?.track?.switchCamera) throw new Error("Camera is not active.");
    await publication.track.switchCamera();
    const room = roomRef.current;
    const localAudioTrackCount = await publishMicrophoneForPath(room, useV2Ref.current, {
      role: roleRef.current,
      room: roomNameRef.current,
      correlationId: correlationIdRef.current
    });
    if (localAudioTrackCount <= 0) throw new Error("Camera switched, but microphone audio is no longer published.");
    if (useV2Ref.current) {
      await stabilizeLivePublisherAudio(room, audioDeviceModuleRef.current, audioSessionRef.current, {
        settleMs: 450,
        context: {
          sessionId: roomNameRef.current,
          correlationId: correlationIdRef.current,
          roomType: "livestream",
          participantRole: roleRef.current
        }
      });
    }
    refreshParticipants(room);
    setState((current) => ({ ...current, audioEnabled: true, localAudioTrackCount, error: "", diagnosticCode: "" }));
  }, [refreshParticipants]);

  useEffect(
    () => () => {
      const room = roomRef.current;
      const lease = audioLeaseRef.current;
      traceRef.current?.emit("component_unmounted", { room_state: String(room?.state || "unmounting"), currentOwner: lease?.ownerId, audioGeneration: lease?.leaseId, caller: "useLiveBroadcastRoom.effectCleanup", reason: "component_unmounted" });
      // Unmount must not leave a pending reconnect or refresh timer alive - it
      // would fire against a torn-down room and reclaim the audio session.
      intentionalTeardownRef.current = true;
      clearRecoveryTimers();
      // Navigation/remount is not broadcast termination. The module-scoped
      // runtime retains the current room and generation; only an explicit
      // stop/terminal command may disconnect or release media ownership.
      runtimeRef.current.attachResources({ room, audioLease: lease });
    },
    [clearRecoveryTimers]
  );

  return {
    ...state,
    lifecycle: lifecycleRef.current.getState(),
    connect,
    disconnect,
    startBroadcast: connect,
    stopBroadcast: disconnect,
    joinAsViewer: connect,
    leaveViewer: disconnect,
    setMicrophoneEnabled,
    setCameraEnabled,
    setSpeakerEnabled,
    setRemoteAudioEnabled,
    showAudioRoutePicker,
    switchCamera,
    getLastConnectError: () => lastConnectErrorRef.current,
    getAudioTrace: () => traceRef.current?.snapshot() || []
  };
}
