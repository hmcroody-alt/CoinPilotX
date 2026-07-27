import { useCallback, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";
import { PulseCallJoin } from "../api/calls";
import { reportPresenceActivity } from "../api/presenceSession";

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

function readableError(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function audioPublications(participant: any): any[] {
  return Array.from(participant?.audioTrackPublications?.values?.() || []) as any[];
}

function videoPublications(participant: any): any[] {
  return Array.from(participant?.videoTrackPublications?.values?.() || []) as any[];
}

function publicationHasTrack(publication: any): boolean {
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

export async function applyCallRemoteAudioEnabled(room: any, enabled: boolean): Promise<number> {
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

export async function ensureCallMicrophonePublished(room: any): Promise<number> {
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

export function useNativeCallRoom() {
  const roomRef = useRef<any>(null);
  const audioSessionRef = useRef<any>(null);
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
        await livekitNative.AudioSession.setAppleAudioConfiguration({
          audioCategory: "playAndRecord",
          audioMode: "videoChat",
          audioCategoryOptions: ["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay", "defaultToSpeaker"]
        }).catch(() => undefined);
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
        ensureCallMicrophonePublished(room)
          .then((count) => {
            if (count <= 0) {
              setState((current) => ({
                ...current,
                audioEnabled: false,
                localAudioTrackCount: 0,
                error: "Microphone reconnected, but PulseSoc could not verify published call audio.",
                diagnosticCode: "CALL_LOCAL_AUDIO_REPUBLISH_FAILED"
              }));
            }
            return applyCallRemoteAudioEnabled(room, true);
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
        if (String(track?.kind || "") === "audio") applyCallRemoteAudioEnabled(room, true).catch(() => undefined);
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
      const localAudioTrackCount = await ensureCallMicrophonePublished(room);
      if (localAudioTrackCount <= 0) {
        const message = "Microphone connected, but PulseSoc could not verify published call audio.";
        await room.disconnect?.().catch(() => undefined);
        await livekitNative.AudioSession.stopAudioSession?.().catch(() => undefined);
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
      await livekitNative.AudioSession.selectAudioOutput(Platform.OS === "ios" ? "force_speaker" : "speaker").catch(() => undefined);
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
    await room.localParticipant.setMicrophoneEnabled(enabled);
    const localAudioTrackCount = countPublishedAudioTracks(room.localParticipant);
    if (enabled && localAudioTrackCount <= 0) throw new Error("Microphone could not publish call audio.");
    setState((current) => ({ ...current, audioEnabled: enabled && localAudioTrackCount > 0, localAudioTrackCount, error: "", diagnosticCode: "" }));
  }, []);

  const setCameraEnabled = useCallback(async (enabled: boolean) => {
    const room = roomRef.current;
    if (!room) throw new Error("Call media is not connected.");
    await room.localParticipant.setCameraEnabled(enabled);
    const localAudioTrackCount = enabled ? await ensureCallMicrophonePublished(room) : countPublishedAudioTracks(room.localParticipant);
    if (localAudioTrackCount <= 0) throw new Error("Camera changed, but microphone audio is no longer published.");
    refreshMediaState(room);
    setState((current) => ({ ...current, videoEnabled: enabled, audioEnabled: true, localAudioTrackCount, error: "", diagnosticCode: "" }));
  }, [refreshMediaState]);

  const setSpeakerEnabled = useCallback(async (enabled: boolean) => {
    const audioSession = audioSessionRef.current;
    if (!audioSession) throw new Error("Call audio session is not available.");
    const output = Platform.OS === "ios" ? (enabled ? "force_speaker" : "default") : (enabled ? "speaker" : "earpiece");
    await audioSession.selectAudioOutput(output);
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
    const localAudioTrackCount = countPublishedAudioTracks(room?.localParticipant);
    if (localAudioTrackCount <= 0) throw new Error("Camera switched, but microphone audio is no longer published.");
    refreshMediaState(room);
  }, [refreshMediaState]);

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
