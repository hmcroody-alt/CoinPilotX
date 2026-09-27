/**
 * The related-products row under a canonical Marketplace product.
 *
 * The dismissal sets, the snooze race and the optimistic feedback write are the
 * same as the other four hooks and are tested where they are first introduced.
 * What is tested here is what is different about this surface:
 *
 *   - **The heading may only say what is true of every card.** The shelf renders
 *     no per-card subtitle, so the heading is this row's single user-visible
 *     claim. "Similar products" over a row where two cards cleared the floor on
 *     freshness is the brief's forbidden fabricated reason. The row therefore
 *     narrows its *heading* on a mixed response rather than dropping cards —
 *     dropping them would empty the row on most responses, since the server cap
 *     is six.
 *
 *   - **It does not read the master switch.** "Show Marketplace suggestions"
 *     governs being recommended to while doing something else; a product page is
 *     inside Marketplace, which the settings screen promises keeps working either
 *     way. `preferences.SOCIAL_SURFACES` omits `product_detail` for the same
 *     reason. This is the one difference a viewer could see fail in the *other*
 *     direction — a row that vanishes when they never asked for it to.
 *
 *   - **It sends the anchor id and nothing else.** The id is what excludes the
 *     product from its own recommendations, and what lets the server read the
 *     category the heading claims similarity to. Both uses only narrow the
 *     result.
 *
 *   - **It re-asks per product.** Navigating product to product reuses the hook,
 *     so a fetch keyed on anything but the id would show the previous product's
 *     row under the new one.
 */
import { act, renderHook, waitFor } from "@testing-library/react-native";
import { fetchCommercePlacements, recordCommerceFeedback } from "../../api/commerceDiscovery";
import type { CommercePlacement, CommerceReason, CommerceServeResult } from "../../api/commerceDiscovery";
import { registerCommercePauseWriter, setSocialDiscoveryAllowed } from "../consent";
import { useProductDetailCommerce } from "../useProductDetailCommerce";
import { __resetCommerceSessionId } from "../session";

jest.mock("../../api/commerceDiscovery", () => ({
  fetchCommercePlacements: jest.fn(),
  recordCommerceFeedback: jest.fn(() => Promise.resolve(true))
}));

const fetchPlacements = fetchCommercePlacements as jest.MockedFunction<typeof fetchCommercePlacements>;
const feedback = recordCommerceFeedback as jest.MockedFunction<typeof recordCommerceFeedback>;

/** Matches `config.product_detail_row_size()`. */
const ROW_CADENCE = { leadIn: 0, interval: 1, maxPerPage: 6 };

const ANCHOR = 900;

function placement(
  id: string,
  {
    listingId = Number(id.replace(/\D/g, "")) || 1,
    sellerUserId = 77,
    reason = "similar_to_this_product" as CommerceReason
  } = {}
): CommercePlacement {
  return {
    placementId: id,
    impressionToken: `tok_${id}`,
    surface: "product_detail",
    slot: 0,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason,
    rankingVersion: "commerce-discovery-v1",
    priceMinor: 4999,
    priceCurrency: "USD",
    product: {
      listingId,
      title: `Product ${listingId}`,
      priceLabel: "$49.99",
      coverImageUrl: "https://cdn.example/p.jpg",
      sellerUserId,
      sellerStoreName: "M&W Store",
      category: "shoes",
      rating: 4.8,
      ratingCount: 120
    }
  };
}

/** Three is the floor, so this is the smallest row that renders at all. */
function row(reason: CommerceReason = "similar_to_this_product"): CommercePlacement[] {
  return [1, 2, 3].map((n) => placement(`p${n}`, { listingId: n, reason }));
}

function served(placements: CommercePlacement[], overrides: Partial<CommerceServeResult> = {}): CommerceServeResult {
  return {
    placements,
    visiblePercentThreshold: 60,
    visibleDwellMs: 1000,
    cadence: ROW_CADENCE,
    ...overrides
  };
}

function render(listingId: number = ANCHOR) {
  return renderHook(() => useProductDetailCommerce({ listingId }));
}

beforeEach(() => {
  jest.clearAllMocks();
  __resetCommerceSessionId();
  setSocialDiscoveryAllowed(true);
  registerCommercePauseWriter(null);
  fetchPlacements.mockResolvedValue(served(row()));
});

afterAll(() => {
  registerCommercePauseWriter(null);
});

describe("useProductDetailCommerce — asking", () => {
  it("asks the product_detail surface for its row, and names the anchor", async () => {
    const { result } = render();
    const [surface, options] = fetchPlacements.mock.calls[0];
    expect(surface).toBe("product_detail");
    expect(options?.limit).toBe(ROW_CADENCE.maxPerPage);
    expect(options?.cadence).toEqual(ROW_CADENCE);
    expect(options?.listingId).toBe(ANCHOR);
    await waitFor(() => expect(result.current.modules).toHaveLength(1));
  });

  it("sends nothing while the product is still loading", () => {
    // Zero is also a product that failed to load or was withdrawn. Without the
    // anchor the server could neither exclude it nor read the category the
    // heading claims similarity to, so there is nothing to ask for.
    const { result } = render(0);
    expect(fetchPlacements).not.toHaveBeenCalled();
    expect(result.current.modules).toEqual([]);
  });

  it("re-asks when the viewer navigates to another product", async () => {
    const { result, rerender } = renderHook(
      ({ listingId }: { listingId: number }) => useProductDetailCommerce({ listingId }),
      { initialProps: { listingId: ANCHOR } }
    );
    await waitFor(() => expect(result.current.modules).toHaveLength(1));
    rerender({ listingId: ANCHOR + 1 });
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(2));
    expect(fetchPlacements.mock.calls[1][1]?.listingId).toBe(ANCHOR + 1);
  });

  it("carries one session id for every beacon this row will send", async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.sessionId).toBeTruthy());
    expect(fetchPlacements.mock.calls[0][1]?.sessionId).toBe(result.current.sessionId);
  });
});

describe("useProductDetailCommerce — the shop's consent rule, not the feed's", () => {
  it("still serves a viewer who turned off Marketplace suggestions", async () => {
    // The assertion that would have been inverted a moment ago. Opening a product
    // is asking to be shown things; the switch governs being shown them
    // elsewhere. If this ever fails, the client has started applying the social
    // opt-out to a surface the server deliberately exempts, and the two halves
    // now disagree about what the settings screen promised.
    setSocialDiscoveryAllowed(false);
    const { result } = render();
    await waitFor(() => expect(result.current.modules).toHaveLength(1));
    setSocialDiscoveryAllowed(true);
  });
});

describe("useProductDetailCommerce — the heading is the only claim, so it must hold", () => {
  it("makes the specific claim when every card justifies it", async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.modules).toHaveLength(1));
    expect(result.current.modules[0].titleKey).toBe(
      "commerce:discovery.module.similar_to_this_product"
    );
  });

  it("falls back to a heading that asserts nothing on a mixed row", async () => {
    // The fabricated-claim failure, and the reason the heading narrows instead of
    // the row: two of these three cards were not chosen for similarity, so
    // "Similar products" would be false of them.
    fetchPlacements.mockResolvedValue(
      served([
        placement("p1", { listingId: 1, reason: "similar_to_this_product" }),
        placement("p2", { listingId: 2, reason: "trending" }),
        placement("p3", { listingId: 3, reason: "new_arrival" })
      ])
    );
    const { result } = render();
    await waitFor(() => expect(result.current.modules).toHaveLength(1));
    expect(result.current.modules[0].titleKey).toBe(
      "commerce:discovery.module.you_might_also_like"
    );
  });

  it("keeps every card on a mixed row rather than dropping the odd ones out", async () => {
    // The alternative — requiring one reason throughout — would empty the row on
    // most real responses, because the server cap is six and a six-card pool is
    // rarely homogeneous. A dead end on a product page is worse than a weaker
    // heading.
    fetchPlacements.mockResolvedValue(
      served([
        placement("p1", { listingId: 1, reason: "similar_to_this_product" }),
        placement("p2", { listingId: 2, reason: "trending" }),
        placement("p3", { listingId: 3, reason: "new_arrival" })
      ])
    );
    const { result } = render();
    await waitFor(() => expect(result.current.modules).toHaveLength(1));
    expect(result.current.modules[0].placements).toHaveLength(3);
  });

  it("uses the shelf's own wording where the reason and the heading differ", async () => {
    // `matches_your_interests` is headed "Recommended for you". Interpolating the
    // reason straight into the key would render a missing-key placeholder as a
    // shelf heading.
    fetchPlacements.mockResolvedValue(served(row("matches_your_interests")));
    const { result } = render();
    await waitFor(() => expect(result.current.modules).toHaveLength(1));
    expect(result.current.modules[0].titleKey).toBe(
      "commerce:discovery.module.recommended_for_you"
    );
  });

  it("does not repeat the post wording under a product", async () => {
    // The server substitutes `similar_to_this_product` on this surface, so a
    // placement carrying the post wording here is a server bug. Falling through
    // to a neutral heading is the right way not to print it.
    fetchPlacements.mockResolvedValue(served(row("related_to_this_post" as CommerceReason)));
    const { result } = render();
    await waitFor(() => expect(result.current.modules).toHaveLength(1));
    expect(result.current.modules[0].titleKey).not.toContain("related_to_this_post");
  });
});

describe("useProductDetailCommerce — a short row is no row", () => {
  it("drops a row that would render with fewer than three cards", async () => {
    // Matches `MIN_MODULE_ITEMS` server-side. Two products under "Similar
    // products" reads as the catalogue being empty, which on this screen is a
    // statement about the shop rather than about the ranking.
    fetchPlacements.mockResolvedValue(served(row().slice(0, 2)));
    const { result } = render();
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalled());
    expect(result.current.modules).toEqual([]);
  });

  it("drops the row once dismissals take it below the floor", async () => {
    const { result } = render();
    await waitFor(() => expect(result.current.modules).toHaveLength(1));
    act(() => {
      result.current.onFeedback(result.current.modules[0].placements[0], "not_interested");
    });
    expect(result.current.modules).toEqual([]);
    expect(feedback).toHaveBeenCalled();
  });
});

describe("useProductDetailCommerce — never the product on screen", () => {
  it("drops a card for the anchor even though the server already excluded it", async () => {
    // Re-checked on the client because this is the one wrong answer on this
    // screen that every user would notice: a card advertising the product they
    // already have open. Four cards arrive, one is the anchor, three remain —
    // which is also why the fixture sends four rather than three.
    fetchPlacements.mockResolvedValue(
      served([...row(), placement("anchor", { listingId: ANCHOR })])
    );
    const { result } = render();
    await waitFor(() => expect(result.current.modules).toHaveLength(1));
    const ids = result.current.modules[0].placements.map((p) => p.product.listingId);
    expect(ids).not.toContain(ANCHOR);
    expect(ids).toHaveLength(3);
  });
});

describe("useProductDetailCommerce — dismissals are about this product", () => {
  it("forgets session dismissals when the viewer moves to another product", async () => {
    fetchPlacements.mockResolvedValue(served([...row(), placement("p4", { listingId: 4 })]));
    const { result, rerender } = renderHook(
      ({ listingId }: { listingId: number }) => useProductDetailCommerce({ listingId }),
      { initialProps: { listingId: ANCHOR } }
    );
    await waitFor(() => expect(result.current.modules[0]?.placements).toHaveLength(4));
    act(() => {
      result.current.onFeedback(result.current.modules[0].placements[0], "not_interested");
    });
    expect(result.current.modules[0].placements).toHaveLength(3);

    // The server still holds the dismissal, so clearing the session copy cannot
    // un-hide anything the viewer told us to hide.
    rerender({ listingId: ANCHOR + 1 });
    await waitFor(() => expect(result.current.modules[0]?.placements).toHaveLength(4));
  });
});
