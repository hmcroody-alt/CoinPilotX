import { useCallback, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";
import { PulseCallJoin } from "../api/calls";
import { reportPresenceActivity } from "../api/presenceSession";
import { callAudioSessionConfiguration, nativeAudioOutput, shouldSurfaceVideoAudioWarning, summarizeCallMediaState } from "./callMediaState";

type NativeCallRoomState = {
  supported: boolean;
  connecting: boolean;
  connected: boolean;
  reconnecting: boolean;
  connectionState: string;
  connectionQuality: string;
  error: string;
  participantCount: number;
  audioEnabled: boolean;
  localAudioPublished: boolean;
  localAudioMuted: boolean;
  remoteAudioSubscribed: boolean;
  remoteAudioMuted: boolean;
  videoEnabled: boolean;
  speakerEnabled: boolean;
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
  participantCount: 0,
  audioEnabled: true,
  localAudioPublished: false,
  localAudioMuted: true,
  remoteAudioSubscribed: false,
  remoteAudioMuted: true,
  videoEnabled: false,
  speakerEnabled: true,
  localVideoTrack: null,
  remoteVideoTrack: null,
  reconnectCount: 0,
  disconnectReason: ""
};

let globalsRegistered = false;
const VIDEO_AUDIO_WARNING = "Video call audio is not fully connected. Check microphone access or retry audio.";

function readableError(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

export function useNativeCallRoom() {
  const roomRef = useRef<any>(null);
  const audioSessionRef = useRef<any>(null);
  const desiredAudioEnabledRef = useRef(true);
  const callTypeRef = useRef<"audio" | "video">("audio");
  const [state, setState] = useState<NativeCallRoomState>(initialState);

  const refreshMediaState = useCallback((room = roomRef.current) => {
    if (!room) return;
    const media = summarizeCallMediaState(room);
    const remoteParticipantCount = Number(room.remoteParticipants?.size || 0);
    setState((current) => {
      const shouldWarnAboutVideoAudio = shouldSurfaceVideoAudioWarning({
        callType: callTypeRef.current,
        connected: current.connected,
        localAudioPublished: media.localAudioPublished,
        remoteParticipantCount,
        remoteAudioSubscribed: media.remoteAudioSubscribed
      });
      return {
        ...current,
        participantCount: Math.max(1, remoteParticipantCount + 1),
        audioEnabled: media.localAudioPublished && !media.localAudioMuted,
        localAudioPublished: media.localAudioPublished,
        localAudioMuted: media.localAudioMuted,
        remoteAudioSubscribed: media.remoteAudioSubscribed,
        remoteAudioMuted: media.remoteAudioMuted,
        videoEnabled: media.localVideoPublished,
        localVideoTrack: media.localVideoTrack,
        remoteVideoTrack: media.remoteVideoTrack,
        error: shouldWarnAboutVideoAudio ? VIDEO_AUDIO_WARNING : current.error === VIDEO_AUDIO_WARNING ? "" : current.error
      };
    });
  }, []);

  const ensureMicrophonePublished = useCallback(async (room: any, enabled = desiredAudioEnabledRef.current) => {
    let media = summarizeCallMediaState(room);
    if (!media.localAudioPublished || (enabled && media.localAudioMuted)) {
      await room.localParticipant.setMicrophoneEnabled(true);
      media = summarizeCallMediaState(room);
    }
    if (!media.localAudioPublished) {
      throw new Error("Microphone could not be published for this call.");
    }
    if (!enabled && !media.localAudioMuted) {
      await room.localParticipant.setMicrophoneEnabled(false);
      media = summarizeCallMediaState(room);
    }
    return media;
  }, []);

  const disconnect = useCallback(async (reason = "local_disconnect") => {
    const room = roomRef.current;
    roomRef.current = null;
    desiredAudioEnabledRef.current = true;
    callTypeRef.current = "audio";
    // Restore normal presence the moment we leave the room, so the caller stops
    // reading as "In audio/video call" without waiting for the activity TTL.
    reportPresenceActivity("idle", "").catch(() => undefined);
    if (room?.disconnect) await room.disconnect().catch(() => undefined);
    if (audioSessionRef.current?.stopAudioSession) {
      await audioSessionRef.current.stopAudioSession().catch(() => undefined);
    }
    setState((current) => ({
      ...current,
      connecting: false,
      connected: false,
      reconnecting: false,
      connectionState: "disconnected",
      participantCount: 0,
      localVideoTrack: null,
      remoteVideoTrack: null,
      localAudioPublished: false,
      localAudioMuted: true,
      remoteAudioSubscribed: false,
      remoteAudioMuted: true,
      disconnectReason: reason
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
    callTypeRef.current = options.video ? "video" : "audio";
    setState((current) => ({ ...initialState, supported: current.supported, connecting: true, connectionState: "connecting" }));
    try {
      const livekitNative = await import("@livekit/react-native");
      const livekitClient = await import("livekit-client");
      if (!globalsRegistered) {
        livekitNative.registerGlobals({ autoConfigureAudioSession: false });
        globalsRegistered = true;
      }
      audioSessionRef.current = livekitNative.AudioSession;
      // ROOT-CAUSE FIX (mirrors useLiveBroadcastRoom): autoConfigureAudioSession
      // is false, so LiveKit will not set the iOS AVAudioSession category. Put it
      // into playAndRecord/videoChat BEFORE starting the session so the mic
      // actually captures — otherwise the published audio track is silent.
      if (Platform.OS === "ios" && typeof livekitNative.AudioSession.setAppleAudioConfiguration === "function") {
        await livekitNative.AudioSession.setAppleAudioConfiguration(callAudioSessionConfiguration(options.video ? "video" : "audio")).catch(() => undefined);
      }
      await livekitNative.AudioSession.configureAudio({ ios: { defaultOutput: "speaker" } }).catch(() => undefined);
      await livekitNative.AudioSession.startAudioSession();

      const room = new livekitClient.Room({
        adaptiveStream: true,
        dynacast: true,
        audioCaptureDefaults: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true
        },
        publishDefaults: {
          simulcast: true,
          dtx: true,
          red: true,
          stopMicTrackOnMute: false
        }
      });
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
        setState((current) => ({
          ...current,
          connected: false,
          reconnecting: true,
          connectionState: "reconnecting",
          reconnectCount: current.reconnectCount + 1
        }));
      });
      room.on(livekitClient.RoomEvent.Reconnected, () => {
        setState((current) => ({ ...current, connected: true, reconnecting: false, connectionState: "connected", error: "" }));
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
      room.on(livekitClient.RoomEvent.TrackSubscribed, refresh);
      room.on(livekitClient.RoomEvent.TrackUnsubscribed, refresh);
      room.on(livekitClient.RoomEvent.TrackPublished, refresh);
      room.on(livekitClient.RoomEvent.TrackUnpublished, refresh);
      room.on(livekitClient.RoomEvent.TrackMuted, refresh);
      room.on(livekitClient.RoomEvent.TrackUnmuted, refresh);
      room.on(livekitClient.RoomEvent.LocalTrackPublished, refresh);
      room.on(livekitClient.RoomEvent.LocalTrackUnpublished, refresh);
      room.on(livekitClient.RoomEvent.Disconnected, (reason: unknown) => {
        setState((current) => ({
          ...current,
          connected: false,
          connecting: false,
          reconnecting: false,
          connectionState: "disconnected",
          participantCount: 0,
          localVideoTrack: null,
          remoteVideoTrack: null,
          localAudioPublished: false,
          localAudioMuted: true,
          remoteAudioSubscribed: false,
          remoteAudioMuted: true,
          disconnectReason: String(reason || "provider_disconnected")
        }));
      });

      await room.connect(join.livekit_url, join.token, { autoSubscribe: true });
      desiredAudioEnabledRef.current = true;
      await ensureMicrophonePublished(room, true);
      if (options.video) {
        await room.localParticipant.setCameraEnabled(true);
        await ensureMicrophonePublished(room, true);
      }
      await livekitNative.AudioSession.selectAudioOutput(Platform.OS === "ios" ? "force_speaker" : "speaker").catch(() => undefined);
      refresh();
      const media = summarizeCallMediaState(room);
      setState((current) => ({
        ...current,
        connecting: false,
        connected: true,
        reconnecting: false,
        connectionState: "connected",
        participantCount: room.remoteParticipants.size + 1,
        audioEnabled: media.localAudioPublished && !media.localAudioMuted,
        localAudioPublished: media.localAudioPublished,
        localAudioMuted: media.localAudioMuted,
        remoteAudioSubscribed: media.remoteAudioSubscribed,
        remoteAudioMuted: media.remoteAudioMuted,
        videoEnabled: media.localVideoPublished,
        speakerEnabled: true,
        error: ""
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
  }, [disconnect, ensureMicrophonePublished, refreshMediaState]);

  const setMicrophoneEnabled = useCallback(async (enabled: boolean) => {
    const room = roomRef.current;
    if (!room) throw new Error("Call media is not connected.");
    desiredAudioEnabledRef.current = enabled;
    await room.localParticipant.setMicrophoneEnabled(enabled);
    refreshMediaState(room);
    const media = summarizeCallMediaState(room);
    setState((current) => ({ ...current, audioEnabled: media.localAudioPublished && !media.localAudioMuted, localAudioPublished: media.localAudioPublished, localAudioMuted: media.localAudioMuted, error: "" }));
  }, [refreshMediaState]);

  const setCameraEnabled = useCallback(async (enabled: boolean) => {
    const room = roomRef.current;
    if (!room) throw new Error("Call media is not connected.");
    await room.localParticipant.setCameraEnabled(enabled);
    await ensureMicrophonePublished(room, desiredAudioEnabledRef.current);
    refreshMediaState(room);
    setState((current) => ({ ...current, videoEnabled: enabled, error: "" }));
  }, [ensureMicrophonePublished, refreshMediaState]);

  const setSpeakerEnabled = useCallback(async (enabled: boolean) => {
    const audioSession = audioSessionRef.current;
    if (!audioSession) throw new Error("Call audio session is not available.");
    await audioSession.selectAudioOutput(nativeAudioOutput(enabled));
    setState((current) => ({ ...current, speakerEnabled: enabled, error: "" }));
  }, []);

  const showAudioRoutePicker = useCallback(async () => {
    const audioSession = audioSessionRef.current;
    if (!audioSession) throw new Error("Call audio session is not available.");
    if (Platform.OS === "ios") await audioSession.showAudioRoutePicker();
  }, []);

  const switchCamera = useCallback(async () => {
    const localParticipant = roomRef.current?.localParticipant;
    const publications = Array.from(localParticipant?.videoTrackPublications?.values?.() || []) as any[];
    const publication = publications.find((item) => item?.track);
    if (!publication?.track?.switchCamera) throw new Error("Camera is not active.");
    await publication.track.switchCamera();
    const room = roomRef.current;
    if (room) await ensureMicrophonePublished(room, desiredAudioEnabledRef.current);
  }, [ensureMicrophonePublished]);

  useEffect(() => () => {
    const room = roomRef.current;
    roomRef.current = null;
    room?.disconnect?.().catch(() => undefined);
    audioSessionRef.current?.stopAudioSession?.().catch(() => undefined);
  }, []);

  return {
    ...state,
    connect,
    disconnect,
    setMicrophoneEnabled,
    setCameraEnabled,
    setSpeakerEnabled,
    showAudioRoutePicker,
    switchCamera
  };
}
