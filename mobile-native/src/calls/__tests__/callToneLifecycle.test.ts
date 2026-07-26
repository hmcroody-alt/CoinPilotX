import type { PulseCall } from "../../api/calls";
import {
  INBOUND_RINGING_STATES,
  TERMINAL_CALL_STATES,
  isIncomingRingingCall,
  isTerminalCallStatus,
  shouldConnectCallMedia,
  shouldPlayUnavailablePrompt,
  shouldPlayRingback
} from "../callToneLifecycle";

const NONE = new Set<string>();
const ignored = (...ids: string[]) => new Set<string>(ids);

function inboundCall(overrides: Partial<PulseCall> = {}): PulseCall {
  return { call_id: "call-1", status: "ringing", ...overrides } as PulseCall;
}

describe("shouldPlayRingback (caller side)", () => {
  it("rings back while an outgoing call is ringing and media has not connected", () => {
    expect(shouldPlayRingback({ direction: "outgoing", mediaConnected: false, status: "ringing" })).toBe(true);
  });

  it("never rings back on the incoming side (that side plays ringtone)", () => {
    expect(shouldPlayRingback({ direction: "incoming", mediaConnected: false, status: "ringing" })).toBe(false);
  });

  it("stops the moment the secure media room connects", () => {
    expect(shouldPlayRingback({ direction: "outgoing", mediaConnected: true, status: "ringing" })).toBe(false);
  });

  it("does not ring back for any terminal status", () => {
    for (const status of TERMINAL_CALL_STATES) {
      expect(shouldPlayRingback({ direction: "outgoing", mediaConnected: false, status })).toBe(false);
    }
  });

  it("does not ring back before the backend has actually signaled ringing", () => {
    // A freshly-created (not-yet-ringing) call must stay silent — playing ringback
    // on 'created' or 'connecting' would be the false-ring regression.
    expect(shouldPlayRingback({ direction: "outgoing", mediaConnected: false, status: "created" })).toBe(false);
    expect(shouldPlayRingback({ direction: "outgoing", mediaConnected: false, status: "connecting" })).toBe(false);
    expect(shouldPlayRingback({ direction: "outgoing", mediaConnected: false, status: undefined })).toBe(false);
  });
});

describe("isIncomingRingingCall (recipient side)", () => {
  it("rings for a fresh inbound call with no known viewer", () => {
    expect(isIncomingRingingCall(inboundCall(), undefined, NONE)).toBe(true);
  });

  it("treats both 'created' and 'ringing' as ring-worthy", () => {
    for (const status of INBOUND_RINGING_STATES) {
      expect(isIncomingRingingCall(inboundCall({ status }), undefined, NONE)).toBe(true);
    }
  });

  it("does not ring for a call already dismissed/ignored", () => {
    expect(isIncomingRingingCall(inboundCall(), undefined, ignored("call-1"))).toBe(false);
  });

  it("does not ring for a connected or terminal status", () => {
    expect(isIncomingRingingCall(inboundCall({ status: "connected" }), undefined, NONE)).toBe(false);
    expect(isIncomingRingingCall(inboundCall({ status: "ended" }), undefined, NONE)).toBe(false);
  });

  it("rings when the current viewer is the callee participant", () => {
    const call = inboundCall({
      participants: [
        { user_id: 5, role: "caller" },
        { user_id: 9, role: "callee" }
      ]
    });
    expect(isIncomingRingingCall(call, 9, NONE)).toBe(true);
  });

  it("stays silent when the current viewer is the caller, not the callee", () => {
    const call = inboundCall({
      participant: { user_id: 5, role: "caller", status: "calling" }
    });
    expect(isIncomingRingingCall(call, 5, NONE)).toBe(false);
  });

  it("rings on a per-participant ringing status even without a callee role", () => {
    const call = inboundCall({ participant: { user_id: 9, role: "member", status: "ringing" } });
    expect(isIncomingRingingCall(call, 9, NONE)).toBe(true);
  });

  it("is a safe no-op for a null call or a call missing an id", () => {
    expect(isIncomingRingingCall(null, 9, NONE)).toBe(false);
    expect(isIncomingRingingCall(inboundCall({ call_id: "" }), 9, NONE)).toBe(false);
  });
});

describe("isTerminalCallStatus", () => {
  it("recognizes every terminal state and rejects live ones", () => {
    expect(isTerminalCallStatus("ended")).toBe(true);
    expect(isTerminalCallStatus("cancelled")).toBe(true);
    expect(isTerminalCallStatus("ringing")).toBe(false);
    expect(isTerminalCallStatus("connected")).toBe(false);
    expect(isTerminalCallStatus(undefined)).toBe(false);
  });
});

describe("shouldConnectCallMedia", () => {
  it("does not join the media room while the caller is only ringing the recipient", () => {
    expect(shouldConnectCallMedia({ direction: "outgoing", status: "ringing" })).toBe(false);
    expect(shouldConnectCallMedia({ direction: "outgoing", status: "created" })).toBe(false);
  });

  it("joins the media room after the recipient accepts or backend begins connecting", () => {
    expect(shouldConnectCallMedia({ direction: "outgoing", status: "accepted" })).toBe(true);
    expect(shouldConnectCallMedia({ direction: "outgoing", status: "connecting" })).toBe(true);
    expect(shouldConnectCallMedia({ direction: "incoming", status: "connecting" })).toBe(true);
  });

  it("never joins media for terminal calls", () => {
    for (const status of TERMINAL_CALL_STATES) {
      expect(shouldConnectCallMedia({ direction: "outgoing", status })).toBe(false);
    }
  });
});

describe("shouldPlayUnavailablePrompt", () => {
  it("prompts the outgoing caller when a call ends before connecting", () => {
    expect(shouldPlayUnavailablePrompt({ direction: "outgoing", everConnected: false, status: "missed" })).toBe(true);
    expect(shouldPlayUnavailablePrompt({ direction: "outgoing", everConnected: false, status: "expired" })).toBe(true);
    expect(shouldPlayUnavailablePrompt({ direction: "outgoing", everConnected: false, status: "failed" })).toBe(true);
  });

  it("does not play an unavailable prompt after a real connected call or on the incoming side", () => {
    expect(shouldPlayUnavailablePrompt({ direction: "outgoing", everConnected: true, status: "ended" })).toBe(false);
    expect(shouldPlayUnavailablePrompt({ direction: "incoming", everConnected: false, status: "missed" })).toBe(false);
  });
});
