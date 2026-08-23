/**
 * The caller's status poll must survive a cold app launch.
 *
 * Production incident: the callee accepted, the backend recorded
 * accepted -> connecting -> connected, and the caller kept ringing for 51
 * seconds until the owner gave up. The production HTTP log for that window
 * contains ZERO `GET /api/calls/{id}/status` requests from the caller, where
 * the ringing cadence should have produced roughly one every 700ms, and the
 * call-events table shows the caller never emitted `joined` either. Both
 * symptoms have one cause: the poll interval fired, but its foreground gate
 * refused every tick.
 *
 * The gate read a module-scope snapshot of `AppState.currentState`. On iOS that
 * snapshot is taken while the bundle is evaluated — during app launch, before
 * `applicationDidBecomeActive` — where `UIApplication.applicationState` is
 * `UIApplicationStateInactive`, i.e. the string "inactive". Nothing ever
 * corrected it, because the change subscription is only registered once a call
 * begins, by which time the launch-time inactive -> active transition is long
 * gone. So on every cold-started process the caller's single acceptance signal
 * was dead for the lifetime of the app.
 *
 * This never failed CI because react-native's jest mock defines
 * `AppState.currentState` as a jest *function*, and `String(fn)` matches
 * neither "inactive" nor "background" — the gate is unconditionally open in
 * tests and was unconditionally shut on a real device. These tests therefore
 * pin the real iOS launch value explicitly rather than trusting the preset.
 */
jest.mock("react-native/Libraries/AppState/AppState", () => ({
  __esModule: true,
  default: {
    // Exactly what RCTCurrentAppState() returns during app launch on iOS.
    currentState: "inactive",
    addEventListener: jest.fn(() => ({ remove: jest.fn() }))
  }
}));

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

const mockBackend = { status: "ringing", fetches: 0 };

jest.mock("../../api/calls", () => ({
  getCallStatus: jest.fn(async () => {
    mockBackend.fetches += 1;
    return { call_id: "call-1", status: mockBackend.status, join: {} };
  }),
  endCall: jest.fn(async () => ({ call_id: "call-1", status: "ended" })),
  requestCallJoinToken: jest.fn(async () => ({
    token: "t",
    app_id: "a",
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

import { AppState } from "react-native";
import { stopCallTone } from "../callSignalMedia";
import { markCallKitConnected } from "../callKitBridge";
import {
  CALL_RINGING_STATUS_REFRESH_MS,
  __resetCallSessionForTests,
  adoptCallSnapshot,
  beginCallSession,
  getCallSession
} from "../callSessionStore";
import { shouldPlayRingback } from "../callToneLifecycle";

const appStateMock = AppState as unknown as { currentState: string };

async function flush(times = 20) {
  for (let i = 0; i < times; i += 1) await Promise.resolve();
}

/** Advance past several ringing-cadence ticks and settle their promise chains. */
async function tickPolls(count = 3) {
  for (let i = 0; i < count; i += 1) {
    jest.advanceTimersByTime(CALL_RINGING_STATUS_REFRESH_MS + 20);
    await flush();
  }
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

describe("caller status poll foreground gate", () => {
  beforeEach(() => {
    jest.useFakeTimers();
    // Every test starts from the real iOS cold-launch value, because that is
    // the state the incident happened in.
    appStateMock.currentState = "inactive";
    __resetCallSessionForTests();
    mockBackend.status = "ringing";
    mockBackend.fetches = 0;
    (stopCallTone as jest.Mock).mockClear();
    (markCallKitConnected as jest.Mock).mockClear();
  });
  afterEach(() => {
    jest.useRealTimers();
  });

  it("polls after a cold launch, where AppState was captured as 'inactive'", async () => {
    startOutgoingRingingCall();

    await tickPolls(3);

    expect(mockBackend.fetches).toBeGreaterThanOrEqual(3);
  });

  it("sees the acceptance and stops ringback after a cold launch", async () => {
    startOutgoingRingingCall();
    expect(ringbackPlaying()).toBe(true);

    mockBackend.status = "connecting"; // the callee taps Accept

    await tickPolls(1);

    expect(getCallSession().call?.status).toBe("connecting");
    expect(ringbackPlaying()).toBe(false);
    expect(stopCallTone).toHaveBeenCalledTimes(1);
    expect(markCallKitConnected).toHaveBeenCalledTimes(1);
  });

  it("keeps polling while iOS reports 'inactive' mid-call", async () => {
    // CallKit's overlay, the incoming-call banner, Control Center and the app
    // switcher all put the app in "inactive" — precisely the moments that occur
    // during a call. Suppressing the caller's only acceptance signal there is
    // how the ringback got stuck.
    startOutgoingRingingCall();
    appStateMock.currentState = "inactive";

    await tickPolls(2);

    expect(mockBackend.fetches).toBeGreaterThanOrEqual(2);
  });

  it("stops polling once the app is genuinely backgrounded", async () => {
    startOutgoingRingingCall();
    await tickPolls(1);
    const whileForegrounded = mockBackend.fetches;
    expect(whileForegrounded).toBeGreaterThan(0);

    appStateMock.currentState = "background";
    await tickPolls(3);

    expect(mockBackend.fetches).toBe(whileForegrounded);
  });

  it("resumes polling when the app returns from the background", async () => {
    startOutgoingRingingCall();
    appStateMock.currentState = "background";
    await tickPolls(2);
    const whileBackgrounded = mockBackend.fetches;

    appStateMock.currentState = "active";
    await tickPolls(2);

    expect(mockBackend.fetches).toBeGreaterThan(whileBackgrounded);
  });
});
