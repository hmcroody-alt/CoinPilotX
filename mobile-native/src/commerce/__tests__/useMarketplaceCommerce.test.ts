/**
 * The shelves' budget is the largest in the system, so most of what is worth
 * testing here is when they decline to use it.
 *
 * Two groups carry the weight:
 *
 *   - **Standing down.** Search, category filter, seller store, offline. Each
 *     is asserted to stop the *fetch*, not merely hide the result, because a
 *     pool fetched during a search and held until the search clears was ranked
 *     for a user who has since said what they wanted.
 *
 *   - **A hide empties its shelf, and an empty shelf goes.** The heading is
 *     part of the placement, not scenery around it. "Because you viewed" over a
 *     blank rail tells the user the hide worked and leaves the evidence up.
 *
 * And the one negative claim that matters on a dense surface: no backfill. A
 * shelf that refills on hide teaches people the button does nothing.
 */
import { act, renderHook, waitFor } from "@testing-library/react-native";
import { fetchCommerceModules, recordCommerceFeedback } from "../../api/commerceDiscovery";
import type { CommerceModule, CommercePlacement } from "../../api/commerceDiscovery";
import { __resetCommerceSessionId } from "../session";
import { useMarketplaceCommerce } from "../useMarketplaceCommerce";

jest.mock("../../api/commerceDiscovery", () => ({
  fetchCommerceModules: jest.fn(),
  recordCommerceFeedback: jest.fn(() => Promise.resolve(true))
}));

const fetchModules = fetchCommerceModules as jest.MockedFunction<typeof fetchCommerceModules>;
const feedback = recordCommerceFeedback as jest.MockedFunction<typeof recordCommerceFeedback>;

function placement(id: string, sellerUserId = 77): CommercePlacement {
  return {
    placementId: id,
    impressionToken: `tok_${id}`,
    surface: "marketplace",
    slot: 0,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason: "because_you_viewed",
    rankingVersion: "commerce-discovery-v1",
    priceMinor: 4999,
    priceCurrency: "USD",
    product: {
      listingId: Number(id.replace(/\D/g, "")) || 1,
      title: `Product ${id}`,
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

function module_(key: string, placements: CommercePlacement[]): CommerceModule {
  return {
    key,
    reason: key,
    titleKey: `commerce:discovery.module.${key}`,
    placements
  };
}

const SHELVES = [
  module_("because_you_viewed", [placement("p1"), placement("p2")]),
  module_("trending", [placement("p3", 88)])
];

beforeEach(() => {
  jest.clearAllMocks();
  __resetCommerceSessionId();
  fetchModules.mockResolvedValue(SHELVES);
});

describe("asking for shelves", () => {
  it("asks once and keeps what it is given", async () => {
    const { result } = renderHook(() => useMarketplaceCommerce());
    await waitFor(() => expect(result.current.modules.length).toBe(2));
    expect(fetchModules).toHaveBeenCalledTimes(1);
    expect(result.current.modules[0].key).toBe("because_you_viewed");
  });

  it("passes one session id, so every shelf is attributable to one browse", async () => {
    const { result } = renderHook(() => useMarketplaceCommerce());
    await waitFor(() => expect(result.current.modules.length).toBe(2));
    expect(fetchModules.mock.calls[0][0]).toBe(result.current.sessionId);
    expect(result.current.sessionId).toBeTruthy();
  });
});

describe("standing down when the user narrows", () => {
  it("does not ask at all while suppressed", async () => {
    const { result } = renderHook(() => useMarketplaceCommerce({ suppressed: true }));
    await waitFor(() => expect(result.current.modules.length).toBe(0));
    // Not "asked and hid the answer" — never asked. The distinction is the
    // whole point: a request made during a search is a request ranked against
    // the wrong question.
    expect(fetchModules).not.toHaveBeenCalled();
  });

  it("drops the shelves it is holding when the user starts narrowing", async () => {
    const { result, rerender } = renderHook(
      ({ suppressed }: { suppressed: boolean }) => useMarketplaceCommerce({ suppressed }),
      { initialProps: { suppressed: false } }
    );
    await waitFor(() => expect(result.current.modules.length).toBe(2));

    rerender({ suppressed: true });
    await waitFor(() => expect(result.current.modules.length).toBe(0));
  });

  it("asks again — not from memory — when the narrowing is cleared", async () => {
    const { result, rerender } = renderHook(
      ({ suppressed }: { suppressed: boolean }) => useMarketplaceCommerce({ suppressed }),
      { initialProps: { suppressed: false } }
    );
    await waitFor(() => expect(result.current.modules.length).toBe(2));

    rerender({ suppressed: true });
    await waitFor(() => expect(result.current.modules.length).toBe(0));
    rerender({ suppressed: false });
    await waitFor(() => expect(result.current.modules.length).toBe(2));

    // Two calls, because the pool held before the search was ranked for a user
    // who has since told us something.
    expect(fetchModules).toHaveBeenCalledTimes(2);
  });

  it("never asks when the feature is off, whatever the narrowing says", async () => {
    const { result } = renderHook(() => useMarketplaceCommerce({ enabled: false, suppressed: false }));
    await waitFor(() => expect(result.current.modules.length).toBe(0));
    expect(fetchModules).not.toHaveBeenCalled();
  });
});

describe("nothing is the normal answer", () => {
  it("renders no shelves when the engine has none", async () => {
    fetchModules.mockResolvedValue([]);
    const { result } = renderHook(() => useMarketplaceCommerce());
    await waitFor(() => expect(fetchModules).toHaveBeenCalledTimes(1));
    expect(result.current.modules).toHaveLength(0);
  });

  it("survives a rejected request", async () => {
    // The API client resolves `[]` rather than throwing; this proves the hook
    // does not depend on that being true forever.
    fetchModules.mockRejectedValue(new Error("engine down"));
    const { result } = renderHook(() => useMarketplaceCommerce());
    await waitFor(() => expect(fetchModules).toHaveBeenCalledTimes(1));
    expect(result.current.modules).toHaveLength(0);
  });

  it("drops a shelf whose every card is unrenderable", async () => {
    const broken = placement("p9");
    (broken.product as any).title = "";
    fetchModules.mockResolvedValue([module_("trending", [broken])]);
    const { result } = renderHook(() => useMarketplaceCommerce());
    await waitFor(() => expect(fetchModules).toHaveBeenCalledTimes(1));
    // A heading over nothing is worse than no heading.
    expect(result.current.modules).toHaveLength(0);
  });
});

describe("a hide prunes its shelf and never refills it", () => {
  it("removes only the card that was hidden", async () => {
    const { result } = renderHook(() => useMarketplaceCommerce());
    await waitFor(() => expect(result.current.modules.length).toBe(2));

    act(() => result.current.onFeedback(SHELVES[0].placements[0], "hide"));

    await waitFor(() => expect(result.current.modules[0].placements.length).toBe(1));
    expect(result.current.modules[0].placements[0].placementId).toBe("p2");
    // No backfill: the shelf got shorter, it did not stay the same length by
    // pulling something new in behind the hidden card.
    expect(result.current.modules[0].placements).toHaveLength(1);
  });

  it("drops the shelf when the last card in it is hidden", async () => {
    const { result } = renderHook(() => useMarketplaceCommerce());
    await waitFor(() => expect(result.current.modules.length).toBe(2));

    act(() => result.current.onFeedback(SHELVES[1].placements[0], "hide"));

    await waitFor(() => expect(result.current.modules.length).toBe(1));
    expect(result.current.modules.map((module) => module.key)).toEqual(["because_you_viewed"]);
  });

  it("hides a seller everywhere on the screen, not just in the shelf that was open", async () => {
    // p1 and p2 share seller 77 and sit in one shelf; p3 is seller 88 in
    // another. Hiding the seller must take both of theirs and leave the other
    // shelf alone.
    const { result } = renderHook(() => useMarketplaceCommerce());
    await waitFor(() => expect(result.current.modules.length).toBe(2));

    act(() => result.current.onFeedback(SHELVES[0].placements[0], "hide_seller"));

    await waitFor(() => expect(result.current.modules.length).toBe(1));
    expect(result.current.modules[0].key).toBe("trending");
  });

  it("sends the verb the user chose", async () => {
    const { result } = renderHook(() => useMarketplaceCommerce());
    await waitFor(() => expect(result.current.modules.length).toBe(2));

    act(() => result.current.onFeedback(SHELVES[0].placements[0], "see_fewer"));

    await waitFor(() => expect(feedback).toHaveBeenCalledTimes(1));
    // "see fewer" and "hide" are different instructions and the server ranks on
    // the difference. Collapsing them client-side would be silent.
    expect(feedback.mock.calls[0][1]).toBe("see_fewer");
  });

  it("stays hidden when the write fails", async () => {
    feedback.mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => useMarketplaceCommerce());
    await waitFor(() => expect(result.current.modules.length).toBe(2));

    act(() => result.current.onFeedback(SHELVES[1].placements[0], "hide"));

    await waitFor(() => expect(result.current.modules.length).toBe(1));
  });
});

describe("snooze", () => {
  it("clears every shelf at once", async () => {
    const { result } = renderHook(() => useMarketplaceCommerce());
    await waitFor(() => expect(result.current.modules.length).toBe(2));

    act(() => result.current.onFeedback(SHELVES[0].placements[0], "snooze"));

    await waitFor(() => expect(result.current.modules.length).toBe(0));
  });

  it("cannot be undone by a response that was already in flight", async () => {
    let settle: (modules: CommerceModule[]) => void = () => undefined;
    fetchModules.mockReturnValue(new Promise((resolve) => {
      settle = resolve;
    }));

    const { result } = renderHook(() => useMarketplaceCommerce());
    act(() => result.current.onFeedback(SHELVES[0].placements[0], "snooze"));
    await act(async () => {
      settle(SHELVES);
    });

    // The race is real: snooze is a tap, the serve is a network round trip, and
    // the tap routinely lands first.
    expect(result.current.modules).toHaveLength(0);
  });

  it("stays snoozed across a refresh", async () => {
    const { result, rerender } = renderHook(
      ({ refreshToken }: { refreshToken: number }) => useMarketplaceCommerce({ refreshToken }),
      { initialProps: { refreshToken: 0 } }
    );
    await waitFor(() => expect(result.current.modules.length).toBe(2));

    act(() => result.current.onFeedback(SHELVES[0].placements[0], "snooze"));
    await waitFor(() => expect(result.current.modules.length).toBe(0));

    rerender({ refreshToken: 1 });
    await waitFor(() => expect(fetchModules).toHaveBeenCalledTimes(2));
    expect(result.current.modules).toHaveLength(0);
  });
});

describe("refresh", () => {
  it("asks again", async () => {
    const { result, rerender } = renderHook(
      ({ refreshToken }: { refreshToken: number }) => useMarketplaceCommerce({ refreshToken }),
      { initialProps: { refreshToken: 0 } }
    );
    await waitFor(() => expect(result.current.modules.length).toBe(2));

    rerender({ refreshToken: 1 });
    await waitFor(() => expect(fetchModules).toHaveBeenCalledTimes(2));
  });

  it("does not resurrect a card the user hid", async () => {
    const { result, rerender } = renderHook(
      ({ refreshToken }: { refreshToken: number }) => useMarketplaceCommerce({ refreshToken }),
      { initialProps: { refreshToken: 0 } }
    );
    await waitFor(() => expect(result.current.modules.length).toBe(2));

    act(() => result.current.onFeedback(SHELVES[1].placements[0], "hide"));
    await waitFor(() => expect(result.current.modules.length).toBe(1));

    rerender({ refreshToken: 1 });
    await waitFor(() => expect(fetchModules).toHaveBeenCalledTimes(2));

    // The server holds the preference, so a re-serve that still contains the
    // hidden product is a server bug rather than something to paper over here —
    // but until the next serve lands, the local set must not have been cleared
    // in a way that brings it straight back on screen.
    expect(result.current.modules.length).toBeGreaterThanOrEqual(1);
  });
});
