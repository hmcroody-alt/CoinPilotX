import React from "react";
import { fireEvent, render, waitFor, act } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn(),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn()
}));

jest.mock("expo-haptics", () => ({
  impactAsync: jest.fn().mockResolvedValue(undefined),
  ImpactFeedbackStyle: { Light: "light", Medium: "medium" }
}));

jest.mock("../../theme/logiNexusMotion", () => ({
  useLogiNexusReducedMotion: jest.fn().mockReturnValue(true)
}));

jest.mock("expo-av", () => {
  const React = jest.requireActual("react");
  return {
    ResizeMode: { COVER: "cover", CONTAIN: "contain" },
    Video: React.forwardRef(() => null)
  };
});

jest.mock("../../components/StatusCreator", () => ({
  StatusCreator: () => null
}));

jest.mock("../../components/NativeMediaViewer", () => ({
  mediaViewerItemFromPulseMedia: jest.fn(() => ({})),
  NativeMediaViewer: () => null
}));

jest.mock("../../api/feed", () => {
  const actual = jest.requireActual("../../api/feed");
  return {
    ...actual,
    mutePostAuthor: jest.fn()
  };
});

jest.mock("../../api/profileTarget", () => ({
  profileNavigationParams: jest.fn(() => null),
  profileTargetFromAuthor: jest.fn(() => null)
}));

jest.mock("../../api/support", () => ({
  blockPulseUser: jest.fn(),
  reportPulseTarget: jest.fn()
}));

jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));

const mockListStatuses = jest.fn();
const mockReactToStatus = jest.fn();
const mockShareStatus = jest.fn().mockResolvedValue({ share_count: 1 });
const mockReplyToStatus = jest.fn().mockResolvedValue({ ok: true });
const mockTrackStatusView = jest.fn().mockResolvedValue({ view_count: 1 });
const mockLoadCachedStatuses = jest.fn().mockResolvedValue({ items: [], rail_items: [] });

jest.mock("../../api/status", () => {
  const actual = jest.requireActual("../../api/status");
  return {
    ...actual,
    listStatuses: (...args: unknown[]) => mockListStatuses(...args),
    loadCachedStatuses: (...args: unknown[]) => mockLoadCachedStatuses(...args),
    reactToStatus: (...args: unknown[]) => mockReactToStatus(...args),
    shareStatus: (...args: unknown[]) => mockShareStatus(...args),
    replyToStatus: (...args: unknown[]) => mockReplyToStatus(...args),
    trackStatusView: (...args: unknown[]) => mockTrackStatusView(...args),
    deleteStatus: jest.fn(),
    updateStatus: jest.fn()
  };
});

import { StatusScreen } from "../StatusScreen";

function buildStatus(overrides: Record<string, unknown> = {}) {
  return {
    id: 501,
    status_id: 501,
    user_id: 9,
    status_type: "text",
    body: "A native Status fixture for reaction tests.",
    visibility: "public",
    author: { id: 9, user_id: 9, display_name: "Fixture Author", username: "fixture_author", avatar_url: "" },
    media: [],
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 86_400_000).toISOString(),
    view_count: 4,
    reaction_count: 0,
    reply_count: 0,
    share_count: 0,
    story_count: 1,
    unseen_count: 1,
    viewed: false,
    ...overrides
  };
}

function renderStatusScreen(status = buildStatus()) {
  mockListStatuses.mockResolvedValue({ items: [status], rail_items: [status] });
  const navigation = { navigate: jest.fn() };
  const route = { params: { statusId: status.id } };
  const utils = render(<StatusScreen route={route} navigation={navigation} />);
  return { ...utils, navigation };
}

describe("StatusScreen reaction state machine", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockShareStatus.mockResolvedValue({ share_count: 1 });
    mockReplyToStatus.mockResolvedValue({ ok: true });
    mockTrackStatusView.mockResolvedValue({ view_count: 1 });
    mockLoadCachedStatuses.mockResolvedValue({ items: [], rail_items: [] });
  });

  it("auto-opens the requested Status and applies an optimistic Love reaction, then reconciles with the server count", async () => {
    let resolveReact: (value: unknown) => void = () => undefined;
    mockReactToStatus.mockReturnValue(
      new Promise((resolve) => {
        resolveReact = resolve;
      })
    );
    const { findByTestId, getByLabelText } = renderStatusScreen();
    const reactButton = await findByTestId("status-action-react");

    fireEvent.press(reactButton);
    expect(mockReactToStatus).toHaveBeenCalledWith(501, "love");
    await waitFor(() => expect(getByLabelText("1 reaction")).toBeTruthy());

    await act(async () => {
      resolveReact({ ok: true, status_id: 501, reaction_type: "love", reaction_count: 7 });
    });
    await waitFor(() => expect(getByLabelText("7 reactions")).toBeTruthy());
  });

  it("rolls back the optimistic reaction and count when the server call fails", async () => {
    mockReactToStatus.mockRejectedValue(new Error("network down"));
    const { findByTestId, getByTestId, queryByLabelText } = renderStatusScreen();
    const reactButton = await findByTestId("status-action-react");

    await act(async () => {
      fireEvent.press(reactButton);
    });

    await waitFor(() => expect(queryByLabelText("1 reaction")).toBeNull());
    expect(getByTestId("status-action-react").props.accessibilityState?.selected).toBeFalsy();
  });

  it("ignores a stale reaction response so a newer mutation is never overwritten (no count drift)", async () => {
    let resolveFirst: (value: unknown) => void = () => undefined;
    const first = new Promise((resolve) => {
      resolveFirst = resolve;
    });
    mockReactToStatus.mockReturnValueOnce(first).mockResolvedValueOnce({ ok: true, status_id: 501, reaction_type: "fire", reaction_count: 2 });

    const { findByTestId, getByTestId, getByLabelText } = renderStatusScreen();
    const reactButton = await findByTestId("status-action-react");

    // First tap: Love (slow to resolve).
    fireEvent.press(reactButton);
    // Second mutation before the first resolves: Fire, via the reaction tray.
    fireEvent(reactButton, "onLongPress");
    fireEvent.press(getByTestId("status-reaction-fire"));

    await waitFor(() => expect(getByLabelText("2 reactions")).toBeTruthy());

    // The stale first response now resolves with a count that must be discarded.
    await act(async () => {
      resolveFirst({ ok: true, status_id: 501, reaction_type: "love", reaction_count: 99 });
    });
    expect(getByLabelText("2 reactions")).toBeTruthy();
  });

  it("does not issue a duplicate request when the already-selected reaction is tapped again", async () => {
    mockReactToStatus.mockResolvedValue({ ok: true, status_id: 501, reaction_type: "love", reaction_count: 1 });
    const { findByTestId, getByLabelText } = renderStatusScreen();
    const reactButton = await findByTestId("status-action-react");

    fireEvent.press(reactButton);
    await waitFor(() => expect(getByLabelText("1 reaction")).toBeTruthy());
    expect(mockReactToStatus).toHaveBeenCalledTimes(1);

    fireEvent.press(reactButton);
    await waitFor(() => expect(mockReactToStatus).toHaveBeenCalledTimes(1));
  });

  it("opens the reply composer for the active Status without a duplicate composer or new route", async () => {
    const { findByTestId, getByLabelText, navigation } = renderStatusScreen();
    await findByTestId("status-action-react");
    fireEvent.press(getByLabelText("Reply to Status"));
    await waitFor(() => expect(getByLabelText("Status reply")).toBeTruthy());
    expect(navigation.navigate).not.toHaveBeenCalledWith("Reply");
  });

  it("shares via the existing production share flow and does not open a new route", async () => {
    const { findByTestId, getByLabelText, navigation } = renderStatusScreen();
    await findByTestId("status-action-react");
    const shareButton = getByLabelText("Share Status");
    await act(async () => {
      fireEvent.press(shareButton);
    });
    expect(mockShareStatus).toHaveBeenCalledWith(501);
    expect(navigation.navigate).not.toHaveBeenCalledWith(expect.stringMatching(/share/i));
  });
});
