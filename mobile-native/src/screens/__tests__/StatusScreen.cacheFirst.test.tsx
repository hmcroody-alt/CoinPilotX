/**
 * The §91 mutation this file exists to fail: "offline feed ignores cache".
 *
 * Statuses used to reach its cache only from the `catch` block, which made the
 * copy on disk reachable exclusively by failing. A slow network therefore
 * showed an empty screen for as long as the request took, and a dead one showed
 * an empty screen until the socket finally gave up — with a full set of
 * Statuses sitting on disk the whole time.
 *
 * Every test here holds the network request open deliberately. That is the
 * condition under which a cache-on-error screen and a cache-first screen differ,
 * and it is the condition a real reader on a train is in.
 */

import React from "react";
import { render, waitFor } from "@testing-library/react-native";

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
  const ReactActual = jest.requireActual("react");
  return { ResizeMode: { COVER: "cover", CONTAIN: "contain" }, Video: ReactActual.forwardRef(() => null) };
});

jest.mock("../../components/StatusCreator", () => ({ StatusCreator: () => null }));
jest.mock("../../components/NativeMediaViewer", () => ({
  mediaViewerItemFromPulseMedia: jest.fn(() => ({})),
  NativeMediaViewer: () => null
}));
jest.mock("../../api/feed", () => ({ ...jest.requireActual("../../api/feed"), mutePostAuthor: jest.fn() }));
jest.mock("../../api/profileTarget", () => ({
  profileNavigationParams: jest.fn(() => null),
  profileTargetFromAuthor: jest.fn(() => null)
}));
jest.mock("../../api/support", () => ({ blockPulseUser: jest.fn(), reportPulseTarget: jest.fn() }));
jest.mock("../../core/eventSync", () => ({ registerSyncInvalidation: jest.fn(() => () => undefined) }));

const mockListStatuses = jest.fn();
const mockLoadCachedStatusesSnapshot = jest.fn();

jest.mock("../../api/status", () => {
  const actual = jest.requireActual("../../api/status");
  return {
    ...actual,
    listStatuses: (...args: unknown[]) => mockListStatuses(...args),
    loadCachedStatusesSnapshot: (...args: unknown[]) => mockLoadCachedStatusesSnapshot(...args),
    reactToStatus: jest.fn(),
    shareStatus: jest.fn().mockResolvedValue({ share_count: 0 }),
    replyToStatus: jest.fn().mockResolvedValue({ ok: true }),
    trackStatusView: jest.fn().mockResolvedValue({ view_count: 1 }),
    deleteStatus: jest.fn(),
    updateStatus: jest.fn()
  };
});

import { StatusScreen } from "../StatusScreen";

function buildStatus(overrides: Record<string, unknown> = {}) {
  return {
    id: 701,
    status_id: 701,
    user_id: 9,
    status_type: "text",
    body: "Cached Status body",
    visibility: "public",
    author: { id: 9, user_id: 9, display_name: "Cached Author", username: "cached_author", avatar_url: "" },
    media: [],
    created_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 86_400_000).toISOString(),
    view_count: 0,
    reaction_count: 0,
    reply_count: 0,
    share_count: 0,
    story_count: 1,
    unseen_count: 1,
    viewed: false,
    ...overrides
  };
}

/** A request that never settles — a dead tunnel, not a refused connection. */
function neverResolves() {
  return new Promise(() => undefined);
}

function renderScreen(params: Record<string, unknown> = {}) {
  return render(<StatusScreen route={{ params }} navigation={{ navigate: jest.fn() }} />);
}

describe("StatusScreen reads cache before the network", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockLoadCachedStatusesSnapshot.mockResolvedValue({ items: [], rail_items: [], storedAt: null, ageMs: null });
  });

  it("shows cached Statuses while the request is still in flight", async () => {
    const cached = buildStatus();
    mockLoadCachedStatusesSnapshot.mockResolvedValue({
      items: [cached],
      rail_items: [cached],
      storedAt: Date.now() - 12 * 60_000,
      ageMs: 12 * 60_000
    });
    mockListStatuses.mockImplementation(neverResolves);

    const { findByText } = renderScreen();

    // The network call has not settled and never will. Anything on screen came
    // off disk.
    expect(await findByText("Cached Status body")).toBeTruthy();
    expect(mockListStatuses).toHaveBeenCalled();
  });

  it("consults the cache even when the request eventually succeeds", async () => {
    mockListStatuses.mockResolvedValue({ items: [buildStatus({ body: "Live body" })], rail_items: [] });
    const { findByText } = renderScreen();
    await findByText("Live body");
    // The guard against a "fix" that reinstates cache-on-error: the cache must
    // be read on the happy path too, because that is the path where the reader
    // is waiting.
    expect(mockLoadCachedStatusesSnapshot).toHaveBeenCalled();
  });

  it("replaces the cached paint with the live answer rather than merging into it", async () => {
    const cached = buildStatus({ id: 801, status_id: 801, body: "Stale body" });
    mockLoadCachedStatusesSnapshot.mockResolvedValue({
      items: [cached],
      rail_items: [cached],
      storedAt: Date.now() - 60_000,
      ageMs: 60_000
    });
    mockListStatuses.mockResolvedValue({ items: [buildStatus({ body: "Fresh body" })], rail_items: [] });

    const { findByText, queryByText } = renderScreen();
    await findByText("Fresh body");
    // A Status that the server no longer returns has expired. Leaving the
    // cached copy beside the live list would keep showing content the author
    // believes is gone.
    await waitFor(() => expect(queryByText("Stale body")).toBeNull());
  });

  it("does not open the viewer from a cached paint", async () => {
    // A deep link's target may have expired hours ago. The list is recoverable;
    // a full-screen viewer over stale media is not.
    const cached = buildStatus();
    mockLoadCachedStatusesSnapshot.mockResolvedValue({
      items: [cached],
      rail_items: [cached],
      storedAt: Date.now() - 60_000,
      ageMs: 60_000
    });
    mockListStatuses.mockImplementation(neverResolves);

    const { findByText, queryByTestId } = renderScreen({ statusId: cached.id });
    await findByText("Cached Status body");
    expect(queryByTestId("status-action-react")).toBeNull();
  });

  it("reports how old the cached Statuses are once the request fails", async () => {
    const cached = buildStatus();
    mockLoadCachedStatusesSnapshot.mockResolvedValue({
      items: [cached],
      rail_items: [cached],
      storedAt: Date.now() - 12 * 60_000,
      ageMs: 12 * 60_000
    });
    mockListStatuses.mockRejectedValue(new Error("Network request failed"));

    const { findByText } = renderScreen();
    expect(await findByText("Showing saved Status · 12m ago")).toBeTruthy();
  });

  it("says only 'saved' when the age is unknown", async () => {
    // An entry written before cache entries carried timestamps. Rendering
    // "just now" here would be the app asserting a freshness it cannot observe.
    const cached = buildStatus();
    mockLoadCachedStatusesSnapshot.mockResolvedValue({
      items: [cached],
      rail_items: [cached],
      storedAt: null,
      ageMs: null
    });
    mockListStatuses.mockRejectedValue(new Error("Network request failed"));

    const { findByText } = renderScreen();
    expect(await findByText("Showing saved Status")).toBeTruthy();
  });

  it("errors only when the request fails with nothing cached", async () => {
    mockListStatuses.mockRejectedValue(new Error("Network request failed"));
    const { findByText, queryByText } = renderScreen();
    expect(await findByText(/Network request failed/)).toBeTruthy();
    expect(queryByText("Showing saved Status")).toBeNull();
  });
});
