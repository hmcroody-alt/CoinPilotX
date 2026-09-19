/**
 * The session model, pinned from the side that matters.
 *
 * Most of what is asserted here is *absence*: that a post and a reel sharing a
 * number stay two objects, that a recycled item does not come back, that a
 * non-continuable source never grows. Every one of those failures is silent at
 * runtime — no error, no crash, just a hole in the feed or a loop in it — so
 * they cannot be caught anywhere except here.
 */
import {
  ImmersiveEntry,
  ImmersiveOrigin,
  appendImmersivePage,
  beginImmersiveSession,
  currentImmersiveEntry,
  entryKey,
  immersiveReturnTarget,
  moveImmersiveCursor,
  shouldContinueImmersive,
  sourceCanContinue
} from "../immersiveSession";

const post = (id: number, authorId = 1): ImmersiveEntry => ({ kind: "post", id, authorId });
const reel = (id: number, authorId = 1): ImmersiveEntry => ({ kind: "reel", id, authorId });

function origin(over: Partial<ImmersiveOrigin> = {}): ImmersiveOrigin {
  return { source: "HOME_FOR_YOU", entry: post(38), ...over };
}

describe("entry identity", () => {
  it("namespaces the key by kind, so two tables' ids cannot collide", () => {
    expect(entryKey(post(38))).toBe("post:38");
    expect(entryKey(reel(38))).toBe("reel:38");
    expect(entryKey(post(38))).not.toBe(entryKey(reel(38)));
  });

  /**
   * The assertion the id-namespacing exists for. Without it the seen-set records
   * `38` for the post and then silently swallows the reel -- no error, the feed
   * just never shows it.
   */
  it("keeps a post and a reel with the same number as two separate items", () => {
    const session = beginImmersiveSession(origin({ entry: post(38) }), [reel(38)]);
    expect(session.queue).toEqual([post(38), reel(38)]);
  });

  it.each([
    ["zero", 0],
    ["negative", -1],
    ["not a number", NaN]
  ])("refuses to key an entry with %s for an id", (_label, id) => {
    expect(entryKey({ kind: "post", id: id as number })).toBeNull();
  });

  it("fabricates no key for an entry with no kind", () => {
    expect(entryKey({ id: 7 } as ImmersiveEntry)).toBeNull();
  });
});

describe("beginning a session", () => {
  it("opens on the tapped item, whatever position the seed had it in", () => {
    const session = beginImmersiveSession(origin({ entry: post(99) }), [post(1), post(99), post(2)]);
    expect(currentImmersiveEntry(session)).toEqual(post(99));
    expect(session.cursor).toBe(0);
  });

  /**
   * "Tap this, get this" is the guarantee the whole engine rests on, so the
   * tapped item is hoisted rather than merely included -- and hoisting must not
   * duplicate it further down the queue.
   */
  it("does not leave a second copy of the tapped item further down", () => {
    const session = beginImmersiveSession(origin({ entry: post(99) }), [post(1), post(99)]);
    expect(session.queue).toEqual([post(99), post(1)]);
  });

  it("drops a seeded item that could never be opened", () => {
    const session = beginImmersiveSession(origin(), [post(0), post(7)]);
    expect(session.queue).toEqual([post(38), post(7)]);
  });

  it("resumes continuation from the origin's cursor rather than from zero", () => {
    expect(beginImmersiveSession(origin({ cursor: 40 }), [post(1)]).nextCursor).toBe(40);
  });

  /**
   * Without a cursor the next page starts after what was seeded, not at 0 --
   * starting at 0 would re-serve the whole first page, every item of which the
   * user already scrolled past to reach the thing they tapped.
   */
  it("starts after the seed when the origin had no cursor to give", () => {
    expect(beginImmersiveSession(origin(), [post(1), post(2)]).nextCursor).toBe(3);
  });
});

describe("continuing", () => {
  it("appends a page and moves the cursor forward", () => {
    const session = appendImmersivePage(beginImmersiveSession(origin(), [post(1)]), [post(2), post(3)], {
      nextCursor: 60
    });
    expect(session.queue).toEqual([post(38), post(1), post(2), post(3)]);
    expect(session.nextCursor).toBe(60);
  });

  it("drops an item the session has already shown", () => {
    const session = appendImmersivePage(beginImmersiveSession(origin(), [post(1)]), [post(1), post(2)]);
    expect(session.queue).toEqual([post(38), post(1), post(2)]);
  });

  /**
   * The reason `seen` is a separate set from `queue`. A bounded window (§35)
   * forgets early items, so deduping against the queue would let a later page
   * re-show what the first page showed -- which reads as the feed looping.
   */
  it("still refuses an item dropped from the queue but already watched", () => {
    const started = beginImmersiveSession(origin(), [post(1), post(2)]);
    // Window recycling has forgotten post(38) and post(1); only post(2) is left
    // in the queue, but all three remain in `seen`.
    const recycled = { ...started, queue: started.queue.slice(2) };
    expect(recycled.queue).toEqual([post(2)]);
    const after = appendImmersivePage(recycled, [post(1), post(38)]);
    // Nothing was admitted -- the assertion is that the queue did not grow, not
    // that it is empty. post(2) is still legitimately in it.
    expect(after.queue).toEqual([post(2)]);
  });

  /**
   * A page can be entirely duplicates while more genuinely remains behind it, so
   * the end of the feed is the server's word and not an inference from a page
   * that happened to add nothing.
   */
  it("does not call an all-duplicate page the end of the feed", () => {
    const session = appendImmersivePage(beginImmersiveSession(origin(), [post(1)]), [post(1)]);
    expect(session.canContinue).toBe(true);
  });

  it("stops when the server says there is no more", () => {
    const session = appendImmersivePage(beginImmersiveSession(origin(), []), [post(2)], { exhausted: true });
    expect(session.canContinue).toBe(false);
    expect(shouldContinueImmersive(session)).toBe(false);
  });

  it("asks for more only once the end is within the lookahead", () => {
    const session = beginImmersiveSession(origin(), [post(1), post(2), post(3), post(4), post(5)]);
    expect(shouldContinueImmersive(session, 2)).toBe(false);
    expect(shouldContinueImmersive(moveImmersiveCursor(session, 3), 2)).toBe(true);
  });
});

describe("sources that cannot continue", () => {
  /**
   * `SHARED_POST` is one object somebody sent in a message. Continuing from it
   * into a ranked feed takes a person who tapped a specific shared thing and
   * drops them into general browsing. `SEARCH` and `GROUP` have no cursor at all
   * -- continuing them would mean inventing a second, differently-ranked query.
   */
  it.each([
    ["a shared post", "SHARED_POST"],
    ["search results", "SEARCH"],
    ["a group", "GROUP"]
  ] as const)("never continues from %s", (_label, source) => {
    expect(sourceCanContinue(source)).toBe(false);
    const session = beginImmersiveSession(origin({ source, entry: post(5) }), [post(6)]);
    expect(session.canContinue).toBe(false);
    expect(shouldContinueImmersive(session, 99)).toBe(false);
  });

  it.each([
    ["for you", "HOME_FOR_YOU"],
    ["following", "HOME_FOLLOWING"],
    ["friends", "HOME_FRIENDS"],
    ["a profile", "PROFILE"],
    ["reels", "REELS"]
  ] as const)("continues from %s", (_label, source) => {
    expect(sourceCanContinue(source)).toBe(true);
  });

  it("is a finite gallery rather than a broken one", () => {
    const session = beginImmersiveSession(origin({ source: "SEARCH" }), [post(1), post(2)]);
    expect(session.queue).toHaveLength(3);
    expect(currentImmersiveEntry(session)).toEqual(post(38));
  });
});

describe("the cursor", () => {
  it("clamps past the end rather than landing on a blank frame", () => {
    const session = beginImmersiveSession(origin(), [post(1)]);
    expect(moveImmersiveCursor(session, 99).cursor).toBe(1);
    expect(moveImmersiveCursor(session, -5).cursor).toBe(0);
  });

  it("returns the same object when the cursor did not move", () => {
    const session = beginImmersiveSession(origin(), [post(1)]);
    expect(moveImmersiveCursor(session, 0)).toBe(session);
  });

  it("has nothing to move to in an empty session", () => {
    const empty = beginImmersiveSession(origin({ entry: post(0) }), []);
    expect(empty.queue).toEqual([]);
    expect(currentImmersiveEntry(empty)).toBeNull();
    expect(moveImmersiveCursor(empty, 3)).toBe(empty);
  });
});

describe("going back where you came from", () => {
  /**
   * The tapped entry, not the one the cursor ended on. Those differ once the
   * user has swiped onward, and returning them to item 40 of somebody else's
   * media -- which the origin's own list does not contain -- would land them at
   * the top of a feed they were halfway down.
   */
  it("returns to what was tapped, not to where the swiping ended", () => {
    const session = appendImmersivePage(beginImmersiveSession(origin({ entry: post(38) }), [post(1)]), [
      post(2),
      post(3)
    ]);
    const moved = moveImmersiveCursor(session, 3);
    expect(currentImmersiveEntry(moved)).toEqual(post(3));
    expect(immersiveReturnTarget(moved)).toEqual(post(38));
  });

  it("keeps the origin intact across continuation", () => {
    const session = appendImmersivePage(beginImmersiveSession(origin({ cursor: 20 }), []), [post(9)]);
    expect(session.origin.source).toBe("HOME_FOR_YOU");
    expect(session.origin.entry).toEqual(post(38));
  });
});

describe("immutability", () => {
  /**
   * The session is handed to React state, so a mutation in place would update
   * the value without changing its identity and the render would never happen.
   */
  it("never mutates the session it was given", () => {
    const session = beginImmersiveSession(origin(), [post(1)]);
    const snapshot = { queue: session.queue, cursor: session.cursor, seen: session.seen };
    appendImmersivePage(session, [post(2)]);
    moveImmersiveCursor(session, 1);
    expect(session.queue).toBe(snapshot.queue);
    expect(session.cursor).toBe(snapshot.cursor);
    expect(session.seen).toBe(snapshot.seen);
    expect(session.queue).toHaveLength(2);
  });
});
