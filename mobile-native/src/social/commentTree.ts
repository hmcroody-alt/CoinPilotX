// `import type` is deliberate, not stylistic. api/feed.ts imports
// buildCommentTree from this module, so a value import here would close a
// runtime require cycle between the two files. A type-only import is erased at
// compile time, which keeps the dependency one-directional at runtime while
// still sharing exactly one PulseComment definition.
import type { PulseComment } from "../api/feed";

// These helpers were implemented once for Reels and nowhere else, which is why
// feed posts and statuses shipped without nested replies: the logic existed but
// was locked inside a screen. They are pure functions over PulseComment[] with
// zero coupling to any content type, so they live here and every surface shares
// exactly one implementation. A guarantee stated twice is a guarantee that will
// eventually disagree with itself.

/** Depth-first search for a comment anywhere in the tree. */
export function findComment(comments: PulseComment[], commentId: number): PulseComment | null {
  for (const comment of comments) {
    if (comment.id === commentId) return comment;
    const nested = findComment(comment.replies || [], commentId);
    if (nested) return nested;
  }
  return null;
}

/** Depth-first search for the parent of a comment. Null for a root comment. */
export function findCommentParent(comments: PulseComment[], commentId: number, parent: PulseComment | null = null): PulseComment | null {
  for (const comment of comments) {
    if (comment.id === commentId) return parent;
    const nested = findCommentParent(comment.replies || [], commentId, comment);
    if (nested) return nested;
  }
  return null;
}

/** Depth of a comment in the tree: 0 for a root comment, -1 when not present. */
export function commentDepth(comments: PulseComment[], commentId: number, depth = 0): number {
  for (const comment of comments) {
    if (comment.id === commentId) return depth;
    const nested = commentDepth(comment.replies || [], commentId, depth + 1);
    if (nested >= 0) return nested;
  }
  return -1;
}

/**
 * Append a reply under `parentId`, bumping that parent's reply_count.
 * Returns the input unchanged (structurally new, referentially fresh) when the
 * parent is absent, so a stale reply target can never silently drop a comment
 * at the root.
 */
export function insertReply(comments: PulseComment[], parentId: number, reply: PulseComment): PulseComment[] {
  return comments.map((comment) => comment.id === parentId
    ? {
      ...comment,
      replies: [...(comment.replies || []), reply],
      reply_count: Number(comment.reply_count || comment.replies?.length || 0) + 1
    }
    : { ...comment, replies: insertReply(comment.replies || [], parentId, reply) });
}

/** Shallow-merge `next` onto the comment with `commentId`, preserving its subtree. */
export function updateCommentTree(comments: PulseComment[], commentId: number, next: Partial<PulseComment>): PulseComment[] {
  return comments.map((comment) => comment.id === commentId
    ? { ...comment, ...next }
    : { ...comment, replies: updateCommentTree(comment.replies || [], commentId, next) });
}

/**
 * Remove a comment and its whole subtree, and decrement the reply_count of
 * whichever comment was its parent. Reels' original version did not adjust
 * reply_count, so deleting a reply left the parent claiming a reply it no
 * longer had.
 */
export function removeCommentFromTree(comments: PulseComment[], commentId: number): PulseComment[] {
  return comments
    .filter((comment) => comment.id !== commentId)
    .map((comment) => {
      const replies = comment.replies || [];
      const hadDirectChild = replies.some((reply) => reply.id === commentId);
      const nextReplies = removeCommentFromTree(replies, commentId);
      if (!hadDirectChild) return { ...comment, replies: nextReplies };
      return {
        ...comment,
        replies: nextReplies,
        reply_count: Math.max(0, Number(comment.reply_count || replies.length || 0) - 1)
      };
    });
}

/** Total node count including every nested reply. */
export function countCommentTree(comments: PulseComment[]): number {
  return comments.reduce((total, comment) => total + 1 + countCommentTree(comment.replies || []), 0);
}

/**
 * Flatten the tree depth-first, parents before their replies.
 *
 * Two details make this the true inverse of `buildCommentTree`, which is what the
 * paginating screens depend on — they hold rows flat and re-derive the tree over
 * the whole accumulation, so anything flatten loses is gone for good.
 *
 * First, each node comes back WITHOUT its `replies` array. `buildCommentTree`
 * short-circuits when any input node already carries replies, so that calling it
 * on an already-nested tree is a safe no-op. If flatten left the subtrees
 * attached, `buildCommentTree(flattenCommentTree(tree))` would trip that
 * short-circuit and hand the flat list straight back — every reply rendered as a
 * root comment, which is exactly the defect the nesting work exists to remove.
 *
 * Second, each reply is stamped with the parent it was actually nested under. In
 * a pre-nested tree the parent relationship may live ONLY in `replies` — the
 * Reels endpoint returns a tree and need not repeat `parent_comment_id` on each
 * row — so dropping the subtree without recording the edge would flatten the
 * structure away and rebuild it as a list of roots.
 */
export function flattenCommentTree(comments: PulseComment[], parentId = 0): PulseComment[] {
  return comments.reduce<PulseComment[]>(
    (all, comment) => [
      ...all,
      flatCommentRow(comment, parentId),
      ...flattenCommentTree(comment.replies || [], comment.id)
    ],
    []
  );
}

/**
 * A single row with its subtree removed and its parent edge recorded.
 * See `flattenCommentTree` for why both halves matter.
 */
function flatCommentRow(comment: PulseComment, parentId = 0): PulseComment {
  const { replies, ...row } = comment;
  const observedParent = Number(parentId || comment.parent_comment_id || 0);
  return (observedParent ? { ...row, parent_comment_id: observedParent } : row) as PulseComment;
}

/**
 * Merge a page of rows into a FLAT accumulation, keeping server order and
 * de-duplicating by id.
 *
 * This is the flat analogue of `mergeCommentPage`, and it is the one a
 * paginating screen should use. `mergeCommentPage` merges at the root level
 * only, so a page-2 reply whose parent arrived on page 1 gets appended as a
 * top-level comment — a reply silently promoted to a root. Accumulating flat and
 * re-deriving the tree over everything loaded so far cannot do that, because
 * re-parenting always sees both rows.
 *
 * A repeated id is merged field-by-field with the incoming row winning, not
 * replaced outright: a list page legitimately omits fields a detail row carried
 * (`viewer_reaction`, `can_edit`), and letting an omission erase them would
 * flicker a Liked button back to Like. Merging this way is also what lets a
 * server-confirmed comment take the place of its optimistic twin instead of
 * appearing beside it.
 */
export function mergeFlatComments(existing: PulseComment[], incoming: PulseComment[]): PulseComment[] {
  // `existing.map(flatCommentRow)` would be wrong rather than merely terse:
  // Array#map passes (value, index, array), so the index would arrive as
  // `parentId` and every row after the first would be stamped with a fabricated
  // parent edge — the row at index 1 parented to comment 1, index 2 to comment
  // 2. buildCommentTree then nests real comments under unrelated ones and they
  // vanish from the root list. Pass the row explicitly and nothing else.
  const merged = existing.map((comment) => flatCommentRow(comment));
  const indexById = new Map<number, number>();
  merged.forEach((comment, index) => indexById.set(comment.id, index));
  incoming.forEach((comment) => {
    const row = flatCommentRow(comment);
    const index = indexById.get(row.id);
    if (index === undefined) {
      indexById.set(row.id, merged.length);
      merged.push(row);
      return;
    }
    merged[index] = { ...merged[index], ...row };
  });
  return merged;
}

/**
 * Merge a freshly fetched page of comments into an existing tree, keeping the
 * existing order and dropping duplicates by id. Server order wins for new
 * nodes; locally optimistic nodes that the server has now confirmed are
 * replaced rather than duplicated.
 */
export function mergeCommentPage(existing: PulseComment[], incoming: PulseComment[]): PulseComment[] {
  const seen = new Map<number, PulseComment>();
  existing.forEach((comment) => seen.set(comment.id, comment));
  const merged = [...existing];
  incoming.forEach((comment) => {
    const previous = seen.get(comment.id);
    if (!previous) {
      seen.set(comment.id, comment);
      merged.push(comment);
      return;
    }
    const index = merged.findIndex((item) => item.id === comment.id);
    if (index >= 0) {
      // Keep whichever subtree is larger: a list page often omits replies that
      // the client already loaded, and losing them would look like deletion.
      const replies = (comment.replies || []).length >= (previous.replies || []).length
        ? comment.replies || []
        : previous.replies || [];
      merged[index] = { ...previous, ...comment, replies };
    }
  });
  return merged;
}

/**
 * Build a nested tree from a FLAT list of comments carrying parent_comment_id.
 *
 * This is required for feed posts specifically. `pulse_feed_engine.list_comments`
 * (services/pulse_feed_engine.py:1422) returns a flat rows-as-received list with
 * `parent_comment_id` populated and no `replies` key, whereas the Reels endpoint
 * returns a pre-nested tree. Reels therefore got nested replies and posts did
 * not — the difference was never a missing capability, only a missing transform.
 *
 * Ordering is preserved from the input (the engine orders by created_at ASC,
 * id ASC). Guarantees, each covered by a named test:
 *   - a reply whose parent is absent from the page is promoted to a root rather
 *     than dropped, so a truncated page can never lose comments;
 *   - a parent cycle cannot produce infinite recursion;
 *   - a comment that already has `replies` is passed through untouched, so
 *     calling this on an already-nested tree is a safe no-op.
 */
export function buildCommentTree(flat: PulseComment[]): PulseComment[] {
  if (!flat.length) return [];
  const alreadyNested = flat.some((comment) => (comment.replies || []).length > 0);
  if (alreadyNested) return flat;

  const byId = new Map<number, PulseComment>();
  flat.forEach((comment) => byId.set(comment.id, { ...comment, replies: [] }));

  const roots: PulseComment[] = [];
  byId.forEach((comment) => {
    const parentId = Number(comment.parent_comment_id || 0);
    const parent = parentId && parentId !== comment.id ? byId.get(parentId) : undefined;
    if (!parent || createsCycle(byId, comment.id, parentId)) {
      roots.push(comment);
      return;
    }
    parent.replies = [...(parent.replies || []), comment];
  });

  return roots.map(withDerivedReplyCounts);
}

function createsCycle(byId: Map<number, PulseComment>, commentId: number, parentId: number): boolean {
  const seen = new Set<number>([commentId]);
  let cursor = parentId;
  while (cursor) {
    if (seen.has(cursor)) return true;
    seen.add(cursor);
    cursor = Number(byId.get(cursor)?.parent_comment_id || 0);
  }
  return false;
}

function withDerivedReplyCounts(comment: PulseComment): PulseComment {
  const replies = (comment.replies || []).map(withDerivedReplyCounts);
  return { ...comment, replies, reply_count: replies.length };
}

/** Immutable Set toggle, used for expanded-thread and busy-id sets. */
export function toggleSetValue<T>(values: Set<T>, value: T): Set<T> {
  const next = new Set(values);
  if (next.has(value)) next.delete(value);
  else next.add(value);
  return next;
}

/**
 * Apply a reaction result to one comment. `removed` means the viewer withdrew
 * their reaction, which is the case Reels' inline code got wrong by leaving
 * viewer_reaction set.
 */
export function applyCommentReaction(
  comments: PulseComment[],
  commentId: number,
  result: { removed?: boolean; reaction_type?: string; reaction_counts?: Record<string, number> }
): PulseComment[] {
  const existing = findComment(comments, commentId);
  if (!existing) return comments;
  const reactionType = result.reaction_type || "like";
  const removed = Boolean(result.removed);
  const counts = result.reaction_counts
    ? result.reaction_counts
    : nextLocalCounts(existing, reactionType, removed);
  return updateCommentTree(comments, commentId, {
    viewer_reaction: removed ? "" : reactionType,
    reaction_counts: counts,
    like_count: Object.values(counts).reduce((total, value) => total + Number(value || 0), 0)
  });
}

function nextLocalCounts(comment: PulseComment, reactionType: string, removed: boolean): Record<string, number> {
  const counts = { ...(comment.reaction_counts || {}) };
  const previous = comment.viewer_reaction || "";
  if (previous && counts[previous] !== undefined) {
    counts[previous] = Math.max(0, Number(counts[previous] || 0) - 1);
  }
  if (!removed) counts[reactionType] = Number(counts[reactionType] || 0) + 1;
  return counts;
}
