import { useCallback, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";
import { PulseCallJoin } from "../api/calls";

type NativeCallRoomState = {
  supported: boolean;
  connecting: boolean;
  connected: boolean;
  connectionState: string;
  error: string;
  participantCount: number;
  audioEnabled: boolean;
  videoEnabled: boolean;
};

const initialState: NativeCallRoomState = {
  supported: Platform.OS !== "web",
  connecting: false,
  connected: false,
  connectionState: "disconnected",
  error: "",
  participantCount: 0,
  audioEnabled: true,
  videoEnabled: false
};

let globalsRegistered = false;

export function useNativeCallRoom() {
  const roomRef = useRef<any>(null);
  const [state, setState] = useState<NativeCallRoomState>(initialState);

  const disconnect = useCallback(async () => {
    const room = roomRef.current;
    roomRef.current = null;
    if (room?.disconnect) {
      await room.disconnect().catch(() => undefined);
    }
    setState((current) => ({
      ...current,
      connecting: false,
      connected: false,
      connectionState: "disconnected",
      participantCount: 0
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

    setState((current) => ({ ...current, connecting: true, error: "" }));
    try {
      const livekitNative = await import("@livekit/react-native");
      const livekitClient = await import("livekit-client");
      if (!globalsRegistered) {
        livekitNative.registerGlobals({ autoConfigureAudioSession: true });
        globalsRegistered = true;
      }

      const room = new livekitClient.Room({
        adaptiveStream: true,
        dynacast: true
      });

      room.on(livekitClient.RoomEvent.ConnectionStateChanged, (connectionState: string) => {
        setState((current) => ({ ...current, connectionState, connected: connectionState === "connected", connecting: connectionState === "connecting" }));
      });
      room.on(livekitClient.RoomEvent.ParticipantConnected, () => {
        setState((current) => ({ ...current, participantCount: Math.max(current.participantCount, room.remoteParticipants.size + 1) }));
      });
      room.on(livekitClient.RoomEvent.ParticipantDisconnected, () => {
        setState((current) => ({ ...current, participantCount: Math.max(1, room.remoteParticipants.size + 1) }));
      });
      room.on(livekitClient.RoomEvent.Disconnected, () => {
        setState((current) => ({ ...current, connected: false, connecting: false, connectionState: "disconnected", participantCount: 0 }));
      });

      await room.connect(join.livekit_url, join.token, { autoSubscribe: true });
      await room.localParticipant.setMicrophoneEnabled(true).catch(() => undefined);
      if (options.video) await room.localParticipant.setCameraEnabled(true).catch(() => undefined);
      roomRef.current = room;
      setState((current) => ({
        ...current,
        connecting: false,
        connected: true,
        connectionState: "connected",
        participantCount: room.remoteParticipants.size + 1,
        audioEnabled: true,
        videoEnabled: Boolean(options.video)
      }));
      return true;
    } catch (error) {
      setState((current) => ({
        ...current,
        connecting: false,
        connected: false,
        connectionState: "failed",
        error: error instanceof Error ? error.message : "Native LiveKit call connection failed."
      }));
      await disconnect().catch(() => undefined);
      return false;
    }
  }, [disconnect]);

  const setMicrophoneEnabled = useCallback(async (enabled: boolean) => {
    const room = roomRef.current;
    await room?.localParticipant?.setMicrophoneEnabled?.(enabled).catch(() => undefined);
    setState((current) => ({ ...current, audioEnabled: enabled }));
  }, []);

  const setCameraEnabled = useCallback(async (enabled: boolean) => {
    const room = roomRef.current;
    await room?.localParticipant?.setCameraEnabled?.(enabled).catch(() => undefined);
    setState((current) => ({ ...current, videoEnabled: enabled }));
  }, []);

  const switchCamera = useCallback(async () => {
    const localParticipant = roomRef.current?.localParticipant;
    const publications = Array.from(localParticipant?.videoTrackPublications?.values?.() || []);
    const publication = publications[0] as { track?: { switchCamera?: () => Promise<void> } } | undefined;
    await publication?.track?.switchCamera?.().catch(() => undefined);
  }, []);

  useEffect(() => () => {
    roomRef.current?.disconnect?.().catch(() => undefined);
  }, []);

  return {
    ...state,
    connect,
    disconnect,
    setMicrophoneEnabled,
    setCameraEnabled,
    switchCamera
  };
}
