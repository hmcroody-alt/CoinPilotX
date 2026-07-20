import React from "react";
import { AccessibilityInfo } from "react-native";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

jest.mock("expo-haptics", () => ({
  impactAsync: jest.fn().mockResolvedValue(undefined),
  ImpactFeedbackStyle: { Light: "light", Medium: "medium" }
}));

jest.mock("../../theme/logiNexusMotion", () => ({
  useLogiNexusReducedMotion: jest.fn().mockReturnValue(false)
}));

import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";
import { StatusActionRail } from "../StatusActionRail";

const mockedUseReducedMotion = useLogiNexusReducedMotion as jest.Mock;

function baseProps() {
  return {
    reactionCount: 0,
    selectedReaction: undefined as string | undefined,
    reactionPending: false,
    shareBusy: false,
    onReact: jest.fn(),
    onReply: jest.fn(),
    onShare: jest.fn()
  };
}

describe("StatusActionRail", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockedUseReducedMotion.mockReturnValue(false);
    jest.spyOn(AccessibilityInfo, "announceForAccessibility").mockImplementation(() => undefined);
  });

  it("never renders the literal words React, Reply, or Share as visible text", () => {
    const { queryByText } = render(<StatusActionRail {...baseProps()} />);
    expect(queryByText("React")).toBeNull();
    expect(queryByText("Reply")).toBeNull();
    expect(queryByText("Share")).toBeNull();
  });

  it("exposes accessible React, Reply, and Share controls with correct labels", () => {
    const { getByLabelText } = render(<StatusActionRail {...baseProps()} />);
    expect(getByLabelText("React to Status")).toBeTruthy();
    expect(getByLabelText("Reply to Status")).toBeTruthy();
    expect(getByLabelText("Share Status")).toBeTruthy();
  });

  it("does not render a reaction count when count is zero", () => {
    const { queryByLabelText } = render(<StatusActionRail {...baseProps()} />);
    expect(queryByLabelText(/^\d+ reactions?$/)).toBeNull();
  });

  it("renders and announces the reaction count when greater than zero", () => {
    const { getByLabelText } = render(<StatusActionRail {...baseProps()} reactionCount={1} />);
    expect(getByLabelText("1 reaction")).toBeTruthy();
  });

  it("pluralizes the reaction count label for counts greater than one", () => {
    const { getByLabelText } = render(<StatusActionRail {...baseProps()} reactionCount={5} />);
    expect(getByLabelText("5 reactions")).toBeTruthy();
  });

  it("tapping the heart applies the default Love reaction", () => {
    const onReact = jest.fn();
    const { getByTestId } = render(<StatusActionRail {...baseProps()} onReact={onReact} />);
    fireEvent.press(getByTestId("status-action-react"));
    expect(onReact).toHaveBeenCalledWith("love");
  });

  it("announces the applied reaction for VoiceOver users on tap", () => {
    const { getByTestId } = render(<StatusActionRail {...baseProps()} />);
    fireEvent.press(getByTestId("status-action-react"));
    expect(AccessibilityInfo.announceForAccessibility).toHaveBeenCalledWith("Love reaction selected");
  });

  it("long-pressing the heart opens the reaction tray with only backend-supported reactions", () => {
    const { getByTestId, getByLabelText } = render(<StatusActionRail {...baseProps()} />);
    fireEvent(getByTestId("status-action-react"), "onLongPress");
    expect(getByLabelText("Open reaction options")).toBeTruthy();
    expect(getByTestId("status-reaction-love")).toBeTruthy();
    expect(getByTestId("status-reaction-fire")).toBeTruthy();
  });

  it("selecting a tray reaction invokes onReact with that type and closes the tray", () => {
    const onReact = jest.fn();
    const { getByTestId, queryByLabelText } = render(<StatusActionRail {...baseProps()} onReact={onReact} />);
    fireEvent(getByTestId("status-action-react"), "onLongPress");
    fireEvent.press(getByTestId("status-reaction-fire"));
    expect(onReact).toHaveBeenCalledWith("fire");
    expect(queryByLabelText("Open reaction options")).toBeNull();
  });

  it("tapping the backdrop closes the tray without reacting", () => {
    const onReact = jest.fn();
    const { getByTestId, getByLabelText, queryByLabelText } = render(<StatusActionRail {...baseProps()} onReact={onReact} />);
    fireEvent(getByTestId("status-action-react"), "onLongPress");
    fireEvent.press(getByLabelText("Close reaction options"));
    expect(onReact).not.toHaveBeenCalled();
    expect(queryByLabelText("Open reaction options")).toBeNull();
  });

  it("reflects the selected reaction as an accessibility selected state", () => {
    const { getByTestId } = render(<StatusActionRail {...baseProps()} selectedReaction="love" />);
    expect(getByTestId("status-action-react").props.accessibilityState?.selected).toBe(true);
  });

  it("marks the React button busy while a reaction mutation is pending", () => {
    const { getByTestId } = render(<StatusActionRail {...baseProps()} reactionPending />);
    expect(getByTestId("status-action-react").props.accessibilityState?.busy).toBe(true);
  });

  it("tapping Reply invokes onReply", () => {
    const onReply = jest.fn();
    const { getByLabelText } = render(<StatusActionRail {...baseProps()} onReply={onReply} />);
    fireEvent.press(getByLabelText("Reply to Status"));
    expect(onReply).toHaveBeenCalledTimes(1);
  });

  it("tapping Share invokes onShare", () => {
    const onShare = jest.fn();
    const { getByLabelText } = render(<StatusActionRail {...baseProps()} onShare={onShare} />);
    fireEvent.press(getByLabelText("Share Status"));
    expect(onShare).toHaveBeenCalledTimes(1);
  });

  it("disables the Share button while a share is already in flight", () => {
    const onShare = jest.fn();
    const { getByLabelText } = render(<StatusActionRail {...baseProps()} shareBusy onShare={onShare} />);
    const shareButton = getByLabelText("Share Status");
    expect(shareButton.props.accessibilityState?.disabled).toBe(true);
    fireEvent.press(shareButton);
    expect(onShare).not.toHaveBeenCalled();
  });

  it("does not open the reaction tray on a plain tap", () => {
    const { getByTestId, queryByLabelText } = render(<StatusActionRail {...baseProps()} />);
    fireEvent.press(getByTestId("status-action-react"));
    expect(queryByLabelText("Open reaction options")).toBeNull();
  });

  it("skips the press pulse animation when Reduced Motion is enabled", () => {
    mockedUseReducedMotion.mockReturnValue(true);
    const onReact = jest.fn();
    const { getByTestId } = render(<StatusActionRail {...baseProps()} onReact={onReact} />);
    expect(() => fireEvent.press(getByTestId("status-action-react"))).not.toThrow();
    expect(onReact).toHaveBeenCalledWith("love");
  });
});
