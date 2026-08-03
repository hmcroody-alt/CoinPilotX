/**
 * Tests for the Activity feed derivation layer — the model the Activity center
 * renders. Pinned outright:
 *
 * 1. DOMAIN → FILTER mapping is exhaustive and matches the documented design
 *    (payments folds into Orders, live folds into System).
 * 2. CLASSIFICATION reads type/category text, not guesswork.
 * 3. AGGREGATION collapses ONLY the social domain, on the same subject, inside
 *    the rolling window — orders/offers/system pass through untouched.
 * 4. DAY GROUPING lifts unread-since-last-visit rows into "New" and buckets the
 *    rest into Today / Yesterday / dated sections.
 * 5. SINGLE SOURCE OF TRUTH: offer inline actions read the Marketplace offer
 *    state machine — a live offer shows View offer + Message, a terminal
 *    (expired/answered) offer drops both and the row copy already carries it.
 * 6. DELETED SUBJECT: an expired/deleted subject rewrites copy and drops actions.
 * 7. MOCK-DATA gap ledger length is asserted, so changing it is reviewed.
 */

import {
  ACTIVITY_MOCK_DATA_GAPS,
  ACTIVITY_MOCK_DATA_GAP_COUNT,
  DOMAIN_TO_FILTER,
  aggregateFeed,
  classifyDomain,
  domainSuffix,
  filterForDomain,
  filterUnreadCounts,
  groupFeedByDay,
  inlineActionsFor,
  offerActionsLive,
  relativeShort,
  rowMatchesFilter,
  toFeedNotification
} from "../activityFeed";
import type { PulseNotification } from "../notifications";
import type { MarketplaceOffer } from "../marketplaceOffers";

const HOUR = 60 * 60 * 1000;
const DAY = 24 * HOUR;
const NOW = 1_700_000_000_000;

function notif(overrides: Partial<PulseNotification> = {}): PulseNotification {
  return {
    id: 1,
    type: "system",
    created_at: new Date(NOW).toISOString(),
    ...overrides
  };
}

function offer(overrides: Partial<MarketplaceOffer> = {}): MarketplaceOffer {
  return {
    id: "off-1",
    listingId: "lst-1",
    amountMinor: 6000,
    currency: "USD",
    listPriceMinor: 8000,
    direction: "buyer_to_seller",
    state: "open",
    createdAt: NOW,
    updatedAt: NOW,
    buyerName: "Devon",
    itemTitle: "Monstera",
    ...overrides
  };
}

describe("domain classification + filter mapping", () => {
  it("maps every domain to exactly one filter chip (payments→orders, live→system)", () => {
    expect(DOMAIN_TO_FILTER.social).toBe("social");
    expect(DOMAIN_TO_FILTER.marketplace).toBe("marketplace");
    expect(DOMAIN_TO_FILTER.orders).toBe("orders");
    expect(DOMAIN_TO_FILTER.payments).toBe("orders");
    expect(DOMAIN_TO_FILTER.live).toBe("system");
    expect(DOMAIN_TO_FILTER.system).toBe("system");
  });

  it("classifies from the type/category text", () => {
    expect(classifyDomain({ type: "livestream_start", category: undefined })).toBe("live");
    expect(classifyDomain({ type: "offer_received", category: "marketplace" })).toBe("marketplace");
    expect(classifyDomain({ type: "payout_sent", category: undefined })).toBe("payments");
    expect(classifyDomain({ type: "order_shipped", category: undefined })).toBe("orders");
    expect(classifyDomain({ type: "post_like", category: "social" })).toBe("social");
    expect(classifyDomain({ type: "verification_passed", category: undefined })).toBe("system");
    expect(classifyDomain({ type: "something_unknown", category: undefined })).toBe("system");
  });

  it("routes classified domains through to the right chip", () => {
    expect(filterForDomain("payments")).toBe("orders");
    expect(filterForDomain("live")).toBe("system");
    expect(domainSuffix("payments")).toBe("Payments");
    expect(domainSuffix("live")).toBe("Live");
  });
});

describe("normalization", () => {
  it("reads unread from read/read_at and prefers the plain-language body", () => {
    const unread = toFeedNotification(notif({ type: "order_shipped", body: "Your order shipped" }), NOW);
    expect(unread.unread).toBe(true);
    expect(unread.sentence).toBe("Your order shipped");
    expect(unread.domain).toBe("orders");

    const read = toFeedNotification(
      notif({ type: "order_shipped", body: "Your order shipped", read: true }),
      NOW
    );
    expect(read.unread).toBe(false);

    const readAt = toFeedNotification(
      notif({ type: "order_shipped", read_at: new Date(NOW).toISOString() }),
      NOW
    );
    expect(readAt.unread).toBe(false);
  });

  it("rewrites copy and points to a graceful landing when a marketplace subject is gone", () => {
    const row = toFeedNotification(
      notif({ type: "offer_received", metadata: { subject_deleted: true } }),
      NOW
    );
    expect(row.subjectGone).toBe(true);
    expect(row.sentence).toBe("This offer has expired");
    expect(row.target).toBe("/pulse/marketplace");
  });
});

describe("aggregation — client collapse rule", () => {
  it("collapses same-subject social actions inside the window into one row", () => {
    const base = { type: "post_like", metadata: { actor_name: "Maya", subject_id: "reel-9" } };
    const rows = [
      toFeedNotification(notif({ id: 1, ...base, body: "Maya liked your reel", created_at: new Date(NOW).toISOString() }), NOW),
      toFeedNotification(notif({ id: 2, ...base, body: "Ari liked your reel", created_at: new Date(NOW - 1 * HOUR).toISOString() }), NOW),
      toFeedNotification(notif({ id: 3, ...base, body: "Sam liked your reel", created_at: new Date(NOW - 2 * HOUR).toISOString() }), NOW)
    ];
    const out = aggregateFeed(rows);
    expect(out.length).toBe(1);
    expect(out[0].collapsedCount).toBe(2);
    expect(out[0].sentence).toBe("Maya and 2 others liked your reel");
  });

  it("does NOT collapse orders / offers / system — only social", () => {
    const social = toFeedNotification(notif({ id: 1, type: "post_like", metadata: { actor_name: "Maya", subject_id: "reel-9" } }), NOW);
    const order = toFeedNotification(notif({ id: 2, type: "order_shipped", metadata: { order_id: "o-1" } }), NOW);
    const order2 = toFeedNotification(notif({ id: 3, type: "order_shipped", metadata: { order_id: "o-1" } }), NOW);
    const out = aggregateFeed([social, order, order2]);
    // both orders survive; social passes through as its own row
    expect(out.filter((r) => r.domain === "orders").length).toBe(2);
    expect(out.filter((r) => r.domain === "social").length).toBe(1);
  });

  it("does not collapse social actions outside the rolling window", () => {
    const base = { type: "post_like", metadata: { actor_name: "Maya", subject_id: "reel-9" } };
    const rows = [
      toFeedNotification(notif({ id: 1, ...base, created_at: new Date(NOW).toISOString() }), NOW),
      toFeedNotification(notif({ id: 2, ...base, created_at: new Date(NOW - 8 * HOUR).toISOString() }), NOW)
    ];
    const out = aggregateFeed(rows, { windowMs: 6 * HOUR });
    expect(out.length).toBe(2);
  });
});

describe("day grouping", () => {
  it("lifts unread-since-last-visit rows into New, buckets the rest", () => {
    const rows = [
      toFeedNotification(notif({ id: 1, type: "order_shipped", created_at: new Date(NOW).toISOString() }), NOW),
      toFeedNotification(notif({ id: 2, type: "order_shipped", read: true, created_at: new Date(NOW - 2 * HOUR).toISOString() }), NOW),
      toFeedNotification(notif({ id: 3, type: "order_shipped", read: true, created_at: new Date(NOW - 1 * DAY).toISOString() }), NOW)
    ];
    const sections = groupFeedByDay(rows, NOW, { lastVisitMs: NOW - 1 * HOUR });
    expect(sections[0].title).toBe("New");
    expect(sections[0].items.map((i) => i.id)).toEqual([1]);
    const titles = sections.map((s) => s.title);
    expect(titles).toContain("Today");
    expect(titles).toContain("Yesterday");
  });
});

describe("inline actions read the offer state machine", () => {
  it("live rows get a single Open live", () => {
    const row = toFeedNotification(notif({ type: "live_start", deep_link: "/pulse/live/7" }), NOW);
    const actions = inlineActionsFor(row, NOW);
    expect(actions.map((a) => a.key)).toEqual(["open_live"]);
  });

  it("an open offer shows View offer + Message", () => {
    const row = toFeedNotification(notif({ type: "offer_received", metadata: { actor_name: "Devon", offer_id: "off-1" } }), NOW);
    const actions = inlineActionsFor(row, NOW, { offer: offer({ state: "open" }), amountLabel: "$60" });
    expect(actions.map((a) => a.key)).toEqual(["view_offer", "message"]);
    expect(offerActionsLive(offer({ state: "open" }), NOW)).toBe(true);
  });

  it("a terminal (accepted/expired) offer drops both buttons", () => {
    const row = toFeedNotification(notif({ type: "offer_received", metadata: { actor_name: "Devon", offer_id: "off-1" } }), NOW);
    expect(inlineActionsFor(row, NOW, { offer: offer({ state: "accepted" }), amountLabel: "$60" })).toEqual([]);
    // an open offer past its TTL resolves to expired via the shared state machine
    const stale = offer({ state: "open", createdAt: NOW - 100 * HOUR });
    expect(offerActionsLive(stale, NOW)).toBe(false);
    expect(inlineActionsFor(row, NOW, { offer: stale, amountLabel: "$60" })).toEqual([]);
  });

  it("a gone subject drops all actions", () => {
    const row = toFeedNotification(notif({ type: "offer_received", metadata: { subject_deleted: true } }), NOW);
    expect(inlineActionsFor(row, NOW)).toEqual([]);
  });
});

describe("filter counts + matching", () => {
  it("counts unread per chip and once for all", () => {
    const rows = [
      toFeedNotification(notif({ id: 1, type: "post_like", metadata: { actor_name: "Maya" } }), NOW),
      toFeedNotification(notif({ id: 2, type: "order_shipped" }), NOW),
      toFeedNotification(notif({ id: 3, type: "payout_sent" }), NOW),
      toFeedNotification(notif({ id: 4, type: "order_shipped", read: true }), NOW)
    ];
    const counts = filterUnreadCounts(rows);
    expect(counts.all).toBe(3); // 3 unread
    expect(counts.social).toBe(1);
    expect(counts.orders).toBe(2); // order + payout both fold into Orders
    expect(rowMatchesFilter(rows[0], "all")).toBe(true);
    expect(rowMatchesFilter(rows[0], "orders")).toBe(false);
  });
});

describe("relative time + gap ledger", () => {
  it("formats compact relative time", () => {
    expect(relativeShort(NOW, NOW)).toBe("now");
    expect(relativeShort(NOW - 5 * 60 * 1000, NOW)).toBe("5m ago");
    expect(relativeShort(NOW - 3 * HOUR, NOW)).toBe("3h ago");
    expect(relativeShort(NOW - 2 * DAY, NOW)).toBe("2d ago");
  });

  it("locks the MOCK-DATA gap ledger length", () => {
    expect(ACTIVITY_MOCK_DATA_GAPS.length).toBe(ACTIVITY_MOCK_DATA_GAP_COUNT);
  });
});
