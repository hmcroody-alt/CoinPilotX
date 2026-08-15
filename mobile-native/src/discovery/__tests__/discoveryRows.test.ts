/**
 * The placement rules, asserted as arithmetic.
 *
 * Every one of these could in principle be checked by rendering Home and
 * counting carousels, and none of them should be: a rendered assertion passes
 * for the wrong reason the moment a card fails to mount, and it cannot
 * distinguish "the rule is right" from "this particular fixture happened to
 * produce the right shape". The engine is pure, so the rules are testable as
 * what they are — a cadence, a cap, and a rotation.
 *
 * The invariants worth stating out loud, because each one is a real way the
 * feed gets worse and none of them is visible in a diff:
 *
 *   - a suggestion row must never separate a post from the ad it earned;
 *   - two suggestion rows must never touch;
 *   - the feed must never end on a carousel, which reads as "no more posts";
 *   - a module that is short on items must not render as a stub.
 */
import type { FeedRow } from "../../feed/injectAds";
import {
  DiscoveryModule,
  DiscoveryModuleKind,
  HomeRow,
  discoveryRowKey,
  injectDiscoveryRows
} from "../discoveryRows";

type Post = { id: number };

/** `n` organic post rows, in the shape `injectAds` emits. */
function postRows(n: number): FeedRow<Post>[] {
  return Array.from({ length: n }, (_, i) => ({
    type: "post" as const,
    key: `post:${i + 1}`,
    post: { id: i + 1 }
  }));
}

/** A module with `count` items — enough to be eligible unless told otherwise. */
function reelsModule(count = 5): DiscoveryModule {
  return {
    kind: "reels",
    titleKey: "home:discovery.reels",
    items: Array.from({ length: count }, (_, i) => ({ reelId: i + 1, title: `reel ${i + 1}` }))
  };
}

function peopleModule(count = 5): DiscoveryModule {
  return {
    kind: "people",
    titleKey: "home:discovery.people",
    items: Array.from({ length: count }, (_, i) => ({
      profileKey: `user${i + 1}`,
      username: `user${i + 1}`,
      displayName: `User ${i + 1}`
    }))
  };
}

function groupsModule(count = 5): DiscoveryModule {
  return {
    kind: "groups",
    titleKey: "home:discovery.groups",
    items: Array.from({ length: count }, (_, i) => ({ slug: `g${i + 1}`, name: `Group ${i + 1}` }))
  };
}

/** The kinds actually placed, in feed order. */
const kindsOf = (rows: HomeRow<Post>[]): DiscoveryModuleKind[] =>
  rows.flatMap((row) => (row.type === "discovery" ? [row.module.kind] : []));

const shapeOf = (rows: HomeRow<Post>[]): string[] => rows.map((row) => row.type);

describe("cadence", () => {
  it("places nothing before the lead-in is satisfied", () => {
    const rows = injectDiscoveryRows(postRows(4), [reelsModule()], { leadIn: 5, interval: 7 });

    expect(kindsOf(rows)).toEqual([]);
    expect(rows).toHaveLength(4);
  });

  it("places the first row immediately after the lead-in post", () => {
    const rows = injectDiscoveryRows(postRows(10), [reelsModule()], { leadIn: 5, interval: 7 });

    // Five posts, then the carousel. Index 5 and not index 4: the row goes
    // *after* the fifth post, not in place of it.
    expect(shapeOf(rows).slice(0, 7)).toEqual(["post", "post", "post", "post", "post", "discovery", "post"]);
  });

  it("repeats on the interval, counting organic posts only", () => {
    const rows = injectDiscoveryRows(postRows(30), [reelsModule(), peopleModule(), groupsModule()], {
      leadIn: 5,
      interval: 7,
      maxRows: 3
    });

    const positions = rows.flatMap((row, index) => (row.type === "discovery" ? [index] : []));
    // Post 5, post 12, post 19 — each shifted right by the rows already placed.
    expect(positions).toEqual([5, 13, 21]);
  });

  it("never puts two suggestion rows next to each other, even at interval 1", () => {
    const rows = injectDiscoveryRows(postRows(12), [reelsModule(), peopleModule(), groupsModule()], {
      leadIn: 1,
      interval: 1,
      maxRows: 6
    });

    const shape = shapeOf(rows);
    const adjacent = shape.some((type, i) => type === "discovery" && shape[i + 1] === "discovery");
    expect(adjacent).toBe(false);
    // And interval 1 really did place on every post, so this is not passing
    // because nothing was placed.
    expect(kindsOf(rows)).toHaveLength(6);
  });

  it("never ends the feed on a suggestion row", () => {
    // Lead-in 5 against exactly 5 posts: the slot is eligible but there is
    // nothing under it, so a carousel there reads as the end of the feed.
    const rows = injectDiscoveryRows(postRows(5), [reelsModule()], { leadIn: 5, interval: 7 });

    expect(kindsOf(rows)).toEqual([]);
    expect(rows[rows.length - 1].type).toBe("post");
  });
});

describe("ad rows", () => {
  /** Five posts with an ad earned by the fifth — the shape Home actually builds. */
  function rowsWithTrailingAd(): FeedRow<Post>[] {
    return [
      ...postRows(5),
      { type: "ad", key: "ad:c1:cr1:0", ad: { campaignId: "c1", creativeId: "cr1" } as never }
    ];
  }

  it("keeps an ad attached to the post that earned it", () => {
    const rows = injectDiscoveryRows([...rowsWithTrailingAd(), ...postRows(3)], [reelsModule()], {
      leadIn: 5,
      interval: 7
    });

    // post ×5, ad, discovery — the carousel goes after the pair, never between.
    expect(shapeOf(rows)).toEqual(["post", "post", "post", "post", "post", "ad", "discovery", "post", "post", "post"]);
  });

  it("does not count an ad toward the organic cadence", () => {
    const withAds: FeedRow<Post>[] = [];
    postRows(12).forEach((row, i) => {
      withAds.push(row);
      if ((i + 1) % 3 === 0) {
        withAds.push({ type: "ad", key: `ad:${i}`, ad: { campaignId: `c${i}`, creativeId: "x" } as never });
      }
    });

    const rows = injectDiscoveryRows(withAds, [reelsModule(), peopleModule()], {
      leadIn: 5,
      interval: 5,
      maxRows: 2
    });

    // If ads counted, the second row would land early. Organic post 5 and
    // organic post 10 are what must decide.
    const organicBefore = (limit: number) =>
      rows.slice(0, limit).filter((row) => row.type === "post").length;
    const positions = rows.flatMap((row, index) => (row.type === "discovery" ? [index] : []));
    expect(positions).toHaveLength(2);
    expect(organicBefore(positions[0])).toBe(5);
    expect(organicBefore(positions[1])).toBe(10);
  });
});

describe("which module goes where", () => {
  it("rotates round-robin so one kind cannot take every slot", () => {
    const rows = injectDiscoveryRows(postRows(40), [reelsModule(), peopleModule(), groupsModule()], {
      leadIn: 3,
      interval: 4,
      maxRows: 4
    });

    expect(kindsOf(rows)).toEqual(["reels", "people", "groups", "reels"]);
  });

  it("starts the rotation where the offset says, so a refresh reshuffles", () => {
    const modules = [reelsModule(), peopleModule(), groupsModule()];
    const at = (rotationOffset: number) =>
      kindsOf(injectDiscoveryRows(postRows(40), modules, { leadIn: 3, interval: 4, maxRows: 3, rotationOffset }));

    expect(at(0)).toEqual(["reels", "people", "groups"]);
    expect(at(1)).toEqual(["people", "groups", "reels"]);
    expect(at(2)).toEqual(["groups", "reels", "people"]);
    // Wraps rather than throwing, and a negative offset stays in range: this is
    // a counter that only ever goes up on the caller's side, so it will exceed
    // the module count on any long session.
    expect(at(3)).toEqual(at(0));
    expect(at(-1)).toEqual(at(2));
  });

  it("skips a module the user dismissed without leaving a gap", () => {
    const dismissed = new Set<DiscoveryModuleKind>(["people"]);
    const rows = injectDiscoveryRows(postRows(40), [reelsModule(), peopleModule(), groupsModule()], {
      leadIn: 3,
      interval: 4,
      maxRows: 4,
      dismissed
    });

    expect(kindsOf(rows)).toEqual(["reels", "groups", "reels", "groups"]);
  });

  it("places nothing at all when every module is dismissed", () => {
    const dismissed = new Set<DiscoveryModuleKind>(["reels", "people"]);
    const rows = injectDiscoveryRows(postRows(40), [reelsModule(), peopleModule()], {
      leadIn: 3,
      interval: 4,
      dismissed
    });

    expect(kindsOf(rows)).toEqual([]);
    expect(shapeOf(rows)).toEqual(shapeOf(postRows(40)));
  });

  it("skips a module that is too short to fill a carousel", () => {
    const rows = injectDiscoveryRows(postRows(40), [reelsModule(2), peopleModule(5)], {
      leadIn: 3,
      interval: 4,
      maxRows: 3,
      minItems: 3
    });

    expect(kindsOf(rows)).toEqual(["people", "people", "people"]);
  });

  it("treats an empty module list as the feature being off", () => {
    const rows = injectDiscoveryRows(postRows(20), [], { leadIn: 3, interval: 4 });

    expect(rows).toEqual(postRows(20));
  });
});

describe("the cap", () => {
  it("stops at maxRows however long the feed gets", () => {
    const rows = injectDiscoveryRows(postRows(200), [reelsModule(), peopleModule()], {
      leadIn: 3,
      interval: 4,
      maxRows: 4
    });

    expect(kindsOf(rows)).toHaveLength(4);
  });

  it("places nothing when the cap is zero", () => {
    const rows = injectDiscoveryRows(postRows(40), [reelsModule()], { leadIn: 3, interval: 4, maxRows: 0 });

    expect(kindsOf(rows)).toEqual([]);
  });
});

describe("row identity", () => {
  it("gives every row a unique, stable key", () => {
    const rows = injectDiscoveryRows(postRows(40), [reelsModule(), peopleModule()], {
      leadIn: 3,
      interval: 4,
      maxRows: 4
    });

    const keys = rows.map((row) => row.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(rows.flatMap((row) => (row.type === "discovery" ? [row.key] : []))).toEqual([
      discoveryRowKey("reels", 0),
      discoveryRowKey("people", 1),
      discoveryRowKey("reels", 2),
      discoveryRowKey("people", 3)
    ]);
  });

  it("numbers slots from zero in feed order, for analytics", () => {
    const rows = injectDiscoveryRows(postRows(40), [reelsModule(), peopleModule()], {
      leadIn: 3,
      interval: 4,
      maxRows: 3
    });

    expect(rows.flatMap((row) => (row.type === "discovery" ? [row.slot] : []))).toEqual([0, 1, 2]);
  });
});

describe("the rows it was given", () => {
  it("returns them in order, unchanged, by identity", () => {
    // The organic feed is the product. A placement engine that rebuilds post
    // rows would silently break `React.memo` on every card and re-render the
    // whole list on each insertion.
    const input = postRows(20);
    const rows = injectDiscoveryRows(input, [reelsModule()], { leadIn: 3, interval: 4 });

    const passedThrough = rows.filter((row) => row.type !== "discovery");
    expect(passedThrough).toHaveLength(input.length);
    passedThrough.forEach((row, i) => expect(row).toBe(input[i]));
  });

  it("does not mutate the array it was given", () => {
    const input = postRows(20);
    injectDiscoveryRows(input, [reelsModule()], { leadIn: 3, interval: 4 });

    expect(input).toHaveLength(20);
    expect(input.every((row) => row.type === "post")).toBe(true);
  });
});
