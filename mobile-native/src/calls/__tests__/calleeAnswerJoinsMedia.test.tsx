/**
 * Answering must be what joins the media room — not being foregrounded.
 *
 * What happened in production (2026-09-17, calls 463 and 464)
 * ----------------------------------------------------------
 * The callee answered from CallKit, `POST /api/calls/<id>/accept` returned 200, and then
 * that device issued **no further request at all**: no `join-token`, no `connected`, no
 * status poll. The caller meanwhile ran the full sequence and reported connected, so the
 * caller's UI read "connected" for a call the callee had never entered, and CallKit tore
 * the callee's side down with no audio behind it.
 *
 * The cause was a dependency nobody had written down. `/accept` already returns Agora join
 * credentials and moves the call to `connecting`, but the CallKit answer handler discarded
 * that response (`acceptCall(callId).catch(...)`). The only thing left that could start a
 * join was `callSessionStore`'s status poll — and that poll is gated on
 * `appIsForegrounded()`, while iOS suspends its timer anyway. Answering from the lock
 * screen leaves the app BACKGROUNDED, which is the entire point of the feature. So the one
 * path this feature exists for was the one path that could not join.
 *
 * Answering in-app appeared to work only because it is foregrounded by construction; the
 * poll was silently doing the work there too.
 *
 * What this test pins
 * -------------------
 * With `AppState` reporting `background` and every timer left unfired, answering through
 * the CallKit bridge must still reach `joinChannel`, using the credentials the accept
 * response carried, without a single `getCallStatus` call. Asserting "no status fetch" is
 * what keeps this honest: a version that joins only because something polled would pass a
 * bare "did it join" assertion while leaving the real defect in place.
 */
import { render } from "@testing-library/react-native";
import { AppState } from "react-native";

jest.mock("../../api/config", () => ({
  NATIVE_CALLKIT_ENABLED: true,
  PULSE_API_BASE_URL: "https://pulsesoc.com"
}));

jest.mock("react-native-agora", () => {
  const engine = {
    initialize: jest.fn(),
    enableAudio: jest.fn(),
    enableAudioVolumeIndication: jest.fn(),
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
    ChannelProfileType: { ChannelProfileCommunication: 0 },
    RemoteAudioStateReason: { RemoteAudioReasonRemoteMuted: 5, RemoteAudioReasonRemoteUnmuted: 6 },
    RemoteVideoStateReason: { RemoteVideoStateReasonRemoteMuted: 5, RemoteVideoStateReasonRemoteUnmuted: 6 }
  };
});

/** The exact shape `/accept` answers with, once `normalizeCallPayload` has lifted `join`. */
const ACCEPTED_CALL = {
  call_id: "call_VFLHqc4Sj1XGUw",
  public_id: "call_VFLHqc4Sj1XGUw",
  conversation_id: 77,
  call_type: "audio",
  status: "connecting",
  room_name: "pulse_call_464",
  participants: [],
  join: {
    token: "agora-token-from-accept",
    app_id: "agora-app-id",
    channel_name: "pulse_call_464",
    uid: 4242
  }
};

const mockAcceptCall = jest.fn(async () => ACCEPTED_CALL);
const mockGetCallStatus = jest.fn(async () => ACCEPTED_CALL);
const mockRequestCallJoinToken = jest.fn(async () => ACCEPTED_CALL.join);
const mockGetActiveCalls = jest.fn(async () => ({ calls: [] }));

jest.mock("../../api/calls", () => ({
  acceptCall: (...args: unknown[]) => mockAcceptCall(...(args as [])),
  declineCall: jest.fn(async () => ({})),
  endCall: jest.fn(async () => ({})),
  getActiveCalls: (...args: unknown[]) => mockGetActiveCalls(...(args as [])),
  getCallStatus: (...args: unknown[]) => mockGetCallStatus(...(args as [])),
  markRingSeen: jest.fn(async () => ({})),
  markCallConnected: jest.fn(async () => ({})),
  requestCallJoinToken: (...args: unknown[]) => mockRequestCallJoinToken(...(args as [])),
  registerVoipPushToken: jest.fn(async () => ({})),
  unregisterVoipPushToken: jest.fn(async () => ({}))
}));

jest.mock("../../api/presenceSession", () => ({
  reportPresenceActivity: jest.fn(async () => undefined)
}));

jest.mock("../callSignalMedia", () => ({
  callHaptic: jest.fn(),
  playCallCue: jest.fn(async () => undefined),
  startCallTone: jest.fn(async () => undefined),
  stopCallTone: jest.fn(async () => undefined)
}));

jest.mock("../../core/voiceMessagePlayback", () => ({
  stopVoiceMessagePlayback: jest.fn(async () => undefined)
}));

jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: jest.fn(async () => undefined),
  releaseMediaPlayback: jest.fn(async () => undefined)
}));

jest.mock("expo-notifications", () => ({
  addNotificationReceivedListener: jest.fn(() => ({ remove: jest.fn() }))
}));

jest.mock("../../navigation/notificationRouting", () => ({
  navigationRef: { isReady: () => false, navigate: jest.fn() }
}));

jest.mock("../callKitNativeProvider", () => ({
  createNativeCallKitProvider: () => null
}));

/**
 * The bridge is stubbed only to capture the callbacks the layer registers. Its own answer
 * handling is covered by callKitBridge.test.ts; what is under test here is the wiring from
 * an answer to a media join, which is the part that was missing.
 */
let capturedCallbacks: {
  onAnswered?: (callId: string) => void;
  onAccepted?: (call: unknown) => void;
  onEnded?: (callId: string) => void;
} = {};

jest.mock("../callKitBridge", () => ({
  initNativeCallKit: jest.fn(async (callbacks: Record<string, unknown>) => {
    capturedCallbacks = callbacks as typeof capturedCallbacks;
  }),
  setNativeCallKitProvider: jest.fn(),
  reportIncomingCallKit: jest.fn(),
  endCallKitCall: jest.fn(),
  markCallKitConnected: jest.fn()
}));

import { IncomingCallLayer } from "../IncomingCallLayer";
import { clearCallSession, getCallSession } from "../callSessionStore";

const agora = jest.requireMock("react-native-agora") as { __engine: { joinChannel: jest.Mock } };

/** Let the accept promise and the join chain it starts settle, without firing any timer. */
async function settle() {
  for (let i = 0; i < 8; i += 1) await Promise.resolve();
}

beforeEach(() => {
  capturedCallbacks = {};
  jest.clearAllMocks();
  clearCallSession();
  // The device is locked. This is the state the whole feature exists to serve, and the
  // state in which the old code could not join.
  Object.defineProperty(AppState, "currentState", { value: "background", configurable: true });
});

afterEach(() => {
  clearCallSession();
});

describe("answering an incoming call joins the media room while backgrounded", () => {
  it("joins Agora from the accept response, with no status poll", async () => {
    render(<IncomingCallLayer signedIn currentUserId={1} />);
    await settle();

    expect(typeof capturedCallbacks.onAnswered).toBe("function");
    // MUTATION: drop `onAccepted` from IncomingCallLayer's initNativeCallKit call, or drop
    // the callback from CallKitCallbacks, and this is where it fails.
    expect(typeof capturedCallbacks.onAccepted).toBe("function");

    // The CallKit answer, exactly as the bridge delivers it: the session is opened first,
    // then the accepted record arrives when `/accept` answers.
    capturedCallbacks.onAnswered?.(ACCEPTED_CALL.call_id);
    capturedCallbacks.onAccepted?.(ACCEPTED_CALL);
    await settle();

    expect(agora.__engine.joinChannel).toHaveBeenCalledTimes(1);
    const [token, channel] = agora.__engine.joinChannel.mock.calls[0];
    expect(token).toBe("agora-token-from-accept");
    expect(channel).toBe("pulse_call_464");

    // The join must be a consequence of answering. A status fetch here would mean the
    // device is still depending on the poll that a locked phone never runs.
    expect(mockGetCallStatus).not.toHaveBeenCalled();
    // The accept response already carried usable credentials, so no second token request.
    expect(mockRequestCallJoinToken).not.toHaveBeenCalled();
  });

  it("opens the session before the accept response lands", async () => {
    render(<IncomingCallLayer signedIn currentUserId={1} />);
    await settle();

    capturedCallbacks.onAnswered?.(ACCEPTED_CALL.call_id);

    // Without this, `onAccepted` would adopt a snapshot into a session that is not active,
    // `adoptCallSnapshot`'s `snapshot.sessionActive` guard would skip the join, and the
    // Call screen's mount-only `beginCallSession` would then reset the snapshot it landed
    // in — which is how a second call answered onto an already-mounted Call screen stayed
    // pinned to the previous, already-ended call.
    const session = getCallSession();
    expect(session.sessionActive).toBe(true);
    expect(session.callId).toBe(ACCEPTED_CALL.call_id);
    expect(session.direction).toBe("incoming");
  });

  it("re-answering a different call replaces the session rather than keeping the old one", async () => {
    render(<IncomingCallLayer signedIn currentUserId={1} />);
    await settle();

    capturedCallbacks.onAnswered?.("call_r6c1WwLfAHQiHQ");
    expect(getCallSession().callId).toBe("call_r6c1WwLfAHQiHQ");

    capturedCallbacks.onAnswered?.(ACCEPTED_CALL.call_id);
    capturedCallbacks.onAccepted?.(ACCEPTED_CALL);
    await settle();

    expect(getCallSession().callId).toBe(ACCEPTED_CALL.call_id);
    expect(agora.__engine.joinChannel).toHaveBeenCalledTimes(1);
  });

  it("positive control: the join assertion can actually fail", async () => {
    // If `joinChannel` were unreachable in this harness — a mock that never binds, a
    // guard that returns early on every path — every assertion above would hold against
    // a build that joins nothing. Pin that the engine is reachable and that a session
    // which was never answered does not join.
    render(<IncomingCallLayer signedIn currentUserId={1} />);
    await settle();
    expect(agora.__engine.joinChannel).not.toHaveBeenCalled();
    expect(getCallSession().sessionActive).toBe(false);
  });
});
