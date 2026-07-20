import React from "react";
import { act, fireEvent, render } from "@testing-library/react-native";
import { StatusActionRail } from "../StatusActionRail";

jest.mock("@expo/vector-icons", () => {
  const { Text: MockText } = require("react-native");
  return { Ionicons: ({ name, testID }: { name: string; testID?: string }) => <MockText testID={testID}>{name}</MockText> };
});

jest.mock("expo-haptics", () => ({
  ImpactFeedbackStyle: { Light: "light" },
  impactAsync: jest.fn(() => Promise.resolve()),
  selectionAsync: jest.fn(() => Promise.resolve())
}));

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 47, right: 0, bottom: 34, left: 0 })
}));

jest.mock("../../theme/logiNexusMotion", () => ({
  useLogiNexusReducedMotion: () => true
}));

function renderRail(overrides: Partial<React.ComponentProps<typeof StatusActionRail>> = {}) {
  const props: React.ComponentProps<typeof StatusActionRail> = {
    reactionCount: 1,
    selectedReaction: null,
    onReact: jest.fn(),
    onReply: jest.fn(),
    onShare: jest.fn(),
    ...overrides
  };
  return { props, ...render(<StatusActionRail {...props} />) };
}

describe("StatusActionRail", () => {
  it("renders icon-only controls with the reaction count and no visible text labels", () => {
    const view = renderRail();
    expect(view.queryByText("React")).toBeNull();
    expect(view.queryByText("Reply")).toBeNull();
    expect(view.queryByText("Share")).toBeNull();
    expect(view.getByTestId("status-action-react-icon")).toBeTruthy();
    expect(view.getByTestId("status-action-reply-icon")).toBeTruthy();
    expect(view.getByTestId("status-action-share-icon")).toBeTruthy();
    expect(view.getByTestId("status-action-reaction-count").props.children).toBe(1);
  });

  it("keeps complete VoiceOver labels and selected state", () => {
    const view = renderRail({ selectedReaction: "love" });
    expect(view.getByLabelText(/React to Status, selected/).props.accessibilityState.selected).toBe(true);
    expect(view.getByLabelText("Reply to Status")).toBeTruthy();
    expect(view.getByLabelText("Share Status")).toBeTruthy();
    expect(view.getByLabelText("1 reaction")).toBeTruthy();
  });

  it("routes each tap only to its existing action handler", () => {
    const view = renderRail();
    fireEvent.press(view.getByTestId("status-action-react"));
    fireEvent.press(view.getByTestId("status-action-reply"));
    fireEvent.press(view.getByTestId("status-action-share"));
    expect(view.props.onReact).toHaveBeenCalledTimes(1);
    expect(view.props.onReact).toHaveBeenCalledWith("love");
    expect(view.props.onReply).toHaveBeenCalledTimes(1);
    expect(view.props.onShare).toHaveBeenCalledTimes(1);
  });

  it("opens the reaction tray on long press without applying the default reaction", () => {
    const view = renderRail();
    act(() => {
      fireEvent(view.getByTestId("status-action-react"), "longPress", { stopPropagation: jest.fn() });
    });
    expect(view.getByTestId("status-reaction-tray")).toBeTruthy();
    expect(view.props.onReact).not.toHaveBeenCalled();
    fireEvent.press(view.getByTestId("status-reaction-fire"));
    expect(view.props.onReact).toHaveBeenCalledWith("fire");
  });

  it("prevents rapid duplicate requests while an action is pending", () => {
    const view = renderRail({ reactionPending: true, sharePending: true });
    fireEvent.press(view.getByTestId("status-action-react"));
    fireEvent.press(view.getByTestId("status-action-share"));
    expect(view.props.onReact).not.toHaveBeenCalled();
    expect(view.props.onShare).not.toHaveBeenCalled();
    expect(view.getByTestId("status-action-react").props.accessibilityState.busy).toBe(true);
  });
});
