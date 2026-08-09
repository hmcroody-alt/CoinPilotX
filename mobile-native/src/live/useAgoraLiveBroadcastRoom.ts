import { useCallback, useEffect, useRef, useState } from "react";
import type { IRtcEngine, IRtcEngineEventHandler } from "react-native-agora";
import type { LiveKitCredentials } from "./liveSession";
import type { LiveParticipant } from "./useLiveBroadcastRoom";

const initial = {
  provider: "agora" as const, supported: true, connecting: false, connected: false, reconnecting: false,
  connectionState: "disconnected", connectionQuality: "unknown", error: "", canPublish: false,
  audioEnabled: false, videoEnabled: false, speakerEnabled: true, remoteAudioEnabled: true,
  localVideoTrack: null as any, localAudioTrackCount: 0, remoteAudioTrackCount: 0, remoteVideoTrackCount: 0,
  participants: [] as LiveParticipant[], reconnectCount: 0, disconnectReason: "", diagnosticCode: "",
  audioPath: "v1_legacy" as const, audioBusy: false, recovering: false, audioWarning: ""
};

export function useAgoraLiveBroadcastRoom() {
  const engineRef = useRef<IRtcEngine | null>(null);
  const handlerRef = useRef<IRtcEngineEventHandler | null>(null);
  const refreshRef = useRef<(() => Promise<LiveKitCredentials | null>) | null>(null);
  const credentialsRef = useRef<LiveKitCredentials | null>(null);
  const renewalRef = useRef<Promise<void> | null>(null);
  const [state, setState] = useState(initial);

  const renewToken = useCallback(() => {
    if (renewalRef.current || !refreshRef.current || !engineRef.current || !credentialsRef.current) return;
    const current = credentialsRef.current;
    renewalRef.current = refreshRef.current().then((next) => {
      if (!next || next.provider !== "agora" || !next.token || next.channelName !== current.channelName || next.uid !== current.uid) {
        throw new Error("mismatched renewal credentials");
      }
      const result = engineRef.current?.renewToken(next.token) ?? -1;
      if (result < 0) throw new Error("renewal rejected");
      credentialsRef.current = next;
      setState((s) => ({ ...s, error: "", diagnosticCode: "" }));
    }).catch(() => setState((s) => ({ ...s, error: "Secure Live access could not be refreshed.", diagnosticCode: "AGORA_LIVE_TOKEN_RENEWAL_FAILED" })))
      .finally(() => { renewalRef.current = null; });
  }, []);

  const disconnect = useCallback(async (reason = "local_disconnect") => {
    const engine = engineRef.current; engineRef.current = null;
    if (engine) { if (handlerRef.current) engine.unregisterEventHandler(handlerRef.current); engine.leaveChannel(); engine.release(); }
    handlerRef.current = null; credentialsRef.current = null; refreshRef.current = null;
    setState((s) => ({ ...initial, supported: s.supported, disconnectReason: reason, diagnosticCode: reason }));
  }, []);

  const connect = useCallback(async (credentials: LiveKitCredentials, options: { publish?: boolean; video?: boolean; refreshCredentials?: () => Promise<LiveKitCredentials | null> } = {}) => {
    if (credentials.provider !== "agora" || !credentials.token || !credentials.appId || !credentials.channelName || !credentials.uid) return false;
    if (options.publish && !credentials.canPublish) {
      setState((s) => ({ ...s, error: "PulseSoc did not authorize this account to publish.", diagnosticCode: "AGORA_LIVE_PUBLISH_FORBIDDEN" }));
      return false;
    }
    if (engineRef.current) await disconnect("replaced_room");
    credentialsRef.current = credentials; refreshRef.current = options.refreshCredentials || null;
    setState((s) => ({ ...initial, supported: s.supported, connecting: true, connectionState: "connecting", canPublish: credentials.canPublish }));
    try {
      const agora = await import("react-native-agora");
      const engine = agora.createAgoraRtcEngine(); engineRef.current = engine; engine.initialize({ appId: credentials.appId }); engine.enableAudio();
      const publish = Boolean(options.publish && credentials.canPublish);
      if (publish && options.video !== false) { engine.enableVideo(); engine.startPreview(); }
      const localTrack = publish && options.video !== false ? { provider: "agora", uid: 0, local: true } : null;
      const localParticipant: LiveParticipant = { identity: credentials.identity, name: credentials.participantName || "You", isLocal: true, isHost: credentials.role === "host", videoTrack: localTrack, audioTrack: publish ? { provider: "agora", uid: 0 } : null, hasVideo: Boolean(localTrack), hasAudio: publish, audioMuted: false, speaking: false };
      const handler: IRtcEngineEventHandler = {
        onJoinChannelSuccess: () => setState((s) => ({ ...s, connecting: false, connected: true, reconnecting: false, connectionState: "connected", audioEnabled: publish, videoEnabled: Boolean(localTrack), localVideoTrack: localTrack, localAudioTrackCount: publish ? 1 : 0, participants: publish ? [localParticipant] : [], error: "", diagnosticCode: "" })),
        onConnectionStateChanged: (_c, value) => {
          const reconnecting = value === agora.ConnectionStateType.ConnectionStateReconnecting;
          const connected = value === agora.ConnectionStateType.ConnectionStateConnected;
          const failed = value === agora.ConnectionStateType.ConnectionStateFailed;
          setState((s) => ({ ...s, connected, reconnecting, recovering: reconnecting, connecting: !connected && !reconnecting && !failed, connectionState: failed ? "failed" : reconnecting ? "reconnecting" : connected ? "connected" : "connecting", reconnectCount: reconnecting && !s.reconnecting ? s.reconnectCount + 1 : s.reconnectCount, diagnosticCode: failed ? "AGORA_LIVE_CONNECTION_FAILED" : s.diagnosticCode }));
        },
        onUserJoined: (_c, uid) => setState((s) => { const participant: LiveParticipant = { identity: `agora-${uid}`, name: "Live participant", isLocal: false, isHost: !publish && s.participants.length === 0, videoTrack: { provider: "agora", uid }, audioTrack: { provider: "agora", uid }, hasVideo: true, hasAudio: true, audioMuted: false, speaking: false }; return { ...s, participants: [...s.participants.filter(p => p.identity !== participant.identity), participant], remoteAudioTrackCount: s.remoteAudioTrackCount + 1, remoteVideoTrackCount: s.remoteVideoTrackCount + 1 }; }),
        onUserOffline: (_c, uid) => setState((s) => ({ ...s, participants: s.participants.filter(p => p.identity !== `agora-${uid}`), remoteAudioTrackCount: Math.max(0, s.remoteAudioTrackCount - 1), remoteVideoTrackCount: Math.max(0, s.remoteVideoTrackCount - 1) })),
        onTokenPrivilegeWillExpire: () => renewToken(), onRequestToken: () => renewToken(),
        onError: (code) => setState((s) => ({ ...s, error: `Agora Live media error (${code}).`, diagnosticCode: `AGORA_LIVE_${code}` }))
      };
      handlerRef.current = handler; engine.registerEventHandler(handler);
      const result = engine.joinChannel(credentials.token, credentials.channelName, credentials.uid, { clientRoleType: publish ? agora.ClientRoleType.ClientRoleBroadcaster : agora.ClientRoleType.ClientRoleAudience, channelProfile: agora.ChannelProfileType.ChannelProfileLiveBroadcasting, publishMicrophoneTrack: publish, publishCameraTrack: Boolean(localTrack), autoSubscribeAudio: true, autoSubscribeVideo: true });
      if (result < 0) throw new Error(`Agora rejected the Live join (${result}).`);
      return true;
    } catch (error) { await disconnect("connect_failed"); setState((s) => ({ ...s, connectionState: "failed", error: error instanceof Error ? error.message : "Agora Live connection failed.", diagnosticCode: "AGORA_LIVE_CONNECT_FAILED" })); return false; }
  }, [disconnect, renewToken]);

  const engine = () => { if (!engineRef.current) throw new Error("Live media is not connected."); return engineRef.current; };
  const setMicrophoneEnabled = useCallback(async (enabled: boolean) => { engine().muteLocalAudioStream(!enabled); setState(s => ({...s,audioEnabled:enabled})); }, []);
  const setCameraEnabled = useCallback(async (enabled: boolean) => { engine().muteLocalVideoStream(!enabled); setState(s => ({...s,videoEnabled:enabled})); }, []);
  const setSpeakerEnabled = useCallback(async (enabled: boolean) => { engine().setEnableSpeakerphone(enabled); setState(s => ({...s,speakerEnabled:enabled})); }, []);
  const setRemoteAudioEnabled = useCallback(async (enabled: boolean) => { engine().muteAllRemoteAudioStreams(!enabled); setState(s => ({...s,remoteAudioEnabled:enabled})); }, []);
  const switchCamera = useCallback(async () => { engine().switchCamera(); }, []);
  const showAudioRoutePicker = useCallback(async () => { throw new Error("Use the iOS system audio-route control for Agora Live."); }, []);
  useEffect(() => () => { disconnect("unmounted").catch(() => undefined); }, [disconnect]);
  return { ...state, lifecycle: null, connect, disconnect, startBroadcast: connect, stopBroadcast: disconnect, joinAsViewer: connect, leaveViewer: disconnect, setMicrophoneEnabled, setCameraEnabled, setSpeakerEnabled, setRemoteAudioEnabled, showAudioRoutePicker, recheckAudio: async () => undefined, switchCamera, getLastConnectError: () => state.error, getAudioTrace: () => [] };
}
