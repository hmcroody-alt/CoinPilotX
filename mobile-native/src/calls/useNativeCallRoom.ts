import { useCallback, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";
import { PulseCallJoin } from "../api/calls";
import { reportPresenceActivity } from "../api/presenceSession";
import {
  activateRealtimeAudioSession,
  applyRemoteAudioEnabled,
  countPublishedAudioTracks,
  countSubscribedRemoteAudioTracks,
  ensureMicrophonePublished,
  reassertRealtimeMicrophone,
  releaseRealtimeAudioSession,
  selectRealtimeAudioOutput,
  showRealtimeAudioRoutePicker,
  stabilizeRealtimeAudioEngine,
  videoPublications,
  type RealtimeAudioLease
} from "../core/realtimeAudioEngine";
import {
  publishRealtimeMicrophone,
  setRealtimeMicrophoneEnabled,
  type RealtimePublicationContext
} from "../core/realtimeMicrophonePublisher";
import { RealtimeAudioStateMachine } from "../core/realtimeAudioStateMachine";
import { createRealtimeAudioCorrelationId, emitRealtimeAudioEvent } from "../core/realtimeAudioTelemetry";
import { initializeCallGradePublisherMedia } from "../core/realtimePublisherMedia";
import { parseMediaQualityFlags } from "../core/mediaQualityFlags";
import {
  buildRoomQualityOptions,
  resolveMediaQualityPlan,
  type MediaQualityPlan
} from "../core/mediaQualityPolicy";
import { emitMediaQualityEvent } from "../core/mediaQualityTelemetry";

type NativeCallRoomState = {
  supported: boolean;
  connecting: boolean;
  connected: boolean;
  reconnecting: boolean;
  connectionState: string;
  connectionQuality: string;
  error: string;
  diagnosticCode: string;
  participantCount: number;
  audioEnabled: boolean;
  videoEnabled: boolean;
  speakerEnabled: boolean;
  localAudioTrackCount: number;
  remoteAudioTrackCount: number;
  remoteAudioAvailable: boolean;
  localVideoTrack: any | null;
  remoteVideoTrack: any | null;
  reconnectCount: number;
  disconnectReason: string;
};

const initialState: NativeCallRoomState = {
  supported: Platform.OS !== "web",
  connecting: false,
  connected: false,
  reconnecting: false,
  connectionState: "disconnected",
  connectionQuality: "unknown",
  error: "",
  diagnosticCode: "",
  participantCount: 0,
  audioEnabled: true,
  videoEnabled: false,
  speakerEnabled: true,
  localAudioTrackCount: 0,
  remoteAudioTrackCount: 0,
  remoteAudioAvailable: false,
  localVideoTrack: null,
  remoteVideoTrack: null,
  reconnectCount: 0,
  disconnectReason: ""
};

let globalsRegistered = false;

export {
  countPublishedAudioTracks,
  countSubscribedRemoteAudioTracks
};

export const applyCallRemoteAudioEnabled = applyRemoteAudioEnabled;
export async function ensureCallMicrophonePublished(
  room: any,
  options: { timeoutMs?: number; useV2?: boolean; fallbackEnabled?: boolean; context?: RealtimePublicationContext } = {}
): Promise<number> {
  if (options.useV2 !== false) {
    return (await publishRealtimeMicrophone(room, { timeoutMs: options.timeoutMs, context: options.context })).audioTrackCount;
  }
  if (options.fallbackEnabled === false) return 0;
  return ensureMicrophonePublished(room);
}

export async function initializeCallLocalMedia(
  room: any,
  options: {
    video: boolean;
    useV2?: boolean;
    fallbackEnabled?: boolean;
    context?: RealtimePublicationContext;
    /**
     * Resolved quality plan. At `stable` a video call carries no capture or
     * publish options, so the call below stays literally `setCameraEnabled(true)`
     * — the exact call the verified baseline made.
     */
    quality?: MediaQualityPlan | null;
  }
): Promise<number> {
  const publishMicrophone = () => ensureCallMicrophonePublished(room, {
      useV2: options.useV2,
      fallbackEnabled: options.fallbackEnabled,
      context: options.context
    });
  return initializeCallGradePublisherMedia({
    video: options.video,
    publishMicrophone,
    enableCamera: async () => {
      emitRealtimeAudioEvent({ name: "camera_publish_started", ...options.context });
      const capture = options.quality?.videoCaptureDefaults;
      const publish = options.quality?.videoPublishDefaults;
      if (capture && publish) await room.localParticipant.setCameraEnabled(true, capture, publish);
      else await room.localParticipant.setCameraEnabled(true);
      emitRealtimeAudioEvent({ name: "camera_published", ...options.context, outcome: "published" });
    },
    reassertMicrophone: () => reassertRealtimeMicrophone(room, options.context)
  });
}

function readableError(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function useNativeCallRoom() {
  const roomRef = useRef<any>(null);
  const audioSessionRef = useRef<any>(null);
  const audioDeviceModuleRef = useRef<any>(null);
  const audioLeaseRef = useRef<RealtimeAudioLease | null>(null);
  const lifecycleRef = useRef(new RealtimeAudioStateMachine());
  const audioV2Ref = useRef(false);
  const audioV2FallbackRef = useRef(true);
  const qualityPlanRef = useRef<MediaQualityPlan | null>(null);
  const publicationContextRef = useRef<RealtimePublicationContext>({ roomType: "audio_call", participantRole: "member" });
  const [state, setState] = useState<NativeCallRoomState>(initialState);

  const refreshMediaState = useCallback((room = roomRef.current) => {
    if (!room) return;
    const localAudioTrackCount = countPublishedAudioTracks(room.localParticipant);
    const remoteAudioTrackCount = countSubscribedRemoteAudioTracks(room);
    const localPublications = videoPublications(room.localParticipant);
    const localVideoTrack = localPublications.find((publication) => publication?.track)?.track || null;
    let remoteVideoTrack: any | null = null;
    for (const participant of Array.from(room.remoteParticipants?.values?.() || []) as any[]) {
      const publications = videoPublications(participant);
      remoteVideoTrack = publications.find((publication) => publication?.track && publication?.isSubscribed !== false)?.track || remoteVideoTrack;
      if (remoteVideoTrack) break;
    }
    setState((current) => ({
      ...current,
      participantCount: Math.max(1, Number(room.remoteParticipants?.size || 0) + 1),
      localAudioTrackCount,
      remoteAudioTrackCount,
      remoteAudioAvailable: remoteAudioTrackCount > 0,
      audioEnabled: localAudioTrackCount > 0 && room.localParticipant?.isMicrophoneEnabled !== false,
      localVideoTrack,
      remoteVideoTrack,
      videoEnabled: Boolean(localVideoTrack)
    }));
  }, []);

  const disconnect = useCallback(async (reason = "local_disconnect") => {
    lifecycleRef.current.markTerminal();
    lifecycleRef.current.tryTransition("room", "disconnecting");
    lifecycleRef.current.tryTransition("local", "unpublishing");
    const room = roomRef.current;
    roomRef.current = null;
    audioDeviceModuleRef.current = null;
    // Restore normal presence the moment we leave the room, so the caller stops
    // reading as "In audio/video call" without waiting for the activity TTL.
    reportPresenceActivity("idle", "").catch(() => undefined);
    if (room?.disconnect) await room.disconnect().catch(() => undefined);
    const lease = audioLeaseRef.current;
    audioLeaseRef.current = null;
    if (lease) await releaseRealtimeAudioSession(audioSessionRef.current, lease).catch(() => undefined);
    lifecycleRef.current.tryTransition("local", "released");
    lifecycleRef.current.tryTransition("remote", "ended");
    lifecycleRef.current.tryTransition("room", "disconnected");
    setState((current) => ({
      ...current,
      connecting: false,
      connected: false,
      reconnecting: false,
      connectionState: "disconnected",
      participantCount: 0,
      localAudioTrackCount: 0,
      remoteAudioTrackCount: 0,
      remoteAudioAvailable: false,
      localVideoTrack: null,
      remoteVideoTrack: null,
      disconnectReason: reason,
      diagnosticCode: reason
    }));
  }, []);

  const connect = useCallback(async (join: PulseCallJoin, options: { video?: boolean } = {}) => {
    if (Platform.OS === "web") {
      setState((current) => ({ ...current, supported: false, error: "Native LiveKit calls require an installed iOS or Android build." }));
      return false;
    }
    if (!join.token || !join.livekit_url) {
      setState((current) => ({ ...current, error: "PulseSoc did not return a usable LiveKit token for this call." }));
      return false;
    }

    if (roomRef.current) await disconnect("replaced_room");
    audioV2Ref.current = join.realtime_audio_v2_enabled === true;
    audioV2FallbackRef.current = join.realtime_audio_v2_fallback_enabled !== false;
    publicationContextRef.current = {
      correlationId: createRealtimeAudioCorrelationId(),
      sessionId: join.room_name,
      roomType: join.room_type || (options.video ? "video_call" : "audio_call"),
      participantRole: join.participant_role || "member",
      canPublishMicrophone: join.can_publish !== false && (join.can_publish_sources || ["microphone"]).includes("microphone")
    };
    lifecycleRef.current = new RealtimeAudioStateMachine();
    lifecycleRef.current.transition("room", "connecting");
    lifecycleRef.current.transition("local", "acquiringSession");
    setState((current) => ({ ...initialState, supported: current.supported, connecting: true, connectionState: "connecting" }));
    try {
      const livekitNative = await import("@livekit/react-native");
      const livekitClient = await import("livekit-client");
      if (!globalsRegistered) {
        livekitNative.registerGlobals({ autoConfigureAudioSession: false });
        globalsRegistered = true;
      }
      audioSessionRef.current = livekitNative.AudioSession;
      audioDeviceModuleRef.current = livekitNative.AudioDeviceModule;
      const ownerId = `call:${join.room_name || Date.now()}`;
      audioLeaseRef.current = await activateRealtimeAudioSession(
        livekitNative.AudioSession,
        options.video ? "video_call" : "audio_call",
        ownerId,
        {
          speaker: true,
          correlationId: publicationContextRef.current.correlationId,
          participantRole: join.participant_role || "member"
        }
      );
      lifecycleRef.current.transition("local", "publishing");

      // The quality plan is resolved once, before the Room exists, and never
      // recomputed mid-session. Resolving it later would mean a Room whose
      // configuration depends on when a flag happened to be read.
      //
      // With every flag off — the shipping state — buildRoomQualityOptions
      // returns exactly the literal that used to be written here:
      //   { adaptiveStream: true, dynacast: true,
      //     audioCaptureDefaults: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      //     publishDefaults: { simulcast: true, dtx: true, red: true, stopMicTrackOnMute: false } }
      // mediaQualityPolicy.test.ts asserts that equality against this file's
      // own source, so the claim cannot rot.
      const plan = resolveMediaQualityPlan({
        feature: options.video ? "video_call" : "audio_call",
        flags: parseMediaQualityFlags(join.media_quality)
      });
      qualityPlanRef.current = plan;
      emitMediaQualityEvent({
        name: "quality_plan_resolved",
        correlationId: publicationContextRef.current.correlationId,
        sessionId: join.room_name,
        feature: plan.feature,
        profile: plan.profile,
        requestedProfile: plan.requestedProfile,
        contentMode: plan.contentMode,
        reasons: plan.reasons,
        audioPathUnchanged: true
      });

      const room = new livekitClient.Room(buildRoomQualityOptions(plan));
      roomRef.current = room;

      const refresh = () => refreshMediaState(room);
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
        lifecycleRef.current.tryTransition("local", "recovering");
        lifecycleRef.current.tryTransition("remote", "recovering");
        setState((current) => ({
          ...current,
          connected: false,
          reconnecting: true,
          connectionState: "reconnecting",
          reconnectCount: current.reconnectCount + 1
        }));
      });
      room.on(livekitClient.RoomEvent.Reconnected, () => {
        lifecycleRef.current.tryTransition("room", "connected");
        lifecycleRef.current.tryTransition("local", "publishing");
        setState((current) => ({ ...current, connected: true, reconnecting: false, connectionState: "connected", error: "" }));
        ensureCallMicrophonePublished(room, {
          useV2: audioV2Ref.current,
          fallbackEnabled: audioV2FallbackRef.current,
          context: publicationContextRef.current
        })
          .then((count) => {
            if (count <= 0) {
              setState((current) => ({
                ...current,
                audioEnabled: false,
                localAudioTrackCount: 0,
                error: "Microphone reconnected, but PulseSoc could not verify published call audio.",
                diagnosticCode: "CALL_LOCAL_AUDIO_REPUBLISH_FAILED"
              }));
            } else lifecycleRef.current.tryTransition("local", "published");
            return applyCallRemoteAudioEnabled(room, true);
          })
          .then(() => options.video
            ? stabilizeRealtimeAudioEngine(livekitNative.AudioDeviceModule, {
                playout: true,
                recording: true,
                settleMs: 250,
                context: publicationContextRef.current
              })
            : undefined
          )
          .then(() => options.video ? selectRealtimeAudioOutput(livekitNative.AudioSession, true) : undefined)
          .catch(() => undefined);
        refresh();
      });
      room.on(livekitClient.RoomEvent.ConnectionQualityChanged, (quality: unknown, participant: any) => {
        if (participant?.isLocal !== false) {
          setState((current) => ({ ...current, connectionQuality: String(quality || "unknown").toLowerCase() }));
        }
      });
      room.on(livekitClient.RoomEvent.MediaDevicesError, (mediaError: unknown) => {
        setState((current) => ({ ...current, error: readableError(mediaError, "Camera or microphone access failed.") }));
      });
      room.on(livekitClient.RoomEvent.ParticipantConnected, refresh);
      room.on(livekitClient.RoomEvent.ParticipantDisconnected, refresh);
      room.on(livekitClient.RoomEvent.TrackSubscribed, (track: any) => {
        if (String(track?.kind || "") === "audio") {
          lifecycleRef.current.tryTransition("remote", "publicationAvailable");
          lifecycleRef.current.tryTransition("remote", "subscribing");
          lifecycleRef.current.tryTransition("remote", "subscribed");
          lifecycleRef.current.tryTransition("remote", "playing");
          applyCallRemoteAudioEnabled(room, true).catch(() => undefined);
          if (options.video) {
            stabilizeRealtimeAudioEngine(livekitNative.AudioDeviceModule, {
              playout: true,
              recording: true,
              settleMs: 0,
              context: publicationContextRef.current
            }).catch(() => undefined);
            selectRealtimeAudioOutput(livekitNative.AudioSession, true).catch(() => undefined);
          }
        }
        refresh();
      });
      room.on(livekitClient.RoomEvent.TrackUnsubscribed, refresh);
      room.on(livekitClient.RoomEvent.TrackMuted, refresh);
      room.on(livekitClient.RoomEvent.TrackUnmuted, refresh);
      room.on(livekitClient.RoomEvent.LocalTrackPublished, refresh);
      room.on(livekitClient.RoomEvent.LocalTrackUnpublished, refresh);
      room.on(livekitClient.RoomEvent.Disconnected, (reason: unknown) => {
        lifecycleRef.current.markTerminal();
        lifecycleRef.current.tryTransition("room", "disconnected");
        lifecycleRef.current.tryTransition("remote", "ended");
        setState((current) => ({
          ...current,
          connected: false,
          connecting: false,
          reconnecting: false,
          connectionState: "disconnected",
          participantCount: 0,
          localAudioTrackCount: 0,
          remoteAudioTrackCount: 0,
          remoteAudioAvailable: false,
          localVideoTrack: null,
          remoteVideoTrack: null,
          disconnectReason: String(reason || "provider_disconnected")
        }));
      });

      await room.connect(join.livekit_url, join.token, { autoSubscribe: true });
      lifecycleRef.current.tryTransition("room", "connected");
      const localAudioTrackCount = await initializeCallLocalMedia(room, {
        video: Boolean(options.video),
        useV2: audioV2Ref.current,
        fallbackEnabled: audioV2FallbackRef.current,
        context: publicationContextRef.current,
        quality: qualityPlanRef.current
      });
      if (localAudioTrackCount <= 0) {
        lifecycleRef.current.tryTransition("local", "failed");
        const message = "Microphone connected, but PulseSoc could not verify published call audio.";
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
          diagnosticCode: "CALL_LOCAL_AUDIO_NOT_PUBLISHED",
          audioEnabled: false,
          localAudioTrackCount: 0
        }));
        return false;
      }
      lifecycleRef.current.tryTransition("local", "published");
      if (options.video) {
        await stabilizeRealtimeAudioEngine(livekitNative.AudioDeviceModule, {
          playout: true,
          recording: true,
          settleMs: 650,
          context: publicationContextRef.current
        });
      }
      await selectRealtimeAudioOutput(livekitNative.AudioSession, true).catch(() => undefined);
      await applyCallRemoteAudioEnabled(room, true).catch(() => undefined);
      refresh();
      setState((current) => ({
        ...current,
        connecting: false,
        connected: true,
        reconnecting: false,
        connectionState: "connected",
        participantCount: room.remoteParticipants.size + 1,
        audioEnabled: true,
        localAudioTrackCount,
        remoteAudioTrackCount: countSubscribedRemoteAudioTracks(room),
        remoteAudioAvailable: countSubscribedRemoteAudioTracks(room) > 0,
        videoEnabled: Boolean(options.video),
        speakerEnabled: true,
        error: "",
        diagnosticCode: ""
      }));
      // Call presence is session-bound on the server: it lives as long as this
      // device's presence session does and is cleared on disconnect, so a
      // crashed call cannot strand the user as permanently "in a call".
      reportPresenceActivity(options.video ? "in_video_call" : "in_audio_call", String(join.room_name || "")).catch(() => undefined);
      return true;
    } catch (error) {
      const message = readableError(error, "Native LiveKit call connection failed.");
      await disconnect("connect_failed").catch(() => undefined);
      setState((current) => ({ ...current, connectionState: "failed", error: message, disconnectReason: "connect_failed" }));
      return false;
    }
  }, [disconnect, refreshMediaState]);

  const setMicrophoneEnabled = useCallback(async (enabled: boolean) => {
    const room = roomRef.current;
    if (!room) throw new Error("Call media is not connected.");
    const localAudioTrackCount = await setRealtimeMicrophoneEnabled(room, enabled);
    if (enabled && localAudioTrackCount <= 0) throw new Error("Microphone could not publish call audio.");
    lifecycleRef.current.tryTransition("local", enabled ? "published" : "muted");
    setState((current) => ({ ...current, audioEnabled: enabled && localAudioTrackCount > 0, localAudioTrackCount, error: "", diagnosticCode: "" }));
  }, []);

  const setCameraEnabled = useCallback(async (enabled: boolean) => {
    const room = roomRef.current;
    if (!room) throw new Error("Call media is not connected.");
    await room.localParticipant.setCameraEnabled(enabled);
    let localAudioTrackCount = await reassertRealtimeMicrophone(room, publicationContextRef.current);
    if (localAudioTrackCount <= 0) {
      localAudioTrackCount = await ensureCallMicrophonePublished(room, {
        useV2: audioV2Ref.current,
        fallbackEnabled: audioV2FallbackRef.current,
        context: publicationContextRef.current
      });
    }
    if (localAudioTrackCount <= 0) throw new Error("Camera changed, but microphone audio is no longer published.");
    await stabilizeRealtimeAudioEngine(audioDeviceModuleRef.current, {
      playout: true,
      recording: true,
      settleMs: 450,
      context: publicationContextRef.current
    });
    await selectRealtimeAudioOutput(audioSessionRef.current, true).catch(() => undefined);
    refreshMediaState(room);
    setState((current) => ({ ...current, videoEnabled: enabled, audioEnabled: true, localAudioTrackCount, error: "", diagnosticCode: "" }));
  }, [refreshMediaState]);

  const setSpeakerEnabled = useCallback(async (enabled: boolean) => {
    const audioSession = audioSessionRef.current;
    if (!audioSession) throw new Error("Call audio session is not available.");
    await selectRealtimeAudioOutput(audioSession, enabled);
    setState((current) => ({ ...current, speakerEnabled: enabled, error: "" }));
  }, []);

  const showAudioRoutePicker = useCallback(async () => {
    const audioSession = audioSessionRef.current;
    if (!audioSession) throw new Error("Call audio session is not available.");
    await showRealtimeAudioRoutePicker(audioSession);
  }, []);

  const switchCamera = useCallback(async () => {
    const localParticipant = roomRef.current?.localParticipant;
    const publications = Array.from(localParticipant?.videoTrackPublications?.values?.() || []) as any[];
    const publication = publications.find((item) => item?.track);
    if (!publication?.track?.switchCamera) throw new Error("Camera is not active.");
    await publication.track.switchCamera();
    const room = roomRef.current;
    let localAudioTrackCount = await reassertRealtimeMicrophone(room, publicationContextRef.current);
    if (localAudioTrackCount <= 0) {
      localAudioTrackCount = await ensureCallMicrophonePublished(room, {
        useV2: audioV2Ref.current,
        fallbackEnabled: audioV2FallbackRef.current,
        context: publicationContextRef.current
      });
    }
    if (localAudioTrackCount <= 0) throw new Error("Camera switched, but microphone audio is no longer published.");
    await stabilizeRealtimeAudioEngine(audioDeviceModuleRef.current, {
      playout: true,
      recording: true,
      settleMs: 450,
      context: publicationContextRef.current
    });
    await selectRealtimeAudioOutput(audioSessionRef.current, true).catch(() => undefined);
    refreshMediaState(room);
  }, [refreshMediaState]);

  useEffect(() => () => {
    const room = roomRef.current;
    const lease = audioLeaseRef.current;
    roomRef.current = null;
    audioDeviceModuleRef.current = null;
    audioLeaseRef.current = null;
    room?.disconnect?.().catch(() => undefined);
    if (lease) releaseRealtimeAudioSession(audioSessionRef.current, lease).catch(() => undefined);
  }, []);

  return {
    ...state,
    lifecycle: lifecycleRef.current.getState(),
    connect,
    disconnect,
    setMicrophoneEnabled,
    setCameraEnabled,
    setSpeakerEnabled,
    showAudioRoutePicker,
    switchCamera
  };
}
