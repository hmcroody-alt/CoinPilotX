jest.mock("../../api/config", () => ({
  NATIVE_CALLKIT_ENABLED: true,
  PULSE_API_BASE_URL: "https://pulsesoc.com"
}));

const mockAcceptCall = jest.fn().mockResolvedValue(undefined);
const mockDeclineCall = jest.fn().mockResolvedValue(undefined);
const mockEndCall = jest.fn().mockResolvedValue(undefined);
const mockRegisterVoipPushToken = jest.fn().mockResolvedValue(undefined);
const mockUnregisterVoipPushToken = jest.fn().mockResolvedValue(undefined);

jest.mock("../../api/calls", () => ({
  acceptCall: (...args: unknown[]) => mockAcceptCall(...args),
  declineCall: (...args: unknown[]) => mockDeclineCall(...args),
  endCall: (...args: unknown[]) => mockEndCall(...args),
  registerVoipPushToken: (...args: unknown[]) => mockRegisterVoipPushToken(...args),
  unregisterVoipPushToken: (...args: unknown[]) => mockUnregisterVoipPushToken(...args)
}));

import {
  CallKitIncoming,
  NativeCallKitProvider,
  endCallKitCall,
  initNativeCallKit,
  isNativeCallKitEnabled,
  markCallKitConnected,
  rememberCallKitCall,
  reportIncomingCallKit,
  revokeVoipPushRegistration,
  setNativeCallKitProvider,
  teardownNativeCallKit
} from "../callKitBridge";

function makeFakeProvider() {
  const handlers: { answer?: (uuid: string) => void; end?: (uuid: string) => void; token?: (token: string) => void } = {};
  const displayed: Array<{ uuid: string; incoming: CallKitIncoming }> = [];
  const provider: NativeCallKitProvider = {
    setup: jest.fn(),
    registerVoipToken: jest.fn(),
    displayIncomingCall: (uuid, incoming) => displayed.push({ uuid, incoming }),
    setCallConnected: jest.fn(),
    endCall: jest.fn(),
    onAnswer: (cb) => {
      handlers.answer = cb;
      return () => undefined;
    },
    onEnd: (cb) => {
      handlers.end = cb;
      return () => undefined;
    },
    onVoipToken: (cb) => {
      handlers.token = cb;
      return () => undefined;
    }
  };
  return { provider, handlers, displayed };
}

// The UUID is the server's, never the client's. See `CallKitIncoming.callUuid`.
const SERVER_UUID = "6b1f0d2e-9c7a-5f3b-8a41-2d0c7e5b91aa";
const incoming: CallKitIncoming = {
  callId: "call_123",
  callUuid: SERVER_UUID,
  displayName: "Ada",
  handle: "ada",
  hasVideo: false
};

afterEach(() => {
  teardownNativeCallKit();
  setNativeCallKitProvider(null);
  jest.clearAllMocks();
});

describe("callKitBridge without a native provider", () => {
  it("stays disabled and every entry point is a safe no-op", async () => {
    expect(isNativeCallKitEnabled()).toBe(false);
    await expect(initNativeCallKit()).resolves.toBeUndefined();
    expect(() => reportIncomingCallKit(incoming)).not.toThrow();
    expect(() => markCallKitConnected("call_123")).not.toThrow();
    expect(() => endCallKitCall("call_123")).not.toThrow();
    expect(mockAcceptCall).not.toHaveBeenCalled();
  });
});

describe("callKitBridge with an injected provider", () => {
  it("enables, registers the VoIP token, and forwards it to the backend", async () => {
    const { provider, handlers } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    expect(isNativeCallKitEnabled()).toBe(true);

    await initNativeCallKit();
    expect(provider.setup).toHaveBeenCalledTimes(1);
    expect(provider.registerVoipToken).toHaveBeenCalledTimes(1);

    handlers.token?.("voip-token-abc");
    expect(mockRegisterVoipPushToken).toHaveBeenCalledWith("voip-token-abc");
  });

  it("still receives a token that registerVoipToken replays synchronously", async () => {
    // MUTATION: call `provider.registerVoipToken()` before subscribing `onVoipToken`.
    //
    // This is the real pod's behaviour, not a hypothetical. AppDelegate creates the
    // PKPushRegistry at launch, so `voipRegistration` is already registered by the time JS
    // runs and takes its early-return branch, which re-emits the cached token immediately
    // and synchronously. An emission with no listener attached is diverted into the pod's
    // `_delayedEvents` and never reaches `register`.
    //
    // The old ordering therefore worked exactly once per install: on a first launch PushKit
    // has no token yet, so the real one arrives later, after JS subscribed. On every
    // relaunch the cached token arrives before React Native is up and was dropped — so a
    // device whose token the server had revoked could never register again, and its calls
    // silently downgraded from CallKit to an ordinary notification banner.
    //
    // Emitting from inside `registerVoipToken` is what makes this test able to fail; the
    // test above hand-fires the handler afterwards and passes under either ordering.
    const { provider } = makeFakeProvider();
    let emit: ((token: string) => void) | undefined;
    provider.onVoipToken = (cb) => {
      emit = cb;
      return () => undefined;
    };
    provider.registerVoipToken = jest.fn(() => emit?.("voip-token-replayed"));

    setNativeCallKitProvider(provider);
    await initNativeCallKit();

    expect(mockRegisterVoipPushToken).toHaveBeenCalledWith("voip-token-replayed");
  });

  it("accepts the call when CallKit answers", async () => {
    const { provider, handlers, displayed } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    const onAnswered = jest.fn();
    await initNativeCallKit({ onAnswered });

    reportIncomingCallKit(incoming);
    const { uuid } = displayed[0];
    handlers.answer?.(uuid);

    expect(mockAcceptCall).toHaveBeenCalledWith("call_123");
    expect(onAnswered).toHaveBeenCalledWith("call_123");
  });

  it("declines when CallKit ends before the call was answered", async () => {
    const { provider, handlers, displayed } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    await initNativeCallKit();

    reportIncomingCallKit(incoming);
    handlers.end?.(displayed[0].uuid);

    expect(mockDeclineCall).toHaveBeenCalledWith("call_123", "callkit_decline");
    expect(mockEndCall).not.toHaveBeenCalled();
  });

  it("hangs up when CallKit ends after the call was answered", async () => {
    const { provider, handlers, displayed } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    await initNativeCallKit();

    reportIncomingCallKit(incoming);
    const { uuid } = displayed[0];
    handlers.answer?.(uuid);
    handlers.end?.(uuid);

    expect(mockEndCall).toHaveBeenCalledWith("call_123", "callkit_hangup");
    expect(mockDeclineCall).not.toHaveBeenCalled();
  });

  it("marks the CallKit call connected and can end it", async () => {
    const { provider, displayed } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    await initNativeCallKit();

    reportIncomingCallKit(incoming);
    const { uuid } = displayed[0];
    markCallKitConnected("call_123");
    expect(provider.setCallConnected).toHaveBeenCalledWith(uuid);

    endCallKitCall("call_123");
    expect(provider.endCall).toHaveBeenCalledWith(uuid);
  });
});

// The identity rules. CallKit, the PushKit payload and the backend all have to name one
// call with one string; every test below fails if the bridge ever mints a UUID of its own
// again, which is what it used to do.
describe("call identity", () => {
  it("reports the call under the server's UUID rather than minting one", async () => {
    const { provider, displayed } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    await initNativeCallKit();

    reportIncomingCallKit(incoming);
    expect(displayed).toHaveLength(1);
    expect(displayed[0].uuid).toBe(SERVER_UUID);
  });

  it("skips a call the server gave no UUID for", async () => {
    const { provider, displayed } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    await initNativeCallKit();

    // An older backend, or a payload that lost the field. Reporting it under an invented
    // UUID would ring a call whose answer resolves to a call id the server cannot match,
    // which is worse than not ringing: the user picks up and nothing happens.
    reportIncomingCallKit({ ...incoming, callUuid: "" });
    expect(displayed).toHaveLength(0);
  });

  it("does not ring twice when the poller re-reports a call PushKit already displayed", async () => {
    const { provider, displayed } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    await initNativeCallKit();

    // getActiveCalls polls on an interval, so a call that stays ringing arrives here
    // repeatedly. Each repeat used to reset the CallKit ringer.
    reportIncomingCallKit(incoming);
    reportIncomingCallKit(incoming);
    reportIncomingCallKit(incoming);
    expect(displayed).toHaveLength(1);
  });

  it("rings again for a genuinely new call after the first one ends", async () => {
    const { provider, handlers, displayed } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    await initNativeCallKit();

    reportIncomingCallKit(incoming);
    handlers.end?.(SERVER_UUID);

    const second = { ...incoming, callId: "call_456", callUuid: "11112222-3333-4444-5555-666677778888" };
    reportIncomingCallKit(second);
    expect(displayed).toHaveLength(2);
    expect(displayed[1].uuid).toBe(second.callUuid);
  });

  it("maps a push-reported call so it can later be connected and ended by call id", async () => {
    const { provider } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    await initNativeCallKit();

    // The PushKit path: AppDelegate reported the call to CallKit natively, before JS was
    // running. Nothing is displayed from JS — that work is done — but without the mapping
    // the id-addressed operations below would find nothing and silently do nothing,
    // stranding a CallKit call on screen after the call is over.
    rememberCallKitCall("call_789", "aaaabbbb-cccc-dddd-eeee-ffff00001111");

    markCallKitConnected("call_789");
    expect(provider.setCallConnected).toHaveBeenCalledWith("aaaabbbb-cccc-dddd-eeee-ffff00001111");

    endCallKitCall("call_789");
    expect(provider.endCall).toHaveBeenCalledWith("aaaabbbb-cccc-dddd-eeee-ffff00001111");
  });

  it("does not invent a CallKit call for an id it has never seen", async () => {
    const { provider } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    await initNativeCallKit();

    markCallKitConnected("call_never_reported");
    expect(provider.setCallConnected).not.toHaveBeenCalled();
  });

  it("does not re-report a pushed call the poller then finds still ringing", async () => {
    // MUTATION: delete the `reportedByPush` check from `reportIncomingCallKit`.
    //
    // The real sequence on a push-delivered call. AppDelegate reports it to CallKit before
    // JS exists; the provider's `notification` listener records the mapping — which is
    // exactly what `rememberCallKitCall` does here — and then the foreground poller finds
    // the same call still ringing and reports it again, a second report of a call already
    // on screen.
    //
    // `reportedUuids` did not cover this, because recording a mapping never wrote to it, so
    // the duplicate went straight through. CallKit rejects a repeated UUID, so nothing
    // visibly doubled — which is why it survived, and why this asserts on what the provider
    // was *asked* to do rather than on what the user would have seen.
    const { provider, displayed } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    await initNativeCallKit();

    rememberCallKitCall(incoming.callId, incoming.callUuid);
    reportIncomingCallKit(incoming);

    expect(displayed).toHaveLength(0);
  });

  it("still displays a call that arrived without a push", async () => {
    // MUTATION: treat any existing mapping as proof the call is already on screen.
    //
    // `reportIncomingCallKit` records the mapping itself, so a check that cannot tell "the
    // push recorded this" from "I just recorded this" would mark every call and then take
    // its own early return. Every call with no VoIP push — no token, push refused, an older
    // build — would stop ringing in-app entirely. That is a far worse failure than the
    // duplicate above, so it is pinned next to it.
    const { provider, displayed } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    await initNativeCallKit();

    reportIncomingCallKit(incoming);

    expect(displayed).toHaveLength(1);
    expect(displayed[0].uuid).toBe(SERVER_UUID);
  });
});

describe("sign-out", () => {
  it("revokes the VoIP registration, naming the token it saw", async () => {
    const { provider, handlers } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    await initNativeCallKit();
    handlers.token?.("voip-token-abc");

    await revokeVoipPushRegistration("logout");
    expect(mockUnregisterVoipPushToken).toHaveBeenCalledWith({ token: "voip-token-abc", reason: "logout" });
  });

  it("still revokes when this session never received a token", async () => {
    const { provider } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    await initNativeCallKit();

    // PushKit only emits `register` once per launch, so signing out of a session in which
    // nobody called leaves JS with no token at all. The request still has to go — the
    // backend identifies the device by installation id — or the server keeps suppressing
    // this handset's alert push forever.
    await revokeVoipPushRegistration("logout");
    expect(mockUnregisterVoipPushToken).toHaveBeenCalledWith({ token: "", reason: "logout" });
  });

  it("forgets the token so it cannot be re-sent under the next account", async () => {
    const { provider, handlers } = makeFakeProvider();
    setNativeCallKitProvider(provider);
    await initNativeCallKit();
    handlers.token?.("voip-token-abc");

    await revokeVoipPushRegistration("logout");
    await revokeVoipPushRegistration("logout");
    expect(mockUnregisterVoipPushToken).toHaveBeenLastCalledWith({ token: "", reason: "logout" });
  });
});
