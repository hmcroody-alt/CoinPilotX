/**
 * Global active-call banner regression tests.
 *
 * The banner is the only affordance that proves a call survived navigation, so
 * its visibility rule is load-bearing: alive session + Call screen not focused.
 * It must also offer the way back and a hang-up, and it must disappear the
 * moment the call reaches a terminal state — a banner for a dead call is the
 * "ghost call" symptom the persistent-session work exists to prevent.
 */
import React from "react";
import { act, fireEvent, render } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 20, bottom: 0, left: 0, right: 0 })
}));

jest.mock("@expo/vector-icons", () => ({
  Ionicons: "Ionicons"
}));

jest.mock("../../i18n", () => ({
  // Identity translator: the assertions below check that a *key* reached the
  // translator, which is what keeps hardcoded copy out of this component.
  useTranslation: () => ({ t: (key: string) => key })
}));

jest.mock("../../navigation/notificationRouting", () => ({
  navigationRef: { isReady: jest.fn(() => true), navigate: jest.fn() }
}));

// The store is exercised for real (that is the point of these tests), so its
// native-module dependencies have to be stubbed the same way the store's own
// suite stubs them.
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

jest.mock("../callSessionStore", () => {
  const actual = jest.requireActual("../callSessionStore");
  return { ...actual, hangupCallSession: jest.fn(async () => undefined) };
});

import { navigationRef } from "../../navigation/notificationRouting";
import {
  __resetCallSessionForTests,
  adoptCallSnapshot,
  beginCallSession,
  hangupCallSession,
  setCallScreenFocused
} from "../callSessionStore";
import { MinimizedCallBanner } from "../MinimizedCallBanner";

function startSessionOffScreen() {
  beginCallSession({ callId: "call-1", conversationId: 9, direction: "outgoing", callType: "audio", title: "Ada" });
  setCallScreenFocused(false);
}

describe("MinimizedCallBanner", () => {
  afterEach(() => {
    __resetCallSessionForTests();
    jest.clearAllMocks();
  });

  it("is visible outside the Call screen while a session is alive", () => {
    startSessionOffScreen();
    const { getByLabelText, getByText } = render(<MinimizedCallBanner />);
    expect(getByLabelText("messaging:calls.returnToCall")).toBeTruthy();
    expect(getByText("Ada")).toBeTruthy();
  });

  it("hides itself while the Call screen is focused", () => {
    startSessionOffScreen();
    setCallScreenFocused(true);
    const { queryByLabelText } = render(<MinimizedCallBanner />);
    expect(queryByLabelText("messaging:calls.returnToCall")).toBeNull();
  });

  it("navigates back to the live call when tapped", () => {
    startSessionOffScreen();
    const { getByLabelText } = render(<MinimizedCallBanner />);
    fireEvent.press(getByLabelText("messaging:calls.returnToCall"));
    expect(navigationRef.navigate).toHaveBeenCalledWith(
      "Call",
      expect.objectContaining({ callId: "call-1", conversationId: 9, callType: "audio" })
    );
  });

  it("ends the call through the single teardown path when hang-up is tapped", () => {
    startSessionOffScreen();
    const { getByLabelText } = render(<MinimizedCallBanner />);
    fireEvent.press(getByLabelText("messaging:calls.endCall"));
    expect(hangupCallSession).toHaveBeenCalledWith("native_hangup");
  });

  it("disappears once the call reaches a terminal state", () => {
    startSessionOffScreen();
    const { queryByLabelText } = render(<MinimizedCallBanner />);
    expect(queryByLabelText("messaging:calls.returnToCall")).toBeTruthy();
    act(() => { adoptCallSnapshot({ call_id: "call-1", status: "ended" } as never); });
    expect(queryByLabelText("messaging:calls.returnToCall")).toBeNull();
  });
});
