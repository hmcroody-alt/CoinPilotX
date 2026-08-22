/**
 * CallSessionStore — the ONE owner of a live call's Agora engine and lifecycle.
 *
 * Before this store the engine lived in a hook-local ref inside
 * `useAgoraCallRoom`, and the hook's unmount effect called
 * `disconnect("unmounted")`. That made the call co-terminous with the Call
 * *screen*: navigating anywhere in the app (or tapping Minimize, which is
 * `goBack()`) tore down the media room while the backend still showed the call
 * as active — a ghost call for the other participant. App Review requires the
 * opposite: a call survives in-app navigation and ends only on an explicit
 * hang-up, a terminal backend status, or an authoritative failure.
 *
 * So the session moved to module scope, following the app's store idiom
 * (module singleton + listener Set + `useSyncExternalStore`, see
 * `core/unreadCounts.ts`). The Call screen and the minimized banner are both
 * plain consumers; mounting or unmounting either changes nothing about media.
 *
 * Real-time audio policy compliance (docs/realtime_audio_change_policy.md):
 * this file is the SAME single publication path that `useAgoraCallRoom` was —
 * the engine code below is lifted from it verbatim, not duplicated. There is
 * still exactly one engine, one microphone publication, and no screen-level
 * audio-session configuration anywhere in this module. The public hook surface
 * (`useNativeCallRoom` → `useAgoraCallRoom`) is unchanged.
 *
 * Remote hang-up latency: the backend stays authoritative (status polling,
 * exactly as before), but Agora's `onUserOffline` / connection-failure events
 * are used as a FAST-PATH HINT that triggers one immediate status re-fetch, so
 * the callee learns about a hang-up in well under a second instead of waiting
 * for the next 4.2s poll tick.
 */
import { useSyncExternalStore } from "react";
import { AppState, AppStateStatus, Platform } from "react-native";
import type { IRtcEngine, IRtcEngineEventHandler } from "react-native-agora";
import {
  endCall,
  getCallStatus,
  markCallConnected,
  PulseCall,
  PulseCallJoin,
  PulseCallType,
  requestCallJoinToken
} from "../api/calls";
import { reportPresenceActivity } from "../api/presenceSession";
import { claimMediaPlayback, releaseMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { stopVoiceMessagePlayback } from "../core/voiceMessagePlayback";
import { playCallCue, stopCallTone } from "./callSignalMedia";
import { endCallKitCall, markCallKitConnected } from "./callKitBridge";
import { isTerminalCallStatus, shouldConnectCallMedia, shouldPlayUnavailablePrompt } from "./callToneLifecycle";
import { CallSyncSource, traceCallSync } from "./callSyncTrace";

export const CALL_STATUS_REFRESH_MS = 4200;
export const CALL_RINGING_STATUS_REFRESH_MS = 1100;

/** Media-room state, exactly the shape `useAgoraCallRoom` always exposed. */
const baseMediaState = {
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

export type CallDirection = "incoming" | "outgoing" | "";

export type CallSessionSnapshot = typeof baseMediaState & {
  /** Backend call id ("" until the outgoing start call returns one). */
  callId: string;
  /** Latest authoritative call record from the backend. */
  call: PulseCall | null;
  direction: CallDirection;
  callType: PulseCallType;
  conversationId?: number;
  /** Display name captured from navigation params / backend participants. */
  title: string;
  /** True from beginCallSession until explicit end / terminal / failure. */
  sessionActive: boolean;
  /** True while the Call screen is focused; the minimized banner hides then. */
  callScreenFocused: boolean;
  /** Epoch ms when media first connected (drives the duration readouts). */
  connectedAtMs: number;
  everConnected: boolean;
};

const initialSnapshot: CallSessionSnapshot = {
  ...baseMediaState,
  callId: "",
  call: null,
  direction: "",
  callType: "audio",
  conversationId: undefined,
  title: "",
  sessionActive: false,
  callScreenFocused: false,
  connectedAtMs: 0,
  everConnected: false
};

let snapshot: CallSessionSnapshot = { ...initialSnapshot };
const listeners = new Set<() => void>();

/* Engine ownership — module scope, so navigation cannot touch it. */
let engine: IRtcEngine | null = null;
let engineHandler: IRtcEngineEventHandler | null = null;

/* Session plumbing. */
let pollTimer: ReturnType<typeof setInterval> | null = null;
let currentPollMs = 0;
let appStateSubscription: { remove: () => void } | null = null;
let appState: AppStateStatus = AppState.currentState;
let joinRequested = false;
let refreshInFlight: Promise<PulseCall | null> | null = null;
let terminalHandledFor = "";
let mediaLeaseHeld = false;
/** Epoch ms the current session opened; the zero point for acceptance latency. */
let sessionOpenedAtMs = 0;

/** Local role, derived from direction. Used only for telemetry. */
function localRole(): "caller" | "callee" | "unknown" {
  if (snapshot.direction === "outgoing") return "caller";
  if (snapshot.direction === "incoming") return "callee";
  return "unknown";
}

function traceStatus(prevStatus: string, nextStatus: string, source: CallSyncSource, detail?: string) {
  traceCallSync({
    callId: snapshot.callId,
    role: localRole(),
    prevStatus,
    nextStatus,
    source,
    elapsedMs: sessionOpenedAtMs ? Date.now() - sessionOpenedAtMs : 0,
    detail
  });
}

function emit() {
  listeners.forEach((listener) => listener());
}

function setSnapshot(patch: Partial<CallSessionSnapshot>) {
  snapshot = { ...snapshot, ...patch };
  emit();
}

export function getCallSession(): CallSessionSnapshot {
  return snapshot;
}

export function subscribeCallSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

/** React binding — the Call screen and the minimized banner both use this. */
export function useCallSession(): CallSessionSnapshot {
  return useSyncExternalStore(subscribeCallSession, getCallSession, getCallSession);
}

export function addAgoraRemoteUid(remoteUids: number[], remoteUid: number) {
  return remoteUids.includes(remoteUid) ? remoteUids : [...remoteUids, remoteUid];
}

export function removeAgoraRemoteUid(remoteUids: number[], remoteUid: number) {
  return remoteUids.filter((uid) => uid !== remoteUid);
}

/* ------------------------------------------------------------------ *
 * Session lifecycle
 * ------------------------------------------------------------------ */

export type CallSessionInit = {
  callId?: string;
  conversationId?: number;
  direction?: CallDirection;
  callType?: PulseCallType;
  title?: string;
};

/**
 * Open (or re-attach to) the call session. Called by the Call screen on mount.
 * Re-attaching to the already-active call is a no-op apart from refreshing the
 * display params, which is what makes "tap the banner to return" seamless.
 */
export function beginCallSession(init: CallSessionInit = {}) {
  const incomingId = init.callId ? String(init.callId) : "";
  if (snapshot.sessionActive) {
    const sameCall = incomingId && snapshot.callId && incomingId === snapshot.callId;
    const sameConversation = !incomingId && !snapshot.callId && init.conversationId && init.conversationId === snapshot.conversationId;
    if (sameCall || sameConversation || (!incomingId && snapshot.callId)) {
      // Re-attach: keep the live engine, only refresh presentation params.
      setSnapshot({
        title: init.title || snapshot.title,
        direction: snapshot.direction || init.direction || "",
        conversationId: snapshot.conversationId ?? init.conversationId
      });
      ensurePolling();
      return;
    }
    // A different call is starting; end the previous session locally first.
    finalizeCallSession("replaced_session");
    clearCallSession();
  }
  snapshot = {
    ...initialSnapshot,
    supported: snapshot.supported,
    callId: incomingId,
    conversationId: init.conversationId,
    direction: init.direction || "",
    callType: init.callType === "video" ? "video" : "audio",
    title: init.title || "",
    sessionActive: true,
    callScreenFocused: snapshot.callScreenFocused
  };
  terminalHandledFor = "";
  joinRequested = false;
  sessionOpenedAtMs = Date.now();
  emit();
  stopVoiceMessagePlayback("call_opened").catch(() => undefined);
  if (!mediaLeaseHeld) {
    mediaLeaseHeld = true;
    claimMediaPlayback({ id: "native-call", kind: "call", pause: () => undefined }).catch(() => undefined);
  }
  ensureAppStateSubscription();
  ensurePolling();
}

/**
 * Merge an authoritative backend call record into the session. Restarts the
 * poll cadence, handles terminal statuses, and auto-connects media when the
 * signaling state says the media room should be live.
 */
export function adoptCallSnapshot(call: PulseCall | null | undefined, source: CallSyncSource = "poll") {
  if (!call || !call.call_id) return;
  const callId = String(call.call_id);
  const nextType: PulseCallType = call.call_type === "video" ? "video" : snapshot.callType;
  const prevStatus = String(snapshot.call?.status || "");
  setSnapshot({ call, callId, callType: nextType, conversationId: call.conversation_id ?? snapshot.conversationId });
  const status = String(call.status || "");
  // Only real transitions are traced; a steady-state poll would otherwise emit
  // one line per second for the entire call and bury the transition we care about.
  if (status !== prevStatus) traceStatus(prevStatus, status, source);
  if (isTerminalCallStatus(status)) {
    handleTerminalStatus(status);
    return;
  }
  ensurePolling();
  if (snapshot.sessionActive && shouldConnectCallMedia({ direction: snapshot.direction || undefined, status })) {
    ensureCallMediaConnected().catch(() => undefined);
  }
}

/**
 * One authoritative status fetch. Deduped so the fast-path hint and the poll
 * tick can never stampede the endpoint. A 404/410 means the backend no longer
 * knows the call — that is treated as terminal, never as a transient error, so
 * a ghost session cannot outlive its call record.
 */
export function refreshCallSessionStatus(source: CallSyncSource = "poll"): Promise<PulseCall | null> {
  if (!snapshot.callId) return Promise.resolve(null);
  if (refreshInFlight) return refreshInFlight;
  refreshInFlight = (async () => {
    try {
      const next = await getCallStatus(snapshot.callId);
      adoptCallSnapshot(next, source);
      return next;
    } catch (error) {
      const status = (error as { status?: number })?.status;
      if (status === 404 || status === 410) {
        handleTerminalStatus("ended");
        return null;
      }
      // The caller's ONLY acceptance signal is this poll: while ringing it is not
      // in the media room, so no RTC event can tell it the callee answered. A
      // silently dropped rejection here is therefore indistinguishable from "the
      // callee never picked up", which is exactly how a stuck ringing caller used
      // to present. Record the failure class so the stall is diagnosable.
      traceStatus(String(snapshot.call?.status || ""), "", "poll_error", pollFailureClass(error));
      throw error;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

/** A coarse, non-identifying failure class. Never the server's message body. */
function pollFailureClass(error: unknown): string {
  const status = Number((error as { status?: number })?.status || 0);
  if (status > 0) return `http_${status}`;
  const name = String((error as { name?: string })?.name || "");
  if (/abort/i.test(name)) return "aborted";
  return "network";
}

function pollNow(source: CallSyncSource = "poll") {
  refreshCallSessionStatus(source).catch(() => undefined);
}

function desiredPollMs() {
  return String(snapshot.call?.status || "").toLowerCase() === "ringing"
    ? CALL_RINGING_STATUS_REFRESH_MS
    : CALL_STATUS_REFRESH_MS;
}

/**
 * Foreground test for the poll gate. Deliberately asks "not backgrounded"
 * rather than "=== active": `AppState.currentState` is null/undefined until the
 * first change event on some cold starts, and an `=== "active"` gate would then
 * suppress every tick until the app was backgrounded and restored — an active
 * call whose status silently stops updating.
 */
function appIsForegrounded() {
  return !/inactive|background/.test(String(appState || ""));
}

function ensurePolling() {
  if (!snapshot.sessionActive || !snapshot.callId) return;
  const ms = desiredPollMs();
  if (pollTimer && currentPollMs === ms) return;
  stopPolling();
  currentPollMs = ms;
  pollTimer = setInterval(() => {
    // Same app-state gate the screen-scoped poll always had.
    if (appIsForegrounded()) pollNow();
  }, ms);
}

function stopPolling() {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = null;
  currentPollMs = 0;
}

function ensureAppStateSubscription() {
  if (appStateSubscription) return;
  appStateSubscription = AppState.addEventListener("change", (nextState) => {
    const wasBackgrounded = /inactive|background/.test(appState);
    appState = nextState;
    if (wasBackgrounded && nextState === "active" && snapshot.sessionActive) pollNow("app_foreground");
  });
}

function releaseAppStateSubscription() {
  appStateSubscription?.remove();
  appStateSubscription = null;
}

/** The single place the connected transition is observed (cue + CallKit). */
function noteConnectedTransition() {
  if (snapshot.everConnected) return;
  setSnapshot({ everConnected: true, connectedAtMs: snapshot.connectedAtMs || Date.now() });
  stopCallTone().catch(() => undefined);
  playCallCue("connect").catch(() => undefined);
  if (snapshot.callId) markCallKitConnected(snapshot.callId);
}

/**
 * Terminal backend status: release everything, exactly once per call. This is
 * one of only three paths that release the engine (the others being an
 * explicit local hang-up/decline and an authoritative connect failure) —
 * screen unmount is deliberately NOT one of them.
 */
function handleTerminalStatus(status: string) {
  if (!snapshot.callId || terminalHandledFor === snapshot.callId) {
    setSnapshot({ sessionActive: false });
    return;
  }
  terminalHandledFor = snapshot.callId;
  stopCallTone().catch(() => undefined);
  endCallKitCall(snapshot.callId);
  if (
    snapshot.everConnected ||
    shouldPlayUnavailablePrompt({ direction: snapshot.direction || undefined, everConnected: snapshot.everConnected, status })
  ) {
    playCallCue("disconnect").catch(() => undefined);
  }
  disconnectCallMedia(`backend_${status || "ended"}`).catch(() => undefined);
  teardownSessionPlumbing();
  setSnapshot({ sessionActive: false });
}

/**
 * Local, explicit end: report to the backend, then tear down. Used by the End
 * Call button and the minimized banner's hang-up control.
 */
export async function hangupCallSession(reason = "native_hangup") {
  const callId = snapshot.callId;
  await stopCallTone().catch(() => undefined);
  try {
    if (callId) await endCall(callId, reason);
  } finally {
    finalizeCallSession(reason);
  }
}

/**
 * Local teardown WITHOUT calling the end endpoint (decline already told the
 * backend; failure paths have nothing to tell it).
 */
export function finalizeCallSession(reason: string) {
  const callId = snapshot.callId;
  if (callId && terminalHandledFor === callId) {
    // Another path already tore this exact call down. Local hang-up and a
    // terminal poll result routinely land in the same tick (the hang-up awaits
    // the end endpoint while a poll is in flight); running cleanup twice fires
    // a second endCallKitCall, which can end a *newer* CallKit call.
    setSnapshot({ sessionActive: false });
    return;
  }
  if (callId) terminalHandledFor = callId;
  stopCallTone().catch(() => undefined);
  if (callId) endCallKitCall(callId);
  disconnectCallMedia(reason).catch(() => undefined);
  teardownSessionPlumbing();
  setSnapshot({ sessionActive: false });
}

function teardownSessionPlumbing() {
  stopPolling();
  releaseAppStateSubscription();
  joinRequested = false;
  if (mediaLeaseHeld) {
    mediaLeaseHeld = false;
    releaseMediaPlayback("native-call").catch(() => undefined);
  }
}

/** Full reset back to the empty snapshot (after the terminal UI is dismissed). */
export function clearCallSession() {
  if (engine) disconnectCallMedia("session_cleared").catch(() => undefined);
  teardownSessionPlumbing();
  terminalHandledFor = "";
  snapshot = { ...initialSnapshot, supported: snapshot.supported, callScreenFocused: snapshot.callScreenFocused };
  emit();
}

export function setCallScreenFocused(focused: boolean) {
  if (snapshot.callScreenFocused === focused) return;
  setSnapshot({ callScreenFocused: focused });
}

/**
 * Call screen unmount hook. The session survives — that is the entire point —
 * but a screen that leaves behind no live call (start failed, or the call is
 * already terminal) must not leave a stale snapshot for the banner to render.
 */
export function handleCallScreenUnmount() {
  setCallScreenFocused(false);
  if (!snapshot.sessionActive) clearCallSession();
}

/* ------------------------------------------------------------------ *
 * Media room — lifted verbatim from useAgoraCallRoom (hook-local refs
 * became the module-scope `engine` / `engineHandler` above; setState
 * became setSnapshot). Backend signaling remains authoritative.
 * ------------------------------------------------------------------ */

/**
 * Join the media room for the current session. Idempotent per session: the
 * `joinRequested` latch plays the role the screen's ref used to.
 */
export async function ensureCallMediaConnected() {
  const call = snapshot.call;
  const callId = snapshot.callId;
  if (!callId || joinRequested || snapshot.connected || snapshot.connecting) return;
  joinRequested = true;
  const targetType = snapshot.callType;
  try {
    const join = call?.join?.token ? call.join : await requestCallJoinToken(callId);
    const joined = await connectCallMedia(join, {
      video: targetType === "video",
      refreshCredentials: () => requestCallJoinToken(callId)
    });
    if (!joined) throw new Error("The secure media room could not connect.");
    await markCallConnected(callId, {
      native_state: "connected",
      platform: Platform.OS,
      media: targetType
    }).catch(() => undefined);
  } catch (joinError) {
    joinRequested = false;
    setSnapshot({
      error: joinError instanceof Error ? joinError.message : "The secure media room could not connect.",
      diagnosticCode: snapshot.diagnosticCode || "CALL_JOIN_FAILED"
    });
    throw joinError;
  }
}

export async function disconnectCallMedia(reason = "local_disconnect") {
  const current = engine;
  engine = null;
  if (current) {
    if (engineHandler) current.unregisterEventHandler(engineHandler);
    current.leaveChannel();
    current.release();
  }
  engineHandler = null;
  joinRequested = false;
  reportPresenceActivity("idle", "").catch(() => undefined);
  setSnapshot({ ...baseMediaState, supported: snapshot.supported, disconnectReason: reason, diagnosticCode: reason });
}

export async function connectCallMedia(
  join: PulseCallJoin,
  options: { video?: boolean; refreshCredentials?: () => Promise<PulseCallJoin> } = {}
) {
  if (Platform.OS === "web") return false;
  if (!join.token || !join.app_id || !join.channel_name || !join.uid) {
    setSnapshot({ error: "PulseSoc did not return usable Agora credentials.", diagnosticCode: "AGORA_CREDENTIALS_MISSING" });
    return false;
  }
  if (engine) await disconnectCallMedia("replaced_room");
  setSnapshot({
    ...baseMediaState,
    supported: snapshot.supported,
    connecting: true,
    connectionState: "connecting",
    localUid: Number(join.uid)
  });
  try {
    // Lazy require (not dynamic import): Metro inlines dynamic imports anyway,
    // and jest's CJS environment cannot evaluate import() without extra flags.
    // The Platform.OS === "web" guard above keeps this off the web bundle path.
    const agora = require("react-native-agora") as typeof import("react-native-agora");
    const nextEngine = agora.createAgoraRtcEngine();
    engine = nextEngine;
    nextEngine.initialize({ appId: join.app_id });
    nextEngine.enableAudio();
    if (options.video) {
      nextEngine.enableVideo();
      nextEngine.startPreview();
    }
    let renewalPromise: Promise<void> | null = null;
    const renew = () => {
      if (renewalPromise || !options.refreshCredentials) return renewalPromise;
      renewalPromise = options.refreshCredentials().then((next) => {
        if (next.provider !== "agora" || !next.token || next.channel_name !== join.channel_name || Number(next.uid) !== Number(join.uid)) {
          throw new Error("PulseSoc returned mismatched Agora renewal credentials.");
        }
        const renewed = nextEngine.renewToken(next.token);
        if (renewed < 0) throw new Error(`Agora rejected the renewed token (${renewed}).`);
        setSnapshot({ error: "", diagnosticCode: "" });
      }).catch(() => {
        setSnapshot({ error: "Secure call access could not be refreshed. Reconnect before the token expires.", diagnosticCode: "AGORA_TOKEN_RENEWAL_FAILED" });
      }).finally(() => { renewalPromise = null; });
      return renewalPromise;
    };
    const handler: IRtcEngineEventHandler = {
      onJoinChannelSuccess: (_connection, _elapsed) => {
        setSnapshot({
          connecting: false,
          connected: true,
          reconnecting: false,
          connectionState: "connected",
          participantCount: Math.max(1, snapshot.participantCount),
          localAudioTrackCount: 1,
          audioEnabled: true,
          videoEnabled: Boolean(options.video),
          error: "",
          diagnosticCode: ""
        });
        noteConnectedTransition();
      },
      onConnectionStateChanged: (_connection, connectionState) => {
        const reconnecting = connectionState === agora.ConnectionStateType.ConnectionStateReconnecting;
        const connected = connectionState === agora.ConnectionStateType.ConnectionStateConnected;
        const failed = connectionState === agora.ConnectionStateType.ConnectionStateFailed;
        setSnapshot({
          connected,
          reconnecting,
          connecting: !connected && !reconnecting && !failed,
          connectionState: failed ? "failed" : reconnecting ? "reconnecting" : connected ? "connected" : "connecting",
          reconnectCount: reconnecting && !snapshot.reconnecting ? snapshot.reconnectCount + 1 : snapshot.reconnectCount,
          diagnosticCode: failed ? "AGORA_CONNECTION_FAILED" : snapshot.diagnosticCode
        });
        if (connected) noteConnectedTransition();
        // Fast-path hint: a failed media link often means the far end is gone.
        // The backend stays authoritative — this only re-fetches status NOW.
        if (failed) pollNow("rtc_hint");
      },
      onUserJoined: (_connection, remoteUid) => {
        const remoteUids = addAgoraRemoteUid(snapshot.remoteUids, remoteUid);
        setSnapshot({
          remoteUid: remoteUids[0] || 0,
          remoteUids,
          participantCount: 1 + remoteUids.length,
          remoteAudioTrackCount: remoteUids.length,
          remoteAudioAvailable: remoteUids.length > 0
        });
      },
      onUserOffline: (_connection, remoteUid) => {
        const remoteUids = removeAgoraRemoteUid(snapshot.remoteUids, remoteUid);
        setSnapshot({
          remoteUid: remoteUids[0] || 0,
          remoteUids,
          participantCount: 1 + remoteUids.length,
          remoteAudioTrackCount: remoteUids.length,
          remoteAudioAvailable: remoteUids.length > 0
        });
        // Fast-path hint: the other participant left the media room. Re-fetch
        // the authoritative status immediately instead of waiting for a poll
        // tick, so a remote hang-up ends this side in sub-second time.
        pollNow("rtc_hint");
      },
      onTokenPrivilegeWillExpire: () => {
        setSnapshot({ diagnosticCode: "AGORA_TOKEN_RENEWAL_REQUIRED", error: "Refreshing secure call access…" });
        void renew();
      },
      onRequestToken: () => {
        setSnapshot({ diagnosticCode: "AGORA_TOKEN_EXPIRED", error: "Restoring secure call access…" });
        void renew();
      },
      onError: (errorCode) => setSnapshot({ error: `Agora media error (${errorCode}).`, diagnosticCode: `AGORA_${errorCode}` })
    };
    engineHandler = handler;
    nextEngine.registerEventHandler(handler);
    const result = nextEngine.joinChannel(join.token, join.channel_name, Number(join.uid), {
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
    await disconnectCallMedia("connect_failed");
    setSnapshot({
      connectionState: "failed",
      error: error instanceof Error ? error.message : "Agora call connection failed.",
      diagnosticCode: "AGORA_CONNECT_FAILED"
    });
    return false;
  }
}

function requireEngine() {
  if (!engine) throw new Error("Call media is not connected.");
  return engine;
}

export async function setCallMicrophoneEnabled(enabled: boolean) {
  requireEngine().muteLocalAudioStream(!enabled);
  setSnapshot({ audioEnabled: enabled });
}

export async function setCallCameraEnabled(enabled: boolean) {
  requireEngine().muteLocalVideoStream(!enabled);
  setSnapshot({ videoEnabled: enabled });
}

export async function setCallSpeakerEnabled(enabled: boolean) {
  requireEngine().setEnableSpeakerphone(enabled);
  setSnapshot({ speakerEnabled: enabled });
}

export async function showCallAudioRoutePicker(): Promise<void> {
  throw new Error("Use the iOS system audio-route control for Agora calls.");
}

export async function switchCallCamera() {
  requireEngine().switchCamera();
}

/** Test-only reset of the module singleton. */
export function __resetCallSessionForTests() {
  stopPolling();
  releaseAppStateSubscription();
  engine = null;
  engineHandler = null;
  joinRequested = false;
  refreshInFlight = null;
  terminalHandledFor = "";
  mediaLeaseHeld = false;
  appState = AppState.currentState;
  snapshot = { ...initialSnapshot };
  listeners.clear();
}
