import { Platform } from "react-native";
import { NATIVE_CALLKIT_ENABLED } from "../api/config";
import { acceptCall, declineCall, endCall, registerVoipPushToken, unregisterVoipPushToken } from "../api/calls";

// Stage 2 of the native call experience: CallKit + PushKit VoIP so an incoming call
// rings the iOS system call UI even when the app is backgrounded or killed (the Stage 1
// in-app ringer only runs while the app is foregrounded and polling getActiveCalls).
//
// The actual react-native-callkeep + react-native-voip-push-notification calls live behind
// an injectable NativeCallKitProvider. This keeps ALL orchestration (UUID<->callId mapping,
// flag gating, signaling calls, VoIP-token registration) in plain testable JS that builds
// WITHOUT the native pods present. The real provider is registered via
// setNativeCallKitProvider() once the pods, the `voip` background mode, and the VoIP push
// certificate (COINPLOTXAI APNs) are in place — see reports/native_callkit_voip_integration.md.

export type CallKitIncoming = {
  callId: string;
  /**
   * The server-issued CallKit UUID for this call (`PulseCall.call_uuid`).
   *
   * Required, and deliberately not defaulted. CallKit, the PushKit payload and the backend
   * all have to name the same call with the same UUID: a VoIP push reports the call natively
   * before JS is even running, so if the foreground poller then reported the *same* call
   * under a locally minted UUID, iOS would show two incoming calls and the answer for one of
   * them would resolve to a call id nothing on the server recognises. One issuer, one string.
   */
  callUuid: string;
  displayName: string;
  handle: string;
  hasVideo: boolean;
};

export type NativeCallKitProvider = {
  setup(): Promise<void> | void;
  displayIncomingCall(uuid: string, incoming: CallKitIncoming): void;
  setCallConnected(uuid: string): void;
  endCall(uuid: string): void;
  registerVoipToken(): void;
  onAnswer(cb: (uuid: string) => void): () => void;
  onEnd(cb: (uuid: string) => void): () => void;
  onVoipToken(cb: (token: string) => void): () => void;
};

export type CallKitCallbacks = {
  onAnswered?: (callId: string) => void;
  onEnded?: (callId: string) => void;
};

let provider: NativeCallKitProvider | null = null;
let initialized = false;
let subscriptions: Array<() => void> = [];
const uuidByCallId = new Map<string, string>();
const callIdByUuid = new Map<string, string>();
const answeredUuids = new Set<string>();
const reportedUuids = new Set<string>();
/**
 * The last PushKit token this process saw, kept so logout can name it explicitly.
 *
 * PushKit delivers the token through a `register` event that fires once per launch, so a
 * session where nobody called never sees one. That is why revocation identifies the device
 * by installation id too and treats this as an optimisation rather than a requirement.
 */
let lastVoipToken = "";

export function setNativeCallKitProvider(next: NativeCallKitProvider | null) {
  provider = next;
}

export function isNativeCallKitEnabled() {
  return Platform.OS === "ios" && NATIVE_CALLKIT_ENABLED && Boolean(provider);
}

export async function initNativeCallKit(callbacks: CallKitCallbacks = {}) {
  if (initialized || !isNativeCallKitEnabled() || !provider) return;
  initialized = true;
  await provider.setup();
  subscriptions.push(
    provider.onVoipToken((token) => {
      if (!token) return;
      lastVoipToken = token;
      registerVoipPushToken(token).catch(() => undefined);
    }),
    provider.onAnswer((uuid) => {
      const callId = callIdByUuid.get(uuid);
      if (!callId) return;
      answeredUuids.add(uuid);
      acceptCall(callId).catch(() => undefined);
      callbacks.onAnswered?.(callId);
    }),
    provider.onEnd((uuid) => {
      const answered = answeredUuids.has(uuid);
      const callId = forget(uuid);
      if (!callId) return;
      // Before answering, a CallKit end means the callee rejected → decline.
      // After answering, it's a hang-up → end. Backend is idempotent on terminal calls.
      (answered ? endCall(callId, "callkit_hangup") : declineCall(callId, "callkit_decline")).catch(() => undefined);
      callbacks.onEnded?.(callId);
    })
  );

  // Strictly after the listeners above exist. AppDelegate creates the PKPushRegistry at
  // launch, so by the time JS runs, `voipRegistration` is already registered and this call
  // takes the pod's early-return branch: it re-emits the *cached* token immediately and
  // synchronously. The pod drops an emission that has no listener attached — it diverts it
  // into `_delayedEvents` — so calling this first threw the token away.
  //
  // That race is why a device registers on its first launch after install and never again.
  // On first launch PushKit has no token yet, so the real `didUpdate` lands later, after JS
  // subscribed, and registration works. On every relaunch iOS hands over the cached token
  // before React Native is up, so both that emission and this replay were discarded, and
  // the device silently kept whatever token the server already had. Once the server revokes
  // that token the phone can never re-register, alert-push suppression stops applying, and
  // incoming calls permanently downgrade to a banner instead of CallKit.
  provider.registerVoipToken();
}

export function reportIncomingCallKit(incoming: CallKitIncoming) {
  if (!isNativeCallKitEnabled() || !provider || !incoming.callId || !incoming.callUuid) return;
  // A VoIP push reports the call natively, from AppDelegate, before this ever runs. The
  // foreground poller then finds the same call still ringing and arrives here — so on a
  // push-delivered call this is a *second* report of a call CallKit is already showing.
  //
  // Holding this call's UUID *before* being asked to report it is what identifies that
  // case. The only thing that records a mapping ahead of time is the push listener, and it
  // cannot run until AppDelegate has already put the call on screen. `reportedUuids` alone
  // does not cover this — recording a mapping never wrote to it, though the comment here
  // used to claim otherwise — so the pushed call fell straight through and was re-reported.
  // CallKit rejects the duplicate UUID, which is why nothing visibly doubled and why this
  // survived: only what the provider is *asked* to do reveals it.
  const reportedByPush = uuidByCallId.get(incoming.callId) === incoming.callUuid;
  const uuid = rememberCallKitCall(incoming.callId, incoming.callUuid);
  if (reportedByPush || reportedUuids.has(uuid)) return;
  reportedUuids.add(uuid);
  provider.displayIncomingCall(uuid, incoming);
}

/**
 * Record the server's UUID for a call without reporting it to CallKit.
 *
 * The push path needs this. When AppDelegate reports a call natively, JS holds no mapping
 * at all, so a later `markCallKitConnected(callId)` or `endCallKitCall(callId)` — both of
 * which only know the call id — would find nothing and silently do nothing, leaving a
 * CallKit call stuck ringing or stuck connected after the call is over. The provider calls
 * this as soon as the VoIP push reaches JS so the reverse lookup exists from that moment.
 *
 * Recording a mapping here is also what tells `reportIncomingCallKit` that this call is
 * already on screen, so the foreground poller does not report it a second time. That works
 * because this function is the only way a mapping can exist before a report, and no flag is
 * needed to say so — which matters, because a flag would have to be passed from the
 * provider, and the provider is the one file in this feature that jest cannot load.
 *
 * Returns the UUID so callers can use it directly.
 */
export function rememberCallKitCall(callId: string, callUuid: string) {
  const existing = uuidByCallId.get(callId);
  if (existing === callUuid) return existing;
  if (existing) forget(existing);
  uuidByCallId.set(callId, callUuid);
  callIdByUuid.set(callUuid, callId);
  return callUuid;
}

export function markCallKitConnected(callId: string) {
  if (!isNativeCallKitEnabled() || !provider || !callId) return;
  const uuid = uuidByCallId.get(callId);
  if (!uuid) return;
  provider.setCallConnected(uuid);
}

export function endCallKitCall(callId: string) {
  if (!isNativeCallKitEnabled() || !provider || !callId) return;
  const uuid = uuidByCallId.get(callId);
  if (!uuid) return;
  provider.endCall(uuid);
  forget(uuid);
}

/**
 * Release this device's VoIP registration. Called on sign-out.
 *
 * Deliberately gated on the platform alone — not on `isNativeCallKitEnabled()`, and not on
 * a provider being installed. A token is stored server-side by a *build*, but it is revoked
 * by whatever build happens to be running at sign-out, and those need not agree: a device
 * that registered while the flag was on and is now running with it off would, under a
 * flag-gated revoke, keep an active token forever. The backend would go on suppressing that
 * device's alert push — the one delivery path the disabled build still has — and the user
 * would stop being told about calls entirely. Revoking unconditionally on iOS costs one
 * idempotent request and cannot strand a registration.
 *
 * The local state is cleared regardless of what the server answers. A failed revoke leaves
 * a stale row the next registration will overwrite; a token kept in memory after sign-out
 * would be re-sent under the next account.
 */
export async function revokeVoipPushRegistration(reason = "logout") {
  const token = lastVoipToken;
  lastVoipToken = "";
  if (Platform.OS !== "ios") return;
  await unregisterVoipPushToken({ token, reason }).catch(() => undefined);
}

export function teardownNativeCallKit() {
  subscriptions.forEach((off) => {
    try {
      off();
    } catch {
      // ignore
    }
  });
  subscriptions = [];
  uuidByCallId.clear();
  callIdByUuid.clear();
  answeredUuids.clear();
  reportedUuids.clear();
  lastVoipToken = "";
  initialized = false;
}

function forget(uuid: string) {
  const callId = callIdByUuid.get(uuid);
  callIdByUuid.delete(uuid);
  answeredUuids.delete(uuid);
  reportedUuids.delete(uuid);
  if (callId) uuidByCallId.delete(callId);
  return callId;
}
