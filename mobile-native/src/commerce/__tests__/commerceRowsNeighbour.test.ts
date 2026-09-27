/**
 * A commerce row never lands next to a post the server said to keep away from.
 *
 * This is the half of the sensitive-content rule the serve endpoint cannot
 * enforce. `useFeedCommerce.ts` sends no context, and that is not an oversight:
 * the feed's commerce row is a sibling row inserted *between* posts rather than
 * attached to one, so the request that fetched the products genuinely does not
 * know which posts it will land between. The server answers "may commerce sit
 * beside this one?" per post on the feed response — `commerce_suitable` — and
 * this module is the only place that knows where the row finally goes.
 *
 * Why the answer comes from the server and is not computed here: the decision
 * reads the post's `moderation_status` and `risk_score`, neither of which is on
 * the wire, plus a tiered sensitive-content vocabulary shared with the ads
 * layer. A client-side re-derivation would be a second, weaker implementation
 * that disagrees with the first — which is the exact failure `postContext.ts`
 * already demonstrates for tags.
 */
import type { FeedRow } from "../../feed/injectAds";
import type { CommercePlacement } from "../../api/commerceDiscovery";
import {
  COMMERCE_INTERVAL,
  COMMERCE_LEAD_IN,
  injectCommerceRows,
  neighbourAllowsCommerce
} from "../commerceRows";

type Post = { id: number; commerce_suitable?: boolean };

/** `n` post rows, with `unsuitable` naming the 1-based posts marked false. */
function postRows(n: number, unsuitable: readonly number[] = []): FeedRow<Post>[] {
  return Array.from({ length: n }, (_, i) => ({
    type: "post" as const,
    key: `post:${i + 1}`,
    post: { id: i + 1, commerce_suitable: !unsuitable.includes(i + 1) }
  }));
}

/** The same rows with no flag at all — an older server, or an older payload. */
function unannotatedRows(n: number): FeedRow<Post>[] {
  return Array.from({ length: n }, (_, i) => ({
    type: "post" as const,
    key: `post:${i + 1}`,
    post: { id: i + 1 }
  }));
}

function placement(id: string): CommercePlacement {
  const listingId = Number(id.replace(/\D/g, "")) || 1;
  return {
    placementId: id,
    impressionToken: `tok-${id}`,
    surface: "feed",
    slot: 0,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason: "popular",
    rankingVersion: "commerce-discovery-v1",
    priceMinor: 4999,
    priceCurrency: "USD",
    product: {
      listingId,
      title: `Product ${listingId}`,
      priceLabel: "$49.99",
      coverImageUrl: "https://cdn.example/x.jpg",
      sellerUserId: listingId * 10,
      sellerStoreName: `Store ${listingId}`,
      category: "shoes",
      rating: 4.8,
      ratingCount: 120
    }
  };
}

function placements(n: number): CommercePlacement[] {
  return Array.from({ length: n }, (_, i) => placement(`p${i + 1}`));
}

/** For each commerce row, the ids of the posts immediately either side of it. */
function neighboursOf(rows: readonly { type: string }[]): { before: number; after: number }[] {
  const out: { before: number; after: number }[] = [];
  rows.forEach((row, index) => {
    if (row.type !== "commerce") return;
    const before = rows[index - 1] as { type: string; post?: Post } | undefined;
    const after = rows[index + 1] as { type: string; post?: Post } | undefined;
    out.push({ before: before?.post?.id ?? -1, after: after?.post?.id ?? -1 });
  });
  return out;
}

function commerceCount(rows: readonly { type: string }[]): number {
  return rows.filter((row) => row.type === "commerce").length;
}

/**
 * Where the strips land with nothing suppressed.
 *
 * Derived from the exported cadence rather than hardcoded, so this file keeps
 * testing the neighbour rule if the cadence is ever retuned instead of quietly
 * becoming a test of the old numbers.
 */
const FIRST = COMMERCE_LEAD_IN;
const SECOND = COMMERCE_LEAD_IN + COMMERCE_INTERVAL;

describe("the default placement, for comparison", () => {
  it("puts strips after the lead-in and one interval later", () => {
    const rows = injectCommerceRows(postRows(40), placements(8));

    expect(neighboursOf(rows)).toEqual([
      { before: FIRST, after: FIRST + 1 },
      { before: SECOND, after: SECOND + 1 }
    ]);
  });
});

describe("injectCommerceRows — an unsuitable neighbour", () => {
  it("does not put a strip under an unsuitable post", () => {
    const rows = injectCommerceRows(postRows(40, [FIRST]), placements(8));

    for (const { before } of neighboursOf(rows)) {
      expect(before).not.toBe(FIRST);
    }
  });

  it("does not put a strip on top of an unsuitable post either", () => {
    // A strip reads as belonging to the post above it, which is why the existing
    // adjacency invariant only looks up. But a product shelf directly on top of
    // a bereavement is the same harm seen a moment earlier, and which post a
    // reader associates the strip with is a convention, not a guarantee.
    const rows = injectCommerceRows(postRows(40, [FIRST + 1]), placements(8));

    for (const { after } of neighboursOf(rows)) {
      expect(after).not.toBe(FIRST + 1);
    }
  });

  it("keeps the products, offering them at the next eligible position", () => {
    // The slot is not spent. The post is what commerce is being kept away from,
    // not the viewer, so the page does not lose a strip — the same window is
    // offered where the cadence next allows. The cost is honest and worth
    // stating: because the cadence gates the retry, a post suppressed exactly on
    // a cadence position pushes that strip a full interval down the feed.
    const suppressed = injectCommerceRows(postRows(40, [FIRST]), placements(8));
    const normal = injectCommerceRows(postRows(40), placements(8));

    const firstStrip = suppressed.find((row) => row.type === "commerce");
    const normalFirst = normal.find((row) => row.type === "commerce");

    expect(firstStrip).toBeDefined();
    // Same products, later position — not a different, worse window.
    expect((firstStrip as { placements: CommercePlacement[] }).placements).toEqual(
      (normalFirst as { placements: CommercePlacement[] }).placements
    );
    expect(neighboursOf(suppressed)[0].before).toBe(SECOND);
  });

  it("emits no strip at all when every post is unsuitable", () => {
    const all = Array.from({ length: 40 }, (_, i) => i + 1);
    const rows = injectCommerceRows(postRows(40, all), placements(8));

    expect(commerceCount(rows)).toBe(0);
  });

  it("leaves the posts themselves untouched", () => {
    // The rule removes commerce, never content. A suppression that dropped the
    // post would turn a safety feature into censorship of the bereaved.
    const input = postRows(40, [FIRST, FIRST + 1]);
    const rows = injectCommerceRows(input, placements(8));

    expect(rows.filter((row) => row.type === "post")).toEqual(input);
  });

  it("still places strips elsewhere in the same feed", () => {
    // The check is per-position. One unsuitable post must not switch commerce
    // off for the whole page — that would make a single bereavement in a
    // follower's feed indistinguishable from the engine being broken.
    const rows = injectCommerceRows(postRows(60, [FIRST]), placements(8));

    expect(commerceCount(rows)).toBeGreaterThan(0);
  });
});

describe("injectCommerceRows — a post with no answer", () => {
  it("treats a missing flag as permission", () => {
    // Default-allow, deliberately. A native build reaching a deployment whose
    // `/api/pulse/feed` does not annotate yet would otherwise show no feed
    // commerce at all, and native releases ship on App Store review time while
    // the server ships in minutes — "client newer than server" is the normal
    // state for days at a stretch.
    //
    // The cost is that a server path which forgets to annotate loses the
    // protection silently, which is why that guarantee is pinned server-side in
    // `tests/commerce_discovery/test_feed_posts_are_annotated.py` rather than
    // being left to this function to notice.
    const rows = injectCommerceRows(unannotatedRows(40), placements(8));

    expect(commerceCount(rows)).toBe(2);
  });

  it("reads only an explicit false as a refusal", () => {
    expect(neighbourAllowsCommerce({ commerce_suitable: false })).toBe(false);
    expect(neighbourAllowsCommerce({ commerce_suitable: true })).toBe(true);
    expect(neighbourAllowsCommerce({})).toBe(true);
    expect(neighbourAllowsCommerce({ commerce_suitable: undefined })).toBe(true);
  });

  it("does not treat a falsy-but-not-false value as a refusal", () => {
    // `0`, `""` and `null` are what a lossy serializer produces, not what the
    // server sends. Reading them as refusals would let an encoding change
    // silently switch feed commerce off; the server test asserts a real boolean
    // so these can stay permissive here.
    expect(neighbourAllowsCommerce({ commerce_suitable: 0 })).toBe(true);
    expect(neighbourAllowsCommerce({ commerce_suitable: null })).toBe(true);
    expect(neighbourAllowsCommerce({ commerce_suitable: "false" })).toBe(true);
  });

  it("survives a post that is not an object", () => {
    // The post must render even when every commerce layer fails, which includes
    // this one throwing on a shape it did not expect.
    expect(neighbourAllowsCommerce(null)).toBe(true);
    expect(neighbourAllowsCommerce(undefined)).toBe(true);
    expect(neighbourAllowsCommerce("a post")).toBe(true);
    expect(neighbourAllowsCommerce(42)).toBe(true);
  });
});

describe("injectCommerceRows — the override", () => {
  it("lets a caller whose posts are a different shape supply the test", () => {
    const rows = injectCommerceRows(unannotatedRows(40), placements(8), {
      isNeighbourSuitable: (post) => (post as Post).id !== FIRST
    });

    for (const { before } of neighboursOf(rows)) {
      expect(before).not.toBe(FIRST);
    }
  });

  it("is consulted for both neighbours", () => {
    const seen: number[] = [];
    injectCommerceRows(postRows(40), placements(8), {
      isNeighbourSuitable: (post) => {
        seen.push((post as Post).id);
        return true;
      }
    });

    expect(seen).toContain(FIRST);
    expect(seen).toContain(FIRST + 1);
  });
});

describe("injectCommerceRows — still pure", () => {
  it("returns the same rows for the same arguments", () => {
    const build = () => injectCommerceRows(postRows(40, [FIRST]), placements(8));

    expect(build()).toEqual(build());
  });

  it("does not mutate the rows it was given", () => {
    const input = postRows(40, [FIRST]);
    const snapshot = JSON.parse(JSON.stringify(input));

    injectCommerceRows(input, placements(8));

    expect(input).toEqual(snapshot);
  });
});
