import { useCallback, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";
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
  diagnosticCode: ""
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

export function useLiveBroadcastRoom() {
  const roomRef = useRef<any>(null);
  const audioSessionRef = useRef<any>(null);
  const audioOwnerIdRef = useRef("");
  const activeSpeakersRef = useRef<Set<string>>(new Set());
  // Desired viewer remote-audio state; reapplied to tracks that subscribe after
  // the user toggled sound off (co-host join, host republish, reconnect).
  const remoteAudioEnabledRef = useRef(true);
  const [state, setState] = useState<LiveBroadcastState>(initialState);

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
    roomRef.current = null;
    activeSpeakersRef.current = new Set();
    remoteAudioEnabledRef.current = true;
    if (room?.disconnect) await room.disconnect().catch(() => undefined);
    await releaseRealtimeAudioSession(audioSessionRef.current, audioOwnerIdRef.current || reason).catch(() => undefined);
    audioOwnerIdRef.current = "";
    setState((current) => ({
      ...current,
      connecting: false,
      connected: false,
      reconnecting: false,
      connectionState: "disconnected",
      localVideoTrack: null,
      localAudioTrackCount: 0,
      remoteAudioTrackCount: 0,
      remoteVideoTrackCount: 0,
      participants: [],
      disconnectReason: reason,
      diagnosticCode: reason
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
      remoteAudioEnabledRef.current = true;
      setState((current) => ({ ...initialState, supported: current.supported, connecting: true, connectionState: "connecting", canPublish: credentials.canPublish }));
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
        await activateRealtimeAudioSession(livekitNative.AudioSession, mediaMode, audioOwnerIdRef.current, {
          speaker: true
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
          setState((current) => ({ ...current, connected: true, reconnecting: false, connectionState: "connected", error: "" }));
          const audioTasks = [applyRemoteAudioEnabled(room, remoteAudioEnabledRef.current)];
          if (publish) audioTasks.push(ensureLiveMicrophonePublished(room));
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
          await ensureLiveMicrophonePublished(room);
          await room.localParticipant.setCameraEnabled(true, PULSE_LIVE_VIDEO_CAPTURE_OPTIONS, PULSE_LIVE_VIDEO_PUBLISH_OPTIONS);
          await ensureLiveMicrophonePublished(room);
          await new Promise((resolve) => setTimeout(resolve, 150));
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
          remoteAudioEnabled: true,
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
    await room.localParticipant.setCameraEnabled(enabled, PULSE_LIVE_VIDEO_CAPTURE_OPTIONS, PULSE_LIVE_VIDEO_PUBLISH_OPTIONS);
    const localAudioTrackCount = await ensureLiveMicrophonePublished(room);
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
    const localAudioTrackCount = await ensureLiveMicrophonePublished(room);
    if (localAudioTrackCount <= 0) throw new Error("Camera switched, but microphone audio is no longer published.");
    refreshParticipants(room);
    setState((current) => ({ ...current, audioEnabled: true, localAudioTrackCount, error: "", diagnosticCode: "" }));
  }, [refreshParticipants]);

  useEffect(
    () => () => {
      const room = roomRef.current;
      const ownerId = audioOwnerIdRef.current;
      roomRef.current = null;
      audioOwnerIdRef.current = "";
      room?.disconnect?.().catch(() => undefined);
      releaseRealtimeAudioSession(audioSessionRef.current, ownerId).catch(() => undefined);
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
    setRemoteAudioEnabled,
    showAudioRoutePicker,
    switchCamera
  };
}
