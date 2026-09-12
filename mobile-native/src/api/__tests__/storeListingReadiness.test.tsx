/**
 * The readiness verdict on the CLIENT side of the wire.
 *
 * `tests/marketplace/test_seller_listing_readiness_route.py` proves the server
 * sends a verdict, computed from the database row so the nullable `quantity`
 * survives. This file is the other half of that same journey, and it exists
 * because the server getting it right was never the hard part — the app threw
 * the answer away twice over:
 *
 *   * `normalizeMarketplaceListing` ran `Number(item.quantity || 0)`, which is
 *     GAP 22. That is the ONE hop every seller surface shares, so a null the
 *     server took care to preserve became a hard zero before any screen could
 *     read it, and "nobody counted this" was filed under "sold out";
 *   * the seller row rendered a blank price as nothing at all (GAP 23), so the
 *     one listing that could not be bought looked like the ones that could.
 *
 * Neither is visible from the server side, and neither is visible from a
 * component test with a hand-built fixture — both need a payload shaped like the
 * real one carried through the real normalizer, which is what these do.
 *
 * The rule underneath all of it: ABSENCE IS NOT A CLEAN BILL OF HEALTH. A
 * missing quantity is not zero, a missing price is not free, and a missing
 * verdict is not "nothing wrong". Each is asserted below, because each was got
 * wrong at least once.
 */

import { render } from "@testing-library/react-native";
import {
  normalizeMarketplaceListing,
  normalizeMarketplaceListings,
  type ListingReadiness,
  type MarketplaceListing
} from "../marketplace";
import { deriveAttention, deriveRows, deriveTabs, listingHealth } from "../storeDashboard";
import {
  listingPriceCopy,
  listingRemainingCopy,
  listingStatusCopy,
  StoreListingRow
} from "../../components/store/StoreListingRow";

/** A listing as the seller route actually sends it, verdict included. */
function payload(over: Partial<MarketplaceListing> = {}): MarketplaceListing {
  return {
    id: 1,
    listing_id: 1,
    title: "Brass desk lamp",
    price_label: "$24.00",
    currency: "USD",
    quantity: 40,
    status: "active",
    approval_status: "approved",
    listing_type: "physical",
    product_type: "physical",
    readiness: { publishable: true, checkout_ready: true, blockers: [], warnings: [] },
    ...over
  } as MarketplaceListing;
}

function verdict(over: Partial<ListingReadiness> = {}): ListingReadiness {
  return { publishable: true, checkout_ready: true, blockers: [], warnings: [], ...over };
}

/* ------------------------------------------------------------------ *
 * GAP 22 — the null has to survive the normalizer
 * ------------------------------------------------------------------ */

describe("normalizeMarketplaceListing", () => {
  it("preserves an untracked quantity as null rather than coercing it to zero", () => {
    // The regression itself. `Number(null || 0)` is 0, and this was the line
    // that ran it on every seller surface at once.
    expect(normalizeMarketplaceListing(payload({ quantity: null })).quantity).toBeNull();
    expect(normalizeMarketplaceListing(payload({ quantity: undefined })).quantity).toBeNull();
  });

  it("keeps a real zero as a real zero", () => {
    // The other direction, and the reason the fix is not `|| null`. A seller who
    // genuinely has none left must not be told nobody counted.
    expect(normalizeMarketplaceListing(payload({ quantity: 0 })).quantity).toBe(0);
  });

  it("does not turn an unparseable quantity into a count", () => {
    const listing = normalizeMarketplaceListing(
      payload({ quantity: "not a number" as unknown as number })
    );
    expect(listing.quantity).toBeNull();
    expect(Number.isNaN(listing.quantity as number)).toBe(false);
  });

  it("treats an empty string as untracked, not as zero", () => {
    // `Number("")` is 0, so this one needs catching before coercion too.
    expect(normalizeMarketplaceListing(payload({ quantity: "" as unknown as number })).quantity)
      .toBeNull();
  });

  it("carries the server's verdict through untouched", () => {
    // The normalizer rebuilds most fields by hand. If it ever stops spreading
    // the source object, `readiness` is exactly the kind of field that would
    // vanish silently -- every screen would fall back to deriving, and the
    // fallback is what this whole change retires.
    const readiness = verdict({ checkout_ready: false, warnings: ["UNKNOWN_INVENTORY"] });
    expect(normalizeMarketplaceListing(payload({ readiness })).readiness).toEqual(readiness);
  });

  it("survives the list normalizer too, which is what the screens call", () => {
    const [listing] = normalizeMarketplaceListings([
      payload({ quantity: null, readiness: verdict({ warnings: ["UNKNOWN_INVENTORY"] }) })
    ]);
    expect(listing.quantity).toBeNull();
    expect(listing.readiness?.warnings).toEqual(["UNKNOWN_INVENTORY"]);
  });
});

/* ------------------------------------------------------------------ *
 * End to end: payload -> normalizer -> rows
 * ------------------------------------------------------------------ */

describe("a seller payload carried all the way to a row", () => {
  const rowsFor = (listings: MarketplaceListing[]) =>
    deriveRows(
      {
        listings: normalizeMarketplaceListings(listings),
        orders: [],
        cached_at: ""
      } as never,
      new Date(2026, 6, 15)
    );

  it("keeps unknown and sold-out as different answers, through every hop", () => {
    // The end-to-end statement of GAP 22. Both of these used to arrive as
    // `out_of_stock` with a red LED, and the seller of the first one had nothing
    // to restock.
    const [unknown, empty] = rowsFor([
      payload({ id: 1, listing_id: 1, quantity: null, readiness: verdict({ warnings: ["UNKNOWN_INVENTORY"] }) }),
      payload({ id: 2, listing_id: 2, quantity: 0, readiness: verdict({ warnings: ["OUT_OF_STOCK"] }) })
    ]);
    expect(unknown.health).toBe("unknown_stock");
    expect(empty.health).toBe("out_of_stock");
    expect(unknown.health).not.toBe(empty.health);
    expect(unknown.quantity).toBeNull();
    expect(empty.quantity).toBe(0);
  });

  it("files a listing nobody can buy under Out rather than Active", () => {
    // Checkout refuses an unknown quantity, so calling it Active would be the
    // false all-clear again -- one layer further up than last time.
    const rows = rowsFor([
      payload({ quantity: null, readiness: verdict({ checkout_ready: false, warnings: ["UNKNOWN_INVENTORY"] }) })
    ]);
    const tabs = deriveTabs(rows);
    expect(tabs.find((tab) => tab.key === "out")?.count).toBe(1);
    expect(tabs.find((tab) => tab.key === "active")?.count).toBe(0);
  });

  it("tells the seller to count the stock, not to reorder it", () => {
    const rows = rowsFor([
      payload({ quantity: null, readiness: verdict({ warnings: ["UNKNOWN_INVENTORY"] }) })
    ]);
    const attention = deriveAttention(rows);
    expect(attention?.kind).toBe("unknown_stock");
    // A seller sent to "restock" an item whose shelf is full has been sent to
    // solve a problem they do not have.
    expect(attention?.kind).not.toBe("out_of_stock");
  });

  it("ranks a genuine sell-out above an uncounted one", () => {
    const rows = rowsFor([
      payload({ id: 1, listing_id: 1, quantity: null, readiness: verdict({ warnings: ["UNKNOWN_INVENTORY"] }) }),
      payload({ id: 2, listing_id: 2, quantity: 0, readiness: verdict({ warnings: ["OUT_OF_STOCK"] }) })
    ]);
    expect(deriveAttention(rows)?.kind).toBe("out_of_stock");
  });

  it("carries the verdict onto the row so the row can name the gap", () => {
    const readiness = verdict({ publishable: false, checkout_ready: false, blockers: ["MISSING_PRICE"] });
    expect(rowsFor([payload({ price_label: "", readiness })])[0].readiness).toEqual(readiness);
  });

  it("leaves the verdict null when the payload had none, rather than inventing one", () => {
    const [row] = rowsFor([payload({ readiness: undefined })]);
    expect(row.readiness).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * §12 — missing is not free
 * ------------------------------------------------------------------ */

describe("the price line", () => {
  it("names a missing price instead of rendering silence", () => {
    // GAP 23. The blank rendered as nothing at all, which is safe from "Free"
    // but tells the seller nothing and is indistinguishable from a row that has
    // no price element.
    const copy = listingPriceCopy("", verdict({ publishable: false, blockers: ["MISSING_PRICE"] }));
    expect(copy?.text).toBe("Price required");
    expect(copy?.required).toBe(true);
  });

  it("never says Free or $0.00 about an unpriced listing", () => {
    // §12, stated as the thing that must not appear rather than as the thing
    // that should. A price of zero is a claim; no price is the absence of one.
    const copy = listingPriceCopy("", verdict({ publishable: false, blockers: ["MISSING_PRICE"] }));
    expect(copy?.text).not.toMatch(/free/i);
    expect(copy?.text).not.toMatch(/0\.00/);
    expect(copy?.text).not.toMatch(/\$0/);
  });

  it("renders a real price as a price", () => {
    expect(listingPriceCopy("$24.00", verdict())).toEqual({ text: "$24.00", required: false });
  });

  it("stays silent about a blank price when no verdict said it was missing", () => {
    // Without the server's say-so the row cannot tell an unpriced listing from a
    // payload that simply omitted the field, and "Price required" over a priced
    // listing would be this module's own lie.
    expect(listingPriceCopy("", null)).toBeNull();
    expect(listingPriceCopy("", verdict())).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * §7 — state the exact remaining work
 * ------------------------------------------------------------------ */

describe("the remaining-work line", () => {
  it("states the count and the actual tasks", () => {
    expect(
      listingRemainingCopy(verdict({ publishable: false, blockers: ["MISSING_PRICE", "NO_VALID_MEDIA"] }))
    ).toBe("2 things left · Add price + Add photo");
  });

  it("agrees with itself about one", () => {
    expect(listingRemainingCopy(verdict({ publishable: false, blockers: ["MISSING_TITLE"] })))
      .toBe("1 thing left · Add title");
  });

  it("counts a code it cannot phrase rather than pretending it is not there", () => {
    // Shrinking the number to the subset this build understands would quietly
    // tell the seller there is less to do than there is.
    const copy = listingRemainingCopy(
      verdict({ publishable: false, blockers: ["MISSING_PRICE", "SOME_FUTURE_CODE"] })
    );
    expect(copy).toContain("2 things left");
    expect(copy).toContain("Add price");
    expect(copy).not.toContain("SOME_FUTURE_CODE");
  });

  it("says nothing when there is nothing left, and nothing when it was not told", () => {
    expect(listingRemainingCopy(verdict())).toBeNull();
    // Not the same situation, deliberately the same result: an old cached
    // snapshot has no news, which is not the same as good news, and the row
    // must not claim completeness on the strength of it.
    expect(listingRemainingCopy(null)).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * What the seller actually reads
 * ------------------------------------------------------------------ */

describe("the rendered row", () => {
  const rowData = (over: Record<string, unknown> = {}) => ({
    id: 1,
    title: "Brass desk lamp",
    thumbnailUrl: null,
    priceLabel: "",
    currency: "USD",
    quantity: null,
    health: "unknown_stock" as const,
    readiness: verdict({ publishable: false, blockers: ["MISSING_PRICE"], warnings: ["UNKNOWN_INVENTORY"] }),
    unitsSold7d: 0,
    rating: null,
    reviewCount: null,
    ...over
  });

  function renderRow(over: Record<string, unknown> = {}) {
    return render(
      <StoreListingRow
        row={rowData(over) as never}
        priceText=""
        soldText={null}
        onPress={() => undefined}
        onEdit={() => undefined}
        reducedMotion
      />
    );
  }

  it("shows the seller the price gap and the work left", () => {
    const { getByText } = renderRow();
    expect(getByText("Price required")).toBeTruthy();
    expect(getByText("1 thing left · Add price")).toBeTruthy();
  });

  it("does not tell a seller with a full shelf that they are out of stock", () => {
    const { getByText, queryByText } = renderRow();
    expect(getByText("No stock count — buyers can't order")).toBeTruthy();
    expect(queryByText("Out of stock — hidden")).toBeNull();
  });

  it("announces the gaps to a screen reader too", () => {
    // The blank price left nothing to announce. A seller using VoiceOver got
    // less information than a sighted one, about the listing that needed the
    // most.
    const { getByLabelText } = renderRow();
    expect(getByLabelText(/Price required/)).toBeTruthy();
    expect(getByLabelText(/1 thing left/)).toBeTruthy();
  });
});

/* ------------------------------------------------------------------ *
 * No state may fall through to a wrong default
 * ------------------------------------------------------------------ */

describe("status copy", () => {
  it("gives every health state its own words", () => {
    const states = [
      "in_stock",
      "low_stock",
      "out_of_stock",
      "unknown_stock",
      "hidden",
      "draft"
    ] as const;
    const labels = states.map((health) => listingStatusCopy(health, 2).label);
    expect(new Set(labels).size).toBe(states.length);
  });

  it("does not describe an unknown state as a draft", () => {
    // The old `default:` arm was "Draft — not published", so `unknown_stock`
    // would have rendered as unpublished on a published listing -- and so would
    // every state added after it.
    const label = listingStatusCopy("something_new" as never, null).label;
    expect(label).not.toMatch(/draft/i);
    expect(label).toBe("Needs attention");
  });

  it("distinguishes an uncounted shelf from an empty one in words, not just in state", () => {
    const unknown = listingStatusCopy("unknown_stock", null);
    const empty = listingStatusCopy("out_of_stock", 0);
    expect(unknown.label).not.toBe(empty.label);
    // The actions differ too, because the fixes differ.
    expect(unknown.action).toBe("Add stock count");
    expect(empty.action).toBe("Restock");
  });
});

/* ------------------------------------------------------------------ *
 * The rule the client must not restate
 * ------------------------------------------------------------------ */

describe("the server owns the threshold", () => {
  it("reports low stock on the server's word even at a comfortable count", () => {
    // The client used to own `LOW_STOCK_THRESHOLD = 5` outright, so the number a
    // seller saw and the number the server believed were two independent facts.
    // If the server ever moves its threshold, this is what has to follow it.
    expect(
      listingHealth(payload({ quantity: 40, readiness: verdict({ warnings: ["LOW_STOCK"] }) }))
    ).toBe("low_stock");
  });

  it("does not overrule a healthy verdict with its own arithmetic", () => {
    expect(listingHealth(payload({ quantity: 1, readiness: verdict() }))).toBe("in_stock");
  });
});
