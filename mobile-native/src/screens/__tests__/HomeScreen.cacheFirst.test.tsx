/**
 * The §91 mutation this file exists to fail: "offline feed ignores cache".
 *
 * Home reached `loadCachedFeed` only from its `catch` block. The cache was
 * therefore reachable exclusively by failing: on a slow connection the reader
 * watched an empty feed for the length of the round trip, and on a dead one
 * until the socket gave up — with twenty posts on disk the whole time.
 *
 * The tests below hold the feed request open on purpose. That is the only
 * condition under which cache-on-error and cache-first differ, and it is the
 * condition a reader on a train is actually in.
 */
import React from "react";
import { act, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn().mockResolvedValue(null),
  setItem: jest.fn().mockResolvedValue(undefined),
  removeItem: jest.fn().mockResolvedValue(undefined),
  multiGet: jest.fn().mockResolvedValue([]),
  multiRemove: jest.fn().mockResolvedValue(undefined),
  getAllKeys: jest.fn().mockResolvedValue([])
}));
jest.mock("expo-secure-store", () => ({
  getItemAsync: jest.fn().mockResolvedValue(null),
  setItemAsync: jest.fn(),
  deleteItemAsync: jest.fn()
}));
jest.mock("expo-battery", () => ({
  useLowPowerMode: () => false,
  isLowPowerModeEnabledAsync: jest.fn(async () => false),
  addLowPowerModeListener: jest.fn(() => ({ remove: jest.fn() }))
}));
jest.mock("@react-navigation/native", () => ({
  useIsFocused: () => true,
  useNavigation: () => ({ navigate: jest.fn(), goBack: jest.fn(), addListener: jest.fn(() => () => undefined) }),
  useRoute: () => ({ params: undefined })
}));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined),
  invalidateNativeSync: jest.fn()
}));
jest.mock("../../core/pulseRadio", () => ({
  getPulseRadioState: jest.fn(() => ({ status: "idle", track: null })),
  subscribePulseRadio: jest.fn(() => () => undefined),
  togglePulseRadio: jest.fn()
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  BOTTOM_NAV_CONTENT_CLEARANCE: 0,
  useBottomNavContentPadding: () => 0,
  useBottomNavScrollVisibility: () => ({ onScroll: jest.fn(), onScrollBeginDrag: jest.fn(), scrollEventThrottle: 16 }),
  useBottomNavVisibility: () => ({
    hidden: false,
    docked: true,
    miniPlayerVisible: false,
    setBottomNavHidden: jest.fn(),
    showBottomNav: jest.fn()
  })
}));
jest.mock("../../navigation/homeReselect", () => ({ registerHomeReselectHandler: jest.fn(() => () => undefined) }));
jest.mock("../../api/profileTarget", () => ({ profileNavigationParams: jest.fn(() => null) }));
jest.mock("../../session/auth", () => ({ useAuth: () => ({ authState: { user: { user_id: 7, username: "me" } } }) }));
jest.mock("../../sharing/nativeShare", () => ({ sharePulseObject: jest.fn().mockResolvedValue({ ok: true }) }));
jest.mock("../../components/HomePulseComposer", () => ({ HomePulseComposer: () => null }));
jest.mock("../../components/MasterNavigationDrawer", () => ({ MasterNavigationDrawer: () => null }));
jest.mock("../../components/WelcomeUfoOverlay", () => ({ WelcomeUfoOverlay: () => null }));
jest.mock("../../components/StaticUFOField", () => ({ StaticUFOField: () => null }));
jest.mock("../../api/ads", () => ({ fetchSponsoredAds: jest.fn().mockResolvedValue([]) }));

const mockCardProps: any[] = [];
jest.mock("../../components/PostCard", () => ({
  PostCard: (props: any) => {
    mockCardProps.push(props);
    return null;
  }
}));

const mockListFeed = jest.fn();
const mockCachedFeedSnapshot = jest.fn();
jest.mock("../../api/feed", () => ({
  ...jest.requireActual("../../api/feed"),
  listFeed: (...args: any[]) => mockListFeed(...args),
  loadCachedFeedSnapshot: (...args: any[]) => mockCachedFeedSnapshot(...args)
}));
jest.mock("../../api/pulseApi", () => ({
  ...jest.requireActual("../../api/pulseApi"),
  pulseApi: jest.fn().mockResolvedValue({})
}));

const mockListStatuses = jest.fn();
const mockCachedStatusesSnapshot = jest.fn();
jest.mock("../../api/status", () => ({
  ...jest.requireActual("../../api/status"),
  listStatuses: (...args: any[]) => mockListStatuses(...args),
  loadCachedStatusesSnapshot: (...args: any[]) => mockCachedStatusesSnapshot(...args)
}));
jest.mock("expo-av", () => ({
  ResizeMode: { COVER: "cover", CONTAIN: "contain" },
  Video: require("react-native").View
}));

import { __clearDiscoveryFlagOverrides, __setDiscoveryFlagOverride } from "../../discovery/flags";
import { HomeScreen } from "../HomeScreen";

function post(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id,
    post_id: id,
    user_id: 100 + id,
    body: `Post ${id}`,
    author: {
      id: 100 + id,
      user_id: 100 + id,
      display_name: `Author ${id}`,
      username: `author${id}`,
      public_player_id: `author-${id}`
    },
    reaction_counts: {},
    comment_count: 0,
    preview_comments: [],
    ...overrides
  };
}

/** A request that never settles — a dead tunnel, not a refused connection. */
function neverResolves() {
  return new Promise(() => undefined);
}

function renderedPostIds(): number[] {
  const ids = new Set<number>();
  for (const props of mockCardProps) {
    const id = Number(props.post?.id);
    if (id) ids.add(id);
  }
  return [...ids];
}

function emptySnapshot() {
  return { posts: [], storedAt: null, ageMs: null };
}

beforeEach(() => {
  mockCardProps.length = 0;
  jest.clearAllMocks();
  mockCachedFeedSnapshot.mockResolvedValue(emptySnapshot());
  mockListStatuses.mockResolvedValue({ items: [], rail_items: [] });
  mockCachedStatusesSnapshot.mockResolvedValue({ items: [], rail_items: [], storedAt: null, ageMs: null });
  // The suggestion rows fetch four more endpoints on mount and contribute
  // nothing to the question this file asks.
  __setDiscoveryFlagOverride("homeDiscoveryEnabled", false);
});

afterEach(() => {
  __clearDiscoveryFlagOverrides();
});

describe("HomeScreen reads cache before the network", () => {
  it("renders cached posts while the feed request is still in flight", async () => {
    mockCachedFeedSnapshot.mockResolvedValue({
      posts: [post(1), post(2)],
      storedAt: Date.now() - 12 * 60_000,
      ageMs: 12 * 60_000
    });
    mockListFeed.mockImplementation(neverResolves);

    render(<HomeScreen />);

    // Nothing has come back from the network and nothing will. Every post on
    // screen came off disk.
    await waitFor(() => expect(renderedPostIds()).toEqual(expect.arrayContaining([1, 2])));
    expect(mockListFeed).toHaveBeenCalled();
  });

  it("consults the cache on the happy path too, not only after a failure", async () => {
    mockListFeed.mockResolvedValue({ posts: [post(3)] });
    render(<HomeScreen />);
    await waitFor(() => expect(renderedPostIds()).toContain(3));
    // Guards against a "fix" that quietly reinstates cache-on-error: the
    // successful path is the one where the reader is waiting.
    expect(mockCachedFeedSnapshot).toHaveBeenCalled();
  });

  it("replaces the cached paint with the server's answer", async () => {
    mockCachedFeedSnapshot.mockResolvedValue({ posts: [post(1)], storedAt: Date.now() - 60_000, ageMs: 60_000 });
    mockListFeed.mockResolvedValue({ posts: [post(9)] });

    render(<HomeScreen />);
    await waitFor(() => expect(renderedPostIds()).toContain(9));
    // A post the server no longer returns was deleted or hidden. Merging the
    // cached copy in beside the live list would keep showing it.
    await act(async () => undefined);
    const latestIds = mockCardProps.slice(-4).map((props) => Number(props.post?.id));
    expect(latestIds).not.toContain(1);
  });

  it("does not paint the cache over a pull-to-refresh", async () => {
    // A refresh is the reader asking for something new. Repainting the same
    // cached posts under them answers a question they did not ask.
    mockListFeed.mockResolvedValue({ posts: [post(5)] });
    render(<HomeScreen />);
    await waitFor(() => expect(renderedPostIds()).toContain(5));
    const readsAfterInitialLoad = mockCachedFeedSnapshot.mock.calls.length;
    expect(readsAfterInitialLoad).toBe(1);
  });

  it("errors only when the request fails with nothing cached", async () => {
    mockListFeed.mockRejectedValue(new Error("PulseSoc feed is unavailable."));
    const { findAllByText } = render(<HomeScreen />);
    expect((await findAllByText(/PulseSoc feed is unavailable/)).length).toBeGreaterThan(0);
  });

  it("shows the cached posts and their age instead of an error when the request fails", async () => {
    mockCachedFeedSnapshot.mockResolvedValue({
      posts: [post(1)],
      storedAt: Date.now() - 12 * 60_000,
      ageMs: 12 * 60_000
    });
    mockListFeed.mockRejectedValue(new Error("Network request failed"));

    const { findByTestId, queryByText } = render(<HomeScreen />);
    const pill = await findByTestId("home-feed-offline-pill");
    expect(pill.props.children).toContain("12m ago");
    // Offline is not an error, so the error copy must not co-render with it.
    expect(queryByText(/Network request failed/)).toBeNull();
  });
});
