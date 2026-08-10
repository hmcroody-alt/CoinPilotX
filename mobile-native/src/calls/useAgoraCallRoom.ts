import { useCallback, useEffect, useRef, useState } from "react";
import { Platform } from "react-native";
import type { IRtcEngine, IRtcEngineEventHandler } from "react-native-agora";
import type { PulseCallJoin } from "../api/calls";
import { reportPresenceActivity } from "../api/presenceSession";

const baseState = {
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
  localVideoTrack: null as any,
  remoteVideoTrack: null as any,
  localUid: 0,
  remoteUid: 0,
  remoteUids: [] as number[],
  reconnectCount: 0,
  disconnectReason: "",
  provider: "agora" as const
};

export function addAgoraRemoteUid(remoteUids: number[], remoteUid: number) {
  return remoteUids.includes(remoteUid) ? remoteUids : [...remoteUids, remoteUid];
}

export function removeAgoraRemoteUid(remoteUids: number[], remoteUid: number) {
  return remoteUids.filter((uid) => uid !== remoteUid);
}

/** Agora media adapter. PulseSoc backend signaling remains authoritative. */
export function useAgoraCallRoom() {
  const engineRef = useRef<IRtcEngine | null>(null);
  const handlerRef = useRef<IRtcEngineEventHandler | null>(null);
  const [state, setState] = useState(baseState);

  const disconnect = useCallback(async (reason = "local_disconnect") => {
    const engine = engineRef.current;
    engineRef.current = null;
    if (engine) {
      if (handlerRef.current) engine.unregisterEventHandler(handlerRef.current);
      engine.leaveChannel();
      engine.release();
    }
    handlerRef.current = null;
    reportPresenceActivity("idle", "").catch(() => undefined);
    setState((current) => ({ ...baseState, supported: current.supported, disconnectReason: reason, diagnosticCode: reason }));
  }, []);

  const connect = useCallback(async (join: PulseCallJoin, options: { video?: boolean; refreshCredentials?: () => Promise<PulseCallJoin> } = {}) => {
    if (Platform.OS === "web") return false;
    if (!join.token || !join.app_id || !join.channel_name || !join.uid) {
      setState((current) => ({ ...current, error: "PulseSoc did not return usable Agora credentials.", diagnosticCode: "AGORA_CREDENTIALS_MISSING" }));
      return false;
    }
    if (engineRef.current) await disconnect("replaced_room");
    setState((current) => ({ ...baseState, supported: current.supported, connecting: true, connectionState: "connecting", localUid: Number(join.uid) }));
    try {
      const agora = await import("react-native-agora");
      const engine = agora.createAgoraRtcEngine();
      engineRef.current = engine;
      engine.initialize({ appId: join.app_id });
      engine.enableAudio();
      if (options.video) {
        engine.enableVideo();
        engine.startPreview();
      }
      let renewalPromise: Promise<void> | null = null;
      const renew = () => {
        if (renewalPromise || !options.refreshCredentials) return renewalPromise;
        renewalPromise = options.refreshCredentials().then((next) => {
          if (next.provider !== "agora" || !next.token || next.channel_name !== join.channel_name || Number(next.uid) !== Number(join.uid)) {
            throw new Error("PulseSoc returned mismatched Agora renewal credentials.");
          }
          const renewed = engine.renewToken(next.token);
          if (renewed < 0) throw new Error(`Agora rejected the renewed token (${renewed}).`);
          setState((current) => ({ ...current, error: "", diagnosticCode: "" }));
        }).catch(() => {
          setState((current) => ({ ...current, error: "Secure call access could not be refreshed. Reconnect before the token expires.", diagnosticCode: "AGORA_TOKEN_RENEWAL_FAILED" }));
        }).finally(() => { renewalPromise = null; });
        return renewalPromise;
      };
      const handler: IRtcEngineEventHandler = {
        onJoinChannelSuccess: (_connection, _elapsed) => setState((current) => ({
          ...current, connecting: false, connected: true, reconnecting: false, connectionState: "connected",
          participantCount: Math.max(1, current.participantCount), localAudioTrackCount: 1, audioEnabled: true,
          videoEnabled: Boolean(options.video), error: "", diagnosticCode: ""
        })),
        onConnectionStateChanged: (_connection, connectionState) => {
          const reconnecting = connectionState === agora.ConnectionStateType.ConnectionStateReconnecting;
          const connected = connectionState === agora.ConnectionStateType.ConnectionStateConnected;
          const failed = connectionState === agora.ConnectionStateType.ConnectionStateFailed;
          setState((current) => ({ ...current, connected, reconnecting, connecting: !connected && !reconnecting && !failed,
            connectionState: failed ? "failed" : reconnecting ? "reconnecting" : connected ? "connected" : "connecting",
            reconnectCount: reconnecting && !current.reconnecting ? current.reconnectCount + 1 : current.reconnectCount,
            diagnosticCode: failed ? "AGORA_CONNECTION_FAILED" : current.diagnosticCode }));
        },
        onUserJoined: (_connection, remoteUid) => setState((current) => {
          const remoteUids = addAgoraRemoteUid(current.remoteUids, remoteUid);
          return {
            ...current,
            remoteUid: remoteUids[0] || 0,
            remoteUids,
            participantCount: 1 + remoteUids.length,
            remoteAudioTrackCount: remoteUids.length,
            remoteAudioAvailable: remoteUids.length > 0
          };
        }),
        onUserOffline: (_connection, remoteUid) => setState((current) => {
          const remoteUids = removeAgoraRemoteUid(current.remoteUids, remoteUid);
          return {
            ...current,
            remoteUid: remoteUids[0] || 0,
            remoteUids,
            participantCount: 1 + remoteUids.length,
            remoteAudioTrackCount: remoteUids.length,
            remoteAudioAvailable: remoteUids.length > 0
          };
        }),
        onTokenPrivilegeWillExpire: () => { setState((current) => ({ ...current, diagnosticCode: "AGORA_TOKEN_RENEWAL_REQUIRED", error: "Refreshing secure call access…" })); void renew(); },
        onRequestToken: () => { setState((current) => ({ ...current, diagnosticCode: "AGORA_TOKEN_EXPIRED", error: "Restoring secure call access…" })); void renew(); },
        onError: (errorCode) => setState((current) => ({ ...current, error: `Agora media error (${errorCode}).`, diagnosticCode: `AGORA_${errorCode}` }))
      };
      handlerRef.current = handler;
      engine.registerEventHandler(handler);
      const result = engine.joinChannel(join.token, join.channel_name, Number(join.uid), {
        clientRoleType: agora.ClientRoleType.ClientRoleBroadcaster,
        channelProfile: agora.ChannelProfileType.ChannelProfileCommunication,
        publishMicrophoneTrack: true,
        publishCameraTrack: Boolean(options.video),
        autoSubscribeAudio: true,
        autoSubscribeVideo: true
      });
      if (result < 0) throw new Error(`Agora rejected the join request (${result}).`);
      reportPresenceActivity(options.video ? "in_video_call" : "in_audio_call", join.channel_name).catch(() => undefined);
      return true;
    } catch (error) {
      await disconnect("connect_failed");
      setState((current) => ({ ...current, connectionState: "failed", error: error instanceof Error ? error.message : "Agora call connection failed.", diagnosticCode: "AGORA_CONNECT_FAILED" }));
      return false;
    }
  }, [disconnect]);

  const requireEngine = () => {
    if (!engineRef.current) throw new Error("Call media is not connected.");
    return engineRef.current;
  };
  const setMicrophoneEnabled = useCallback(async (enabled: boolean) => { requireEngine().muteLocalAudioStream(!enabled); setState((s) => ({ ...s, audioEnabled: enabled })); }, []);
  const setCameraEnabled = useCallback(async (enabled: boolean) => { requireEngine().muteLocalVideoStream(!enabled); setState((s) => ({ ...s, videoEnabled: enabled })); }, []);
  const setSpeakerEnabled = useCallback(async (enabled: boolean) => { requireEngine().setEnableSpeakerphone(enabled); setState((s) => ({ ...s, speakerEnabled: enabled })); }, []);
  const showAudioRoutePicker = useCallback(async () => { throw new Error("Use the iOS system audio-route control for Agora calls."); }, []);
  const switchCamera = useCallback(async () => { requireEngine().switchCamera(); }, []);

  useEffect(() => () => { disconnect("unmounted").catch(() => undefined); }, [disconnect]);
  return { ...state, lifecycle: null, connect, disconnect, setMicrophoneEnabled, setCameraEnabled, setSpeakerEnabled, showAudioRoutePicker, switchCamera };
}
