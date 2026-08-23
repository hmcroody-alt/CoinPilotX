/**
 * The caller must detect the callee's acceptance on its own, with no second action.
 *
 * While the call is ringing the caller has NOT joined the Agora channel, so no
 * RTC event can reach it — `onUserJoined` cannot fire for a client that is not
 * in the room. The authoritative status poll is therefore the caller's single
 * and only acceptance signal, which is why these tests assert on the poll rather
 * than on any media event, and why the failure path is asserted just as hard as
 * the happy path: a silently dropped poll rejection is indistinguishable, from
 * the user's seat, from "they never picked up".
 */
jest.mock("react-native-agora", () => {
  const engine = {
    initialize: jest.fn(),
    enableAudio: jest.fn(),
    enableVideo: jest.fn(),
    startPreview: jest.fn(),
    registerEventHandler: jest.fn(),
    unregisterEventHandler: jest.fn(),
    joinChannel: jest.fn(() => 0),
    leaveChannel: jest.fn(),
    release: jest.fn(),
    renewToken: jest.fn(() => 0),
    muteLocalAudioStream: jest.fn(),
    muteLocalVideoStream: jest.fn(),
    setEnableSpeakerphone: jest.fn(),
    switchCamera: jest.fn()
  };
  return {
    __engine: engine,
    createAgoraRtcEngine: jest.fn(() => engine),
    ConnectionStateType: { ConnectionStateConnected: 3, ConnectionStateReconnecting: 4, ConnectionStateFailed: 5 },
    ClientRoleType: { ClientRoleBroadcaster: 1 },
    ChannelProfileType: { ChannelProfileCommunication: 0 }
  };
});

/** Mock backend the caller polls. `mock`-prefixed so jest's hoist guard allows it. */
const mockBackend = {
  status: "ringing",
  failWith: null as { status?: number; name?: string } | null,
  fetches: [] as string[]
};

jest.mock("../../api/calls", () => ({
  getCallStatus: jest.fn(async () => {
    if (mockBackend.failWith) throw Object.assign(new Error("poll failed"), mockBackend.failWith);
    mockBackend.fetches.push(mockBackend.status);
    return { call_id: "call-1", status: mockBackend.status, join: {} };
  }),
  endCall: jest.fn(async () => ({ call_id: "call-1", status: "ended" })),
  requestCallJoinToken: jest.fn(async () => ({
    token: "super-secret-rtc-token",
    app_id: "secret-app-id",
    channel_name: "c",
    uid: 7,
    provider: "agora"
  })),
  markCallConnected: jest.fn(async () => ({}))
}));
jest.mock("../../api/presenceSession", () => ({ reportPresenceActivity: jest.fn(async () => undefined) }));
jest.mock("../callSignalMedia", () => ({
  playCallCue: jest.fn(async () => undefined),
  stopCallTone: jest.fn(async () => undefined)
}));
jest.mock("../callKitBridge", () => ({ markCallKitConnected: jest.fn(), endCallKitCall: jest.fn() }));
jest.mock("../../core/voiceMessagePlayback", () => ({ stopVoiceMessagePlayback: jest.fn(async () => undefined) }));
jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: jest.fn(async () => undefined),
  releaseMediaPlayback: jest.fn(async () => undefined)
}));

import { requestCallJoinToken } from "../../api/calls";
import { stopCallTone } from "../callSignalMedia";
import { markCallKitConnected } from "../callKitBridge";
import { __resetCallSessionForTests, adoptCallSnapshot, beginCallSession, getCallSession } from "../callSessionStore";
import { shouldPlayRingback } from "../callToneLifecycle";
import { __resetCallSyncTraceForTests, readCallSyncTrace } from "../callSyncTrace";

async function flush(times = 20) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
}

/** Advance past one ringing-cadence poll tick and let its promise chain settle. */
async function tickPoll() {
  jest.advanceTimersByTime(1200);
  await flush();
}

function startOutgoingRingingCall() {
  beginCallSession({ conversationId: 5, direction: "outgoing", callType: "audio" });
  adoptCallSnapshot({ call_id: "call-1", status: "ringing", join: {} } as any, "start");
}

function ringbackPlaying() {
  const session = getCallSession();
  return shouldPlayRingback({
    direction: session.direction,
    mediaConnected: session.connected,
    status: session.call?.status
  });
}

describe("caller detects remote acceptance", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    __resetCallSessionForTests();
    __resetCallSyncTraceForTests({ console: false });
    mockBackend.status = "ringing";
    mockBackend.failWith = null;
    mockBackend.fetches.length = 0;
    (requestCallJoinToken as jest.Mock).mockClear();
    (stopCallTone as jest.Mock).mockClear();
    (markCallKitConnected as jest.Mock).mockClear();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it("plays ringback while the far end is still ringing", () => {
    startOutgoingRingingCall();
    expect(getCallSession().callId).toBe("call-1");
    expect(ringbackPlaying()).toBe(true);
  });

  it("polls, sees the acceptance, and joins the media room with no second action", async () => {
    startOutgoingRingingCall();
    mockBackend.status = "connecting"; // the callee accepts on the far end

    await tickPoll();

    expect(mockBackend.fetches).toContain("connecting");
    expect(getCallSession().call?.status).toBe("connecting");
    expect((requestCallJoinToken as jest.Mock).mock.calls.length).toBeGreaterThan(0);
  });

  it("stops ringback as soon as the acceptance is observed", async () => {
    startOutgoingRingingCall();
    expect(ringbackPlaying()).toBe(true);

    mockBackend.status = "connecting";
    await tickPoll();

    expect(ringbackPlaying()).toBe(false);
    expect(stopCallTone).toHaveBeenCalledTimes(1);
    expect(markCallKitConnected).toHaveBeenCalledTimes(1);
  });

  it("runs signaling acceptance cleanup once when poll and media states repeat", async () => {
    startOutgoingRingingCall();
    mockBackend.status = "connecting";
    await tickPoll();
    await tickPoll();
    mockBackend.status = "connected";
    await tickPoll();

    expect(stopCallTone).toHaveBeenCalledTimes(1);
    expect(markCallKitConnected).toHaveBeenCalledTimes(1);
  });

  it("detects an acceptance that stops at 'accepted' because the media token failed", async () => {
    // Backend commits the acceptance even when the RTC token mint fails, so the
    // caller must leave the ringing state on 'accepted' alone.
    startOutgoingRingingCall();
    mockBackend.status = "accepted";
    await tickPoll();

    expect(getCallSession().call?.status).toBe("accepted");
    expect(ringbackPlaying()).toBe(false);
  });

  it("detects the acceptance within one ringing poll interval", async () => {
    startOutgoingRingingCall();
    mockBackend.status = "connecting";

    jest.advanceTimersByTime(750);
    await flush();

    expect(getCallSession().call?.status).toBe("connecting");
  });

  it("is idempotent when the same accepted status is polled repeatedly", async () => {
    startOutgoingRingingCall();
    mockBackend.status = "connecting";
    await tickPoll();
    const joinCallsAfterFirst = (requestCallJoinToken as jest.Mock).mock.calls.length;

    await tickPoll();
    await tickPoll();

    expect((requestCallJoinToken as jest.Mock).mock.calls.length).toBe(joinCallsAfterFirst);
  });

  it("tears the session down when the call ends instead of ringing on", async () => {
    startOutgoingRingingCall();
    mockBackend.status = "ended";
    await tickPoll();

    expect(getCallSession().sessionActive).toBe(false);
  });
});

describe("call sync telemetry", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    __resetCallSessionForTests();
    __resetCallSyncTraceForTests({ console: false });
    mockBackend.status = "ringing";
    mockBackend.failWith = null;
    mockBackend.fetches.length = 0;
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it("records the acceptance transition with role, both statuses and the source", async () => {
    startOutgoingRingingCall();
    mockBackend.status = "connecting";
    await tickPoll();

    const accepted = readCallSyncTrace().find((event) => event.nextStatus === "connecting");
    expect(accepted).toBeDefined();
    expect(accepted?.role).toBe("caller");
    expect(accepted?.prevStatus).toBe("ringing");
    expect(accepted?.source).toBe("poll");
    expect(accepted?.callId).toBe("call-1");
  });

  it("records a poll failure, which used to be swallowed silently", async () => {
    startOutgoingRingingCall();
    mockBackend.failWith = { status: 500 };
    await tickPoll();

    const failure = readCallSyncTrace().find((event) => event.source === "poll_error");
    expect(failure).toBeDefined();
    expect(failure?.detail).toBe("http_500");
    expect(failure?.prevStatus).toBe("ringing");
  });

  it("classifies a network failure without a status code", async () => {
    startOutgoingRingingCall();
    mockBackend.failWith = { name: "TypeError" };
    await tickPoll();

    expect(readCallSyncTrace().find((event) => event.source === "poll_error")?.detail).toBe("network");
  });

  it("does not emit a line for every steady-state poll", async () => {
    startOutgoingRingingCall();
    const afterStart = readCallSyncTrace().length;
    await tickPoll();
    await tickPoll();
    await tickPoll();

    expect(readCallSyncTrace().length).toBe(afterStart);
  });

  it("never records tokens, credentials or any other call content", async () => {
    startOutgoingRingingCall();
    mockBackend.status = "connecting";
    await tickPoll();

    const serialized = JSON.stringify(readCallSyncTrace());
    expect(serialized).not.toContain("super-secret-rtc-token");
    expect(serialized).not.toContain("secret-app-id");
    // The event shape is a closed allowlist, so nothing new can leak in later.
    readCallSyncTrace().forEach((event) => {
      expect(Object.keys(event).sort()).toEqual(
        expect.arrayContaining(["callId", "elapsedMs", "nextStatus", "prevStatus", "role", "source"])
      );
      Object.keys(event).forEach((key) => {
        expect(["callId", "role", "prevStatus", "nextStatus", "source", "elapsedMs", "detail"]).toContain(key);
      });
    });
  });
});
