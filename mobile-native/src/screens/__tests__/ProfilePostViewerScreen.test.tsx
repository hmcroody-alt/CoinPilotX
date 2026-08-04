import { fireEvent, render, waitFor } from "@testing-library/react-native";
import React from "react";
import { ProfilePostViewerScreen } from "../ProfilePostViewerScreen";

const mockSetSaved = jest.fn(async (_object?: unknown, _saved?: unknown) => ({ ok: true, saved: true }));
const mockGetPostDetail = jest.fn(async (postId: number) => ({ post: { id: postId, post_id: postId, body: `Post ${postId}` } }));

jest.mock("../../api/feed", () => ({
  getPostDetail: (id: number) => mockGetPostDetail(id),
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
  it("loads the exact selected post and wires its visible Save action to canonical saved storage", async () => {
    const route = { params: { profileId: 7, profileKey: "roodycherie", postId: 22, postIds: [11, 22, 33], owner: true, source: "PROFILE_GRID" } };
    const screen = render(<ProfilePostViewerScreen route={route as never} navigation={{ goBack: jest.fn(), navigate: jest.fn() } as never} />);
    await waitFor(() => expect(screen.getByTestId("viewer-save-22")).toBeTruthy());
    fireEvent.press(screen.getByTestId("viewer-save-22"));
    await waitFor(() => expect(mockSetSaved).toHaveBeenCalledWith({ type: "post", id: 22 }, true));
    expect(mockGetPostDetail).toHaveBeenCalledWith(22);
  });
});
