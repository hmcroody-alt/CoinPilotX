import { fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";
import { ProfilePostGridTile, ProfileScreen } from "../ProfileScreen";
import { PulsePost } from "../../api/feed";

const navigate = jest.fn();
const mockListFeed = jest.fn();
const mockGetPublicProfile = jest.fn(async (..._args: unknown[]) => ({ user_id: 8, display_name: "Maria Cherie", username: "mariacherie", public_player_id: "Pilot-8008", post_count: 5 }));

jest.mock("../../api/profile", () => ({
  getMyProfile: jest.fn(async () => ({ user_id: 7, display_name: "Roody Cherie", username: "roodycherie", public_player_id: "roodycherie", post_count: 4 })),
  getPublicProfile: (...args: unknown[]) => mockGetPublicProfile(...args),
  listPublicProfilePosts: jest.fn(),
  loadCachedProfile: jest.fn(),
  profileErrorState: jest.fn(() => ({ title: "Error", body: "Error", retryable: true, offline: false })),
  toggleProfileFollow: jest.fn()
}));
jest.mock("../../api/feed", () => ({
  listFeed: (...args: unknown[]) => mockListFeed(...args),
  pulsePostUrl: jest.fn(), reactToPost: jest.fn(), repostPost: jest.fn(), deletePost: jest.fn(), savablePostId: (post: PulsePost) => post.id
}));
jest.mock("../../components/ProfileHeader", () => ({ ProfileHeader: () => null }));
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

const posts: PulsePost[] = [
  { id: 1, post_id: 1, body: "Text-only identity post" },
  { id: 2, post_id: 2, body: "Video", media: [{ media_type: "video", thumbnail_url: "https://cdn.example/video.jpg", duration_seconds: 62 }] },
  { id: 3, post_id: 3, body: "Album", media: [{ media_type: "image", thumbnail_url: "https://cdn.example/a.jpg" }, { media_type: "image", thumbnail_url: "https://cdn.example/b.jpg" }] },
  { id: 4, post_id: 4, body: "Music", music: { title: "Pulse" }, thumbnail_url: "https://cdn.example/music.jpg" }
];

describe("Profile posts grid", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockListFeed.mockResolvedValue({ posts, next_offset: 4, has_more: false });
  });

  it("defaults to a three-column grid and never renders full PostCards", async () => {
    const screen = render(<ProfileScreen navigation={{ navigate } as never} />);
    await waitFor(() => expect(screen.getByTestId("profile-grid-tile-1")).toBeTruthy());
    expect(screen.UNSAFE_getByType(require("react-native").FlatList).props.numColumns).toBe(3);
    expect(screen.queryByTestId("post-card-1")).toBeNull();
  });

  it("opens the exact selected post with profile grid context", async () => {
    const screen = render(<ProfileScreen navigation={{ navigate } as never} />);
    await waitFor(() => screen.getByTestId("profile-grid-tile-3"));
    fireEvent.press(screen.getByTestId("profile-grid-tile-3"));
    expect(navigate).toHaveBeenCalledWith("ProfilePostViewer", expect.objectContaining({ postId: 3, postIds: [1, 2, 3, 4], nextOffset: 4, hasMore: false, contentTab: "posts", source: "PROFILE_GRID" }));
  });

  it("renders a readable designed tile for text-only posts", () => {
    const screen = render(<ProfilePostGridTile post={posts[0]} onPress={jest.fn()} />);
    expect(screen.getByText("Text-only identity post")).toBeTruthy();
    expect(screen.getByLabelText(/Text post/)).toBeTruthy();
  });

  it("announces video duration and represents multi-media tiles", () => {
    const video = render(<ProfilePostGridTile post={posts[1]} onPress={jest.fn()} />);
    expect(video.getByText("1:02")).toBeTruthy();
    expect(video.getByLabelText(/Duration 1:02/)).toBeTruthy();
    const album = render(<ProfilePostGridTile post={posts[2]} onPress={jest.fn()} />);
    expect(album.getByTestId("profile-grid-tile-3")).toBeTruthy();
  });

  it("deduplicates pagination without clearing the existing grid", async () => {
    mockListFeed.mockResolvedValueOnce({ posts, next_offset: 4, has_more: true }).mockResolvedValueOnce({ posts: [posts[3], { id: 5, post_id: 5, body: "Next" }], next_offset: 6, has_more: false });
    const screen = render(<ProfileScreen navigation={{ navigate } as never} />);
    await waitFor(() => screen.getByTestId("profile-grid-tile-4"));
    fireEvent(screen.UNSAFE_getByType(require("react-native").FlatList), "onEndReached");
    await waitFor(() => expect(screen.getByTestId("profile-grid-tile-5")).toBeTruthy());
    expect(screen.getAllByTestId("profile-grid-tile-4")).toHaveLength(1);
  });

  it("paginates another user's canonical profile instead of repeating its first page", async () => {
    mockListFeed
      .mockResolvedValueOnce({ posts, next_offset: 4, has_more: true })
      .mockResolvedValueOnce({ posts: [posts[3], { id: 5, post_id: 5, body: "Visitor next" }], next_offset: 6, has_more: false });
    const screen = render(<ProfileScreen route={{ params: { profileKey: "mariacherie" } } as never} navigation={{ navigate } as never} />);
    await waitFor(() => screen.getByTestId("profile-grid-tile-4"));
    fireEvent(screen.UNSAFE_getByType(require("react-native").FlatList), "onEndReached");

    await waitFor(() => expect(screen.getByTestId("profile-grid-tile-5")).toBeTruthy());
    expect(mockListFeed).toHaveBeenNthCalledWith(1, { feed: "for_you", profile: "Pilot-8008", limit: 20, offset: 0 });
    expect(mockListFeed).toHaveBeenNthCalledWith(2, { feed: "for_you", profile: "Pilot-8008", limit: 20, offset: 4 });
    expect(screen.getAllByTestId("profile-grid-tile-4")).toHaveLength(1);
  });
});
