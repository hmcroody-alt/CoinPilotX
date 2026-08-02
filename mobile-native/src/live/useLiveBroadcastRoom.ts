import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, Platform } from "react-native";
import { RealtimeAudioOwnershipError, ownershipDenialMessage } from "../core/audioOwnershipPolicy";
import {
  activateRealtimeAudioSession,
  applyRemoteAudioEnabled as driveRemoteAudioEnabled,
  audioPublications,
  ensureMicrophonePublished,
  PULSE_LIVE_VIDEO_CAPTURE_OPTIONS,
  PULSE_LIVE_VIDEO_PUBLISH_OPTIONS,
  publicationHasTrack,
  releaseRealtimeAudioSession,
  resolveRealtimeAudioConfiguration,
  selectRealtimeAudioOutput,
  type RealtimeAudioMode,
  videoPublications
} from "../core/realtimeAudioEngine";
import { isLiveAudioV2Enabled, resolveLiveAudioPath } from "./liveAudioFlags";
import { publishLiveMicrophone } from "./liveAudioPublisher";
import {
  classifyDisconnect,
  millisecondsUntilRefresh,
  nextReconnectDelayMs,
  shouldAttemptReconnect
} from "./liveAudioRecovery";
import { emitLiveAudioEvent } from "./liveAudioTelemetry";
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

function liveAudioMode(credentials: LiveKitCredentials, publish: boolean): RealtimeAudioMode {
  if (!publish) return "live_viewer";
  return credentials.role === "cohost" || credentials.guestId > 0 ? "live_guest" : "live_host";
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
async function publishMicrophoneForPath(room: any, useV2: boolean, context: { role: string; room: string }): Promise<number> {
  if (!useV2) return ensureMicrophonePublished(room);
  emitLiveAudioEvent({ name: "live_audio_publish_started", path: "v2_isolated", role: context.role, room: context.room });
  const result = await publishLiveMicrophone(room);
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
      room: context.room,
      duplicatesRemoved: result.duplicatesRemoved
    });
  }
  return result.audioTrackCount;
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
  const roomRef = useRef<any>(null);
  const audioSessionRef = useRef<any>(null);
  const audioOwnerIdRef = useRef("");
  const activeSpeakersRef = useRef<Set<string>>(new Set());
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
    const room = roomRef.current;
    const ownerId = audioOwnerIdRef.current;
    intentionalTeardownRef.current = true;
    clearRecoveryTimers();
    reconnectAttemptRef.current = 0;
    roomRef.current = null;
    activeSpeakersRef.current = new Set();
    remoteAudioEnabledRef.current = true;
    if (room?.disconnect) await room.disconnect().catch(() => undefined);
    // Only release when we actually hold the session. The previous
    // `ownerId || reason` fallback passed a REASON STRING as the owner id, which
    // can never match the real owner, so the release silently no-opped and the
    // AVAudioSession leaked - blocking the next call or broadcast.
    if (ownerId) {
      await releaseRealtimeAudioSession(audioSessionRef.current, ownerId).catch(() => undefined);
      emitLiveAudioEvent({
        name: "live_audio_session_released",
        path: resolveLiveAudioPath({ audioV2Enabled: useV2Ref.current }),
        role: roleRef.current,
        room: roomNameRef.current,
        reason
      });
    }
    audioOwnerIdRef.current = "";
    credentialsRef.current = null;
    refreshCredentialsRef.current = undefined;
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
    if (!useV2Ref.current || intentionalTeardownRef.current) return false;
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

  const connect = useCallback(
    async (credentials: LiveKitCredentials, options: LiveConnectOptions = {}) => {
      if (Platform.OS === "web") {
        setState((current) => ({ ...current, supported: false, error: "Native LiveKit broadcasting requires an installed iOS or Android build." }));
        return false;
      }
      if (!credentials.token || !credentials.url) {
        setState((current) => ({ ...current, error: "PulseSoc did not return a usable LiveKit token for this broadcast." }));
        return false;
      }
      const publish = Boolean(options.publish && credentials.canPublish);

      // SERVER-AUTHORITATIVE GATE. The decision arrives on the token response the
      // client already fetches for every broadcast, so flipping the backend flag
      // takes effect on the next token fetch with no app release. There is no
      // local override on purpose - a client-side flag is not a kill switch.
      const useV2 = isLiveAudioV2Enabled(credentials);
      const audioPath = resolveLiveAudioPath(credentials);
      const telemetryRole = credentials.role || (publish ? "host" : "viewer");

      if (roomRef.current) await disconnect("replaced_room");
      clearRecoveryTimers();
      intentionalTeardownRef.current = false;
      useV2Ref.current = useV2;
      roleRef.current = telemetryRole;
      roomNameRef.current = credentials.room || credentials.identity || "";
      publishRef.current = publish;
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
        // AUDIO SESSION OWNERSHIP: use the canonical call-grade media engine.
        // LiveKit auto audio configuration is disabled, so PulseSoc must claim
        // one owner-controlled AVAudioSession before connecting or publishing.
        const mediaMode = liveAudioMode(credentials, publish);
        const appleAudioConfiguration = resolveLiveAudioConfiguration(mediaMode);
        audioOwnerIdRef.current = `live:${mediaMode}:${credentials.room || credentials.identity || Date.now()}`;
        try {
          await activateRealtimeAudioSession(livekitNative.AudioSession, mediaMode, audioOwnerIdRef.current, {
            speaker: true,
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
        } catch (ownershipError) {
          // A call already owns the audio session. Report it honestly instead of
          // stealing the session and cutting the user's call off mid-sentence.
          if (ownershipError instanceof RealtimeAudioOwnershipError) {
            audioOwnerIdRef.current = "";
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
        emitLiveAudioEvent({
          name: "live_audio_session_claimed",
          path: audioPath,
          role: telemetryRole,
          room: roomNameRef.current,
          outcome: mediaMode
        });

        const room = new livekitClient.Room({
          adaptiveStream: true,
          dynacast: true,
          audioCaptureDefaults: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true
          },
          videoCaptureDefaults: PULSE_LIVE_VIDEO_CAPTURE_OPTIONS,
          publishDefaults: {
            ...PULSE_LIVE_VIDEO_PUBLISH_OPTIONS,
            simulcast: true,
            dtx: true,
            red: true,
            stopMicTrackOnMute: false
          }
        });
        roomRef.current = room;

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
          setState((current) => ({ ...current, connected: false, reconnecting: true, connectionState: "reconnecting", reconnectCount: current.reconnectCount + 1 }));
        });
        room.on(livekitClient.RoomEvent.Reconnected, () => {
          reconnectAttemptRef.current = 0;
          setState((current) => ({ ...current, connected: true, reconnecting: false, recovering: false, connectionState: "connected", error: "" }));
          const audioTasks: Promise<unknown>[] = [applyRemoteAudioEnabled(room, remoteAudioEnabledRef.current)];
          if (publish) {
            audioTasks.push(publishMicrophoneForPath(room, useV2, { role: telemetryRole, room: roomNameRef.current }));
          }
          if (useV2) {
            // A reconnect can land on a different output device; reassert the
            // route we chose rather than inheriting whatever iOS picked.
            audioTasks.push(reapplyAudioRoute("reconnected"));
          }
          Promise.all(audioTasks).catch(() => undefined);
          refresh();
        });
        room.on(livekitClient.RoomEvent.ConnectionQualityChanged, (quality: unknown, participant: any) => {
          if (participant?.isLocal !== false) {
            setState((current) => ({ ...current, connectionQuality: String(quality || "unknown").toLowerCase() }));
          }
        });
        room.on(livekitClient.RoomEvent.ActiveSpeakersChanged, (speakers: any[]) => {
          activeSpeakersRef.current = new Set((speakers || []).map((speaker) => String(speaker?.identity || "")));
          refresh();
        });
        room.on(livekitClient.RoomEvent.MediaDevicesError, (mediaError: unknown) => {
          setState((current) => ({ ...current, error: readableError(mediaError, "Camera or microphone access failed.") }));
        });
        room.on(livekitClient.RoomEvent.ParticipantConnected, refresh);
        room.on(livekitClient.RoomEvent.ParticipantDisconnected, refresh);
        room.on(livekitClient.RoomEvent.TrackSubscribed, (track: any) => {
          // A newly subscribed audio track must follow the viewer's current sound
          // choice immediately. When sound is on, this mirrors the call path and
          // force-enables remote host/co-host audio instead of trusting defaults.
          if (String(track?.kind || "") === "audio") {
            applyRemoteAudioEnabled(room, remoteAudioEnabledRef.current).catch(() => undefined);
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
              void publishMicrophoneForPath(roomRef.current, true, { role: telemetryRole, room: roomNameRef.current });
            }
          });
        }

        room.on(livekitClient.RoomEvent.Disconnected, (reason: unknown) => {
          const reasonText = String(reason || "provider_disconnected");
          const classification = classifyDisconnect(reasonText);
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

        await room.connect(credentials.url, credentials.token, { autoSubscribe: true });
        if (publish) {
          if (useV2) {
            // ONE publish, event-driven. The legacy branch below publishes twice
            // around setCameraEnabled and then sleeps 150ms; each of those calls
            // could run its own enable/toggle cycle, so a single "go live" ran up
            // to four cycles and could leave duplicate audio publications.
            await publishMicrophoneForPath(room, true, { role: telemetryRole, room: roomNameRef.current });
            await room.localParticipant.setCameraEnabled(true, PULSE_LIVE_VIDEO_CAPTURE_OPTIONS, PULSE_LIVE_VIDEO_PUBLISH_OPTIONS);
            // Idempotent: a room that is already publishing is left alone, and
            // any duplicate produced by the camera publish is reconciled away.
            await publishMicrophoneForPath(room, true, { role: telemetryRole, room: roomNameRef.current });
          } else {
            await ensureLiveMicrophonePublished(room);
            await room.localParticipant.setCameraEnabled(true, PULSE_LIVE_VIDEO_CAPTURE_OPTIONS, PULSE_LIVE_VIDEO_PUBLISH_OPTIONS);
            await ensureLiveMicrophonePublished(room);
            await new Promise((resolve) => setTimeout(resolve, 150));
          }
        }
        await selectRealtimeAudioOutput(livekitNative.AudioSession, true).catch(() => undefined);
        await applyRemoteAudioEnabled(room, remoteAudioEnabledRef.current).catch(() => undefined);
        refresh();
        const publishedAudioCount = audioPublications(room.localParticipant).filter(publicationHasTrack).length;
        if (publish && publishedAudioCount <= 0) {
          const message = "Microphone connected, but PulseSoc could not verify a published audio track.";
          await room.disconnect?.().catch(() => undefined);
          await releaseRealtimeAudioSession(livekitNative.AudioSession, audioOwnerIdRef.current).catch(() => undefined);
          audioOwnerIdRef.current = "";
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
        console.info("PulseSoc Live media connected", {
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
        const message = readableError(error, "Native LiveKit broadcast connection failed.");
        await disconnect("connect_failed").catch(() => undefined);
        setState((current) => ({ ...current, connectionState: "failed", error: message, disconnectReason: "connect_failed", diagnosticCode: "LIVEKIT_CONNECT_FAILED" }));
        return false;
      }
    },
    [clearRecoveryTimers, disconnect, reapplyAudioRoute, refreshParticipants, scheduleReconnect, scheduleTokenRefresh]
  );
  connectRef.current = connect;

  const setMicrophoneEnabled = useCallback(async (enabled: boolean) => {
    const room = roomRef.current;
    if (!room) throw new Error("Broadcast media is not connected.");
    await room.localParticipant.setMicrophoneEnabled(enabled);
    refreshParticipants(room);
    setState((current) => ({ ...current, audioEnabled: enabled, error: "" }));
  }, [refreshParticipants]);

  const setCameraEnabled = useCallback(async (enabled: boolean) => {
    const room = roomRef.current;
    if (!room) throw new Error("Broadcast media is not connected.");
    await room.localParticipant.setCameraEnabled(enabled, PULSE_LIVE_VIDEO_CAPTURE_OPTIONS, PULSE_LIVE_VIDEO_PUBLISH_OPTIONS);
    const localAudioTrackCount = await publishMicrophoneForPath(room, useV2Ref.current, {
      role: roleRef.current,
      room: roomNameRef.current
    });
    if (localAudioTrackCount <= 0) throw new Error("Camera changed, but microphone audio is no longer published.");
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
    await applyRemoteAudioEnabled(room, enabled);
    setState((current) => ({ ...current, remoteAudioEnabled: enabled, error: "" }));
  }, []);

  const showAudioRoutePicker = useCallback(async () => {
    const audioSession = audioSessionRef.current;
    if (!audioSession) throw new Error("Broadcast audio session is not available.");
    if (Platform.OS === "ios") await audioSession.showAudioRoutePicker();
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
      room: roomNameRef.current
    });
    if (localAudioTrackCount <= 0) throw new Error("Camera switched, but microphone audio is no longer published.");
    refreshParticipants(room);
    setState((current) => ({ ...current, audioEnabled: true, localAudioTrackCount, error: "", diagnosticCode: "" }));
  }, [refreshParticipants]);

  useEffect(
    () => () => {
      const room = roomRef.current;
      const ownerId = audioOwnerIdRef.current;
      // Unmount must not leave a pending reconnect or refresh timer alive - it
      // would fire against a torn-down room and reclaim the audio session.
      intentionalTeardownRef.current = true;
      clearRecoveryTimers();
      roomRef.current = null;
      audioOwnerIdRef.current = "";
      credentialsRef.current = null;
      refreshCredentialsRef.current = undefined;
      room?.disconnect?.().catch(() => undefined);
      if (ownerId) releaseRealtimeAudioSession(audioSessionRef.current, ownerId).catch(() => undefined);
    },
    [clearRecoveryTimers]
  );

  return {
    ...state,
    connect,
    disconnect,
    setMicrophoneEnabled,
    setCameraEnabled,
    setSpeakerEnabled,
    setRemoteAudioEnabled,
    showAudioRoutePicker,
    switchCamera
  };
}
