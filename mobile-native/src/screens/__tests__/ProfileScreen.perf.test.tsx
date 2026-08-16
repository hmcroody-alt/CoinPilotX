/**
 * Performance regression tests for the profile load path.
 *
 * The properties under test are about ORDERING and REQUEST COUNT, which is what
 * a latency regression actually looks like in code. None of them assert timings.
 *
 *  - the grid request is issued alongside the profile request, not after it,
 *    whenever the lookup key is already known (the common case);
 *  - a cached profile is painted while the canonical request is still in flight;
 *  - the cache is never allowed to overwrite a canonical response that has
 *    already landed — cache is display, never authority;
 *  - an eager grid fetched under a key the server then disagrees with is
 *    discarded rather than rendered as someone else's posts.
 */

import { render, waitFor } from "@testing-library/react-native";
import React from "react";
import { ProfileScreen } from "../ProfileScreen";
import { PulsePost } from "../../api/feed";

const navigate = jest.fn();
const mockListFeed = jest.fn();
const mockAuthState = { user: { user_id: 7 } };
const mockGetMyProfile = jest.fn();
const mockGetPublicProfile = jest.fn();
const mockLoadCachedProfile = jest.fn();

jest.mock("../../api/profile", () => ({
  getMyProfile: () => mockGetMyProfile(),
  getPublicProfile: (...args: unknown[]) => mockGetPublicProfile(...args),
  listPublicProfilePosts: (...args: unknown[]) => mockListFeed(...args),
  loadCachedProfile: (...args: unknown[]) => mockLoadCachedProfile(...args),
  profileErrorState: jest.fn(() => ({ title: "Error", body: "Error", retryable: true, offline: false })),
  toggleProfileFollow: jest.fn()
}));
jest.mock("../../api/feed", () => ({
  listFeed: (...args: unknown[]) => mockListFeed(...args),
  pulsePostUrl: jest.fn(), reactToPost: jest.fn(), repostPost: jest.fn(), deletePost: jest.fn(), savablePostId: (post: PulsePost) => post.id
}));
// Renders the display name so a test can see WHICH profile is currently painted.
jest.mock("../../components/ProfileHeader", () => {
  const { Text } = require("react-native");
  return { ProfileHeader: ({ profile }: { profile: { display_name?: string } }) => <Text>{profile?.display_name || ""}</Text> };
});
jest.mock("../../components/GalacticAtmosphere", () => ({ GalacticAtmosphere: () => null }));
jest.mock("../../components/Screen", () => ({
  LogiNexusScreenShell: ({ children }: { children: React.ReactNode }) => children,
  LogiNexusStatePanel: ({ title }: { title: string }) => title
}));
jest.mock("../../navigation/refreshCoordinator", () => ({ registerRefreshDestination: jest.fn(() => jest.fn()) }));
jest.mock("../../social/actionGuard", () => ({ actionKey: jest.fn(), useSocialActionGuard: () => ({ run: jest.fn(), isItemBusy: () => false }) }));
jest.mock("../../social/savedStore", () => ({ peekSaveState: jest.fn() }));
jest.mock("../../social/useSaveAction", () => ({ setSaved: jest.fn() }));
jest.mock("../../sharing/nativeShare", () => ({ sharePulseObject: jest.fn() }));
jest.mock("../../core/eventSync", () => ({ invalidateNativeSync: jest.fn() }));
jest.mock("../../api/messenger", () => ({ openDirectConversation: jest.fn() }));
jest.mock("../../navigation/BottomNavVisibility", () => ({ useBottomNavSurface: () => ({ contentPadding: {}, handlers: { onScroll: jest.fn(), onScrollBeginDrag: jest.fn(), scrollEventThrottle: 16 } }) }));
jest.mock("../../session/auth", () => ({ useAuth: () => ({ authState: mockAuthState }) }));
// Owner-only, and it reaches the network. Left unmocked it outlives the test and
// logs after teardown, which is noise that hides real failures.
jest.mock("../../profile/usePremiumTile", () => ({ usePremiumTile: () => undefined }));
jest.mock("../../payments/premiumAnalytics", () => ({ trackPremium: jest.fn() }));

const posts: PulsePost[] = [{ id: 1, post_id: 1, body: "One" }];

/** A promise plus the handle to settle it, so a test can control ordering. */
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((settle) => { resolve = settle; });
  return { promise, resolve };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockAuthState.user = { user_id: 7 };
  mockLoadCachedProfile.mockResolvedValue(null);
  mockGetMyProfile.mockResolvedValue({ user_id: 7, display_name: "Roody Cherie", username: "roodycherie", post_count: 4 });
  mockGetPublicProfile.mockResolvedValue({ user_id: 8, display_name: "Maria Cherie", username: "mariacherie", post_count: 5 });
  mockListFeed.mockResolvedValue({ posts, next_offset: 1, has_more: false });
});

describe("profile load parallelism", () => {
  it("issues the owner grid request without waiting for the profile response", async () => {
    const profile = deferred<Record<string, unknown>>();
    mockGetMyProfile.mockReturnValue(profile.promise);

    render(<ProfileScreen navigation={{ navigate } as never} />);

    // The grid request is in flight while the profile request is still pending.
    // This is the assertion that fails if the waterfall is reintroduced.
    await waitFor(() => expect(mockListFeed).toHaveBeenCalledTimes(1));
    expect(mockGetMyProfile).toHaveBeenCalledTimes(1);

    profile.resolve({ user_id: 7, display_name: "Roody Cherie", post_count: 4 });
    await waitFor(() => expect(mockListFeed).toHaveBeenCalledTimes(1));
  });

  it("issues a visitor grid request in parallel when the route carries the user id", async () => {
    const profile = deferred<Record<string, unknown>>();
    mockGetPublicProfile.mockReturnValue(profile.promise);

    render(<ProfileScreen navigation={{ navigate } as never} route={{ params: { userId: 8 } } as never} />);

    await waitFor(() => expect(mockListFeed).toHaveBeenCalledTimes(1));
    expect(mockListFeed.mock.calls[0][0]).toMatchObject({ userId: 8 });

    profile.resolve({ user_id: 8, display_name: "Maria Cherie", post_count: 5 });
    await waitFor(() => expect(mockGetPublicProfile).toHaveBeenCalledTimes(1));
    // The eager key matched the canonical identity, so no second grid request.
    expect(mockListFeed).toHaveBeenCalledTimes(1);
  });

  it("refetches the grid when the server's canonical identity disagrees with the eager key", async () => {
    // The route said 8; the server resolves the target to 99. The grid raced
    // under 8 belongs to a different person and must not be rendered.
    mockGetPublicProfile.mockResolvedValue({ user_id: 99, display_name: "Someone Else", post_count: 1 });

    render(<ProfileScreen navigation={{ navigate } as never} route={{ params: { userId: 8 } } as never} />);

    await waitFor(() => expect(mockListFeed).toHaveBeenCalledTimes(2));
    expect(mockListFeed.mock.calls[0][0]).toMatchObject({ userId: 8 });
    expect(mockListFeed.mock.calls[1][0]).toMatchObject({ userId: 99 });
  });

  it("waits for the canonical profile when the route has only a username", async () => {
    mockGetPublicProfile.mockResolvedValue({ user_id: 8, display_name: "Maria Cherie", username: "mariacherie", post_count: 5 });

    render(<ProfileScreen navigation={{ navigate } as never} route={{ params: { username: "mariacherie" } } as never} />);

    // No key is known up front, so guessing one would fetch the wrong grid.
    await waitFor(() => expect(mockListFeed).toHaveBeenCalledTimes(1));
    expect(mockListFeed.mock.calls[0][0]).toMatchObject({ userId: 8 });
  });
});

describe("stale-while-revalidate paint", () => {
  it("paints the cached profile while the canonical request is still in flight", async () => {
    const profile = deferred<Record<string, unknown>>();
    mockGetMyProfile.mockReturnValue(profile.promise);
    mockLoadCachedProfile.mockResolvedValue({ user_id: 7, display_name: "Cached Roody", post_count: 4 });

    const screen = render(<ProfileScreen navigation={{ navigate } as never} />);

    await waitFor(() => expect(screen.getByText("Cached Roody")).toBeTruthy());

    profile.resolve({ user_id: 7, display_name: "Canonical Roody", post_count: 4 });
    await waitFor(() => expect(screen.getByText("Canonical Roody")).toBeTruthy());
    expect(screen.queryByText("Cached Roody")).toBeNull();
  });

  it("never lets a slow cache read overwrite a canonical response that already landed", async () => {
    const cached = deferred<Record<string, unknown>>();
    mockLoadCachedProfile.mockReturnValue(cached.promise);
    mockGetMyProfile.mockResolvedValue({ user_id: 7, display_name: "Canonical Roody", post_count: 4 });

    const screen = render(<ProfileScreen navigation={{ navigate } as never} />);
    await waitFor(() => expect(screen.getByText("Canonical Roody")).toBeTruthy());

    // The disk read finally answers, with an older copy. Cache is display, not
    // authority: it must lose to a response that has already rendered.
    cached.resolve({ user_id: 7, display_name: "Stale Roody", post_count: 4 });
    await waitFor(() => expect(screen.getByText("Canonical Roody")).toBeTruthy());
    expect(screen.queryByText("Stale Roody")).toBeNull();
  });

  it("does not let a rejected duplicate refresh orphan the load already in flight", async () => {
    // A second pull-to-refresh while one is running is dropped. If dropping it
    // also claimed a sequence number, the running load would see itself as
    // stale, discard its response and never clear its spinner.
    const profile = deferred<Record<string, unknown>>();
    mockGetMyProfile.mockReturnValue(profile.promise);

    const registerRefreshDestination = require("../../navigation/refreshCoordinator").registerRefreshDestination as jest.Mock;
    const screen = render(<ProfileScreen navigation={{ navigate } as never} />);
    await waitFor(() => expect(registerRefreshDestination).toHaveBeenCalled());
    const destination = registerRefreshDestination.mock.calls.at(-1)![1];

    profile.resolve({ user_id: 7, display_name: "First", post_count: 4 });
    await waitFor(() => expect(screen.getByText("First")).toBeTruthy());

    const slow = deferred<Record<string, unknown>>();
    mockGetMyProfile.mockReturnValue(slow.promise);
    const running = destination.refresh();
    await waitFor(() => expect(destination.isRefreshing()).toBe(true));

    // The duplicate is refused outright.
    await destination.refresh();

    slow.resolve({ user_id: 7, display_name: "Refreshed", post_count: 4 });
    await running;

    await waitFor(() => expect(screen.getByText("Refreshed")).toBeTruthy());
    expect(destination.isRefreshing()).toBe(false);
  });

  it("reuses the one cache read on the error path instead of hitting disk twice", async () => {
    mockGetMyProfile.mockRejectedValue(new Error("offline"));
    mockLoadCachedProfile.mockResolvedValue({ user_id: 7, display_name: "Cached Roody", post_count: 4 });

    const screen = render(<ProfileScreen navigation={{ navigate } as never} />);

    await waitFor(() => expect(screen.getByText("Cached Roody")).toBeTruthy());
    expect(mockLoadCachedProfile).toHaveBeenCalledTimes(1);
  });
});
