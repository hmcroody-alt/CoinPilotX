import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, Platform } from "react-native";
import {
  applyRemoteAudioEnabled,
  audioPublications,
  countPublishedAudioTracks,
  countSubscribedRemoteAudioTracks,
  ensureMicrophonePublished,
  publicationHasSubscribedTrack,
  realtimeRoomOptions,
  resolveRealtimeAudioConfiguration,
  restoreRealtimeRoomAudio,
  resumeRealtimeAudioSession,
  selectRealtimeSpeakerOutput,
  setLocalMicrophoneEnabled,
  showRealtimeAudioRoutePicker,
  startRealtimeAudioSession,
  stopRealtimeAudioSession,
  type AppleRealtimeAudioConfiguration,
  type RealtimeAudioRuntime
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

export type AppleAudioConfiguration = AppleRealtimeAudioConfiguration;

/**
 * Pick the iOS AVAudioSession profile for a Live participant.
 *
 * The publisher (host / co-host) and a listen-only viewer need DIFFERENT
 * categories, and getting this wrong is the production "viewers can't hear the
 * host" bug:
 *
 * - A publisher must capture the microphone, so it needs `playAndRecord` plus a
 *   communication `videoChat` mode.
 * - A viewer only PLAYS the subscribed host audio and, crucially, may never have
 *   granted microphone permission. Activating a `playAndRecord` session without a
 *   mic grant can fail to activate the session at all — subscribed remote audio
 *   then has no active output route (silent host) even though video, which is
 *   independent of the audio session, keeps rendering. Listen-only viewers must
 *   use the `playback` category so host audio plays at full media volume with no
 *   microphone dependency.
 *
 * Exported so the regression suite can assert the mapping without booting the
 * native LiveKit stack.
 */
export function resolveLiveAudioConfiguration(publish: boolean): AppleAudioConfiguration {
  return resolveRealtimeAudioConfiguration(publish ? "interactive" : "listener");
}

function readableError(error: unknown, fallback: string) {
  return error instanceof Error && error.message ? error.message : fallback;
}

function firstVideoTrack(participant: any): any | null {
  const publications = Array.from(participant?.videoTrackPublications?.values?.() || []) as any[];
  return publications.find((publication) => publication?.track && publication?.isSubscribed !== false)?.track || null;
}

/**
 * Drive the viewer's remote-audio on/off preference onto EVERY currently
 * subscribed remote audio track.
 *
 * The production bug this closes: muting host audio only toggled the tracks that
 * happened to be subscribed at that instant. A track that arrives LATER — the
 * host republishing after a mic toggle, a co-host joining, or every remote track
 * being re-subscribed after a LiveKit reconnect — starts enabled, so a viewer who
 * turned the host's sound off would suddenly hear it again. Callers persist the
 * desired state in a ref and re-invoke this on TrackSubscribed and Reconnected so
 * the preference is authoritative for the whole session, not just one moment.
 *
 * Exported so the regression suite can assert the toggling against a fake room
 * without the native LiveKit stack. Returns how many tracks were actually driven.
 */
export { applyRemoteAudioEnabled };

function videoPublications(participant: any): any[] {
  return Array.from(participant?.videoTrackPublications?.values?.() || []) as any[];
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

export function useLiveBroadcastRoom() {
  const roomRef = useRef<any>(null);
  const audioRuntimeRef = useRef<RealtimeAudioRuntime | null>(null);
  const activeSpeakersRef = useRef<Set<string>>(new Set());
  // Desired viewer remote-audio state; reapplied to tracks that subscribe after
  // the user toggled sound off (co-host join, host republish, reconnect).
  const remoteAudioEnabledRef = useRef(true);
  const microphoneEnabledRef = useRef(true);
  const publishRef = useRef(false);
  const [state, setState] = useState<LiveBroadcastState>(initialState);

  const refreshParticipants = useCallback((room = roomRef.current) => {
    if (!room) return;
    const speaking = activeSpeakersRef.current;
    const local = room.localParticipant;
    const localVideoTrack = firstVideoTrack(local);
    const localAudioTrack = firstAudioTrack(local);
    const localAudioTrackCount = countPublishedAudioTracks(local);
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
      remoteAudioTrackCount += countPublishedAudioTracks(remote);
      remoteVideoTrackCount += videoPublications(remote).filter(publicationHasSubscribedTrack).length;
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
    microphoneEnabledRef.current = true;
    publishRef.current = false;
    if (room?.disconnect) await room.disconnect().catch(() => undefined);
    await stopRealtimeAudioSession(audioRuntimeRef.current).catch(() => undefined);
    audioRuntimeRef.current = null;
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
      microphoneEnabledRef.current = publish;
      publishRef.current = publish;
      setState((current) => ({ ...initialState, supported: current.supported, connecting: true, connectionState: "connecting", canPublish: credentials.canPublish }));
      try {
        const livekitNative = await import("@livekit/react-native");
        const livekitClient = await import("livekit-client");
        audioRuntimeRef.current = await startRealtimeAudioSession(
          livekitNative,
          publish ? "interactive" : "listener"
        );
        const appleAudioConfiguration = audioRuntimeRef.current.configuration;

        const room = new livekitClient.Room(realtimeRoomOptions());
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
          const runtime = audioRuntimeRef.current;
          const restoreSession = runtime
            ? resumeRealtimeAudioSession(runtime.session, runtime.configuration)
            : Promise.resolve();
          restoreSession
            .then(async () => {
              const { localAudioTrackCount } = await restoreRealtimeRoomAudio(room, {
                publishMicrophone: publishRef.current,
                microphoneEnabled: microphoneEnabledRef.current,
                remoteAudioEnabled: remoteAudioEnabledRef.current
              });
              if (publishRef.current && microphoneEnabledRef.current && localAudioTrackCount <= 0) {
                throw new Error("Live microphone did not republish after reconnect.");
              }
              refresh();
            })
            .catch((restoreError) => {
              setState((current) => ({
                ...current,
                audioEnabled: false,
                error: readableError(restoreError, "Live audio could not recover after reconnect."),
                diagnosticCode: "LIVE_AUDIO_RECONNECT_FAILED"
              }));
            });
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
          // A newly subscribed audio track starts enabled; if the viewer has
          // muted remote audio, silence this one too before the UI updates.
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
        let publishedAudioCount = 0;
        if (publish) {
          await room.localParticipant.setCameraEnabled(true);
          publishedAudioCount = await ensureMicrophonePublished(room);
        }
        await selectRealtimeSpeakerOutput(audioRuntimeRef.current, true).catch(() => undefined);
        await applyRemoteAudioEnabled(room, remoteAudioEnabledRef.current).catch(() => undefined);
        refresh();
        if (publish && publishedAudioCount <= 0) {
          const message = "Microphone connected, but PulseSoc could not verify a published audio track.";
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
        const remoteAudioAtConnect = countSubscribedRemoteAudioTracks(room);
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
    microphoneEnabledRef.current = enabled;
    const localAudioTrackCount = await setLocalMicrophoneEnabled(room, enabled);
    if (enabled && localAudioTrackCount <= 0) throw new Error("Microphone could not publish Live audio.");
    refreshParticipants(room);
    setState((current) => ({
      ...current,
      audioEnabled: enabled && localAudioTrackCount > 0,
      localAudioTrackCount,
      error: "",
      diagnosticCode: ""
    }));
  }, [refreshParticipants]);

  const setCameraEnabled = useCallback(async (enabled: boolean) => {
    const room = roomRef.current;
    if (!room) throw new Error("Broadcast media is not connected.");
    await room.localParticipant.setCameraEnabled(enabled);
    const localAudioTrackCount =
      publishRef.current && microphoneEnabledRef.current
        ? await ensureMicrophonePublished(room)
        : countPublishedAudioTracks(room.localParticipant);
    if (publishRef.current && microphoneEnabledRef.current && localAudioTrackCount <= 0) {
      throw new Error("Camera changed, but microphone audio is no longer published.");
    }
    refreshParticipants(room);
    setState((current) => ({ ...current, videoEnabled: enabled, localAudioTrackCount, error: "" }));
  }, [refreshParticipants]);

  const setSpeakerEnabled = useCallback(async (enabled: boolean) => {
    await selectRealtimeSpeakerOutput(audioRuntimeRef.current, enabled);
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
    await showRealtimeAudioRoutePicker(audioRuntimeRef.current);
  }, []);

  const switchCamera = useCallback(async () => {
    const localParticipant = roomRef.current?.localParticipant;
    const publications = Array.from(localParticipant?.videoTrackPublications?.values?.() || []) as any[];
    const publication = publications.find((item) => item?.track);
    if (!publication?.track?.switchCamera) throw new Error("Camera is not active.");
    await publication.track.switchCamera();
    const room = roomRef.current;
    const localAudioTrackCount =
      publishRef.current && microphoneEnabledRef.current
        ? await ensureMicrophonePublished(room)
        : countPublishedAudioTracks(room?.localParticipant);
    if (publishRef.current && microphoneEnabledRef.current && localAudioTrackCount <= 0) {
      throw new Error("Camera switched, but microphone audio is no longer published.");
    }
    refreshParticipants(room);
  }, [refreshParticipants]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (nextState) => {
      if (nextState !== "active" || !roomRef.current || !audioRuntimeRef.current) return;
      const room = roomRef.current;
      const runtime = audioRuntimeRef.current;
      resumeRealtimeAudioSession(runtime.session, runtime.configuration)
        .then(async () => {
          const { localAudioTrackCount } = await restoreRealtimeRoomAudio(room, {
            publishMicrophone: publishRef.current,
            microphoneEnabled: microphoneEnabledRef.current,
            remoteAudioEnabled: remoteAudioEnabledRef.current
          });
          if (publishRef.current && microphoneEnabledRef.current && localAudioTrackCount <= 0) {
            throw new Error("Live microphone did not recover after interruption.");
          }
          refreshParticipants(room);
        })
        .catch((restoreError) => {
          setState((current) => ({
            ...current,
            audioEnabled: false,
            error: readableError(restoreError, "Live audio could not recover after interruption."),
            diagnosticCode: "LIVE_AUDIO_INTERRUPTION_RECOVERY_FAILED"
          }));
        });
    });
    return () => subscription.remove();
  }, [refreshParticipants]);

  useEffect(
    () => () => {
      const room = roomRef.current;
      roomRef.current = null;
      room?.disconnect?.().catch(() => undefined);
      stopRealtimeAudioSession(audioRuntimeRef.current).catch(() => undefined);
      audioRuntimeRef.current = null;
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
