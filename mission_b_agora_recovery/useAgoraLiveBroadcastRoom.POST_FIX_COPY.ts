import { useCallback, useEffect, useRef, useState } from "react";
import type { IRtcEngine, IRtcEngineEventHandler } from "react-native-agora";
import type { LiveRtcCredentials } from "./liveSession";
import type { LiveParticipant } from "./useLiveBroadcastRoom";
import { emitAgoraLiveEvent } from "./agoraLiveTelemetry";
import { reconcileLiveSeat } from "./liveSeatReconciliation";
import { normalizeLiveRole } from "./liveParticipantRegistry";
import { nextEchoScenario, resolveLiveAudioPlan, resolveLiveEchoControl } from "./liveAudioMatrix";
import { INITIAL_ACTIVE_SPEAKER_STATE, reduceActiveSpeaker, type ActiveSpeakerState } from "./liveStageLayout";
import { publisherVideoProfile } from "./liveStreamQuality";
import {
  DEFAULT_LIVE_MUSIC_MIXING_STATE,
  clampLiveMixLevel,
  liveMixLevelToAgoraVolume,
  musicRestorationAfterAudioChange,
  normalizeLiveMusicTrack,
  type LiveMusicMixingState,
  type LiveMusicMixingTrack
} from "./liveMusicMixing";

const initial = {
  provider: "agora" as const, supported: true, connecting: false, connected: false, reconnecting: false,
  connectionState: "disconnected", connectionQuality: "unknown", error: "", canPublish: false,
  audioEnabled: false, videoEnabled: false, speakerEnabled: true, remoteAudioEnabled: true,
  localVideoTrack: null as any, localVideoTrackCount: 0, localAudioTrackCount: 0, remoteAudioTrackCount: 0, remoteVideoTrackCount: 0,
  participants: [] as LiveParticipant[], reconnectCount: 0, disconnectReason: "", diagnosticCode: "",
  audioPath: "v1_legacy" as const, audioBusy: false, recovering: false, audioWarning: "",
  liveMusic: DEFAULT_LIVE_MUSIC_MIXING_STATE,
  /** Agora uid of the current active speaker, 0 for nobody. */
  activeSpeakerUid: 0
};

/**
 * How often Agora reports speaker volumes, in milliseconds.
 *
 * 300ms is fast enough for the highlight to feel attached to the conversation
 * and slow enough that it is not a per-frame cost. The smoothing that stops it
 * flickering lives in `reduceActiveSpeaker`, not here — a longer interval would
 * make the highlight laggy without making it any more stable.
 */
const VOLUME_REPORT_INTERVAL_MS = 300;

export function useAgoraLiveBroadcastRoom() {
  const engineRef = useRef<IRtcEngine | null>(null);
  const handlerRef = useRef<IRtcEngineEventHandler | null>(null);
  const refreshRef = useRef<(() => Promise<LiveRtcCredentials | null>) | null>(null);
  const credentialsRef = useRef<LiveRtcCredentials | null>(null);
  const renewalRef = useRef<Promise<void> | null>(null);
  // Held in a ref, not in state: a volume report arrives roughly three times a
  // second, and only the handful that actually move the highlight should cause
  // a render.
  const speakerRef = useRef<ActiveSpeakerState>(INITIAL_ACTIVE_SPEAKER_STATE);
  // The echo-control scenario currently applied to the engine, and the plan it
  // was derived from. Held in refs so a roster change that does not move the
  // scenario never reaches the SDK: reapplying an audio scenario mid-broadcast
  // is a real audible glitch, not a no-op.
  const echoScenarioRef = useRef<"default" | "chatroom">("default");
  const audioPlanRef = useRef(resolveLiveAudioPlan("audience", false));
  // Stage 35. The music mix is read from a ref rather than from state inside
  // `setStagePublisherCount`, because that callback must not be rebuilt every
  // time a volume slider moves — a new identity there would re-run the effect
  // that reports the stage size and re-apply the audio scenario mid-broadcast.
  const musicStateRef = useRef<LiveMusicMixingState>(DEFAULT_LIVE_MUSIC_MIXING_STATE);
  // Stage 24. The stage size this device's encoder is currently configured for,
  // and whether it is publishing video at all. Both are refs because
  // `setStagePublisherCount` must keep a stable identity — see the music note
  // above — and because re-applying an unchanged encoder configuration mid-
  // broadcast is a visible resolution blip rather than a no-op.
  const encoderStageRef = useRef(0);
  const publishingVideoRef = useRef(false);
  const [state, setState] = useState(initial);
  musicStateRef.current = state.liveMusic;

  /**
   * Stage 24. Configure the encoder for a stage of `count` publishers.
   *
   * Publishing 720x1280 into a six-way grid uploads a resolution nobody
   * subscribes to, from a phone that is simultaneously decoding five other
   * streams. It also costs money in a place that is easy to miss: Agora bills
   * cloud recording on the *aggregate* resolution of every stream the recorder
   * subscribes to, and the recorder takes the high stream of every publisher.
   * Six publishers at 720x1280 aggregate to 5,529,600 — the 2K+ tier — where
   * the same six on this ladder aggregate to 1,382,400 and stay in Full HD.
   *
   * Applied only when the stage size actually moves it, and never for a client
   * that is not publishing video: an audience member configures no encoder,
   * which is Stage 25 and a privacy property before it is a performance one.
   */
  const applyPublisherEncoder = useCallback(async (count: number) => {
    if (!publishingVideoRef.current) return;
    const rtcEngine = engineRef.current;
    if (!rtcEngine) return;
    const stage = Math.max(1, Math.floor(Number(count) || 1));
    const previous = publisherVideoProfile(encoderStageRef.current || 1);
    const profile = publisherVideoProfile(stage);
    if (encoderStageRef.current && profile.width === previous.width && profile.frameRate === previous.frameRate) {
      encoderStageRef.current = stage;
      return;
    }
    const agora = await import("react-native-agora");
    rtcEngine.setVideoEncoderConfiguration({
      dimensions: { width: profile.width, height: profile.height },
      frameRate: profile.frameRate,
      bitrate: 0,
      orientationMode: agora.OrientationMode.OrientationModeAdaptive,
      degradationPreference: agora.DegradationPreference.MaintainBalanced
    });
    encoderStageRef.current = stage;
    emitAgoraLiveEvent({
      name: "publish_profile_changed",
      liveId: credentialsRef.current?.broadcastId,
      uid: credentialsRef.current?.uid,
      reason: `${profile.width}x${profile.height}@${profile.frameRate}`,
      participantCount: stage
    });
  }, []);

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
    handlerRef.current = null; credentialsRef.current = null; refreshRef.current = null; speakerRef.current = INITIAL_ACTIVE_SPEAKER_STATE;
    echoScenarioRef.current = "default"; audioPlanRef.current = resolveLiveAudioPlan("audience", false);
    encoderStageRef.current = 0; publishingVideoRef.current = false;
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
    // What this call should do to a session that is already running. `rejoin` —
    // the only outcome that destroys the engine and restarts the camera, mic and
    // audio session — is reachable only when the channel or uid actually
    // changed, so no amount of guest churn or token refreshing can restart a
    // host's broadcast.
    const action = reconcileLiveSeat(
      activeEngine && activeCredentials
        ? { provider: String(activeCredentials.provider || ""), channelName: String(activeCredentials.channelName || ""), uid: Number(activeCredentials.uid || 0), publishing: Boolean(activeCredentials.canPublish), token: String(activeCredentials.token || "") }
        : null,
      { provider: String(credentials.provider || ""), channelName: String(credentials.channelName || ""), uid: Number(credentials.uid || 0), publishing: Boolean(options.publish && credentials.canPublish), token: String(credentials.token || "") }
    );
    if (action === "noop" && activeEngine) {
      // Same seat, same role, same token. Re-entering connect() here would mean
      // a live host loses their camera because something upstream re-rendered.
      refreshRef.current = options.refreshCredentials || refreshRef.current;
      return true;
    }
    if (action === "renew_token" && activeEngine) {
      // A re-minted token for the seat we already hold. Agora accepts it in
      // place; leaving and rejoining to apply it would drop the broadcast.
      if (activeEngine.renewToken(credentials.token) < 0) {
        setState((s) => ({ ...s, error: "Secure Live access could not be refreshed.", diagnosticCode: "AGORA_LIVE_TOKEN_RENEWAL_FAILED" }));
        return false;
      }
      credentialsRef.current = credentials;
      refreshRef.current = options.refreshCredentials || refreshRef.current;
      emitAgoraLiveEvent({ name: "token_renewed", liveId: credentials.broadcastId, uid: credentials.uid });
      return true;
    }
    if ((action === "promote" || action === "demote") && activeEngine && activeCredentials) {
      try {
        const agora = await import("react-native-agora");
        const promote = Boolean(options.publish && credentials.canPublish);
        if (activeEngine.renewToken(credentials.token) < 0) throw new Error("Agora rejected the refreshed co-host permission.");
        if (promote) {
          activeEngine.enableVideo();
          // Stage 24. A guest promoted onto a stage that already has people on
          // it must not open at solo-Live resolution and step down a moment
          // later; the audience sees that as the new tile flickering. The stage
          // size the encoder was last configured for is the right starting
          // point, and it is at least 2 because this device is joining it.
          publishingVideoRef.current = options.video !== false;
          await applyPublisherEncoder(Math.max(2, encoderStageRef.current));
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
          // Stage 25. Back in the audience, this device configures no encoder.
          publishingVideoRef.current = false;
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
    // Only `rejoin` reaches here with an engine still up, and only a different
    // channel or uid produces `rejoin`.
    if (engineRef.current) await disconnect("replaced_room");
    credentialsRef.current = credentials; refreshRef.current = options.refreshCredentials || null;
    setState((s) => ({ ...initial, supported: s.supported, connecting: true, connectionState: "connecting", canPublish: credentials.canPublish }));
    try {
      const agora = await import("react-native-agora");
      const engine = agora.createAgoraRtcEngine(); engineRef.current = engine; engine.initialize({ appId: credentials.appId }); engine.enableAudio();
      // The video MODULE is not the camera. `enableVideo()` is what lets this
      // client DECODE remote video — an audience member that skips it joins,
      // hears audio, and never receives a first remote video frame, which reads
      // as "native playback unavailable" on every viewer surface. Capture only
      // begins with `startPreview()`/publication, both still gated below, and
      // the join options pin `publishCameraTrack:false` for the audience — so
      // Stage 25 ("an audience member initialises nothing") still holds for
      // every capture device. See `resolvePublishPlan().enableVideoModule`.
      engine.enableVideo();
      const publish = Boolean(options.publish && credentials.canPublish);
      // Stage 15/17. The audio topology is decided in `liveAudioMatrix` and only
      // told to the SDK here. `publisherCount: 1` at join time is deliberate: it
      // resolves to the default scenario, so a single-host Live is configured
      // exactly as it was before multi-guest existed. The chatroom scenario is
      // engaged later, by `setStagePublisherCount`, when a second publisher
      // actually arrives.
      // A publisher whose role string is unrecognised is still a publisher — the
      // server said so by issuing publishable credentials. Falling back to
      // `guest` keeps the plan honest instead of quietly downgrading a live
      // broadcaster to a listener because of a label.
      const credentialRole = normalizeLiveRole(credentials.role);
      const stageRole = publish && credentialRole === "audience" ? "guest" : credentialRole;
      const plan = resolveLiveAudioPlan(stageRole, publish);
      audioPlanRef.current = plan;
      const echo = resolveLiveEchoControl(plan, 1);
      echoScenarioRef.current = echo.scenario;
      engine.setAudioProfile(
        agora.AudioProfileType.AudioProfileMusicHighQuality,
        echo.scenario === "chatroom" ? agora.AudioScenarioType.AudioScenarioChatroom : agora.AudioScenarioType.AudioScenarioDefault
      );
      engine.setRemoteSubscribeFallbackOption(agora.StreamFallbackOptions.StreamFallbackOptionAudioOnly);
      // Who is speaking, for the stage highlight. Enabled for audience members
      // too: on a multi-guest Live the audience needs to know which of six faces
      // is talking, and this reports remote volumes without touching the
      // subscriber's microphone.
      engine.enableAudioVolumeIndication(VOLUME_REPORT_INTERVAL_MS, 3, true);
      publishingVideoRef.current = Boolean(publish && options.video !== false);
      encoderStageRef.current = 0;
      if (publishingVideoRef.current) {
        // Stage 24. Join at the solo profile. A host going live is alone on the
        // stage until someone is brought up, and `setStagePublisherCount` steps
        // the ladder down from here as that happens — so a single-host Live is
        // encoded exactly as it was before multi-guest existed.
        await applyPublisherEncoder(1);
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
        onAudioMixingStateChanged: (mixingState, reason) => {
          emitAgoraLiveEvent({ name: "audio_mixing_state", liveId: credentials.broadcastId, uid: credentials.uid, code: mixingState, reason: String(reason) });
          setState((s) => {
            if (mixingState === agora.AudioMixingStateType.AudioMixingStatePlaying) {
              return { ...s, liveMusic: { ...s.liveMusic, status: "playing", error: "" } };
            }
            if (mixingState === agora.AudioMixingStateType.AudioMixingStatePaused) {
              return { ...s, liveMusic: { ...s.liveMusic, status: "paused", error: "" } };
            }
            if (mixingState === agora.AudioMixingStateType.AudioMixingStateStopped) {
              return { ...s, liveMusic: { ...s.liveMusic, status: "idle", track: null, error: "" } };
            }
            if (mixingState === agora.AudioMixingStateType.AudioMixingStateFailed) {
              return { ...s, liveMusic: { ...s.liveMusic, status: "error", error: `PulseSoc Music could not start (${reason}).` } };
            }
            return s;
          });
        },
        onLocalVideoStats: (_c, _source, stats) => emitAgoraLiveEvent({ name: "local_video_stats", liveId: credentials.broadcastId, uid: credentials.uid, videoBitrateKbps: stats.sentBitrate, videoFps: stats.sentFrameRate, packetLossPercent: stats.txPacketLossRate, width: stats.encodedFrameWidth, height: stats.encodedFrameHeight }, true),
        onAudioVolumeIndication: (_c: any, speakers: any[]) => {
          // Agora reports the local speaker as uid 0. Resolving it to this
          // client's real uid is what lets one comparison rank local and remote
          // voices against each other instead of treating "me" as a separate
          // case that can never win the highlight.
          const volumes = (speakers || []).map((speaker: any) => ({
            rtcUid: Number(speaker?.uid) === 0 ? Number(credentials.uid || 0) : Number(speaker?.uid) || 0,
            volume: Number(speaker?.volume) || 0
          }));
          const next = reduceActiveSpeaker(speakerRef.current, volumes, Date.now());
          if (next.activeUid === speakerRef.current.activeUid) {
            // Nothing visible changed. Keep the bookkeeping, skip the render.
            speakerRef.current = next;
            return;
          }
          speakerRef.current = next;
          setState((s) => ({
            ...s,
            activeSpeakerUid: next.activeUid,
            participants: s.participants.map((participant) => {
              const uid = participant.isLocal ? credentials.uid : Number(String(participant.identity).replace("agora-", "")) || 0;
              const speaking = uid === next.activeUid && !participant.audioMuted;
              return participant.speaking === speaking ? participant : { ...participant, speaking };
            })
          }));
        },
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
  /**
   * Tell the audio path how many people are publishing on the stage right now.
   *
   * Stage 17. This is the only thing guest churn is allowed to change about
   * audio: the echo-control scenario. It does not touch the engine, the
   * microphone, the audio session, or the local capture — a guest arriving must
   * never reconfigure any of those, which is what
   * `guestArrivalRequiresAudioReconfiguration()` states and what this function
   * is careful not to contradict.
   *
   * Returns the scenario in force, and applies nothing when it has not moved.
   */
  const setStagePublisherCount = useCallback(async (count: number) => {
    // Stage 24. Deliberately before the echo-scenario early return. The audio
    // scenario moves once, when the stage stops being solo; the encoder ladder
    // moves again at three publishers and again at five. Putting this after the
    // return would silently pin every stage larger than two to the two-publisher
    // profile, which is the kind of bug that only shows up on a busy Live.
    await applyPublisherEncoder(count);
    const target = nextEchoScenario(echoScenarioRef.current, audioPlanRef.current, count);
    if (!target) return echoScenarioRef.current;
    const rtcEngine = engineRef.current;
    if (!rtcEngine) return echoScenarioRef.current;
    const agora = await import("react-native-agora");
    const scenario = target === "chatroom" ? agora.AudioScenarioType.AudioScenarioChatroom : agora.AudioScenarioType.AudioScenarioDefault;
    if (rtcEngine.setAudioScenario(scenario) < 0) return echoScenarioRef.current;
    echoScenarioRef.current = target;
    // Stage 35. Changing the scenario reconfigures Agora's audio module, which
    // silently drops any mixing in flight. This is the one moment that matters,
    // because the scenario moves precisely when the first guest comes on stage
    // — so without this a host's music stops the instant they bring someone up.
    const music = musicStateRef.current;
    const restoration = musicRestorationAfterAudioChange(music);
    if (restoration.reapplyVolumes) {
      const musicVolume = liveMixLevelToAgoraVolume(music.musicVolume);
      rtcEngine.adjustAudioMixingPublishVolume(musicVolume);
      rtcEngine.adjustAudioMixingPlayoutVolume(musicVolume);
    }
    if (restoration.reapplyMicVolume) {
      rtcEngine.adjustRecordingSignalVolume(liveMixLevelToAgoraVolume(music.micVolume, 100));
    }
    if (restoration.resumePlayback) {
      // Resuming mixing that is already running is a no-op in the SDK, so this
      // is safe whether or not the scenario change actually interrupted it.
      rtcEngine.resumeAudioMixing();
    }
    emitAgoraLiveEvent({ name: "audio_scenario_changed", liveId: credentialsRef.current?.broadcastId, uid: credentialsRef.current?.uid, reason: target, participantCount: Math.max(0, Math.floor(Number(count) || 0)) });
    return target;
  }, [applyPublisherEncoder]);
  const showAudioRoutePicker = useCallback(async () => { throw new Error("Use the iOS system audio-route control for Agora Live."); }, []);
  const startLiveMusicMixing = useCallback(async (input: LiveMusicMixingTrack) => {
    const track = normalizeLiveMusicTrack(input);
    if (!track) throw new Error("Choose an approved PulseSoc Music track with playable audio.");
    const rtcEngine = engine();
    const current = credentialsRef.current;
    if (!current?.canPublish) throw new Error("Only the Live host or approved co-host can publish music.");
    setState((s) => ({ ...s, liveMusic: { ...s.liveMusic, status: "loading", track, error: "" } }));
    const startResult = rtcEngine.startAudioMixing(track.audioUrl, false, -1, 0);
    if (startResult < 0) {
      setState((s) => ({ ...s, liveMusic: { ...s.liveMusic, status: "error", track, error: `PulseSoc Music could not start (${startResult}).` } }));
      throw new Error(`PulseSoc Music could not start (${startResult}).`);
    }
    const musicVolume = liveMixLevelToAgoraVolume(state.liveMusic.musicVolume);
    rtcEngine.adjustAudioMixingPublishVolume(musicVolume);
    rtcEngine.adjustAudioMixingPlayoutVolume(musicVolume);
    rtcEngine.adjustRecordingSignalVolume(liveMixLevelToAgoraVolume(state.liveMusic.micVolume, 100));
    emitAgoraLiveEvent({ name: "audio_mixing_started", liveId: current.broadcastId, uid: current.uid, reason: track.id });
    setState((s) => ({ ...s, liveMusic: { ...s.liveMusic, status: "playing", track, error: "" } }));
  }, [state.liveMusic.micVolume, state.liveMusic.musicVolume]);
  const pauseLiveMusicMixing = useCallback(async () => {
    const result = engine().pauseAudioMixing();
    if (result < 0) throw new Error(`PulseSoc Music could not pause (${result}).`);
    setState((s) => ({ ...s, liveMusic: { ...s.liveMusic, status: "paused", error: "" } }));
  }, []);
  const resumeLiveMusicMixing = useCallback(async () => {
    const result = engine().resumeAudioMixing();
    if (result < 0) throw new Error(`PulseSoc Music could not resume (${result}).`);
    setState((s) => ({ ...s, liveMusic: { ...s.liveMusic, status: "playing", error: "" } }));
  }, []);
  const stopLiveMusicMixing = useCallback(async () => {
    const result = engine().stopAudioMixing();
    if (result < 0) throw new Error(`PulseSoc Music could not stop (${result}).`);
    setState((s) => ({ ...s, liveMusic: { ...s.liveMusic, status: "idle", track: null, error: "" } }));
  }, []);
  const setLiveMusicVolume = useCallback(async (level: number) => {
    const next = clampLiveMixLevel(level);
    const volume = liveMixLevelToAgoraVolume(next);
    const rtcEngine = engine();
    const publishResult = rtcEngine.adjustAudioMixingPublishVolume(volume);
    const playoutResult = rtcEngine.adjustAudioMixingPlayoutVolume(volume);
    if (publishResult < 0 || playoutResult < 0) throw new Error("PulseSoc Music level could not be updated.");
    setState((s) => ({ ...s, liveMusic: { ...s.liveMusic, musicVolume: next, error: "" } }));
  }, []);
  const setLiveMicVolume = useCallback(async (level: number) => {
    const next = clampLiveMixLevel(level);
    const result = engine().adjustRecordingSignalVolume(liveMixLevelToAgoraVolume(next, 100));
    if (result < 0) throw new Error("Microphone level could not be updated.");
    setState((s) => ({ ...s, liveMusic: { ...s.liveMusic, micVolume: next, error: "" } }));
  }, []);
  useEffect(() => () => { disconnect("unmounted").catch(() => undefined); }, [disconnect]);
  return { ...state, lifecycle: null, connect, disconnect, startBroadcast: connect, stopBroadcast: disconnect, joinAsViewer: connect, leaveViewer: disconnect, setMicrophoneEnabled, setCameraEnabled, setSpeakerEnabled, setRemoteAudioEnabled, showAudioRoutePicker, recheckAudio: async () => undefined, switchCamera, setStagePublisherCount, startLiveMusicMixing, pauseLiveMusicMixing, resumeLiveMusicMixing, stopLiveMusicMixing, setLiveMusicVolume, setLiveMicVolume, getLastConnectError: () => state.error, getAudioTrace: () => [] };
}
