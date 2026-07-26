import { PulseComment } from "../../api/feed";
import {
  applyCommentReaction,
  buildCommentTree,
  commentDepth,
  countCommentTree,
  findComment,
  findCommentParent,
  flattenCommentTree,
  insertReply,
  mergeCommentPage,
  mergeFlatComments,
  removeCommentFromTree,
  toggleSetValue,
  updateCommentTree
} from "../commentTree";

function comment(id: number, body: string, replies: PulseComment[] = []): PulseComment {
  return {
    id,
    comment_id: id,
    body,
    replies,
    reply_count: replies.length
  };
}

/** 1 ─ 2 ─ 4, 1 ─ 3, 5 */
function tree(): PulseComment[] {
  return [
    comment(1, "root one", [comment(2, "reply two", [comment(4, "nested four")]), comment(3, "reply three")]),
    comment(5, "root five")
  ];
}

describe("comment tree helpers", () => {
  it("finds a comment at every depth and returns null for an absent id", () => {
    expect(findComment(tree(), 1)?.body).toBe("root one");
    expect(findComment(tree(), 3)?.body).toBe("reply three");
    expect(findComment(tree(), 4)?.body).toBe("nested four");
    expect(findComment(tree(), 99)).toBeNull();
  });

  it("reports the parent of a reply and null for a root comment", () => {
    expect(findCommentParent(tree(), 4)?.id).toBe(2);
    expect(findCommentParent(tree(), 2)?.id).toBe(1);
    expect(findCommentParent(tree(), 1)).toBeNull();
    expect(findCommentParent(tree(), 99)).toBeNull();
  });

  it("reports depth as 0 for roots and -1 when the comment is absent", () => {
    expect(commentDepth(tree(), 1)).toBe(0);
    expect(commentDepth(tree(), 5)).toBe(0);
    expect(commentDepth(tree(), 2)).toBe(1);
    expect(commentDepth(tree(), 4)).toBe(2);
    expect(commentDepth(tree(), 99)).toBe(-1);
  });

  it("inserts a reply under the requested parent and bumps only that reply_count", () => {
    const next = insertReply(tree(), 2, comment(6, "sixth"));
    expect(findComment(next, 2)?.replies?.map((item) => item.id)).toEqual([4, 6]);
    expect(findComment(next, 2)?.reply_count).toBe(2);
    expect(findComment(next, 1)?.reply_count).toBe(2);
  });

  it("does not relocate a reply to the root when the parent id is stale", () => {
    const next = insertReply(tree(), 99, comment(6, "sixth"));
    expect(findComment(next, 6)).toBeNull();
    expect(countCommentTree(next)).toBe(countCommentTree(tree()));
  });

  it("never mutates the input tree", () => {
    const original = tree();
    insertReply(original, 2, comment(6, "sixth"));
    removeCommentFromTree(original, 4);
    updateCommentTree(original, 1, { body: "clobbered" });
    expect(original[0].replies?.[0].replies?.map((item) => item.id)).toEqual([4]);
    expect(original[0].body).toBe("root one");
  });

  it("shallow-merges an update while preserving the target's subtree", () => {
    const next = updateCommentTree(tree(), 2, { body: "edited", edited_at: "now" });
    expect(findComment(next, 2)?.body).toBe("edited");
    expect(findComment(next, 2)?.edited_at).toBe("now");
    expect(findComment(next, 4)?.body).toBe("nested four");
  });

  it("removes a comment together with its whole subtree", () => {
    const next = removeCommentFromTree(tree(), 2);
    expect(findComment(next, 2)).toBeNull();
    expect(findComment(next, 4)).toBeNull();
    expect(findComment(next, 3)?.body).toBe("reply three");
  });

  it("decrements the parent reply_count when a reply is removed", () => {
    const next = removeCommentFromTree(tree(), 3);
    expect(findComment(next, 1)?.reply_count).toBe(1);
  });

  it("does not let a removed reply drive reply_count below zero", () => {
    const zeroed = [comment(1, "root", [comment(2, "reply")])];
    zeroed[0].reply_count = 0;
    expect(findComment(removeCommentFromTree(zeroed, 2), 1)?.reply_count).toBe(0);
  });

  it("counts every node including nested replies", () => {
    expect(countCommentTree(tree())).toBe(5);
    expect(countCommentTree([])).toBe(0);
  });

  it("flattens depth-first with parents before their replies", () => {
    expect(flattenCommentTree(tree()).map((item) => item.id)).toEqual([1, 2, 4, 3, 5]);
  });

  it("merges a page by appending new nodes and de-duplicating known ids", () => {
    const merged = mergeCommentPage(tree(), [comment(5, "root five"), comment(7, "root seven")]);
    expect(merged.map((item) => item.id)).toEqual([1, 5, 7]);
  });

  it("keeps the richer subtree when a page omits replies the client already has", () => {
    const thin = [comment(1, "root one")];
    const merged = mergeCommentPage(tree(), thin);
    expect(findComment(merged, 4)?.body).toBe("nested four");
  });

  it("toggles a value into and out of a set without mutating the original", () => {
    const original = new Set([1]);
    expect(Array.from(toggleSetValue(original, 2))).toEqual([1, 2]);
    expect(Array.from(toggleSetValue(original, 1))).toEqual([]);
    expect(Array.from(original)).toEqual([1]);
  });

  it("clears viewer_reaction when the server reports the reaction was removed", () => {
    const liked = updateCommentTree(tree(), 1, { viewer_reaction: "like", reaction_counts: { like: 3 } });
    const next = applyCommentReaction(liked, 1, { removed: true, reaction_type: "like" });
    expect(findComment(next, 1)?.viewer_reaction).toBe("");
    expect(findComment(next, 1)?.reaction_counts?.like).toBe(2);
  });

  it("moves the count from the previous reaction when the viewer switches type", () => {
    const liked = updateCommentTree(tree(), 1, { viewer_reaction: "like", reaction_counts: { like: 3, fire: 1 } });
    const next = applyCommentReaction(liked, 1, { reaction_type: "fire" });
    expect(findComment(next, 1)?.reaction_counts).toEqual({ like: 2, fire: 2 });
    expect(findComment(next, 1)?.like_count).toBe(4);
  });

  it("prefers server-supplied reaction counts over the local estimate", () => {
    const next = applyCommentReaction(tree(), 1, { reaction_type: "like", reaction_counts: { like: 42 } });
    expect(findComment(next, 1)?.reaction_counts?.like).toBe(42);
  });

  it("leaves the tree untouched when reacting to an absent comment", () => {
    const original = tree();
    expect(applyCommentReaction(original, 99, { reaction_type: "like" })).toBe(original);
  });
});

/**
 * buildCommentTree is the transform that gives feed posts the nested replies
 * Reels already had. The backend endpoint for posts returns a flat list with
 * parent_comment_id (services/pulse_feed_engine.py:1422), so these cases mirror
 * exactly what that query can emit.
 */
describe("building a tree from a flat comment list", () => {
  function flat(id: number, parentId = 0): PulseComment {
    return { id, comment_id: id, body: `c${id}`, parent_comment_id: parentId };
  }

  it("nests replies under their parents and keeps roots at the top level", () => {
    const built = buildCommentTree([flat(1), flat(2, 1), flat(3, 1), flat(4, 2), flat(5)]);
    expect(built.map((item) => item.id)).toEqual([1, 5]);
    expect(findComment(built, 1)?.replies?.map((item) => item.id)).toEqual([2, 3]);
    expect(findComment(built, 2)?.replies?.map((item) => item.id)).toEqual([4]);
  });

  it("preserves the order the server returned", () => {
    const built = buildCommentTree([flat(9), flat(3), flat(7, 9), flat(1, 9)]);
    expect(built.map((item) => item.id)).toEqual([9, 3]);
    expect(findComment(built, 9)?.replies?.map((item) => item.id)).toEqual([7, 1]);
  });

  it("keeps every comment: none are lost and none are duplicated", () => {
    const input = [flat(1), flat(2, 1), flat(3, 2), flat(4)];
    const built = buildCommentTree(input);
    expect(countCommentTree(built)).toBe(input.length);
    expect(flattenCommentTree(built).map((item) => item.id).sort()).toEqual([1, 2, 3, 4]);
  });

  it("promotes an orphaned reply to a root rather than dropping it", () => {
    const built = buildCommentTree([flat(2, 999), flat(3)]);
    expect(built.map((item) => item.id)).toEqual([2, 3]);
    expect(countCommentTree(built)).toBe(2);
  });

  it("derives reply_count from the actual nesting", () => {
    const built = buildCommentTree([flat(1), flat(2, 1), flat(3, 1), flat(4, 2)]);
    expect(findComment(built, 1)?.reply_count).toBe(2);
    expect(findComment(built, 2)?.reply_count).toBe(1);
    expect(findComment(built, 4)?.reply_count).toBe(0);
  });

  it("does not recurse forever on a parent cycle", () => {
    const cycle = [
      { id: 1, comment_id: 1, body: "a", parent_comment_id: 2 },
      { id: 2, comment_id: 2, body: "b", parent_comment_id: 1 }
    ];
    const built = buildCommentTree(cycle);
    expect(countCommentTree(built)).toBe(2);
  });

  it("treats a comment parented to itself as a root", () => {
    const built = buildCommentTree([{ id: 1, comment_id: 1, body: "a", parent_comment_id: 1 }]);
    expect(built.map((item) => item.id)).toEqual([1]);
  });

  it("is a safe no-op on an already-nested tree", () => {
    const nested = tree();
    expect(buildCommentTree(nested)).toBe(nested);
  });

  it("returns an empty array for an empty page", () => {
    expect(buildCommentTree([])).toEqual([]);
  });
});

/**
 * The round trip a paginating screen actually performs: hold rows flat, re-derive
 * the tree over everything loaded so far. These are the cases that make flat
 * accumulation safe where merging nested pages is not.
 */
describe("flat accumulation for paginated comments", () => {
  function flat(id: number, parentId = 0): PulseComment {
    return { id, comment_id: id, body: `c${id}`, parent_comment_id: parentId };
  }

  it("strips subtrees when flattening, so rebuilding is not a no-op", () => {
    // Without the strip, buildCommentTree sees a node carrying replies, takes its
    // already-nested short-circuit, and hands the flat list straight back — every
    // reply rendered as a root. This is the whole reason flatten removes them.
    const rows = flattenCommentTree(tree());
    expect(rows.every((row) => row.replies === undefined)).toBe(true);
    const rebuilt = buildCommentTree(rows);
    expect(rebuilt.map((item) => item.id)).toEqual([1, 5]);
    expect(findComment(rebuilt, 2)?.replies?.map((item) => item.id)).toEqual([4]);
  });

  it("round-trips a nested tree through flatten and rebuild without losing a node", () => {
    const rebuilt = buildCommentTree(flattenCommentTree(tree()));
    expect(countCommentTree(rebuilt)).toBe(countCommentTree(tree()));
  });

  it("does not mutate the tree it flattens", () => {
    const original = tree();
    flattenCommentTree(original);
    expect(original[0].replies?.map((item) => item.id)).toEqual([2, 3]);
  });

  it("appends a second page in server order", () => {
    const merged = mergeFlatComments([flat(1), flat(2, 1)], [flat(3), flat(4, 3)]);
    expect(merged.map((item) => item.id)).toEqual([1, 2, 3, 4]);
  });

  it("keeps every accumulated row a root when none of them had a parent", () => {
    // Regression: the accumulation was re-flattened with `existing.map(flatRow)`,
    // and Array#map passes (value, index, array) — so the row at index 1 was
    // stamped parent_comment_id 1, index 2 got 2, and so on. Re-deriving then
    // nested unrelated roots under each other and they disappeared from the list.
    // Every fixture above happens to nest reply N under comment N-1, which is
    // exactly the shape that hides an index-as-parent bug, so this case uses
    // rows that are ALL roots and asserts the flat rows carry no parent edge.
    const merged = mergeFlatComments([flat(1), flat(2), flat(3)], [flat(4)]);
    expect(merged.map((item) => Number(item.parent_comment_id || 0))).toEqual([0, 0, 0, 0]);
    expect(buildCommentTree(merged).map((item) => item.id)).toEqual([1, 2, 3, 4]);
  });

  it("does not invent a parent edge when appending to an accumulation of roots", () => {
    // The screen path that caught this: two root comments loaded, the user posts a
    // third. Comment 2 must not become a reply of comment 1.
    const built = buildCommentTree(mergeFlatComments([flat(1), flat(2)], [flat(50)]));
    expect(built.map((item) => item.id)).toEqual([1, 2, 50]);
    expect(built.every((item) => (item.replies || []).length === 0)).toBe(true);
  });

  it("re-parents a reply whose parent arrived on the previous page", () => {
    // This is the case mergeCommentPage cannot handle: comment 9's parent, 1, is
    // on page 1, so merging nested pages at the root level would leave 9 sitting
    // beside 1 as a top-level comment instead of underneath it.
    const page1 = [flat(1), flat(2, 1)];
    const page2 = [flat(9, 1), flat(10)];
    const built = buildCommentTree(mergeFlatComments(page1, page2));
    expect(built.map((item) => item.id)).toEqual([1, 10]);
    expect(findComment(built, 1)?.replies?.map((item) => item.id)).toEqual([2, 9]);
  });

  it("replaces an optimistic row with the confirmed one instead of showing both", () => {
    const optimistic = { ...flat(50, 1), body: "sending" };
    const confirmed = { ...flat(50, 1), body: "sent", created_at: "2026-07-25T00:00:05" };
    const merged = mergeFlatComments([flat(1), optimistic], [confirmed]);
    expect(merged.map((item) => item.id)).toEqual([1, 50]);
    expect(merged[1].body).toBe("sent");
  });

  it("keeps a field the incoming row omits rather than erasing it", () => {
    // A list page legitimately omits viewer_reaction; letting the omission
    // overwrite it would flicker a Liked button back to Like on every page load.
    const known: PulseComment = { ...flat(1), viewer_reaction: "like", can_edit: true };
    const merged = mergeFlatComments([known], [flat(1)]);
    expect(merged[0].viewer_reaction).toBe("like");
    expect(merged[0].can_edit).toBe(true);
  });

  it("lets the incoming row win for a field both carry", () => {
    const merged = mergeFlatComments([{ ...flat(1), body: "old" }], [{ ...flat(1), body: "edited" }]);
    expect(merged[0].body).toBe("edited");
  });

  it("flattens any nested row handed to it, so the accumulation stays flat", () => {
    const merged = mergeFlatComments(tree(), [flat(7)]);
    expect(merged.every((row) => row.replies === undefined)).toBe(true);
    expect(merged.map((item) => item.id)).toEqual([1, 5, 7]);
  });

  it("does not mutate either input", () => {
    const existing = [flat(1)];
    const incoming = [flat(2)];
    mergeFlatComments(existing, incoming);
    expect(existing.map((item) => item.id)).toEqual([1]);
    expect(incoming.map((item) => item.id)).toEqual([2]);
  });

  it("returns the incoming page when there is nothing accumulated yet", () => {
    expect(mergeFlatComments([], [flat(1), flat(2, 1)]).map((item) => item.id)).toEqual([1, 2]);
  });

  it("returns the accumulation unchanged for an empty page", () => {
    expect(mergeFlatComments([flat(1)], []).map((item) => item.id)).toEqual([1]);
  });
});
