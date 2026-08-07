import React from "react";
import { fireEvent, render, waitFor } from "@testing-library/react-native";

// Only the network boundary and the native modules are stubbed. The comment
// tree, the pager arithmetic, the reply draft seeding and the rendered thread all
// run for real — those are the behaviors under test. Asserting that the screen
// "contains" a CommentThread tag would prove nothing about whether a reply
// actually renders underneath the comment it answers.
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

// The post card is a separate surface with its own coverage; stubbing it keeps
// these assertions about the comment thread.
jest.mock("../../components/PostCard", () => ({
  PostCard: () => null
}));

// ContentTranslation reaches for the translation engine and locale storage.
// Reduced to the text it is handed so a comment body is still assertable.
jest.mock("../../components/ContentTranslation", () => {
  const { Text } = jest.requireActual("react-native");
  const ReactActual = jest.requireActual("react");
  return {
    ContentTranslation: ({ text, textStyle }: { text?: string; textStyle?: unknown }) =>
      ReactActual.createElement(Text, { style: textStyle }, text)
  };
});

jest.mock("../../core/eventSync", () => ({
  invalidateNativeSync: jest.fn().mockResolvedValue(undefined),
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));

jest.mock("../../sharing/nativeShare", () => ({
  sharePulseObject: jest.fn().mockResolvedValue({ ok: true })
}));

jest.mock("../../session/auth", () => ({
  useAuth: () => ({ authState: { user: { user_id: 7 } } })
}));

const mockGetPostDetail = jest.fn();
const mockListPostComments = jest.fn();
const mockAddPostComment = jest.fn();
const mockLoadCachedPostDetail = jest.fn().mockResolvedValue(null);

jest.mock("../../api/feed", () => {
  const actual = jest.requireActual("../../api/feed");
  return {
    ...actual,
    getPostDetail: (...args: unknown[]) => mockGetPostDetail(...args),
    listPostComments: (...args: unknown[]) => mockListPostComments(...args),
    addPostComment: (...args: unknown[]) => mockAddPostComment(...args),
    loadCachedPostDetail: (...args: unknown[]) => mockLoadCachedPostDetail(...args)
  };
});

import { POST_COMMENT_PAGE_SIZE, PulseComment } from "../../api/feed";
import { buildCommentTree } from "../../social/commentTree";
import { PostDetailScreen } from "../PostDetailScreen";

const POST_ID = 5;

function post(overrides: Record<string, unknown> = {}) {
  return {
    id: POST_ID,
    post_id: POST_ID,
    user_id: 9,
    body: "A post under test.",
    author: { id: 9, user_id: 9, display_name: "Fixture Author", username: "fixture_author" },
    comment_count: 0,
    reaction_counts: {},
    ...overrides
  };
}

/** A row exactly as services/pulse_feed_engine.py:1426 emits it: flat, with parent_comment_id. */
function row(id: number, parentCommentId = 0, overrides: Record<string, unknown> = {}): PulseComment {
  return {
    id,
    comment_id: id,
    body: `comment ${id}`,
    parent_comment_id: parentCommentId,
    created_at: `2026-07-25T00:00:0${id % 10}`,
    author: { id: 90 + id, user_id: 90 + id, display_name: `Author ${id}`, username: `author_${id}` },
    ...overrides
  } as PulseComment;
}

function page(rows: PulseComment[], overrides: Record<string, unknown> = {}) {
  return {
    comments: buildCommentTree(rows),
    flat: rows,
    total: rows.length,
    hasMore: false,
    limit: POST_COMMENT_PAGE_SIZE,
    offset: 0,
    ...overrides
  };
}

function renderScreen(rows: PulseComment[] = [], pageOverrides: Record<string, unknown> = {}) {
  mockGetPostDetail.mockResolvedValue({ post: post(), comments: buildCommentTree(rows) });
  mockListPostComments.mockResolvedValue(page(rows, pageOverrides));
  const navigation = { navigate: jest.fn(), goBack: jest.fn() };
  const route = { params: { postId: POST_ID } };
  const utils = render(<PostDetailScreen route={route as never} navigation={navigation as never} />);
  return { ...utils, navigation };
}

beforeEach(() => {
  jest.clearAllMocks();
  mockLoadCachedPostDetail.mockResolvedValue(null);
  mockAddPostComment.mockResolvedValue({ ok: true });
});

describe("PostDetailScreen comment thread", () => {
  it("requests a bounded first page rather than an unbounded comment fetch", async () => {
    renderScreen([row(1)]);
    await waitFor(() => expect(mockListPostComments).toHaveBeenCalled());
    expect(mockListPostComments).toHaveBeenCalledWith(POST_ID, { limit: POST_COMMENT_PAGE_SIZE, offset: 0 });
  });

  it("renders a reply underneath the comment it answers, not beside it", async () => {
    // The defect this screen shipped with: a flat list rendered every reply as a
    // root, so an answer appeared as a sibling of the question.
    const { findByTestId, queryByTestId, getByTestId } = renderScreen([row(1), row(2, 1)]);
    await findByTestId("comment-1");
    // Collapsed by default, so the reply is reachable only through its parent.
    expect(queryByTestId("comment-2")).toBeNull();
    fireEvent.press(getByTestId("comment-toggle-replies-1"));
    expect(getByTestId("comment-2")).toBeTruthy();
  });

  it("shows the server's total in the section title, not the number of roots loaded", async () => {
    const { findByTestId } = renderScreen([row(1), row(2, 1)], { total: 340, hasMore: true });
    expect((await findByTestId("post-detail-comments-title")).props.children).toBe("Comments (340)");
  });

  it("offers load-more only while the server says more remain", async () => {
    const { findByTestId, queryByTestId } = renderScreen([row(1)], { total: 1, hasMore: false });
    await findByTestId("comment-1");
    expect(queryByTestId("post-detail-load-more-comments")).toBeNull();
  });

  it("advances the offset by rows consumed, not by roots rendered", async () => {
    // Page one is three rows but only one root. Counting roots would re-request
    // the two replies on every subsequent page.
    const { findByTestId, getByTestId } = renderScreen([row(1), row(2, 1), row(3, 1)], { total: 9, hasMore: true });
    await findByTestId("post-detail-load-more-comments");
    mockListPostComments.mockResolvedValue(page([row(4)], { total: 9, hasMore: false, offset: 3 }));
    fireEvent.press(getByTestId("post-detail-load-more-comments"));
    await waitFor(() => expect(mockListPostComments).toHaveBeenCalledTimes(2));
    expect(mockListPostComments).toHaveBeenLastCalledWith(POST_ID, { limit: POST_COMMENT_PAGE_SIZE, offset: 3 });
  });

  it("re-parents a page-two reply under the parent that arrived on page one", async () => {
    // Merging two already-nested pages at the root level would leave comment 9
    // sitting beside comment 1 as a top-level comment. Accumulating flat and
    // re-deriving the tree over everything loaded is what prevents that.
    const { findByTestId, getByTestId, queryByTestId } = renderScreen([row(1)], { total: 3, hasMore: true });
    await findByTestId("post-detail-load-more-comments");
    mockListPostComments.mockResolvedValue(page([row(9, 1), row(3)], { total: 3, hasMore: false, offset: 1 }));
    fireEvent.press(getByTestId("post-detail-load-more-comments"));
    await waitFor(() => expect(queryByTestId("comment-3")).toBeTruthy());
    // 9 is not a root...
    expect(queryByTestId("comment-9")).toBeNull();
    // ...it is under 1.
    fireEvent.press(getByTestId("comment-toggle-replies-1"));
    expect(getByTestId("comment-9")).toBeTruthy();
  });

  it("keeps every comment when the page count and the server total disagree", async () => {
    const { findByTestId, getByTestId } = renderScreen([row(1)], { total: 2, hasMore: true });
    await findByTestId("post-detail-load-more-comments");
    mockListPostComments.mockResolvedValue(page([row(1), row(4)], { total: 2, hasMore: false, offset: 1 }));
    fireEvent.press(getByTestId("post-detail-load-more-comments"));
    await waitFor(() => expect(getByTestId("comment-4")).toBeTruthy());
    // The repeated row is merged, not duplicated.
    expect(getByTestId("comment-1")).toBeTruthy();
  });
});

describe("PostDetailScreen replies", () => {
  it("seeds the draft with the author's handle and shows who is being replied to", async () => {
    const { findByTestId, getByTestId } = renderScreen([row(1)]);
    await findByTestId("comment-1");
    fireEvent.press(getByTestId("comment-reply-1"));
    expect(getByTestId("post-detail-comment-input").props.value).toBe("@author_1 ");
    expect(getByTestId("post-detail-cancel-reply")).toBeTruthy();
  });

  it("does not stack the handle when Reply is tapped twice", async () => {
    const { findByTestId, getByTestId } = renderScreen([row(1)]);
    await findByTestId("comment-1");
    fireEvent.press(getByTestId("comment-reply-1"));
    fireEvent.press(getByTestId("comment-reply-1"));
    expect(getByTestId("post-detail-comment-input").props.value).toBe("@author_1 ");
  });

  it("clears the reply target when the banner is cancelled", async () => {
    const { findByTestId, getByTestId, queryByTestId } = renderScreen([row(1)]);
    await findByTestId("comment-1");
    fireEvent.press(getByTestId("comment-reply-1"));
    fireEvent.press(getByTestId("post-detail-cancel-reply"));
    expect(queryByTestId("post-detail-cancel-reply")).toBeNull();
  });

  it("sends parent_comment_id so the server files the row as a reply", async () => {
    mockAddPostComment.mockResolvedValue({ ok: true, comment: row(50, 1, { body: "answering" }) });
    const { findByTestId, getByTestId } = renderScreen([row(1)]);
    await findByTestId("comment-1");
    fireEvent.press(getByTestId("comment-reply-1"));
    fireEvent.changeText(getByTestId("post-detail-comment-input"), "answering");
    fireEvent.press(getByTestId("post-detail-submit-comment"));
    await waitFor(() => expect(mockAddPostComment).toHaveBeenCalled());
    expect(mockAddPostComment).toHaveBeenCalledWith(POST_ID, "answering", 1);
  });

  it("sends no parent for a root comment", async () => {
    mockAddPostComment.mockResolvedValue({ ok: true, comment: row(50) });
    const { findByTestId, getByTestId } = renderScreen([row(1)]);
    await findByTestId("comment-1");
    fireEvent.changeText(getByTestId("post-detail-comment-input"), "hello");
    fireEvent.press(getByTestId("post-detail-submit-comment"));
    await waitFor(() => expect(mockAddPostComment).toHaveBeenCalled());
    expect(mockAddPostComment).toHaveBeenCalledWith(POST_ID, "hello", 0);
  });

  it("opens the thread the new reply landed in, so it is not invisible", async () => {
    // A reply the user cannot see is indistinguishable from a reply that failed.
    mockAddPostComment.mockResolvedValue({ ok: true, comment: row(50, 1, { body: "answering" }) });
    const { findByTestId, getByTestId } = renderScreen([row(1)]);
    await findByTestId("comment-1");
    fireEvent.press(getByTestId("comment-reply-1"));
    fireEvent.changeText(getByTestId("post-detail-comment-input"), "answering");
    fireEvent.press(getByTestId("post-detail-submit-comment"));
    await waitFor(() => expect(getByTestId("comment-50")).toBeTruthy());
  });

  it("appends a new root comment below older ones, matching the server's order", async () => {
    // This screen used to prepend, which put a new comment above comments older
    // than it; the next refresh then moved it, which reads as the comment jumping.
    mockAddPostComment.mockResolvedValue({ ok: true, comment: row(50, 0, { body: "newest" }) });
    const { findByTestId, getByTestId, getAllByTestId } = renderScreen([row(1), row(2)]);
    await findByTestId("comment-1");
    fireEvent.changeText(getByTestId("post-detail-comment-input"), "newest");
    fireEvent.press(getByTestId("post-detail-submit-comment"));
    await waitFor(() => expect(getByTestId("comment-50")).toBeTruthy());
    const ids = getAllByTestId(/^comment-\d+$/).map((node) => node.props.testID);
    expect(ids).toEqual(["comment-1", "comment-2", "comment-50"]);
  });

  it("raises the comment total when a comment is accepted", async () => {
    mockAddPostComment.mockResolvedValue({ ok: true, comment: row(50) });
    const { findByTestId, getByTestId } = renderScreen([row(1)], { total: 1 });
    await findByTestId("comment-1");
    fireEvent.changeText(getByTestId("post-detail-comment-input"), "hello");
    fireEvent.press(getByTestId("post-detail-submit-comment"));
    await waitFor(() => expect(getByTestId("post-detail-comments-title").props.children).toBe("Comments (2)"));
  });

  it("restores both the draft and the reply target when the send fails", async () => {
    // Losing the draft on failure means retyping; losing the reply target means
    // the retry silently posts a root comment instead of a reply.
    mockAddPostComment.mockRejectedValue(new Error("nope"));
    const { findByTestId, getByTestId } = renderScreen([row(1)]);
    await findByTestId("comment-1");
    fireEvent.press(getByTestId("comment-reply-1"));
    fireEvent.changeText(getByTestId("post-detail-comment-input"), "@author_1 answering");
    fireEvent.press(getByTestId("post-detail-submit-comment"));
    await waitFor(() => expect(getByTestId("post-detail-comment-input").props.value).toBe("@author_1 answering"));
    expect(getByTestId("post-detail-cancel-reply")).toBeTruthy();
  });

  it("notifies the activity surfaces so the reply shows up there too", async () => {
    const { invalidateNativeSync } = jest.requireMock("../../core/eventSync");
    mockAddPostComment.mockResolvedValue({ ok: true, comment: row(50) });
    const { findByTestId, getByTestId } = renderScreen([row(1)]);
    await findByTestId("comment-1");
    fireEvent.changeText(getByTestId("post-detail-comment-input"), "hello");
    fireEvent.press(getByTestId("post-detail-submit-comment"));
    await waitFor(() => expect(invalidateNativeSync).toHaveBeenCalled());
    expect(invalidateNativeSync).toHaveBeenCalledWith(["activity", "notifications"], "post_detail_comment");
  });
});

describe("PostDetailScreen mentions and navigation", () => {
  it("navigates to the mentioned profile when a mention is tapped", async () => {
    const { findByTestId, getByTestId, navigation } = renderScreen([row(1, 0, { body: "ask @someone about it" })]);
    await findByTestId("comment-1");
    fireEvent.press(getByTestId("comment-mention-1-someone"));
    expect(navigation.navigate).toHaveBeenCalledWith("ProfileDetail", expect.objectContaining({ profileKey: "someone" }));
  });

  it("navigates to the comment author's profile when the name is tapped", async () => {
    // The fixture author carries user_id 91, so the shared profile resolver
    // must navigate by that canonical id (profileKey "91") — not the handle —
    // matching how search and the feed resolve the same member. The username
    // still rides along for display/fallback.
    const { findByTestId, getByLabelText, navigation } = renderScreen([row(1)]);
    await findByTestId("comment-1");
    fireEvent.press(getByLabelText("Open Author 1's profile"));
    expect(navigation.navigate).toHaveBeenCalledWith(
      "ProfileDetail",
      expect.objectContaining({ profileKey: "91", userId: 91, username: "author_1" })
    );
  });
});

describe("PostDetailScreen omits controls with no backend behind them", () => {
  // The acceptance criterion is "no placeholders remain". Post comment
  // edit/delete/react have no routes yet (only the reel-scoped handlers at
  // bot.py:76627 and :76672 exist), so rendering those controls here would
  // promise an action the screen cannot perform. CommentThread omits any control
  // whose handler is absent, and this is the test that keeps it that way.
  it("renders no edit, delete, react or report control on a post comment", async () => {
    const { findByTestId, queryByTestId } = renderScreen([row(1, 0, { can_edit: true, can_delete: true })]);
    await findByTestId("comment-1");
    expect(queryByTestId("comment-edit-1")).toBeNull();
    expect(queryByTestId("comment-delete-1")).toBeNull();
    expect(queryByTestId("comment-react-1")).toBeNull();
    expect(queryByTestId("comment-report-1")).toBeNull();
  });

  it("still renders the reply control, which does have a route", async () => {
    const { findByTestId, getByTestId } = renderScreen([row(1)]);
    await findByTestId("comment-1");
    expect(getByTestId("comment-reply-1")).toBeTruthy();
  });
});

describe("PostDetailScreen degraded reads", () => {
  it("falls back to the comments the detail response carried when the pager fails", async () => {
    mockGetPostDetail.mockResolvedValue({ post: post({ comment_count: 2 }), comments: buildCommentTree([row(1), row(2, 1)]) });
    mockListPostComments.mockRejectedValue(new Error("pager down"));
    const navigation = { navigate: jest.fn(), goBack: jest.fn() };
    const { findByTestId, getByTestId, queryByTestId } = render(
      <PostDetailScreen route={{ params: { postId: POST_ID } } as never} navigation={navigation as never} />
    );
    await findByTestId("comment-1");
    // The nesting survives the fallback: flattening a tree records the parent
    // edge, so rebuilding it does not strand the reply as a root.
    expect(queryByTestId("comment-2")).toBeNull();
    fireEvent.press(getByTestId("comment-toggle-replies-1"));
    expect(getByTestId("comment-2")).toBeTruthy();
    // No load-more, because without a server total it would be a guess.
    expect(queryByTestId("post-detail-load-more-comments")).toBeNull();
  });

  it("shows the cached post and its thread when the post read fails offline", async () => {
    mockGetPostDetail.mockRejectedValue(new Error("offline"));
    mockListPostComments.mockRejectedValue(new Error("offline"));
    mockLoadCachedPostDetail.mockResolvedValue({ post: post(), comments: buildCommentTree([row(1), row(2, 1)]) });
    const navigation = { navigate: jest.fn(), goBack: jest.fn() };
    const { findByTestId, getByTestId } = render(
      <PostDetailScreen route={{ params: { postId: POST_ID } } as never} navigation={navigation as never} />
    );
    await findByTestId("comment-1");
    fireEvent.press(getByTestId("comment-toggle-replies-1"));
    expect(getByTestId("comment-2")).toBeTruthy();
  });
});
