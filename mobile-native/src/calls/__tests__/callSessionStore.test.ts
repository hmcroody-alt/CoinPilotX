/**
 * Call session lifecycle regression tests.
 *
 * The invariant under test is the App Review fix: the Agora engine is owned by
 * the module-scope session store, so unmounting the Call screen (navigation,
 * Minimize) must NOT release it — only an explicit local hang-up, a terminal
 * backend status, or the backend disowning the call may. The second invariant
 * is the fast path: Agora's onUserOffline is a hint that triggers one
 * immediate authoritative status re-fetch (the backend stays authoritative).
 */
import { renderHook } from "@testing-library/react-native";

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

jest.mock("../../api/calls", () => ({
  getCallStatus: jest.fn(async () => ({ call_id: "call-1", status: "active" })),
  endCall: jest.fn(async () => ({ call_id: "call-1", status: "ended" })),
  requestCallJoinToken: jest.fn(async () => ({})),
  markCallConnected: jest.fn(async () => ({}))
}));

jest.mock("../../api/presenceSession", () => ({
  reportPresenceActivity: jest.fn(async () => undefined)
}));

jest.mock("../callSignalMedia", () => ({
  playCallCue: jest.fn(async () => undefined),
  stopCallTone: jest.fn(async () => undefined)
}));

jest.mock("../callKitBridge", () => ({
  markCallKitConnected: jest.fn(),
  endCallKitCall: jest.fn()
}));

jest.mock("../../core/voiceMessagePlayback", () => ({
  stopVoiceMessagePlayback: jest.fn(async () => undefined)
}));

jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: jest.fn(async () => undefined),
  releaseMediaPlayback: jest.fn(async () => undefined)
}));

import { endCall, getCallStatus } from "../../api/calls";
import { releaseMediaPlayback } from "../../core/mediaPlaybackCoordinator";
import { endCallKitCall } from "../callKitBridge";
import {
  __resetCallSessionForTests,
  beginCallSession,
  connectCallMedia,
  getCallSession,
  handleCallScreenUnmount,
  hangupCallSession,
  refreshCallSessionStatus
} from "../callSessionStore";
import { useAgoraCallRoom } from "../useAgoraCallRoom";

const agoraMock = jest.requireMock("react-native-agora") as { __engine: Record<string, jest.Mock> };
const engineMock = agoraMock.__engine;

const JOIN = { token: "token", app_id: "app", channel_name: "channel", uid: 7 };

async function startLiveCall() {
  beginCallSession({ callId: "call-1", conversationId: 9, direction: "outgoing", callType: "audio", title: "Ada" });
  const joined = await connectCallMedia(JOIN);
  expect(joined).toBe(true);
}

describe("callSessionStore", () => {
  beforeEach(() => {
    jest.useFakeTimers();
  });

  afterEach(() => {
    __resetCallSessionForTests();
    jest.useRealTimers();
    jest.clearAllMocks();
  });

  it("does NOT release the engine when the Call screen unmounts while the session is active", async () => {
    await startLiveCall();
    const { unmount } = renderHook(() => useAgoraCallRoom());
    unmount();
    handleCallScreenUnmount();
    // The whole App Review defect was disconnect("unmounted") here.
    expect(engineMock.leaveChannel).not.toHaveBeenCalled();
    expect(engineMock.release).not.toHaveBeenCalled();
    expect(getCallSession().sessionActive).toBe(true);
    expect(getCallSession().callScreenFocused).toBe(false);
  });

  it("releases the engine, CallKit and the media lease on a terminal backend status", async () => {
    await startLiveCall();
    (getCallStatus as jest.Mock).mockResolvedValue({ call_id: "call-1", status: "ended" });
    await refreshCallSessionStatus();
    expect(engineMock.leaveChannel).toHaveBeenCalled();
    expect(engineMock.release).toHaveBeenCalled();
    expect(endCallKitCall).toHaveBeenCalledWith("call-1");
    expect(releaseMediaPlayback).toHaveBeenCalledWith("native-call");
    expect(getCallSession().sessionActive).toBe(false);
    // Once the screen (or banner) is gone, nothing stale is left behind.
    handleCallScreenUnmount();
    expect(getCallSession().callId).toBe("");
  });

  it("uses onUserOffline as a fast-path hint to re-fetch the authoritative status immediately", async () => {
    await startLiveCall();
    const handler = engineMock.registerEventHandler.mock.calls[0][0];
    (getCallStatus as jest.Mock).mockClear();
    (getCallStatus as jest.Mock).mockResolvedValue({ call_id: "call-1", status: "ended" });
    handler.onUserOffline({}, 42);
    // No timer advance: the re-fetch fires immediately, not on the next poll tick.
    expect(getCallStatus).toHaveBeenCalledWith("call-1");
    // Joins the deduped in-flight fast-path fetch so the assertion is deterministic.
    await refreshCallSessionStatus();
    expect(getCallSession().sessionActive).toBe(false);
    expect(engineMock.release).toHaveBeenCalled();
  });

  it("treats a 404 from the status endpoint as terminal so no ghost session survives", async () => {
    await startLiveCall();
    (getCallStatus as jest.Mock).mockRejectedValue(Object.assign(new Error("not found"), { status: 404 }));
    await refreshCallSessionStatus();
    expect(engineMock.release).toHaveBeenCalled();
    expect(getCallSession().sessionActive).toBe(false);
  });

  it("ends the call on the backend and tears down locally on explicit hang-up", async () => {
    await startLiveCall();
    await hangupCallSession("native_hangup");
    expect(endCall).toHaveBeenCalledWith("call-1", "native_hangup");
    expect(engineMock.leaveChannel).toHaveBeenCalled();
    expect(engineMock.release).toHaveBeenCalled();
    expect(endCallKitCall).toHaveBeenCalledWith("call-1");
    expect(getCallSession().sessionActive).toBe(false);
  });
});
