import { useCallback, useEffect, useRef, useState } from "react";
import type { IRtcEngine, IRtcEngineEventHandler } from "react-native-agora";
import type { LiveRtcCredentials } from "./liveSession";
import type { LiveParticipant } from "./useLiveBroadcastRoom";
import { emitAgoraLiveEvent } from "./agoraLiveTelemetry";
import {
  DEFAULT_LIVE_MUSIC_MIXING_STATE,
  clampLiveMixLevel,
  liveMixLevelToAgoraVolume,
  normalizeLiveMusicTrack,
  withLiveMixer,
  type LiveMusicMixingTrack
} from "./liveMusicMixing";
import {
  IDLE_LIVE_DUCK_STATE,
  liveMicPublishVolume,
  liveMusicPublishVolume,
  liveMusicStartPositionMs,
  stepLiveDuck,
  type LiveDuckState
} from "./liveCreatorMusic";
import {
  DEFAULT_CREATOR_MIXER_SETTINGS,
  normalizeCreatorMixerSettings,
  withMicLevel,
  withMusicLevel,
  type CreatorMixerSettings
} from "../audio/creatorMixer";

const initial = {
  provider: "agora" as const, supported: true, connecting: false, connected: false, reconnecting: false,
  connectionState: "disconnected", connectionQuality: "unknown", error: "", canPublish: false,
  audioEnabled: false, videoEnabled: false, speakerEnabled: true, remoteAudioEnabled: true,
  localVideoTrack: null as any, localVideoTrackCount: 0, localAudioTrackCount: 0, remoteAudioTrackCount: 0, remoteVideoTrackCount: 0,
  participants: [] as LiveParticipant[], reconnectCount: 0, disconnectReason: "", diagnosticCode: "",
  audioPath: "v1_legacy" as const, audioBusy: false, recovering: false, audioWarning: "",
  liveMusic: DEFAULT_LIVE_MUSIC_MIXING_STATE
};

export function useAgoraLiveBroadcastRoom() {
  const engineRef = useRef<IRtcEngine | null>(null);
  const handlerRef = useRef<IRtcEngineEventHandler | null>(null);
  const refreshRef = useRef<(() => Promise<LiveRtcCredentials | null>) | null>(null);
  const credentialsRef = useRef<LiveRtcCredentials | null>(null);
  const renewalRef = useRef<Promise<void> | null>(null);
  /**
   * The live mixer, ducking envelope and "is music actually playing" flag are
   * refs rather than state because the Agora event handler is registered once at
   * join time and closes over whatever it captured then. Reading them from state
   * would give the volume-indication callback a permanently stale mixer, and
   * re-registering the handler on every fader move would tear down and rebuild
   * the event bridge mid-broadcast.
   */
  const mixerRef = useRef<CreatorMixerSettings>(DEFAULT_LIVE_MUSIC_MIXING_STATE.mixer);
  const duckRef = useRef<LiveDuckState>(IDLE_LIVE_DUCK_STATE);
  const musicPlayingRef = useRef(false);
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
      emitAgoraLiveEvent({ name: "token_renewed", liveId: next.broadcastId, uid: next.uid });
      setState((s) => ({ ...s, error: "", diagnosticCode: "" }));
    }).catch(() => { emitAgoraLiveEvent({ name: "token_renewal_failed", liveId: current.broadcastId, uid: current.uid }); setState((s) => ({ ...s, error: "Secure Live access could not be refreshed.", diagnosticCode: "AGORA_LIVE_TOKEN_RENEWAL_FAILED" })); })
      .finally(() => { renewalRef.current = null; });
  }, []);

  const disconnect = useCallback(async (reason = "local_disconnect") => {
    const engine = engineRef.current; engineRef.current = null;
    const credentials = credentialsRef.current;
    if (credentials) emitAgoraLiveEvent({ name: "leave", liveId: credentials.broadcastId, uid: credentials.uid, reason });
    if (engine) { engine.stopAudioMixing?.(); if (handlerRef.current) engine.unregisterEventHandler(handlerRef.current); engine.leaveChannel(); engine.release(); }
    handlerRef.current = null; credentialsRef.current = null; refreshRef.current = null;
    musicPlayingRef.current = false; duckRef.current = IDLE_LIVE_DUCK_STATE;
    setState((s) => ({ ...initial, supported: s.supported, disconnectReason: reason, diagnosticCode: reason }));
  }, []);

  const connect = useCallback(async (credentials: LiveRtcCredentials, options: { publish?: boolean; video?: boolean; refreshCredentials?: () => Promise<LiveRtcCredentials | null> } = {}) => {
    if (credentials.provider !== "agora" || !credentials.token || !credentials.appId || !credentials.channelName || !credentials.uid) return false;
    if (options.publish && !credentials.canPublish) {
      setState((s) => ({ ...s, error: "PulseSoc did not authorize this account to publish.", diagnosticCode: "AGORA_LIVE_PUBLISH_FORBIDDEN" }));
      return false;
    }
    const activeEngine = engineRef.current;
    const activeCredentials = credentialsRef.current;
    const sameAgoraSeat = Boolean(activeEngine && activeCredentials && activeCredentials.provider === "agora" && activeCredentials.channelName === credentials.channelName && activeCredentials.uid === credentials.uid);
    if (sameAgoraSeat && activeEngine && activeCredentials && Boolean(options.publish) !== Boolean(activeCredentials.canPublish)) {
      try {
        const agora = await import("react-native-agora");
        const promote = Boolean(options.publish && credentials.canPublish);
        if (activeEngine.renewToken(credentials.token) < 0) throw new Error("Agora rejected the refreshed co-host permission.");
        if (promote) {
          activeEngine.enableVideo();
          activeEngine.setVideoEncoderConfiguration({ dimensions: { width: 720, height: 1280 }, frameRate: 30, bitrate: 0, orientationMode: agora.OrientationMode.OrientationModeAdaptive, degradationPreference: agora.DegradationPreference.MaintainBalanced });
          if (activeEngine.setClientRole(agora.ClientRoleType.ClientRoleBroadcaster) < 0) throw new Error("Agora rejected the co-host role upgrade.");
          if (activeEngine.updateChannelMediaOptions({ clientRoleType: agora.ClientRoleType.ClientRoleBroadcaster, publishMicrophoneTrack: true, publishCameraTrack: options.video !== false, autoSubscribeAudio: true, autoSubscribeVideo: true }) < 0) throw new Error("Agora rejected co-host camera or microphone publication.");
          if (options.video !== false) activeEngine.startPreview();
          const localTrack = options.video !== false ? { provider: "agora", uid: 0, local: true } : null;
          const localParticipant: LiveParticipant = { identity: credentials.identity, name: credentials.participantName || "You", isLocal: true, isHost: false, videoTrack: localTrack, audioTrack: { provider: "agora", uid: 0 }, hasVideo: Boolean(localTrack), hasAudio: true, audioMuted: false, speaking: false };
          setState((s) => ({ ...s, canPublish: true, audioEnabled: true, videoEnabled: Boolean(localTrack), localVideoTrack: localTrack, localAudioTrackCount: 0, localVideoTrackCount: 0, participants: [...s.participants.filter((participant) => !participant.isLocal), localParticipant], error: "", diagnosticCode: "" }));
          emitAgoraLiveEvent({ name: "role_upgraded", liveId: credentials.broadcastId, uid: credentials.uid, reason: "authorized_cohost" });
        } else {
          activeEngine.muteLocalAudioStream(true);
          activeEngine.muteLocalVideoStream(true);
          activeEngine.stopPreview();
          if (activeEngine.updateChannelMediaOptions({ clientRoleType: agora.ClientRoleType.ClientRoleAudience, publishMicrophoneTrack: false, publishCameraTrack: false, autoSubscribeAudio: true, autoSubscribeVideo: true }) < 0) throw new Error("Agora rejected the audience media settings.");
          if (activeEngine.setClientRole(agora.ClientRoleType.ClientRoleAudience) < 0) throw new Error("Agora rejected the audience role restore.");
          setState((s) => ({ ...s, canPublish: false, audioEnabled: false, videoEnabled: false, localVideoTrack: null, localAudioTrackCount: 0, localVideoTrackCount: 0, participants: s.participants.filter((participant) => !participant.isLocal), error: "", diagnosticCode: "" }));
          emitAgoraLiveEvent({ name: "role_demoted", liveId: credentials.broadcastId, uid: credentials.uid, reason: "cohost_left" });
        }
        credentialsRef.current = credentials;
        refreshRef.current = options.refreshCredentials || null;
        return true;
      } catch (error) {
        setState((s) => ({ ...s, error: error instanceof Error ? error.message : "Agora co-host role change failed.", diagnosticCode: "AGORA_LIVE_ROLE_CHANGE_FAILED" }));
        return false;
      }
    }
    if (engineRef.current) await disconnect("replaced_room");
    credentialsRef.current = credentials; refreshRef.current = options.refreshCredentials || null;
    setState((s) => ({ ...initial, supported: s.supported, connecting: true, connectionState: "connecting", canPublish: credentials.canPublish }));
    try {
      const agora = await import("react-native-agora");
      const engine = agora.createAgoraRtcEngine(); engineRef.current = engine; engine.initialize({ appId: credentials.appId }); engine.enableAudio();
      const publish = Boolean(options.publish && credentials.canPublish);
      engine.setAudioProfile(agora.AudioProfileType.AudioProfileMusicHighQuality, agora.AudioScenarioType.AudioScenarioDefault);
      engine.setRemoteSubscribeFallbackOption(agora.StreamFallbackOptions.StreamFallbackOptionAudioOnly);
      if (publish && options.video !== false) {
        engine.enableVideo();
        engine.setVideoEncoderConfiguration({
          dimensions: { width: 720, height: 1280 },
          frameRate: 30,
          bitrate: 0,
          orientationMode: agora.OrientationMode.OrientationModeAdaptive,
          degradationPreference: agora.DegradationPreference.MaintainBalanced
        });
        engine.enableDualStreamMode(true);
        engine.startPreview();
      }
      emitAgoraLiveEvent({ name: "provider_selected", liveId: credentials.broadcastId, uid: credentials.uid, reason: publish ? "broadcaster" : "audience" });
      const localTrack = publish && options.video !== false ? { provider: "agora", uid: 0, local: true } : null;
      const localParticipant: LiveParticipant = { identity: credentials.identity, name: credentials.participantName || "You", isLocal: true, isHost: credentials.role === "host", videoTrack: localTrack, audioTrack: publish ? { provider: "agora", uid: 0 } : null, hasVideo: Boolean(localTrack), hasAudio: publish, audioMuted: false, speaking: false };
      let settleJoin: ((joined: boolean) => void) | null = null;
      const joinOutcome = new Promise<boolean>((resolve) => { settleJoin = resolve; });
      const handler: IRtcEngineEventHandler = {
        onJoinChannelSuccess: () => { emitAgoraLiveEvent({ name: "channel_joined", liveId: credentials.broadcastId, uid: credentials.uid }); settleJoin?.(true); settleJoin = null; setState((s) => ({ ...s, connecting: false, connected: true, reconnecting: false, connectionState: "connected", audioEnabled: publish, videoEnabled: Boolean(localTrack), localVideoTrack: localTrack, localVideoTrackCount: 0, localAudioTrackCount: 0, participants: publish ? [localParticipant] : [], error: "", diagnosticCode: "" })); },
        onConnectionStateChanged: (_c, value) => {
          const reconnecting = value === agora.ConnectionStateType.ConnectionStateReconnecting;
          const connected = value === agora.ConnectionStateType.ConnectionStateConnected;
          const failed = value === agora.ConnectionStateType.ConnectionStateFailed;
          if (failed) { settleJoin?.(false); settleJoin = null; }
          emitAgoraLiveEvent({ name: "connection_state", liveId: credentials.broadcastId, uid: credentials.uid, connectionState: failed ? "failed" : reconnecting ? "reconnecting" : connected ? "connected" : "connecting" });
          setState((s) => ({ ...s, connected, reconnecting, recovering: reconnecting, connecting: !connected && !reconnecting && !failed, connectionState: failed ? "failed" : reconnecting ? "reconnecting" : connected ? "connected" : "connecting", reconnectCount: reconnecting && !s.reconnecting ? s.reconnectCount + 1 : s.reconnectCount, diagnosticCode: failed ? "AGORA_LIVE_CONNECTION_FAILED" : s.diagnosticCode }));
        },
        onUserJoined: (_c, uid) => setState((s) => { const participant: LiveParticipant = { identity: `agora-${uid}`, name: "Live participant", isLocal: false, isHost: !publish && s.participants.length === 0, videoTrack: { provider: "agora", uid }, audioTrack: { provider: "agora", uid }, hasVideo: false, hasAudio: false, audioMuted: false, speaking: false }; const participants = [...s.participants.filter(p => p.identity !== participant.identity), participant]; emitAgoraLiveEvent({ name: "remote_joined", liveId: credentials.broadcastId, uid, participantCount: participants.length }); return { ...s, participants }; }),
        onUserOffline: (_c, uid) => setState((s) => ({ ...s, participants: s.participants.filter(p => p.identity !== `agora-${uid}`), remoteAudioTrackCount: Math.max(0, s.remoteAudioTrackCount - 1), remoteVideoTrackCount: Math.max(0, s.remoteVideoTrackCount - 1) })),
        onFirstRemoteAudioDecoded: (_c, uid) => { emitAgoraLiveEvent({ name: "first_remote_audio", liveId: credentials.broadcastId, uid }); setState((s) => ({ ...s, participants: s.participants.map((p) => p.identity === `agora-${uid}` ? { ...p, hasAudio: true } : p), remoteAudioTrackCount: Math.max(1, s.remoteAudioTrackCount) })); },
        onFirstRemoteVideoDecoded: (_c, uid, width, height) => { emitAgoraLiveEvent({ name: "first_remote_video", liveId: credentials.broadcastId, uid, width, height }); setState((s) => ({ ...s, participants: s.participants.map((p) => p.identity === `agora-${uid}` ? { ...p, hasVideo: true } : p), remoteVideoTrackCount: Math.max(1, s.remoteVideoTrackCount) })); },
        onFirstLocalAudioFramePublished: () => { emitAgoraLiveEvent({ name: "local_audio_published", liveId: credentials.broadcastId, uid: credentials.uid }); setState((s) => ({ ...s, localAudioTrackCount: 1 })); },
        onFirstLocalVideoFramePublished: () => { emitAgoraLiveEvent({ name: "local_video_published", liveId: credentials.broadcastId, uid: credentials.uid }); setState((s) => ({ ...s, localVideoTrackCount: 1 })); },
        onLocalAudioStateChanged: (_c, mediaState, reason) => { emitAgoraLiveEvent({ name: "local_audio_state", liveId: credentials.broadcastId, uid: credentials.uid, code: mediaState, reason: String(reason) }); if (mediaState === agora.LocalAudioStreamState.LocalAudioStreamStateFailed) setState((s) => ({ ...s, error: "Microphone publishing failed. Check microphone permission and retry.", diagnosticCode: `AGORA_LIVE_AUDIO_${reason}` })); },
        onLocalVideoStateChanged: (_source, mediaState, reason) => { emitAgoraLiveEvent({ name: "local_video_state", liveId: credentials.broadcastId, uid: credentials.uid, code: mediaState, reason: String(reason) }); if (mediaState === agora.LocalVideoStreamState.LocalVideoStreamStateFailed) setState((s) => ({ ...s, error: "Camera publishing failed. Check camera permission and retry.", diagnosticCode: `AGORA_LIVE_VIDEO_${reason}` })); },
        onNetworkQuality: (_c, uid, txQuality, rxQuality) => emitAgoraLiveEvent({ name: "network_quality", liveId: credentials.broadcastId, uid, txQuality, rxQuality }, true),
        onRtcStats: (_c, stats) => emitAgoraLiveEvent({ name: "rtc_stats", liveId: credentials.broadcastId, uid: credentials.uid, audioBitrateKbps: stats.txAudioKBitRate, videoBitrateKbps: stats.txVideoKBitRate, latencyMs: stats.lastmileDelay }, true),
        onLocalAudioStats: (_c, stats) => emitAgoraLiveEvent({ name: "local_audio_stats", liveId: credentials.broadcastId, uid: credentials.uid, audioBitrateKbps: stats.sentBitrate, packetLossPercent: stats.txPacketLossRate }, true),
        /**
         * The ducking sidechain.
         *
         * Agora has no sidechain compressor, so the only way to make the music
         * step back for the host's voice is to ride the publish volume from the
         * activity meter. That is acceptable *here* and would not be anywhere
         * else: this fires a few times a second, not per audio sample, and each
         * step is a handful of arithmetic operations against a one-pole smoother.
         * The actual audio mixing still happens inside the SDK on its own thread.
         *
         * It is also gated on music actually playing. Left running it would push
         * volumes at an engine with nothing to apply them to, and — worse — would
         * keep an envelope warm across tracks so the first second of the next one
         * came up already ducked.
         */
        onAudioVolumeIndication: (_connection, speakers, _speakerNumber, _totalVolume) => {
          if (!musicPlayingRef.current) return;
          const settings = mixerRef.current;
          // uid 0 is this device. A remote speaker's level must never move the
          // host's music: the host is not talking, and the viewer would hear the
          // bed dip every time a co-host did.
          const local = (speakers || []).find((speaker) => Number(speaker?.uid ?? -1) === 0);
          const step = stepLiveDuck(
            settings,
            { volume: Number(local?.volume || 0), vad: Number(local?.vad || 0) },
            duckRef.current,
            Date.now()
          );
          duckRef.current = step.state;
          if (step.publishVolume === null) return;
          engineRef.current?.adjustAudioMixingPublishVolume(step.publishVolume);
          // Playout follows publish so the host monitors what the room hears.
          // A host mixing against a different balance than the one going out is
          // the reason "it sounded fine on my phone" happens.
          engineRef.current?.adjustAudioMixingPlayoutVolume(step.publishVolume);
          setState((s) => (Math.abs(s.liveMusic.duckGainDb - step.state.gainDb) < 0.25 ? s : { ...s, liveMusic: { ...s.liveMusic, duckGainDb: step.state.gainDb } }));
        },
        onAudioMixingStateChanged: (mixingState, reason) => {
          emitAgoraLiveEvent({ name: "audio_mixing_state", liveId: credentials.broadcastId, uid: credentials.uid, code: mixingState, reason: String(reason) });
          // The SDK is authoritative on whether music is running, including for
          // the transitions nobody asked for — a track that ends on its own, or a
          // file that fails to open. The sidechain follows it rather than the
          // intent expressed at the call site.
          musicPlayingRef.current = mixingState === agora.AudioMixingStateType.AudioMixingStatePlaying;
          if (!musicPlayingRef.current) duckRef.current = IDLE_LIVE_DUCK_STATE;
          setState((s) => {
            if (mixingState === agora.AudioMixingStateType.AudioMixingStatePlaying) {
              return { ...s, liveMusic: { ...s.liveMusic, status: "playing", error: "" } };
            }
            if (mixingState === agora.AudioMixingStateType.AudioMixingStatePaused) {
              return { ...s, liveMusic: { ...s.liveMusic, status: "paused", error: "" } };
            }
            if (mixingState === agora.AudioMixingStateType.AudioMixingStateStopped) {
              return { ...s, liveMusic: { ...s.liveMusic, status: "idle", track: null, startOffsetSeconds: 0, duckGainDb: 0, error: "" } };
            }
            if (mixingState === agora.AudioMixingStateType.AudioMixingStateFailed) {
              return { ...s, liveMusic: { ...s.liveMusic, status: "error", error: `PulseSoc Music could not start (${reason}).` } };
            }
            return s;
          });
        },
        onLocalVideoStats: (_c, _source, stats) => emitAgoraLiveEvent({ name: "local_video_stats", liveId: credentials.broadcastId, uid: credentials.uid, videoBitrateKbps: stats.sentBitrate, videoFps: stats.sentFrameRate, packetLossPercent: stats.txPacketLossRate, width: stats.encodedFrameWidth, height: stats.encodedFrameHeight }, true),
        onTokenPrivilegeWillExpire: () => { emitAgoraLiveEvent({ name: "token_renewal_requested", liveId: credentials.broadcastId, uid: credentials.uid }); renewToken(); },
        onRequestToken: () => { emitAgoraLiveEvent({ name: "token_expired_recovery", liveId: credentials.broadcastId, uid: credentials.uid }); renewToken(); },
        onError: (code) => { settleJoin?.(false); settleJoin = null; emitAgoraLiveEvent({ name: "sdk_error", liveId: credentials.broadcastId, uid: credentials.uid, code }); setState((s) => ({ ...s, error: `Agora Live media error (${code}).`, diagnosticCode: `AGORA_LIVE_${code}` })); }
      };
      handlerRef.current = handler; engine.registerEventHandler(handler);
      const result = engine.joinChannel(credentials.token, credentials.channelName, credentials.uid, { clientRoleType: publish ? agora.ClientRoleType.ClientRoleBroadcaster : agora.ClientRoleType.ClientRoleAudience, channelProfile: agora.ChannelProfileType.ChannelProfileLiveBroadcasting, publishMicrophoneTrack: publish, publishCameraTrack: Boolean(localTrack), autoSubscribeAudio: true, autoSubscribeVideo: true });
      if (result < 0) throw new Error(`Agora rejected the Live join (${result}).`);
      const joined = await Promise.race([joinOutcome, new Promise<boolean>((resolve) => setTimeout(() => resolve(false), 12_000))]);
      settleJoin = null;
      if (!joined) throw new Error("Agora Live did not finish joining. Retry the broadcast.");
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
  /**
   * Push the whole mixer at the engine at once.
   *
   * Both faders are written together on every change because they describe one
   * balance, not two independent numbers, and because applying them from a
   * single source removes the window where the music has moved and the mic has
   * not. Missing an engine — music adjusted before the broadcast connects — is
   * not an error here: the settings are held and applied at `startAudioMixing`.
   */
  const applyMixerToEngine = useCallback((settings: CreatorMixerSettings) => {
    mixerRef.current = settings;
    const rtcEngine = engineRef.current;
    if (!rtcEngine) return;
    const musicVolume = liveMusicPublishVolume(settings, duckRef.current.gainDb);
    rtcEngine.adjustAudioMixingPublishVolume(musicVolume);
    rtcEngine.adjustAudioMixingPlayoutVolume(musicVolume);
    rtcEngine.adjustRecordingSignalVolume(liveMicPublishVolume(settings));
    duckRef.current = { ...duckRef.current, publishedVolume: musicVolume };
  }, []);

  const setLiveMusicMixer = useCallback(async (input: CreatorMixerSettings) => {
    const settings = normalizeCreatorMixerSettings(input);
    applyMixerToEngine(settings);
    setState((s) => ({ ...s, liveMusic: { ...withLiveMixer(s.liveMusic, settings), error: "" } }));
  }, [applyMixerToEngine]);

  const startLiveMusicMixing = useCallback(async (input: LiveMusicMixingTrack, options: { startOffsetSeconds?: number; mixer?: CreatorMixerSettings } = {}) => {
    const track = normalizeLiveMusicTrack(input);
    if (!track) throw new Error("Choose an approved PulseSoc Music track with playable audio.");
    const rtcEngine = engine();
    const current = credentialsRef.current;
    if (!current?.canPublish) throw new Error("Only the Live host or approved co-host can publish music.");
    const settings = options.mixer ? normalizeCreatorMixerSettings(options.mixer) : mixerRef.current;
    const startOffsetSeconds = Math.max(0, Number(options.startOffsetSeconds || 0));
    mixerRef.current = settings;
    // A fresh envelope per track. Carrying the previous one over would open the
    // new track already ducked if the host happened to be mid-sentence.
    duckRef.current = IDLE_LIVE_DUCK_STATE;
    musicPlayingRef.current = false;
    setState((s) => ({ ...s, liveMusic: { ...withLiveMixer(s.liveMusic, settings), status: "loading", track, startOffsetSeconds, duckGainDb: 0, error: "" } }));
    // `loopback: false` is what makes this a broadcast rather than a private
    // monitor — false means the mixed music goes into the published stream, not
    // only out of the local speaker. Getting this backwards is precisely the
    // "host hears music, viewers hear silence" bug.
    const startResult = rtcEngine.startAudioMixing(track.audioUrl, false, -1, liveMusicStartPositionMs(startOffsetSeconds));
    if (startResult < 0) {
      setState((s) => ({ ...s, liveMusic: { ...s.liveMusic, status: "error", track, error: `PulseSoc Music could not start (${startResult}).` } }));
      throw new Error(`PulseSoc Music could not start (${startResult}).`);
    }
    applyMixerToEngine(settings);
    // ~5 Hz with VAD reporting on. Fast enough for a duck that lands inside the
    // first syllable, slow enough that the callback is not a load on the bridge.
    rtcEngine.enableAudioVolumeIndication?.(200, 3, true);
    musicPlayingRef.current = true;
    emitAgoraLiveEvent({ name: "audio_mixing_started", liveId: current.broadcastId, uid: current.uid, reason: track.id });
    setState((s) => ({ ...s, liveMusic: { ...s.liveMusic, status: "playing", track, error: "" } }));
  }, [applyMixerToEngine]);

  const pauseLiveMusicMixing = useCallback(async () => {
    const result = engine().pauseAudioMixing();
    if (result < 0) throw new Error(`PulseSoc Music could not pause (${result}).`);
    musicPlayingRef.current = false;
    duckRef.current = IDLE_LIVE_DUCK_STATE;
    setState((s) => ({ ...s, liveMusic: { ...s.liveMusic, status: "paused", duckGainDb: 0, error: "" } }));
  }, []);
  const resumeLiveMusicMixing = useCallback(async () => {
    const result = engine().resumeAudioMixing();
    if (result < 0) throw new Error(`PulseSoc Music could not resume (${result}).`);
    musicPlayingRef.current = true;
    setState((s) => ({ ...s, liveMusic: { ...s.liveMusic, status: "playing", error: "" } }));
  }, []);
  const stopLiveMusicMixing = useCallback(async () => {
    const rtcEngine = engine();
    const result = rtcEngine.stopAudioMixing();
    if (result < 0) throw new Error(`PulseSoc Music could not stop (${result}).`);
    musicPlayingRef.current = false;
    duckRef.current = IDLE_LIVE_DUCK_STATE;
    // Hand the microphone back at the level the mixer says, unducked. Leaving the
    // recording gain wherever the last mix put it would make the host quieter for
    // the rest of the broadcast, long after the music that justified it stopped.
    rtcEngine.adjustRecordingSignalVolume(liveMicPublishVolume(mixerRef.current));
    // The indication callback exists for ducking. Nothing else in Live reads it,
    // so it is switched off rather than left running for the rest of the stream.
    rtcEngine.enableAudioVolumeIndication?.(0, 3, false);
    setState((s) => ({ ...s, liveMusic: { ...s.liveMusic, status: "idle", track: null, startOffsetSeconds: 0, duckGainDb: 0, error: "" } }));
  }, []);

  /**
   * Re-cue the running track without restarting it.
   *
   * `setAudioMixingPosition` is why the start-point control can stay live during
   * a broadcast. Restarting the mix to move the cue would drop the music out of
   * the published stream for as long as the file takes to reopen, which viewers
   * hear as a stutter.
   */
  const setLiveMusicPosition = useCallback(async (seconds: number) => {
    const result = engine().setAudioMixingPosition(liveMusicStartPositionMs(seconds));
    if (result < 0) throw new Error("PulseSoc Music could not move to that start point.");
    setState((s) => ({ ...s, liveMusic: { ...s.liveMusic, startOffsetSeconds: Math.max(0, seconds), error: "" } }));
  }, []);

  const setLiveMusicVolume = useCallback(async (level: number) => {
    await setLiveMusicMixer(withMusicLevel(mixerRef.current, clampLiveMixLevel(level)));
  }, [setLiveMusicMixer]);
  const setLiveMicVolume = useCallback(async (level: number) => {
    await setLiveMusicMixer(withMicLevel(mixerRef.current, clampLiveMixLevel(level)));
  }, [setLiveMusicMixer]);
  useEffect(() => () => { disconnect("unmounted").catch(() => undefined); }, [disconnect]);
  return { ...state, lifecycle: null, connect, disconnect, startBroadcast: connect, stopBroadcast: disconnect, joinAsViewer: connect, leaveViewer: disconnect, setMicrophoneEnabled, setCameraEnabled, setSpeakerEnabled, setRemoteAudioEnabled, showAudioRoutePicker, recheckAudio: async () => undefined, switchCamera, startLiveMusicMixing, pauseLiveMusicMixing, resumeLiveMusicMixing, stopLiveMusicMixing, setLiveMusicVolume, setLiveMicVolume, setLiveMusicMixer, setLiveMusicPosition, getLastConnectError: () => state.error, getAudioTrace: () => [] };
}
