import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

jest.mock("expo-haptics", () => ({
  impactAsync: jest.fn().mockResolvedValue(undefined),
  notificationAsync: jest.fn().mockResolvedValue(undefined),
  selectionAsync: jest.fn().mockResolvedValue(undefined),
  ImpactFeedbackStyle: { Light: "light", Medium: "medium", Heavy: "heavy" },
  NotificationFeedbackType: { Success: "success", Warning: "warning", Error: "error" }
}));

jest.mock("expo-av", () => ({
  ResizeMode: { COVER: "cover", CONTAIN: "contain" },
  Video: () => null
}));

jest.mock("@expo/vector-icons", () => ({
  Ionicons: ({ name }: { name: string }) => name
}));

jest.mock("../NativeMediaViewer", () => ({
  NativeMediaViewer: () => null,
  mediaViewerItemFromPulseMedia: jest.fn()
}));

jest.mock("../../core/mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: jest.fn(),
  releaseMediaPlayback: jest.fn()
}));

jest.mock("../../media/mediaAccess", () => ({
  canonicalMediaPlaybackUrl: (url: string) => url,
  refreshCanonicalMediaAccess: jest.fn().mockResolvedValue(undefined)
}));

import { PulsePost } from "../../api/feed";
import { PostCard, computeMediaBleedStyle } from "../PostCard";

function basePost(overrides: Partial<PulsePost> = {}): PulsePost {
  return {
    id: 42,
    body: "Hello from the feed",
    author: { display_name: "Ada", username: "ada" },
    created_at: new Date().toISOString(),
    ...overrides
  } as PulsePost;
}

describe("computeMediaBleedStyle", () => {
  it("returns no horizontal margin for inset layout", () => {
    expect(computeMediaBleedStyle("inset", 390, 366)).toEqual({ marginHorizontal: 0 });
  });

  it("computes symmetric negative bleed and full window width for fullBleed", () => {
    // window 390, card 366 -> bleed (390-366)/2 = 12
    expect(computeMediaBleedStyle("fullBleed", 390, 366)).toEqual({
      marginHorizontal: -12,
      width: 390
    });
  });

  it("falls back to no bleed when measurements are not ready", () => {
    expect(computeMediaBleedStyle("fullBleed", 0, 0)).toEqual({ marginHorizontal: 0 });
    expect(computeMediaBleedStyle("fullBleed", 390, 0)).toEqual({ marginHorizontal: 0 });
  });

  it("returns no bleed when the card already fills the window", () => {
    expect(computeMediaBleedStyle("fullBleed", 390, 390)).toEqual({ marginHorizontal: 0 });
  });
});

describe("PostCard save action", () => {
  it("renders Save inside the shared action row when onSave is provided", () => {
    const { getByTestId } = render(<PostCard post={basePost()} onSave={jest.fn()} onComment={jest.fn()} />);
    expect(getByTestId("home-feed-save-42")).toBeTruthy();
  });

  it("does not render a Save control when onSave is omitted", () => {
    const { queryByTestId } = render(<PostCard post={basePost()} onComment={jest.fn()} />);
    expect(queryByTestId("home-feed-save-42")).toBeNull();
  });

  it("shows unsaved state (Save label, not selected) by default", () => {
    const onSave = jest.fn();
    const { getByTestId, getByText } = render(<PostCard post={basePost({ saved: false })} onSave={onSave} />);
    const button = getByTestId("home-feed-save-42");
    expect(button.props.accessibilityState.selected).toBe(false);
    expect(getByText("Save")).toBeTruthy();
    fireEvent.press(button, { stopPropagation: jest.fn() });
    expect(onSave).toHaveBeenCalledTimes(1);
  });

  it("shows saved state (Saved label, selected) when post is saved", () => {
    const { getByTestId, getByText } = render(<PostCard post={basePost({ saved: true })} onSave={jest.fn()} />);
    const button = getByTestId("home-feed-save-42");
    expect(button.props.accessibilityState.selected).toBe(true);
    expect(getByText("Saved")).toBeTruthy();
  });
});
