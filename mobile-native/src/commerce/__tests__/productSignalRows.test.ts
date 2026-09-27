/**
 * Product placement, asserted as arithmetic — and asserted against the *other
 * two* injectors rather than in isolation.
 *
 * The isolated half is the easy half: cadence, cap, and the three invariants the
 * docblock states. The half that matters is the last describe block. Three
 * injectors write into one list, each owns its own cadence, and a collision
 * between two of them is invisible to a unit test of either — both pass, and the
 * feed still stacks a sponsored card, a suggestion carousel and a product card in
 * a row, which is not a social feed. So the real Home expression is built here
 * with the real `injectAds` and the real `injectDiscoveryRows`, and adjacency is
 * checked across all three row types at once.
 */
import { injectAds, type FeedRow } from "../../feed/injectAds";
import { injectDiscoveryRows, type DiscoveryModule, type HomeRow } from "../../discovery/discoveryRows";
import type { SponsoredAd } from "../../api/ads";
import type { ProductSignal } from "../productSignal";
import {
  PRODUCT_SIGNAL_INTERVAL,
  PRODUCT_SIGNAL_LEAD_IN,
  PRODUCT_SIGNAL_MAX_ROWS,
  injectProductSignalRows,
  productSignalRowKey,
  type CommerceFeedRow
} from "../productSignalRows";

type Post = { id: number };

const posts = (count: number): Post[] => Array.from({ length: count }, (_, i) => ({ id: i + 1 }));

function postRows(count: number): FeedRow<Post>[] {
  return posts(count).map((post) => ({ type: "post" as const, key: `post:${post.id}`, post }));
}

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

/** Only the fields placement reads; the adapter is tested separately. */
function signal(productId: number): ProductSignal {
  return { productId, productName: `Product ${productId}` } as unknown as ProductSignal;
}

const signals = (count: number) => Array.from({ length: count }, (_, i) => signal(i + 1));

/** 1-based positions of the product rows in the output. */
const productIndices = <T,>(rows: CommerceFeedRow<T>[]) =>
  rows.flatMap((row, index) => (row.type === "product" ? [index] : []));

/** How many organic posts precede each product row. */
function organicBefore<T>(rows: CommerceFeedRow<T>[]): number[] {
  const out: number[] = [];
  let organic = 0;
  for (const row of rows) {
    if (row.type === "post") organic += 1;
    else if (row.type === "product") out.push(organic);
  }
  return out;
}

describe("the flag-off guarantee", () => {
  it("returns the same rows when there are no signals", () => {
    const base = postRows(20);
    const out = injectProductSignalRows(base, []);
    expect(out).toHaveLength(base.length);
    base.forEach((row, index) => expect(out[index]).toBe(row));
  });

  it("returns the same rows when the cap is zero", () => {
    const base = postRows(20);
    const out = injectProductSignalRows(base, signals(2), { maxRows: 0 });
    base.forEach((row, index) => expect(out[index]).toBe(row));
  });

  it("does not mutate its input", () => {
    const base = postRows(20);
    injectProductSignalRows(base, signals(2));
    expect(base).toHaveLength(20);
    expect(base.every((row) => row.type === "post")).toBe(true);
  });
});

describe("the cadence", () => {
  it("places the first product after the lead-in post", () => {
    const out = injectProductSignalRows(postRows(30), signals(2));
    expect(organicBefore(out)[0]).toBe(PRODUCT_SIGNAL_LEAD_IN);
  });

  it("spaces the second product by the interval", () => {
    const out = injectProductSignalRows(postRows(30), signals(2));
    expect(organicBefore(out)).toEqual([
      PRODUCT_SIGNAL_LEAD_IN,
      PRODUCT_SIGNAL_LEAD_IN + PRODUCT_SIGNAL_INTERVAL
    ]);
  });

  it("never exceeds the cap however many signals it is given", () => {
    const out = injectProductSignalRows(postRows(200), signals(40));
    expect(productIndices(out)).toHaveLength(PRODUCT_SIGNAL_MAX_ROWS);
  });

  it("places no more products than it has signals", () => {
    const out = injectProductSignalRows(postRows(60), signals(1));
    expect(productIndices(out)).toHaveLength(1);
  });

  it("places nothing in a feed shorter than the lead-in", () => {
    const out = injectProductSignalRows(postRows(PRODUCT_SIGNAL_LEAD_IN - 1), signals(2));
    expect(productIndices(out)).toEqual([]);
  });

  it("consumes signals in order, from the first", () => {
    const out = injectProductSignalRows(postRows(60), signals(5));
    const placed = out.flatMap((row) => (row.type === "product" ? [row.signal.productId] : []));
    expect(placed).toEqual([1, 2]);
  });

  it("numbers slots from zero and keys rows by product and slot", () => {
    const out = injectProductSignalRows(postRows(60), signals(5));
    const rows = out.filter((row) => row.type === "product");
    expect(rows.map((row) => (row.type === "product" ? row.slot : -1))).toEqual([0, 1]);
    expect(rows[0].key).toBe(productSignalRowKey(1, 0));
    expect(rows[1].key).toBe(productSignalRowKey(2, 1));
  });

  it("clamps a zero interval rather than placing two products together", () => {
    const out = injectProductSignalRows(postRows(30), signals(2), { interval: 0 });
    const before = organicBefore(out);
    expect(before[1]).toBeGreaterThan(before[0]);
  });
});

describe("the three invariants", () => {
  it("never ends the feed on a product", () => {
    // Every feed length from just-too-short to comfortably long: a product as the
    // last row reads as the feed having stopped at an advertisement.
    for (let length = 1; length <= 40; length += 1) {
      const out = injectProductSignalRows(postRows(length), signals(2));
      expect(out[out.length - 1]?.type).not.toBe("product");
    }
  });

  it("never places two products next to each other", () => {
    for (let length = 1; length <= 60; length += 1) {
      const out = injectProductSignalRows(postRows(length), signals(6));
      for (let index = 1; index < out.length; index += 1) {
        expect(out[index].type === "product" && out[index - 1].type === "product").toBe(false);
      }
    }
  });

  it("never separates a post from the ad it earned", () => {
    const withAds = injectAds(posts(40), [ad(1), ad(2), ad(3)], { interval: 5, leadIn: 3 });
    const out = injectProductSignalRows(withAds, signals(2));
    // Each ad still directly follows the post above it.
    out.forEach((row, index) => {
      if (row.type !== "ad") return;
      expect(out[index - 1]?.type).toBe("post");
    });
  });

  it("does not count an ad or a carousel toward the organic cadence", () => {
    const withAds = injectAds(posts(40), [ad(1), ad(2), ad(3)], { interval: 5, leadIn: 3 });
    const out = injectProductSignalRows(withAds, signals(2));
    expect(organicBefore(out)).toEqual([
      PRODUCT_SIGNAL_LEAD_IN,
      PRODUCT_SIGNAL_LEAD_IN + PRODUCT_SIGNAL_INTERVAL
    ]);
  });
});

describe("the three injectors composed, as Home composes them", () => {
  /** HomeScreen's expression, verbatim. */
  function homeRows(
    postCount: number,
    ads: SponsoredAd[],
    modules: DiscoveryModule[],
    productSignals: ProductSignal[]
  ): CommerceFeedRow<Post>[] {
    return injectProductSignalRows(
      injectDiscoveryRows(injectAds(posts(postCount), ads, { interval: 5, leadIn: 3 }), modules, {}),
      productSignals
    );
  }

  const ads = [ad(1), ad(2), ad(3), ad(4), ad(5)];
  const modules = [reelsModule(), groupsModule()];

  it("puts the products where the documented cadence table says", () => {
    // ads at 3, 8, 13, 18, 23; discovery at 5, 12; products at 4 and 14.
    const out = homeRows(40, ads, modules, signals(2));
    expect(organicBefore(out)).toEqual([4, 14]);
  });

  it("never stacks two commercial or suggestion rows together", () => {
    const commercial = (row: CommerceFeedRow<Post>) => row.type !== "post";
    for (let length = 1; length <= 60; length += 1) {
      const out = homeRows(length, ads, modules, signals(2));
      for (let index = 1; index < out.length; index += 1) {
        // An ad belongs to the post above it, so an ad directly after a post is
        // the one adjacency that is allowed.
        if (out[index].type === "ad") continue;
        expect(commercial(out[index]) && commercial(out[index - 1])).toBe(false);
      }
    }
  });

  it("still returns exactly injectAds' rows when both new features are off", () => {
    const base = injectAds(posts(40), ads, { interval: 5, leadIn: 3 });
    const out = injectProductSignalRows(injectDiscoveryRows(base, [], {}), []);
    expect(out).toHaveLength(base.length);
    base.forEach((row, index) => expect(out[index]).toBe(row));
  });

  it("produces no duplicate row keys, which would break the list", () => {
    const out = homeRows(40, ads, modules, signals(2)) as (HomeRow<Post> | { key: string })[];
    const keys = out.map((row) => (row as { key?: string }).key).filter(Boolean);
    expect(new Set(keys).size).toBe(keys.length);
  });
});
