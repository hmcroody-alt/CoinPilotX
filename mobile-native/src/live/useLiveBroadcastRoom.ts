import { useCallback, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";
import type { LiveKitCredentials } from "./liveSession";

/**
 * LiveKit room hook for native live broadcasting. Unlike the 1:1 call hook this
 * tracks ALL participants as an array (host + co-host guests + the local
 * publisher) so the Reels/host UI can render a real multi-guest stage. It reuses
 * the same dynamic-import + registerGlobals pattern as `useNativeCallRoom` so no
 * native rebuild is required — the LiveKit pods are already in the binary.
 */

export type LiveParticipant = {
  identity: string;
  name: string;
  isLocal: boolean;
  isHost: boolean;
  videoTrack: any | null;
  hasVideo: boolean;
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
  localVideoTrack: any | null;
  participants: LiveParticipant[];
  reconnectCount: number;
  disconnectReason: string;
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
  localVideoTrack: null,
  participants: [],
  reconnectCount: 0,
  disconnectReason: ""
};

let globalsRegistered = false;

function readableError(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function firstVideoTrack(participant: any): any | null {
  const publications = Array.from(participant?.videoTrackPublications?.values?.() || []) as any[];
  return publications.find((publication) => publication?.track && publication?.isSubscribed !== false)?.track || null;
}

function isAudioMuted(participant: any): boolean {
  const publications = Array.from(participant?.audioTrackPublications?.values?.() || []) as any[];
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

export function useLiveBroadcastRoom() {
  const roomRef = useRef<any>(null);
  const audioSessionRef = useRef<any>(null);
  const activeSpeakersRef = useRef<Set<string>>(new Set());
  const [state, setState] = useState<LiveBroadcastState>(initialState);

  const refreshParticipants = useCallback((room = roomRef.current) => {
    if (!room) return;
    const speaking = activeSpeakersRef.current;
    const local = room.localParticipant;
    const localVideoTrack = firstVideoTrack(local);
    const participants: LiveParticipant[] = [];
    if (local) {
      participants.push({
        identity: String(local.identity || "local"),
        name: participantName(local),
        isLocal: true,
        isHost: readRole(local) === "host" || Boolean(local.permissions?.canPublish),
        videoTrack: localVideoTrack,
        hasVideo: Boolean(localVideoTrack),
        audioMuted: local.isMicrophoneEnabled === false,
        speaking: speaking.has(String(local.identity || "local"))
      });
    }
    for (const remote of Array.from(room.remoteParticipants?.values?.() || []) as any[]) {
      const videoTrack = firstVideoTrack(remote);
      const role = readRole(remote);
      participants.push({
        identity: String(remote.identity || ""),
        name: participantName(remote),
        isLocal: false,
        isHost: role === "host",
        videoTrack,
        hasVideo: Boolean(videoTrack),
        audioMuted: isAudioMuted(remote),
        speaking: speaking.has(String(remote.identity || ""))
      });
    }
    setState((current) => ({ ...current, localVideoTrack, participants }));
  }, []);

  const disconnect = useCallback(async (reason = "local_disconnect") => {
    const room = roomRef.current;
    roomRef.current = null;
    activeSpeakersRef.current = new Set();
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
      localVideoTrack: null,
      participants: [],
      disconnectReason: reason
    }));
  }, []);

  const connect = useCallback(
    async (credentials: LiveKitCredentials, options: { publish?: boolean } = {}) => {
      if (Platform.OS === "web") {
        setState((current) => ({ ...current, supported: false, error: "Native LiveKit broadcasting requires an installed iOS or Android build." }));
        return false;
      }
      if (!credentials.token || !credentials.url) {
        setState((current) => ({ ...current, error: "PulseSoc did not return a usable LiveKit token for this broadcast." }));
        return false;
      }
      const publish = Boolean(options.publish && credentials.canPublish);

      if (roomRef.current) await disconnect("replaced_room");
      setState((current) => ({ ...initialState, supported: current.supported, connecting: true, connectionState: "connecting", canPublish: credentials.canPublish }));
      try {
        const livekitNative = await import("@livekit/react-native");
        const livekitClient = await import("livekit-client");
        if (!globalsRegistered) {
          livekitNative.registerGlobals({ autoConfigureAudioSession: false });
          globalsRegistered = true;
        }
        audioSessionRef.current = livekitNative.AudioSession;
        // ROOT-CAUSE FIX (viewers could not hear the host): registerGlobals ran
        // with autoConfigureAudioSession:false, so LiveKit never sets the iOS
        // AVAudioSession category itself. Without an explicit record-capable
        // category the session stays playback-only, so the published mic track
        // captures silence — while video is unaffected because the camera is
        // independent of the audio session. Put the session into
        // playAndRecord/videoChat BEFORE starting it so the mic actually records.
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
          setState((current) => ({ ...current, connected: true, reconnecting: false, connectionState: "connected", error: "" }));
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
        room.on(livekitClient.RoomEvent.TrackSubscribed, refresh);
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
            localVideoTrack: null,
            participants: [],
            disconnectReason: String(reason || "provider_disconnected")
          }));
        });

        await room.connect(credentials.url, credentials.token, { autoSubscribe: true });
        if (publish) {
          await room.localParticipant.setMicrophoneEnabled(true);
          await room.localParticipant.setCameraEnabled(true);
        }
        await livekitNative.AudioSession.selectAudioOutput(Platform.OS === "ios" ? "force_speaker" : "speaker").catch(() => undefined);
        refresh();
        setState((current) => ({
          ...current,
          connecting: false,
          connected: true,
          reconnecting: false,
          connectionState: "connected",
          canPublish: credentials.canPublish,
          audioEnabled: publish,
          videoEnabled: publish,
          speakerEnabled: true,
          error: ""
        }));
        return true;
      } catch (error) {
        const message = readableError(error, "Native LiveKit broadcast connection failed.");
        await disconnect("connect_failed").catch(() => undefined);
        setState((current) => ({ ...current, connectionState: "failed", error: message, disconnectReason: "connect_failed" }));
        return false;
      }
    },
    [disconnect, refreshParticipants]
  );

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
    await room.localParticipant.setCameraEnabled(enabled);
    refreshParticipants(room);
    setState((current) => ({ ...current, videoEnabled: enabled, error: "" }));
  }, [refreshParticipants]);

  const setSpeakerEnabled = useCallback(async (enabled: boolean) => {
    const audioSession = audioSessionRef.current;
    if (!audioSession) throw new Error("Broadcast audio session is not available.");
    const output = Platform.OS === "ios" ? (enabled ? "force_speaker" : "default") : enabled ? "speaker" : "earpiece";
    await audioSession.selectAudioOutput(output);
    setState((current) => ({ ...current, speakerEnabled: enabled, error: "" }));
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
  }, []);

  useEffect(
    () => () => {
      const room = roomRef.current;
      roomRef.current = null;
      room?.disconnect?.().catch(() => undefined);
      audioSessionRef.current?.stopAudioSession?.().catch(() => undefined);
    },
    []
  );

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
