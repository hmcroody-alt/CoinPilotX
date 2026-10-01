jest.mock("../../api/config", () => ({
  NATIVE_CALLKIT_ENABLED: true,
  PULSE_API_BASE_URL: "https://pulsesoc.com"
}));

const mockAcceptCall = jest.fn().mockResolvedValue(undefined);
const mockDeclineCall = jest.fn().mockResolvedValue(undefined);
const mockEndCall = jest.fn().mockResolvedValue(undefined);
const mockRegisterVoipPushToken = jest.fn().mockResolvedValue(undefined);

jest.mock("../../api/calls", () => ({
  acceptCall: (...args: unknown[]) => mockAcceptCall(...args),
  declineCall: (...args: unknown[]) => mockDeclineCall(...args),
  endCall: (...args: unknown[]) => mockEndCall(...args),
  registerVoipPushToken: (...args: unknown[]) => mockRegisterVoipPushToken(...args)
}));

import {
  CallKitIncoming,
  NativeCallKitProvider,
  endCallKitCall,
  initNativeCallKit,
  isNativeCallKitEnabled,
  markCallKitConnected,
  reportIncomingCallKit,
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

const incoming: CallKitIncoming = { callId: "call_123", displayName: "Ada", handle: "ada", hasVideo: false };

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
