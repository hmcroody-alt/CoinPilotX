import { Platform } from "react-native";
import { NATIVE_CALLKIT_ENABLED } from "../api/config";
import { acceptCall, declineCall, endCall, registerVoipPushToken } from "../api/calls";

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
  provider.registerVoipToken();
  subscriptions.push(
    provider.onVoipToken((token) => {
      if (token) registerVoipPushToken(token).catch(() => undefined);
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
}

export function reportIncomingCallKit(incoming: CallKitIncoming) {
  if (!isNativeCallKitEnabled() || !provider || !incoming.callId) return;
  provider.displayIncomingCall(uuidFor(incoming.callId), incoming);
}

export function markCallKitConnected(callId: string) {
  if (!isNativeCallKitEnabled() || !provider || !callId) return;
  provider.setCallConnected(uuidFor(callId));
}

export function endCallKitCall(callId: string) {
  if (!isNativeCallKitEnabled() || !provider || !callId) return;
  const uuid = uuidByCallId.get(callId);
  if (!uuid) return;
  provider.endCall(uuid);
  forget(uuid);
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
  initialized = false;
}

function uuidFor(callId: string) {
  let uuid = uuidByCallId.get(callId);
  if (!uuid) {
    uuid = generateUuidV4();
    uuidByCallId.set(callId, uuid);
    callIdByUuid.set(uuid, callId);
  }
  return uuid;
}

function forget(uuid: string) {
  const callId = callIdByUuid.get(uuid);
  callIdByUuid.delete(uuid);
  answeredUuids.delete(uuid);
  if (callId) uuidByCallId.delete(callId);
  return callId;
}

// Non-cryptographic UUID for CallKit's local call identity (not security-sensitive).
function generateUuidV4() {
  const hex = "0123456789abcdef";
  let out = "";
  for (let i = 0; i < 36; i += 1) {
    if (i === 8 || i === 13 || i === 18 || i === 23) out += "-";
    else if (i === 14) out += "4";
    else if (i === 19) out += hex[(Math.floor(Math.random() * 16) & 0x3) | 0x8];
    else out += hex[Math.floor(Math.random() * 16)];
  }
  return out;
}
