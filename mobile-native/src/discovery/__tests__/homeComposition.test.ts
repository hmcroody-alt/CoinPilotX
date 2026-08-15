/**
 * §15/§17 — the composition Home actually performs, asserted directly.
 *
 * `HomeScreen.tsx` builds its rows with exactly one expression:
 *
 *     injectDiscoveryRows(injectAds(posts, ads, { interval: 5, leadIn: 3 }), modules, opts)
 *
 * so that expression is what is tested here, with the real `injectAds` rather
 * than a stand-in. Rendering the whole screen would test a hundred unrelated
 * things and would not pin down the property that matters.
 *
 * The property that matters is the rollback guarantee: **with the flags off,
 * Home's row list must be the array `injectAds` produced, unchanged.** Not
 * "equivalent", not "the same length" — the same rows. A regression here is
 * invisible in review and only shows up as a feed that shifted under a user who
 * never opted in.
 */
import { injectAds, FeedRow } from "../../feed/injectAds";
import { injectDiscoveryRows, DiscoveryModule, DiscoveryRow, HomeRow } from "../discoveryRows";
import type { SponsoredAd } from "../../api/ads";

type Post = { id: number };

const posts = (count: number): Post[] => Array.from({ length: count }, (_, i) => ({ id: i + 1 }));

const ad = (n: number) =>
  ({ campaignId: `c${n}`, creativeId: `cr${n}`, headline: `Ad ${n}` }) as unknown as SponsoredAd;

function reelsModule(count = 4): DiscoveryModule {
  return {
    kind: "reels",
    titleKey: "social:feed.discovery.reelsTitle",
    items: Array.from({ length: count }, (_, i) => ({ reelId: i + 1, title: `Reel ${i + 1}` }))
  };
}

function groupsModule(count = 4): DiscoveryModule {
  return {
    kind: "groups",
    titleKey: "social:feed.discovery.groupsTitle",
    items: Array.from({ length: count }, (_, i) => ({ slug: `g${i + 1}`, name: `Group ${i + 1}` }))
  };
}

/** The Home expression, verbatim. */
function homeRows(
  postList: Post[],
  ads: SponsoredAd[],
  modules: DiscoveryModule[],
  options: Parameters<typeof injectDiscoveryRows>[2] = {}
): HomeRow<Post>[] {
  return injectDiscoveryRows(injectAds(postList, ads, { interval: 5, leadIn: 3 }), modules, options);
}

const discoveryRows = <T,>(rows: HomeRow<T>[]) => rows.filter((row): row is DiscoveryRow => row.type === "discovery");

describe("the flags-off rollback guarantee (§15)", () => {
  it("returns rows equal to injectAds' output when there are no modules", () => {
    const base = injectAds(posts(40), [ad(1), ad(2)], { interval: 5, leadIn: 3 });

    const composed = injectDiscoveryRows(base, []);

    expect(composed).toEqual(base);
  });

  it("preserves every key, in order, with no modules", () => {
    const base = injectAds(posts(40), [ad(1), ad(2)], { interval: 5, leadIn: 3 });

    const composed = injectDiscoveryRows(base, []);

    expect(composed.map((row) => row.key)).toEqual(base.map((row) => row.key));
  });

  it("passes the very same row objects through, not copies", () => {
    // Identity, not deep equality: `PostCard` and `SponsoredAdCard` are memoized
    // on these objects, so a copy would re-render the entire visible feed on
    // every recomputation even with the feature off.
    const base = injectAds(posts(20), [ad(1)], { interval: 5, leadIn: 3 });

    const composed = injectDiscoveryRows(base, []);

    composed.forEach((row, index) => expect(row).toBe(base[index]));
  });

  it("adds nothing when every available module is too small to show", () => {
    const base = injectAds(posts(40), [], { interval: 5, leadIn: 3 });

    // A two-card carousel reads as a loading error, so it is not placed at all.
    const composed = injectDiscoveryRows(base, [reelsModule(2)]);

    expect(composed).toEqual(base);
  });

  it("adds nothing when the only module is dismissed", () => {
    const base = injectAds(posts(40), [], { interval: 5, leadIn: 3 });

    const composed = injectDiscoveryRows(base, [reelsModule()], { dismissed: new Set(["reels"]) });

    expect(composed).toEqual(base);
  });
});

describe("living alongside ads (§3)", () => {
  it("never separates an ad from the post that earned it", () => {
    const rows = homeRows(posts(60), [ad(1), ad(2), ad(3)], [reelsModule(), groupsModule()]);

    rows.forEach((row, index) => {
      if (row.type !== "ad") return;
      // `injectAds` places an ad immediately after its post. A suggestion row
      // wedged in between would read as the ad belonging to the carousel.
      expect(rows[index - 1]?.type).toBe("post");
    });
  });

  it("keeps every ad that injectAds placed", () => {
    const base = injectAds(posts(60), [ad(1), ad(2), ad(3)], { interval: 5, leadIn: 3 });
    const rows = homeRows(posts(60), [ad(1), ad(2), ad(3)], [reelsModule(), groupsModule()]);

    const adKeys = (list: HomeRow<Post>[]) => list.filter((row) => row.type === "ad").map((row) => row.key);
    expect(adKeys(rows)).toEqual(adKeys(base));
  });

  it("keeps every post, in the original order", () => {
    const rows = homeRows(posts(60), [ad(1)], [reelsModule(), groupsModule()]);

    const ids = rows.filter((row) => row.type === "post").map((row) => (row as FeedRow<Post> & { post: Post }).post.id);
    expect(ids).toEqual(posts(60).map((post) => post.id));
  });

  it("never places two suggestion rows back to back", () => {
    const rows = homeRows(posts(60), [ad(1), ad(2)], [reelsModule(), groupsModule()]);

    rows.forEach((row, index) => {
      if (row.type !== "discovery") return;
      expect(rows[index + 1]?.type).not.toBe("discovery");
    });
  });

  it("never ends the feed on a suggestion row", () => {
    // A carousel with nothing under it reads as the end of the feed.
    const rows = homeRows(posts(60), [ad(1)], [reelsModule(), groupsModule()]);

    expect(rows[rows.length - 1]?.type).not.toBe("discovery");
  });
});

describe("what actually gets placed", () => {
  it("rotates kinds instead of stacking one", () => {
    const rows = homeRows(posts(60), [], [reelsModule(), groupsModule()]);
    const kinds = discoveryRows(rows).map((row) => row.module.kind);

    expect(kinds.length).toBeGreaterThan(1);
    expect(kinds[0]).not.toBe(kinds[1]);
  });

  it("caps suggestions per page so the feed is never mostly carousels", () => {
    const rows = homeRows(posts(200), [], [reelsModule(), groupsModule()]);

    expect(discoveryRows(rows).length).toBeLessThanOrEqual(4);
  });

  it("gives each placed row a distinct, stable key", () => {
    const rows = homeRows(posts(60), [], [reelsModule(), groupsModule()]);
    const keys = discoveryRows(rows).map((row) => row.key);

    expect(new Set(keys).size).toBe(keys.length);
    // Same inputs, same keys — FlatList reuse depends on it.
    expect(discoveryRows(homeRows(posts(60), [], [reelsModule(), groupsModule()])).map((row) => row.key)).toEqual(keys);
  });

  it("moves which kind lands where when the refresh token advances", () => {
    // §3's rotation: Home passes its refresh counter straight through as the
    // rotation offset, so a pull-to-refresh re-orders the kinds.
    const first = discoveryRows(homeRows(posts(60), [], [reelsModule(), groupsModule()], { rotationOffset: 0 }));
    const second = discoveryRows(homeRows(posts(60), [], [reelsModule(), groupsModule()], { rotationOffset: 1 }));

    expect(first[0].module.kind).not.toBe(second[0].module.kind);
  });

  it("places nothing in a feed too short to have earned a suggestion", () => {
    const base = injectAds(posts(3), [], { interval: 5, leadIn: 3 });

    expect(injectDiscoveryRows(base, [reelsModule()])).toEqual(base);
  });
});
