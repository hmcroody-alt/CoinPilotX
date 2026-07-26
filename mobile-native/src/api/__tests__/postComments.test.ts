jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

// The network is the only thing stubbed. Everything else — the query string, the
// flat-to-nested transform, the has_more contract — is the behavior under test,
// so it runs for real. Asserting that feed.ts "contains" a parent_comment_id
// string would prove nothing about what is actually sent on the wire.
jest.mock("../pulseApi", () => ({
  pulseApi: jest.fn(async () => ({ ok: true }))
}));

import { pulseApi } from "../pulseApi";
import { addPostComment, listPostComments, POST_COMMENT_PAGE_SIZE, PulseComment } from "../feed";

const apiMock = pulseApi as jest.Mock;

function lastPath() {
  const call = apiMock.mock.calls[apiMock.mock.calls.length - 1];
  return String(call[0]);
}

function lastBody() {
  const call = apiMock.mock.calls[apiMock.mock.calls.length - 1];
  return JSON.parse(call[1].body as string);
}

/** A row exactly as services/pulse_feed_engine.py:1426 emits it: flat, with parent_comment_id. */
function row(id: number, parentCommentId: number | null = null): PulseComment {
  return {
    id,
    comment_id: id,
    body: `comment ${id}`,
    parent_comment_id: parentCommentId ?? undefined,
    created_at: `2026-07-25T00:0${id}:00`
  } as PulseComment;
}

beforeEach(() => {
  apiMock.mockReset();
  apiMock.mockResolvedValue({ ok: true });
});

describe("addPostComment", () => {
  it("stays a two-argument call for a root comment and sends no parent", async () => {
    await addPostComment(5, "hello");
    expect(lastPath()).toBe("/api/pulse/posts/5/comments");
    expect(lastBody().parent_comment_id).toBe(0);
    expect(lastBody().body).toBe("hello");
  });

  it("sends parent_comment_id so the server files the row as a reply", async () => {
    await addPostComment(5, "answering", 91);
    expect(lastBody().parent_comment_id).toBe(91);
  });

  it("treats a zero or negative parent as a root comment", async () => {
    await addPostComment(5, "hello", 0);
    expect(lastBody().parent_comment_id).toBe(0);
    await addPostComment(5, "hello", -3);
    expect(lastBody().parent_comment_id).toBe(0);
  });

  it("keeps the requested parent when the server echoes the comment without one", async () => {
    // Otherwise the optimistic insert nests the reply and the confirmed insert
    // moves it to the root, which reads as the reply jumping.
    apiMock.mockResolvedValue({ ok: true, comment: { id: 400, comment_id: 400, body: "answering" } });
    const result = await addPostComment(5, "answering", 91);
    expect(result.comment?.parent_comment_id).toBe(91);
  });

  it("does not invent a parent for a root comment the server echoes back", async () => {
    apiMock.mockResolvedValue({ ok: true, comment: { id: 400, comment_id: 400, body: "hello" } });
    const result = await addPostComment(5, "hello");
    expect(result.comment?.parent_comment_id).toBeFalsy();
  });

  it("prefers the parent the server reports over the one requested", async () => {
    apiMock.mockResolvedValue({ ok: true, comment: { id: 400, comment_id: 400, body: "x", parent_comment_id: 77 } });
    const result = await addPostComment(5, "x", 91);
    expect(result.comment?.parent_comment_id).toBe(77);
  });
});

describe("listPostComments paging", () => {
  it("requests a bounded first page instead of an unbounded fetch", async () => {
    apiMock.mockResolvedValue({ ok: true, comments: [], total: 0, has_more: false });
    await listPostComments(5);
    expect(lastPath()).toBe(`/api/pulse/posts/5/comments?limit=${POST_COMMENT_PAGE_SIZE}&offset=0`);
  });

  it("passes the caller's window through to the server", async () => {
    apiMock.mockResolvedValue({ ok: true, comments: [], total: 0, has_more: false });
    await listPostComments(5, { limit: 7, offset: 14 });
    expect(lastPath()).toBe("/api/pulse/posts/5/comments?limit=7&offset=14");
  });

  it("clamps a nonsensical window rather than sending it", async () => {
    apiMock.mockResolvedValue({ ok: true, comments: [], total: 0, has_more: false });
    await listPostComments(5, { limit: 0, offset: -9 });
    expect(lastPath()).toBe(`/api/pulse/posts/5/comments?limit=${POST_COMMENT_PAGE_SIZE}&offset=0`);
  });

  it("reports the server's total and window, not the page length", async () => {
    apiMock.mockResolvedValue({ ok: true, comments: [row(1), row(2)], total: 340, has_more: true, limit: 20, offset: 40 });
    const page = await listPostComments(5, { limit: 20, offset: 40 });
    expect(page.total).toBe(340);
    expect(page.limit).toBe(20);
    expect(page.offset).toBe(40);
    expect(page.hasMore).toBe(true);
  });

  it("honors has_more:false even when the page is completely full", async () => {
    // Page length alone cannot express this: total is an exact multiple of
    // limit, so a full last page looks identical to a full middle page.
    apiMock.mockResolvedValue({ ok: true, comments: [row(1), row(2)], total: 2, has_more: false, limit: 2, offset: 0 });
    const page = await listPostComments(5, { limit: 2 });
    expect(page.comments).toHaveLength(2);
    expect(page.hasMore).toBe(false);
  });

  // The two cases below are the only ones that actually discriminate between
  // trusting the server and re-deriving has_more locally, so they are the ones
  // that matter. They are not hypothetical: `total` comes from a COUNT query
  // issued separately from the page query (services/pulse_feed_engine.py:1444
  // and :1454), so a concurrent insert or delete between the two makes the
  // arithmetic and the server's own answer disagree. The server owns the fact.

  it("trusts has_more:true even when the arithmetic says the page is the last", async () => {
    apiMock.mockResolvedValue({ ok: true, comments: [row(1), row(2)], total: 2, has_more: true, limit: 2, offset: 0 });
    expect((await listPostComments(5, { limit: 2 })).hasMore).toBe(true);
  });

  it("trusts has_more:false even when the arithmetic says more remain", async () => {
    apiMock.mockResolvedValue({ ok: true, comments: [row(1)], total: 9, has_more: false, limit: 1, offset: 0 });
    // A `||` here instead of `??` would recompute this as true and page forever.
    expect((await listPostComments(5, { limit: 1 })).hasMore).toBe(false);
  });

  it("falls back to arithmetic only when the server omits has_more", async () => {
    apiMock.mockResolvedValue({ ok: true, comments: [row(1)], total: 9, limit: 1, offset: 0 });
    expect((await listPostComments(5, { limit: 1 })).hasMore).toBe(true);
    apiMock.mockResolvedValue({ ok: true, comments: [row(1)], total: 9, limit: 1, offset: 8 });
    expect((await listPostComments(5, { limit: 1, offset: 8 })).hasMore).toBe(false);
  });

  it("accepts an items-keyed response without losing the page", async () => {
    apiMock.mockResolvedValue({ ok: true, items: [row(1)], total: 1, has_more: false });
    expect((await listPostComments(5)).comments.map((item) => item.id)).toEqual([1]);
  });

  it("returns an empty page past the end without throwing", async () => {
    apiMock.mockResolvedValue({ ok: true, comments: [], total: 25, has_more: false, limit: 20, offset: 999 });
    const page = await listPostComments(5, { offset: 999 });
    expect(page.comments).toEqual([]);
    expect(page.total).toBe(25);
  });
});

describe("listPostComments nesting", () => {
  it("nests replies under their parent instead of returning siblings", async () => {
    apiMock.mockResolvedValue({
      ok: true,
      comments: [row(1), row(2, 1), row(3, 1), row(4, 2), row(5)],
      total: 5,
      has_more: false
    });
    const page = await listPostComments(5);
    expect(page.comments.map((item) => item.id)).toEqual([1, 5]);
    expect(page.comments[0].replies?.map((item) => item.id)).toEqual([2, 3]);
    expect(page.comments[0].replies?.[0].replies?.map((item) => item.id)).toEqual([4]);
  });

  it("loses no comment when a page splits a thread across the boundary", async () => {
    // The engine orders by created_at ASC, id ASC, so a parent can be on the
    // previous page. An orphaned reply must still be visible.
    apiMock.mockResolvedValue({ ok: true, comments: [row(4, 2), row(5)], total: 5, has_more: false, offset: 3 });
    const page = await listPostComments(5, { offset: 3 });
    expect(page.comments.map((item) => item.id)).toEqual([4, 5]);
  });

  it("also returns the rows still flat, so a pager can re-parent across pages", async () => {
    // Merging two already-nested pages at the root would strand a reply whose
    // parent arrived on the previous page. The flat rows make that fixable.
    apiMock.mockResolvedValue({ ok: true, comments: [row(1), row(2, 1), row(3)], total: 3, has_more: false });
    const page = await listPostComments(5);
    expect(page.flat.map((item) => item.id)).toEqual([1, 2, 3]);
    expect(page.flat.every((item) => !item.replies?.length)).toBe(true);
    expect(page.comments.map((item) => item.id)).toEqual([1, 3]);
  });

  it("derives reply_count from the nesting the server actually described", async () => {
    apiMock.mockResolvedValue({ ok: true, comments: [row(1), row(2, 1), row(3, 1)], total: 3, has_more: false });
    const page = await listPostComments(5);
    expect(page.comments[0].reply_count).toBe(2);
  });
});
