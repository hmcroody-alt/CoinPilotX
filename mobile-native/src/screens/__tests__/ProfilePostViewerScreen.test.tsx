import { fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";
import { ProfilePostViewerScreen } from "../ProfilePostViewerScreen";

const mockSetSaved = jest.fn(async (_object?: unknown, _saved?: unknown) => ({ ok: true, saved: true }));
const mockGetPostDetail = jest.fn(async (postId: number) => ({ post: { id: postId, post_id: postId, body: `Post ${postId}` } }));
const mockListFeed = jest.fn();

jest.mock("../../api/feed", () => ({
  getPostDetail: (id: number) => mockGetPostDetail(id),
  listFeed: (...args: unknown[]) => mockListFeed(...args),
  savablePostId: (post: { id: number }) => post.id,
  pulsePostUrl: jest.fn(), reactToPost: jest.fn(), repostPost: jest.fn(), deletePost: jest.fn()
}));
jest.mock("../../components/PostCard", () => ({
  PostCard: ({ post, onSave }: { post: { id: number }; onSave: (post: { id: number }) => void }) => {
    const { Pressable, Text } = require("react-native");
    return <Pressable testID={`viewer-save-${post.id}`} accessibilityRole="button" accessibilityLabel="Save post" onPress={() => onSave(post)}><Text>Save</Text></Pressable>;
  }
}));
jest.mock("../../social/useSaveAction", () => ({ setSaved: (object: unknown, saved: unknown) => mockSetSaved(object, saved) }));
jest.mock("../../social/savedStore", () => ({ peekSaveState: jest.fn(() => ({ saved: false })) }));
jest.mock("../../social/actionGuard", () => ({ actionKey: jest.fn(), useSocialActionGuard: () => ({ run: jest.fn(), isItemBusy: () => false }) }));
jest.mock("../../sharing/nativeShare", () => ({ sharePulseObject: jest.fn() }));
jest.mock("../../core/eventSync", () => ({ invalidateNativeSync: jest.fn() }));

describe("ProfilePostViewerScreen", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockListFeed.mockResolvedValue({ posts: [], next_offset: 3, has_more: false });
  });

  it("loads the exact selected post and wires its visible Save action to canonical saved storage", async () => {
    const route = { params: { profileId: 7, profileKey: "roodycherie", postId: 22, postIds: [11, 22, 33], owner: true, source: "PROFILE_GRID" } };
    const screen = render(<ProfilePostViewerScreen route={route as never} navigation={{ goBack: jest.fn(), navigate: jest.fn() } as never} />);
    await waitFor(() => expect(screen.getByTestId("viewer-save-22")).toBeTruthy());
    fireEvent.press(screen.getByTestId("viewer-save-22"));
    await waitFor(() => expect(mockSetSaved).toHaveBeenCalledWith({ type: "post", id: 22 }, true));
    expect(mockGetPostDetail).toHaveBeenCalledWith(22);
  });

  it("uses one continuous vertical list and visits every available post exactly once", async () => {
    const route = { params: { profileId: 7, profileKey: "roodycherie", postId: 22, postIds: [11, 22, 33, 44], owner: true, source: "PROFILE_GRID" } };
    const screen = render(<ProfilePostViewerScreen route={route as never} navigation={{ goBack: jest.fn(), navigate: jest.fn() } as never} />);
    const list = screen.UNSAFE_getByType(require("react-native").FlatList);

    expect(list.props.pagingEnabled).toBeUndefined();
    expect(list.props.data).toEqual([22, 33, 44, 11]);

    fireEvent(list, "onViewableItemsChanged", { viewableItems: [{ isViewable: true, index: 1, item: 33 }] });
    fireEvent(list, "onViewableItemsChanged", { viewableItems: [{ isViewable: true, index: 2, item: 44 }] });
    fireEvent(list, "onViewableItemsChanged", { viewableItems: [{ isViewable: true, index: 3, item: 11 }] });

    await waitFor(() => expect(mockGetPostDetail).toHaveBeenCalledWith(11));
    expect(new Set(list.props.data).size).toBe(4);
    expect(mockGetPostDetail.mock.calls.filter(([id]) => id === 22)).toHaveLength(1);
    expect(mockGetPostDetail.mock.calls.filter(([id]) => id === 33)).toHaveLength(1);
    expect(mockGetPostDetail.mock.calls.filter(([id]) => id === 44)).toHaveLength(1);
    expect(mockGetPostDetail.mock.calls.filter(([id]) => id === 11)).toHaveLength(1);
  });

  it("paginates the profile source and deduplicates overlapping posts", async () => {
    mockListFeed.mockResolvedValueOnce({
      posts: [
        { id: 33, post_id: 33, body: "Existing" },
        { id: 44, post_id: 44, body: "Next" }
      ],
      next_offset: 5,
      has_more: false
    });
    const route = { params: { profileId: 7, profileKey: "roodycherie", postId: 22, postIds: [11, 22, 33], nextOffset: 3, hasMore: true, contentTab: "posts", owner: true, source: "PROFILE_GRID" } };
    const screen = render(<ProfilePostViewerScreen route={route as never} navigation={{ goBack: jest.fn(), navigate: jest.fn() } as never} />);
    const list = screen.UNSAFE_getByType(require("react-native").FlatList);
    fireEvent(list, "onEndReached");

    await waitFor(() => expect(screen.UNSAFE_getByType(require("react-native").FlatList).props.data).toEqual([22, 33, 44, 11]));
    expect(mockListFeed).toHaveBeenCalledWith({ feed: "for_you", profile: "roodycherie", limit: 20, offset: 3 });
  });
});
