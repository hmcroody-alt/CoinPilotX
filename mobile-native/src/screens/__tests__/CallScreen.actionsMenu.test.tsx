/**
 * The ••• call-options menu: entrypoint wiring only.
 *
 * The ••• used to call `openCallWebFallback()` — a hand-off to the web client
 * for a live Agora call the native app is already holding. These tests pin the
 * replacement to what it is allowed to be: a menu that opens an existing sheet.
 *
 * The load-bearing assertions are the negative ones. Opening a menu on top of a
 * live call must not end it, rejoin it, restart media, flip the mic/camera/
 * speaker, or reset the duration timer — the call session store and the Agora
 * room are mocked precisely so that any such touch shows up as a call count.
 */
import React from "react";
import { act, fireEvent, render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 20, bottom: 0, left: 0, right: 0 })
}));

jest.mock("@expo/vector-icons", () => ({ Ionicons: "Ionicons" }));

// Resolved through a dynamic import() for the video surface; absent in Jest.
jest.mock("react-native-agora", () => ({ RtcSurfaceView: "RtcSurfaceView" }), { virtual: true });

jest.mock("../../api/calls", () => ({
  acceptCall: jest.fn(async () => ({})),
  declineCall: jest.fn(async () => ({})),
  markRingSeen: jest.fn(async () => ({})),
  openCallWebFallback: jest.fn(async () => undefined),
  sendCallControl: jest.fn(async () => ({})),
  startConversationCall: jest.fn(async () => ({})),
  submitCallQuality: jest.fn(async () => ({})),
  inviteToCall: jest.fn(async () => ({}))
}));

jest.mock("../../api/messenger", () => ({
  listConversationMembers: jest.fn(async () => [
    { user_id: 77, display_name: "Priya Raman" }
  ])
}));

jest.mock("../../api/support", () => ({
  reportPulseTarget: jest.fn(async () => ({ ok: true }))
}));

jest.mock("../../calls/callSignalMedia", () => ({
  callHaptic: jest.fn(),
  playCallCue: jest.fn(async () => undefined),
  startCallTone: jest.fn(async () => undefined),
  stopCallTone: jest.fn(async () => undefined)
}));

jest.mock("../../theme/logiNexusMotion", () => ({
  useLogiNexusReducedMotion: () => true
}));

jest.mock("../../calls/callCapabilities", () => ({
  loadCallCapabilities: jest.fn(async () => undefined),
  maxParticipantsFor: () => 8,
  useCallCapabilities: () => ({ provider: "agora", group_calls_enabled: true })
}));

// The registry itself is real — the sheet must render the canonical views, not
// a second participant model. Only the subscription hook is fed by the test.
const mockParticipantViews: any[] = [];
jest.mock("../../calls/callParticipants", () => {
  const actual = jest.requireActual("../../calls/callParticipants");
  return { ...actual, useCallParticipants: () => mockParticipantViews };
});

const mockSession = {
  callId: "call-1",
  call: {
    call_id: "call-1",
    status: "active",
    call_type: "audio",
    conversation_id: 42,
    participants: [{ user_id: 77, display_name: "Priya Raman", role: "callee" }]
  } as any,
  direction: "outgoing",
  title: "Priya Raman",
  everConnected: true,
  connectedAtMs: Date.now() - 65_000
};

jest.mock("../../calls/callSessionStore", () => ({
  adoptCallSnapshot: jest.fn(),
  beginCallSession: jest.fn(),
  clearCallSession: jest.fn(),
  ensureCallMediaConnected: jest.fn(async () => undefined),
  finalizeCallSession: jest.fn(),
  getCallSession: jest.fn(() => ({ call: { call_id: "call-1" } })),
  handleCallScreenUnmount: jest.fn(),
  hangupCallSession: jest.fn(async () => undefined),
  refreshCallSessionStatus: jest.fn(async () => undefined),
  setCallScreenFocused: jest.fn(),
  useCallSession: () => mockSession
}));

const mockRoom = {
  connected: true,
  connectionState: "connected",
  reconnecting: false,
  error: "",
  localUid: 1,
  audioEnabled: true,
  videoEnabled: false,
  speakerEnabled: true,
  connectionQuality: "excellent",
  participantCount: 2,
  reconnectCount: 0,
  remoteVideoTrack: null,
  localVideoTrack: null,
  setMicrophoneEnabled: jest.fn(async () => undefined),
  setCameraEnabled: jest.fn(async () => undefined),
  setSpeakerEnabled: jest.fn(async () => undefined),
  switchCamera: jest.fn(async () => undefined),
  showAudioRoutePicker: jest.fn(async () => undefined)
};
jest.mock("../../calls/useNativeCallRoom", () => ({
  useNativeCallRoom: () => mockRoom
}));

import { openCallWebFallback } from "../../api/calls";
import {
  beginCallSession,
  clearCallSession,
  ensureCallMediaConnected,
  finalizeCallSession,
  hangupCallSession
} from "../../calls/callSessionStore";
import { CallScreen } from "../CallScreen";

function view(overrides: Partial<any> = {}) {
  return {
    userId: 77,
    rtcUid: 77,
    displayName: "Priya Raman",
    username: "priya",
    avatarUrl: "",
    role: "callee",
    backendStatus: "joined",
    isLocal: false,
    rtcConnected: true,
    speaking: false,
    audioMuted: false,
    videoMuted: false,
    joinedAt: null,
    ...overrides
  };
}

async function renderCall(callType: "audio" | "video" = "audio") {
  mockSession.call.call_type = callType;
  const navigation = {
    addListener: jest.fn(() => jest.fn()),
    goBack: jest.fn(),
    canGoBack: jest.fn(() => true),
    navigate: jest.fn()
  } as any;
  const route = {
    params: { callId: "call-1", conversationId: 42, callType, direction: "outgoing", title: "Priya Raman" }
  } as any;
  const utils = render(<CallScreen route={route} navigation={navigation} />);
  // Flush the mount effects so the responder tree settles before any press.
  await act(async () => undefined);
  return utils;
}

async function press(utils: ReturnType<typeof render>, label: string) {
  await act(async () => {
    fireEvent.press(utils.getByLabelText(label));
  });
}

beforeEach(() => {
  jest.clearAllMocks();
  mockParticipantViews.length = 0;
  mockParticipantViews.push(view({ userId: 5, rtcUid: 5, displayName: "You", isLocal: true }), view());
  mockRoom.audioEnabled = true;
  mockRoom.videoEnabled = false;
  mockRoom.speakerEnabled = true;
});

describe("call options menu", () => {
  it("opens the native sheet when ••• is tapped", async () => {
    const utils = await renderCall();
    expect(utils.queryByLabelText("Add people")).toBeNull();

    await press(utils, "Call options");

    expect(utils.getByLabelText("Add people")).toBeTruthy();
    expect(utils.getByLabelText("Participants")).toBeTruthy();
    expect(utils.getByLabelText("Call details")).toBeTruthy();
  });

  it("routes 'Add people' into the AddParticipantsSheet the screen already owns", async () => {
    const utils = await renderCall();
    await press(utils, "Call options");
    expect(utils.queryByText("Add to call")).toBeNull();

    await press(utils, "Add people");

    // The existing sheet — not a second picker built for the menu.
    expect(utils.getByText("Add to call")).toBeTruthy();
    expect(utils.getByLabelText("Close add participants")).toBeTruthy();
    // The menu got out of the way rather than stacking.
    expect(utils.queryByLabelText("Call details")).toBeNull();
  });

  it("closes on Cancel and leaves the call untouched", async () => {
    const utils = await renderCall();
    await press(utils, "Call options");
    await press(utils, "Cancel call options");

    expect(utils.queryByLabelText("Add people")).toBeNull();
    expect(utils.queryByText("Add to call")).toBeNull();
    expect(hangupCallSession).not.toHaveBeenCalled();
    expect(finalizeCallSession).not.toHaveBeenCalled();
  });

  it("reports an audio call in call details", async () => {
    const utils = await renderCall("audio");
    await press(utils, "Call options");
    await press(utils, "Call details");

    expect(utils.getByText("Voice call")).toBeTruthy();
    expect(utils.getByText("Secure link")).toBeTruthy();
    expect(utils.getByText("2")).toBeTruthy();
  });

  it("reports a video call in call details", async () => {
    const utils = await renderCall("video");
    await press(utils, "Call options");
    await press(utils, "Call details");

    expect(utils.getByText("Video call")).toBeTruthy();
  });

  it("lists the canonical participant registry, excluding those who left", async () => {
    mockParticipantViews.push(view({ userId: 91, rtcUid: 91, displayName: "Gone Guest", backendStatus: "left" }));
    const utils = await renderCall();
    await press(utils, "Call options");
    await press(utils, "Participants");

    expect(utils.getByLabelText("Participant Priya Raman")).toBeTruthy();
    expect(utils.queryByLabelText("Participant Gone Guest")).toBeNull();
  });

  it("does not end, rejoin, or restart the call when the menu opens", async () => {
    const utils = await renderCall();
    const beginsAtMount = (beginCallSession as jest.Mock).mock.calls.length;

    await press(utils, "Call options");
    await press(utils, "Participants");
    await press(utils, "Back to call options");
    await press(utils, "Call details");

    expect(hangupCallSession).not.toHaveBeenCalled();
    expect(finalizeCallSession).not.toHaveBeenCalled();
    expect(clearCallSession).not.toHaveBeenCalled();
    expect(ensureCallMediaConnected).not.toHaveBeenCalled();
    expect((beginCallSession as jest.Mock).mock.calls.length).toBe(beginsAtMount);
  });

  it("does not mutate mic, camera, speaker or the duration timer", async () => {
    const utils = await renderCall();
    const durationBefore = utils.getByText(/Encrypted · Connected/).props.children;

    await press(utils, "Call options");
    await press(utils, "Add people");

    expect(mockRoom.setMicrophoneEnabled).not.toHaveBeenCalled();
    expect(mockRoom.setCameraEnabled).not.toHaveBeenCalled();
    expect(mockRoom.setSpeakerEnabled).not.toHaveBeenCalled();
    expect(mockRoom.switchCamera).not.toHaveBeenCalled();
    expect(mockRoom.showAudioRoutePicker).not.toHaveBeenCalled();
    // Same rendered clock: the menu never remounted the screen or the timer.
    expect(utils.getByText(/Encrypted · Connected/).props.children).toEqual(durationBefore);
  });

  it("leaves the existing dock 'Add' flow working exactly as before", async () => {
    const utils = await renderCall();

    await press(utils, "Add");

    expect(utils.getByText("Add to call")).toBeTruthy();
    // Reached without ever opening the menu.
    expect(utils.queryByLabelText("Call details")).toBeNull();
  });

  it("never hands an active native call off to the web client", async () => {
    const utils = await renderCall();

    await press(utils, "Call options");
    await press(utils, "Add people");

    expect(openCallWebFallback).not.toHaveBeenCalled();
  });

  it("reopens on the menu rather than the view it was closed on", async () => {
    const utils = await renderCall();
    await press(utils, "Call options");
    await press(utils, "Participants");
    await press(utils, "Close call options");

    await press(utils, "Call options");

    expect(utils.getByLabelText("Call details")).toBeTruthy();
    expect(utils.queryByLabelText("Back to call options")).toBeNull();
  });
});
