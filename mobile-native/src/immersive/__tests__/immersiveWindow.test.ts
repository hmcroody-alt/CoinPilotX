/**
 * The window, pinned on the two things that are invisible until they are fatal:
 * how many decoders can be alive at once, and that exactly one item plays.
 *
 * Neither shows up in a screenshot. An unbounded window looks perfect on a
 * desk-charged phone and gets killed for memory on a three-year-old one; two
 * active items sound wrong for a second and then get blamed on the network.
 * The ceiling tests matter most, because the failure they describe arrives as a
 * crash report with no line in this file anywhere in it.
 */
import {
  DEFAULT_IMMERSIVE_AHEAD,
  DEFAULT_IMMERSIVE_BEHIND,
  MAX_IMMERSIVE_AHEAD,
  MAX_IMMERSIVE_BEHIND,
  MAX_IMMERSIVE_MOUNTED,
  activeImmersiveSlot,
  immersivePreloadKeys,
  immersiveWindow,
  shouldReleaseImmersiveKey
} from "../immersiveWindow";
import {
  ImmersiveEntry,
  ImmersiveSession,
  beginImmersiveSession,
  moveImmersiveCursor
} from "../immersiveSession";

const post = (id: number): ImmersiveEntry => ({ kind: "post", id });

/** A session holding `count` posts numbered from 1, cursor wherever asked. */
function session(count: number, cursor = 0): ImmersiveSession {
  const entries = Array.from({ length: count }, (_unused, index) => post(index + 1));
  const started = beginImmersiveSession({ source: "HOME_FOR_YOU", entry: entries[0] }, entries.slice(1));
  return moveImmersiveCursor(started, cursor);
}

const roles = (slots: ReturnType<typeof immersiveWindow>) => slots.map((slot) => `${slot.key}:${slot.role}`);

describe("what is mounted", () => {
  it("mounts the cursor, the default lookahead, and one item behind", () => {
    expect(roles(immersiveWindow(session(10, 5)))).toEqual([
      "post:5:retained",
      "post:6:active",
      "post:7:preload",
      "post:8:preload"
    ]);
  });

  it("keeps the slice contiguous and in queue order", () => {
    const indices = immersiveWindow(session(10, 5)).map((slot) => slot.index);
    expect(indices).toEqual([4, 5, 6, 7]);
  });

  /**
   * The obvious-looking improvement is to spend the unused trailing slot on one
   * more item ahead. That would make the number of live decoders a function of
   * where the user is standing -- which is the property a bounded window exists
   * to remove. The ceiling has to hold at the top of the feed too, because the
   * top of the feed is where a cold app with the most else still in memory
   * begins.
   */
  it("does not slide the window forward to spend the slot it has no room for", () => {
    const atTop = immersiveWindow(session(10, 0));
    expect(roles(atTop)).toEqual(["post:1:active", "post:2:preload", "post:3:preload"]);
    expect(atTop).toHaveLength(1 + DEFAULT_IMMERSIVE_AHEAD);
  });

  it("stops at the end of the queue rather than describing items that do not exist", () => {
    expect(roles(immersiveWindow(session(3, 2)))).toEqual(["post:2:retained", "post:3:active"]);
  });

  it("has nothing to mount for an empty session", () => {
    const empty = beginImmersiveSession({ source: "HOME_FOR_YOU", entry: post(0) }, []);
    expect(immersiveWindow(empty)).toEqual([]);
    expect(activeImmersiveSlot(empty)).toBeNull();
    expect(immersivePreloadKeys(empty)).toEqual([]);
  });

  it("mounts a one-item session as exactly one active item", () => {
    expect(roles(immersiveWindow(session(1)))).toEqual(["post:1:active"]);
  });
});

describe("exactly one item plays", () => {
  it.each([0, 1, 4, 9])("has one and only one active slot at cursor %s", (cursor) => {
    const slots = immersiveWindow(session(10, cursor));
    expect(slots.filter((slot) => slot.role === "active")).toHaveLength(1);
  });

  it("names the cursor's own item, never a neighbour", () => {
    const active = activeImmersiveSlot(session(10, 5));
    expect(active?.key).toBe("post:6");
    expect(active?.index).toBe(5);
  });

  /**
   * Null rather than "the nearest playable thing". The engine's guarantee is
   * that the cursor and the playing item are the same item -- quietly playing a
   * neighbour would make the overlay, the like button and the view count all
   * describe something other than what is on screen.
   */
  it("reports nothing playing when the current entry cannot be keyed", () => {
    const broken = { ...session(3, 1), queue: [post(1), { kind: "post", id: 0 } as ImmersiveEntry, post(3)] };
    expect(activeImmersiveSlot(broken)).toBeNull();
    expect(immersiveWindow(broken).map((slot) => slot.key)).toEqual(["post:1", "post:3"]);
  });
});

describe("the preload budget", () => {
  it("warms the next items and not the current one", () => {
    // The active item is not *pre*-loading. It is loading, and unconditionally.
    expect(immersivePreloadKeys(session(10, 5))).toEqual(["post:7", "post:8"]);
  });

  /**
   * Nearest-first, because the budget is spent in the order the user reaches
   * the items: if only one of the two finishes before the swipe lands, it
   * should be the one being swiped to.
   */
  it("warms them nearest first", () => {
    expect(immersivePreloadKeys(session(10, 0))).toEqual(["post:2", "post:3"]);
  });

  it("warms nothing at the end of a finished queue", () => {
    expect(immersivePreloadKeys(session(3, 2))).toEqual([]);
  });

  /**
   * A watched item stays mounted so a back-swipe lands on a real frame rather
   * than the black card, but it must not re-buffer: swiping across one boundary
   * repeatedly would otherwise re-download the same video every time, which is
   * invisible on wifi and expensive on a phone plan.
   */
  it("does not re-warm an item that was already watched", () => {
    const slots = immersiveWindow(session(10, 5));
    expect(slots.find((slot) => slot.key === "post:5")?.role).toBe("retained");
    expect(immersivePreloadKeys(session(10, 5))).not.toContain("post:5");
  });
});

describe("the ceiling refuses to be widened", () => {
  /**
   * A bounded window whose bound is a parameter is not a bound. The first screen
   * that wants smoother scrolling passes `ahead: 8`, nothing visibly breaks on a
   * desk-charged iPhone 16 Pro, and the regression lands on older hardware as a
   * memory-pressure kill that no stack trace attributes to a preload setting.
   */
  it("clamps a caller asking for far more than the ceiling", () => {
    const slots = immersiveWindow(session(200, 100), { ahead: 50, behind: 50 });
    expect(slots).toHaveLength(MAX_IMMERSIVE_MOUNTED);
    expect(slots.filter((slot) => slot.role === "preload")).toHaveLength(MAX_IMMERSIVE_AHEAD);
    expect(slots.filter((slot) => slot.role === "retained")).toHaveLength(MAX_IMMERSIVE_BEHIND);
  });

  it.each([
    ["a huge lookahead", { ahead: 999 }],
    ["a huge trailing window", { behind: 999 }],
    ["both", { ahead: 999, behind: 999 }],
    ["fractions that round up past the ceiling", { ahead: 3.9, behind: 2.9 }]
  ])("never mounts more than the ceiling for %s", (_label, options) => {
    expect(immersiveWindow(session(200, 100), options).length).toBeLessThanOrEqual(MAX_IMMERSIVE_MOUNTED);
  });

  it("lets a caller ask for less", () => {
    expect(roles(immersiveWindow(session(10, 5), { ahead: 1, behind: 0 }))).toEqual([
      "post:6:active",
      "post:7:preload"
    ]);
  });

  it("still mounts the active item when asked for no window at all", () => {
    expect(roles(immersiveWindow(session(10, 5), { ahead: 0, behind: 0 }))).toEqual(["post:6:active"]);
  });

  it.each([
    ["negative", -4],
    ["not a number", NaN]
  ])("falls back to the default rather than inverting for %s lookahead", (_label, ahead) => {
    const slots = immersiveWindow(session(10, 5), { ahead });
    const preloads = slots.filter((slot) => slot.role === "preload").length;
    expect(preloads).toBe(ahead < 0 ? 0 : DEFAULT_IMMERSIVE_AHEAD);
    expect(slots.some((slot) => slot.role === "active")).toBe(true);
  });

  it("holds the ceiling it advertises", () => {
    expect(MAX_IMMERSIVE_MOUNTED).toBe(MAX_IMMERSIVE_AHEAD + MAX_IMMERSIVE_BEHIND + 1);
    expect(DEFAULT_IMMERSIVE_AHEAD).toBeLessThanOrEqual(MAX_IMMERSIVE_AHEAD);
    expect(DEFAULT_IMMERSIVE_BEHIND).toBeLessThanOrEqual(MAX_IMMERSIVE_BEHIND);
  });
});

describe("releasing what fell out", () => {
  it("keeps everything the window still holds", () => {
    const current = session(10, 5);
    for (const key of ["post:5", "post:6", "post:7", "post:8"]) {
      expect(shouldReleaseImmersiveKey(current, key)).toBe(false);
    }
  });

  it("releases what the cursor has moved away from", () => {
    expect(shouldReleaseImmersiveKey(session(10, 5), "post:1")).toBe(true);
    expect(shouldReleaseImmersiveKey(session(10, 5), "post:9")).toBe(true);
  });

  /**
   * The leak this guards against is the item that left the *queue* rather than
   * merely the window -- recycled out by §35 and never appearing in any window
   * comparison, so a diff of two windows would never mention it.
   */
  it("releases a key the session no longer has at all", () => {
    expect(shouldReleaseImmersiveKey(session(10, 5), "reel:6")).toBe(true);
    expect(shouldReleaseImmersiveKey(session(10, 5), "post:4000")).toBe(true);
  });

  /**
   * post:6 and reel:6 are different objects wearing the same number. A window
   * that matched on the bare id would keep a reel mounted because a post of the
   * same number is on screen.
   */
  it("does not confuse a post with the reel of the same number", () => {
    const current = session(10, 5);
    expect(shouldReleaseImmersiveKey(current, "post:6")).toBe(false);
    expect(shouldReleaseImmersiveKey(current, "reel:6")).toBe(true);
  });

  it("releases an empty key rather than holding a mount nothing can address", () => {
    expect(shouldReleaseImmersiveKey(session(10, 5), "")).toBe(true);
  });
});

describe("moving the cursor", () => {
  it("advances the window by exactly one when the user swipes once", () => {
    const before = immersiveWindow(session(10, 5)).map((slot) => slot.index);
    const after = immersiveWindow(session(10, 6)).map((slot) => slot.index);
    expect(before).toEqual([4, 5, 6, 7]);
    expect(after).toEqual([5, 6, 7, 8]);
  });

  /**
   * Three of the four mounts survive a swipe. If the keys changed shape between
   * renders the list would tear them all down and rebuild, which is a black
   * frame on every swipe -- §14's failure arriving through the reconciler.
   */
  it("keeps the overlapping mounts addressable by the same keys", () => {
    const before = immersiveWindow(session(10, 5)).map((slot) => slot.key);
    const after = immersiveWindow(session(10, 6)).map((slot) => slot.key);
    expect(after.filter((key) => before.includes(key))).toEqual(["post:6", "post:7", "post:8"]);
  });

  it("is stable: the same session produces the same window twice", () => {
    expect(immersiveWindow(session(10, 5))).toEqual(immersiveWindow(session(10, 5)));
  });
});
