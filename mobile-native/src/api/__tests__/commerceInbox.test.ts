/**
 * Tests for the commerce-inbox derivation layer — the model the Messages screen
 * renders. The things worth pinning outright:
 *
 * 1. CONTEXT-CHIP CONTRACT. Every CommerceLink resolves to one line, one a11y
 *    label and a deep-link that targets the OBJECT (MarketplaceDetail /
 *    BuyerOrderDetail), never the thread. The offer chip reads the SAME expiry
 *    the Marketplace mission owns — there is no second clock here.
 * 2. BATCHED, CACHED, HONEST RESOLUTION. `resolveContextChips` never fabricates a
 *    chip with the mock flag off, resolves real associations always, caches a
 *    resolved id, and returns only ids that resolved to a link.
 * 3. FILTER COUNTS + MATCHING. Spam/blocked/archived are excluded from the base
 *    filters; Offers/Orders read the resolved chip kind; counts and matching
 *    agree.
 * 4. EXPIRY BANNER IS FLAG-GATED OFF. With `MARKETPLACE_OFFERS_ENABLED` false the
 *    banner is dark regardless of how urgent the offers are — the honest default.
 * 5. MOCK-DATA gap count is asserted, so closing/adding a gap is a reviewed change.
 */

import {
  CommerceLink,
  INBOX_MOCK_DATA_GAP_COUNT,
  INBOX_MOCK_DATA_GAPS,
  InboxRow,
  __resetChipCache,
  avatarGradientFor,
  buildContextChip,
  deriveExpiryBanner,
  deriveReplyStat,
  filterCounts,
  formatMinor,
  formatRemaining,
  inboxFilterRail,
  resolveContextChips,
  rowMatchesFilter,
  toInboxRow
} from "../commerceInbox";
import { MarketplaceOffer } from "../marketplaceOffers";

const HOUR = 60 * 60 * 1000;
const NOW = 1_700_000_000_000;

function offer(overrides: Partial<MarketplaceOffer> = {}): MarketplaceOffer {
  return {
    id: "offer-1",
    listingId: "555",
    amountMinor: 9500,
    currency: "USD",
    listPriceMinor: 12000,
    direction: "buyer_to_seller",
    state: "open",
    createdAt: NOW - 68 * HOUR,
    updatedAt: NOW - 68 * HOUR,
    buyerName: "Dana",
    itemTitle: "Aeron chair",
    ...overrides
  };
}

/** Minimal conversation shape the row normalizer reads. */
function conv(overrides: Record<string, unknown> = {}): any {
  return {
    id: 1,
    title: "Dana",
    latest_message: "Is this still available?",
    unread_count: 0,
    ...overrides
  };
}

beforeEach(() => {
  __resetChipCache();
  delete process.env.EXPO_PUBLIC_MESSAGES_MOCK_CHIPS;
  delete process.env.EXPO_PUBLIC_MESSAGES_REPLY_BADGE;
  delete process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT;
});

describe("context-chip contract", () => {
  it("builds an open-offer chip that reads Marketplace expiry and deep-links to the listing", () => {
    const chip = buildContextChip({ kind: "offer", offer: offer() }, NOW);
    expect(chip.kind).toBe("offer");
    // createdAt was NOW-68h and TTL is 72h → 4h remaining.
    expect(chip.line).toMatch(/^Offer \$95 · Aeron chair · expires 4h$/);
    expect(chip.a11yLabel).toContain("expires in 4h");
    expect(chip.target).toEqual({ screen: "MarketplaceDetail", params: { listingId: 555 } });
  });

  it("renders an accepted offer as a completed chip, not an open offer", () => {
    const chip = buildContextChip({ kind: "offer", offer: offer({ state: "accepted" }) }, NOW);
    expect(chip.kind).toBe("completed");
    expect(chip.line).toContain("Offer accepted");
  });

  it("routes an order chip to the buyer order detail", () => {
    const chip = buildContextChip({ kind: "order", orderId: 42, statusLine: "in transit" }, NOW);
    expect(chip.line).toBe("Order #42 · in transit");
    expect(chip.target).toEqual({ screen: "BuyerOrderDetail", params: { orderId: 42 } });
  });

  it("marks a sold-listing question so the seller does not send a dead reply", () => {
    const chip = buildContextChip(
      { kind: "question", listingId: 7, listing: "Desk lamp", priceMinor: 4500, sold: true },
      NOW
    );
    expect(chip.line).toBe("Sold · Desk lamp");
    expect(chip.target).toEqual({ screen: "MarketplaceDetail", params: { listingId: 7 } });
  });

  it("gives no deep-link target when the object has no reachable screen", () => {
    const chip = buildContextChip({ kind: "pickup", day: "Sat", time: "2pm", item: "Bike", amountMinor: 9000 }, NOW);
    expect(chip.target).toBeNull();
    expect(chip.line).toContain("Pickup Sat 2pm");
  });
});

describe("batched, cached, honest resolution", () => {
  it("resolves nothing when there is no real association and the mock flag is off", async () => {
    const map = await resolveContextChips([conv({ id: 1 }), conv({ id: 2 })], NOW);
    expect(map.size).toBe(0);
  });

  it("always resolves a real association carried on the conversation", async () => {
    const link: CommerceLink = { kind: "order", orderId: 99, statusLine: "delivered Tue" };
    const map = await resolveContextChips([conv({ id: 5, commerce_link: link })], NOW);
    expect(map.get(5)).toEqual(link);
  });

  it("produces deterministic mock links only when the flag is on", async () => {
    process.env.EXPO_PUBLIC_MESSAGES_MOCK_CHIPS = "1";
    const convs = [conv({ id: 1 }), conv({ id: 2 }), conv({ id: 3 }), conv({ id: 4 }), conv({ id: 5 })];
    const a = await resolveContextChips(convs, NOW);
    __resetChipCache();
    const b = await resolveContextChips(convs, NOW);
    expect([...a.keys()].sort()).toEqual([...b.keys()].sort());
    expect(a.size).toBeGreaterThan(0);
  });

  it("caches a resolved id: a real link stays even after the mock flag flips", async () => {
    const link: CommerceLink = { kind: "order", orderId: 1, statusLine: "in transit" };
    await resolveContextChips([conv({ id: 8, commerce_link: link })], NOW);
    const again = await resolveContextChips([conv({ id: 8 })], NOW);
    expect(again.get(8)).toEqual(link);
  });
});

describe("filters", () => {
  const rows: InboxRow[] = [
    row({ id: 1, unreadCount: 2 }),
    row({ id: 2, chip: chipOf({ kind: "offer", offer: offer() }) }),
    row({ id: 3, chip: chipOf({ kind: "order", orderId: 3, statusLine: "in transit" }) }),
    row({ id: 4, starred: true }),
    row({ id: 5, archived: true }),
    row({ id: 6, spam: true }),
    row({ id: 7, blocked: true })
  ];

  it("counts each filter honestly, excluding spam/blocked/archived from the base", () => {
    const counts = filterCounts(rows);
    expect(counts.all).toBe(4); // 1,2,3,4
    expect(counts.unread).toBe(1);
    expect(counts.offers).toBe(1);
    expect(counts.orders).toBe(1);
    expect(counts.starred).toBe(1);
    expect(counts.archived).toBe(1);
  });

  it("matching agrees with counting for every filter", () => {
    expect(rows.filter((r) => rowMatchesFilter(r, "all")).map((r) => r.id)).toEqual([1, 2, 3, 4]);
    expect(rows.filter((r) => rowMatchesFilter(r, "unread")).map((r) => r.id)).toEqual([1]);
    expect(rows.filter((r) => rowMatchesFilter(r, "offers")).map((r) => r.id)).toEqual([2]);
    expect(rows.filter((r) => rowMatchesFilter(r, "orders")).map((r) => r.id)).toEqual([3]);
    expect(rows.filter((r) => rowMatchesFilter(r, "archived")).map((r) => r.id)).toEqual([5]);
  });
});

/**
 * Tier 0.4's rail. The three domain filters read `row.domain` — the discriminator
 * the data layer owns — rather than re-deriving anything from a title or a chip.
 */
describe("Tier 0.4 filter rail", () => {
  const rows: InboxRow[] = [
    row({ id: 1, domain: "MARKETPLACE" }),
    row({ id: 2, domain: "STORE_SUPPORT" }),
    row({ id: 3, domain: "DISPUTE" }),
    row({ id: 4, domain: "EVENT" }),
    row({ id: 5, domain: "MARKETPLACE", chip: chipOf({ kind: "order", orderId: 5, statusLine: "in transit" }) })
  ];

  it("splits the inbox by domain rather than by chip kind", () => {
    expect(rows.filter((r) => rowMatchesFilter(r, "marketplace")).map((r) => r.id)).toEqual([1, 5]);
    expect(rows.filter((r) => rowMatchesFilter(r, "store_support")).map((r) => r.id)).toEqual([2]);
    expect(rows.filter((r) => rowMatchesFilter(r, "disputes")).map((r) => r.id)).toEqual([3]);
  });

  it("still identifies an order thread by the money object it points at", () => {
    expect(rows.filter((r) => rowMatchesFilter(r, "orders")).map((r) => r.id)).toEqual([5]);
  });

  it("keeps an EVENT thread reachable under All even with no chip of its own", () => {
    expect(rows.filter((r) => rowMatchesFilter(r, "all")).map((r) => r.id)).toEqual([1, 2, 3, 4, 5]);
  });

  it("shows Returns as honestly empty because nothing can create a return yet", () => {
    expect(rows.filter((r) => rowMatchesFilter(r, "returns"))).toEqual([]);
    expect(filterCounts(rows).returns).toBe(0);
  });

  it("counts the domain filters the same way it matches them", () => {
    const counts = filterCounts(rows);
    expect(counts.marketplace).toBe(2);
    expect(counts.store_support).toBe(1);
    expect(counts.disputes).toBe(1);
    expect(counts.all).toBe(5);
  });

  it("swaps the visible rail on the split flag and never mixes the two", () => {
    delete process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT;
    expect(inboxFilterRail()).toEqual(["all", "unread", "offers", "orders", "starred", "archived"]);
    process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT = "1";
    expect(inboxFilterRail()).toEqual([
      "all",
      "unread",
      "marketplace",
      "store_support",
      "orders",
      "returns",
      "disputes"
    ]);
    delete process.env.EXPO_PUBLIC_MESSAGES_COMMERCE_SPLIT;
  });
});

describe("expiry banner is flag-gated off", () => {
  it("returns null with no offers backend even when an offer is minutes from expiry", () => {
    const soon = offer({ createdAt: NOW - 71.5 * HOUR }); // ~30m left
    const banner = deriveExpiryBanner([soon], () => 1, NOW);
    // MARKETPLACE_OFFERS_ENABLED is false → banner dark regardless of urgency.
    expect(banner).toBeNull();
  });
});

describe("reply stat + formatting + row model", () => {
  it("hides the reply stat when there is no history and no incentive without a badge rule", () => {
    expect(deriveReplyStat()).toEqual({ avgLabel: undefined, showIncentive: false, incentiveThreshold: undefined });
    const withValue = deriveReplyStat({ avg_reply_label: "2h", threshold: "1h" });
    expect(withValue.avgLabel).toBe("2h");
    expect(withValue.showIncentive).toBe(false); // no badge rule flag
  });

  it("formats money and remaining time for display only", () => {
    expect(formatMinor(9500)).toBe("$95");
    expect(formatMinor(9550)).toBe("$95.50");
    expect(formatRemaining(0)).toBe("now");
    expect(formatRemaining(90 * 60 * 1000)).toBe("1h");
  });

  it("normalizes a conversation into a row and derives a stable avatar gradient", () => {
    const r = toInboxRow(conv({ id: 10, unread_count: 3, last_from_me: true }));
    expect(r.id).toBe(10);
    expect(r.unreadCount).toBe(3);
    expect(r.ownLast).toBe(true);
    expect(avatarGradientFor("abc")).toBe(avatarGradientFor("abc")); // deterministic
  });

  it("locks the MOCK-DATA gap ledger length", () => {
    expect(INBOX_MOCK_DATA_GAPS.length).toBe(INBOX_MOCK_DATA_GAP_COUNT);
  });
});

/* ------------------------------------------------------------------ *
 * Local row/chip builders
 * ------------------------------------------------------------------ */

function chipOf(link: CommerceLink) {
  return buildContextChip(link, NOW);
}

function row(overrides: Partial<InboxRow> & { id: number }): InboxRow {
  return {
    domain: "MARKETPLACE",
    title: "Buyer",
    colorKey: String(overrides.id),
    snippet: "hi",
    ownLast: false,
    unreadCount: 0,
    typing: false,
    starred: false,
    archived: false,
    spam: false,
    blocked: false,
    ...overrides
  };
}
