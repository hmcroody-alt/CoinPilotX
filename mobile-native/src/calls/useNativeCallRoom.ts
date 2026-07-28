import { useCallback, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";
import { PulseCallJoin } from "../api/calls";
import { reportPresenceActivity } from "../api/presenceSession";
import {
  applyRemoteAudioEnabled,
  countPublishedAudioTracks,
  countSubscribedRemoteAudioTracks,
  ensureMicrophonePublished,
  realtimeRoomOptions,
  restoreRealtimeRoomAudio,
  selectRealtimeSpeakerOutput,
  setLocalMicrophoneEnabled,
  showRealtimeAudioRoutePicker,
  startRealtimeAudioSession,
  stopRealtimeAudioSession,
  type RealtimeAudioRuntime
} from "../core/realtimeAudioEngine";

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

function readableError(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function videoPublications(participant: any): any[] {
  return Array.from(participant?.videoTrackPublications?.values?.() || []) as any[];
}

export { countPublishedAudioTracks, countSubscribedRemoteAudioTracks };
export const applyCallRemoteAudioEnabled = applyRemoteAudioEnabled;
export const ensureCallMicrophonePublished = ensureMicrophonePublished;

export function useNativeCallRoom() {
  const roomRef = useRef<any>(null);
  const audioRuntimeRef = useRef<RealtimeAudioRuntime | null>(null);
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
    const room = roomRef.current;
    roomRef.current = null;
    // Restore normal presence the moment we leave the room, so the caller stops
    // reading as "In audio/video call" without waiting for the activity TTL.
    reportPresenceActivity("idle", "").catch(() => undefined);
    if (room?.disconnect) await room.disconnect().catch(() => undefined);
    await stopRealtimeAudioSession(audioRuntimeRef.current).catch(() => undefined);
    audioRuntimeRef.current = null;
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
    setState((current) => ({ ...initialState, supported: current.supported, connecting: true, connectionState: "connecting" }));
    try {
      const livekitNative = await import("@livekit/react-native");
      const livekitClient = await import("livekit-client");
      audioRuntimeRef.current = await startRealtimeAudioSession(livekitNative, "interactive");

      const room = new livekitClient.Room(realtimeRoomOptions());
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
        restoreRealtimeRoomAudio(room, {
          publishMicrophone: true,
          microphoneEnabled: true,
          remoteAudioEnabled: true
        })
          .then(({ localAudioTrackCount }) => {
            if (localAudioTrackCount <= 0) {
              setState((current) => ({
                ...current,
                audioEnabled: false,
                localAudioTrackCount: 0,
                error: "Microphone reconnected, but PulseSoc could not verify published call audio.",
                diagnosticCode: "CALL_LOCAL_AUDIO_REPUBLISH_FAILED"
              }));
            }
          })
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
        if (String(track?.kind || "") === "audio") applyRemoteAudioEnabled(room, true).catch(() => undefined);
        refresh();
      });
      room.on(livekitClient.RoomEvent.TrackUnsubscribed, refresh);
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
          localAudioTrackCount: 0,
          remoteAudioTrackCount: 0,
          remoteAudioAvailable: false,
          localVideoTrack: null,
          remoteVideoTrack: null,
          disconnectReason: String(reason || "provider_disconnected")
        }));
      });

      await room.connect(join.livekit_url, join.token, { autoSubscribe: true });
      if (options.video) await room.localParticipant.setCameraEnabled(true);
      const localAudioTrackCount = await ensureMicrophonePublished(room);
      if (localAudioTrackCount <= 0) {
        const message = "Microphone connected, but PulseSoc could not verify published call audio.";
        await room.disconnect?.().catch(() => undefined);
        await stopRealtimeAudioSession(audioRuntimeRef.current).catch(() => undefined);
        audioRuntimeRef.current = null;
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
      await selectRealtimeSpeakerOutput(audioRuntimeRef.current, true).catch(() => undefined);
      await applyRemoteAudioEnabled(room, true).catch(() => undefined);
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
    const localAudioTrackCount = await setLocalMicrophoneEnabled(room, enabled);
    if (enabled && localAudioTrackCount <= 0) throw new Error("Microphone could not publish call audio.");
    setState((current) => ({ ...current, audioEnabled: enabled && localAudioTrackCount > 0, localAudioTrackCount, error: "", diagnosticCode: "" }));
  }, []);

  const setCameraEnabled = useCallback(async (enabled: boolean) => {
    const room = roomRef.current;
    if (!room) throw new Error("Call media is not connected.");
    await room.localParticipant.setCameraEnabled(enabled);
    const localAudioTrackCount = enabled ? await ensureMicrophonePublished(room) : countPublishedAudioTracks(room.localParticipant);
    if (localAudioTrackCount <= 0) throw new Error("Camera changed, but microphone audio is no longer published.");
    refreshMediaState(room);
    setState((current) => ({ ...current, videoEnabled: enabled, audioEnabled: true, localAudioTrackCount, error: "", diagnosticCode: "" }));
  }, [refreshMediaState]);

  const setSpeakerEnabled = useCallback(async (enabled: boolean) => {
    await selectRealtimeSpeakerOutput(audioRuntimeRef.current, enabled);
    setState((current) => ({ ...current, speakerEnabled: enabled, error: "" }));
  }, []);

  const showAudioRoutePicker = useCallback(async () => {
    await showRealtimeAudioRoutePicker(audioRuntimeRef.current);
  }, []);

  const switchCamera = useCallback(async () => {
    const localParticipant = roomRef.current?.localParticipant;
    const publications = Array.from(localParticipant?.videoTrackPublications?.values?.() || []) as any[];
    const publication = publications.find((item) => item?.track);
    if (!publication?.track?.switchCamera) throw new Error("Camera is not active.");
    await publication.track.switchCamera();
    const room = roomRef.current;
    const localAudioTrackCount = countPublishedAudioTracks(room?.localParticipant);
    if (localAudioTrackCount <= 0) throw new Error("Camera switched, but microphone audio is no longer published.");
    refreshMediaState(room);
  }, [refreshMediaState]);

  useEffect(() => () => {
    const room = roomRef.current;
    roomRef.current = null;
    room?.disconnect?.().catch(() => undefined);
    stopRealtimeAudioSession(audioRuntimeRef.current).catch(() => undefined);
    audioRuntimeRef.current = null;
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
