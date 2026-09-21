/**
 * The state that decides whether a hidden card stays hidden.
 *
 * `CommerceFeedCard` proves the tap reaches the parent with the right verb, and
 * `commerceRows` proves a dismissed slot is not refilled. This is the piece in
 * between: the hook that has to make a dismissal *stick* for the rest of the
 * page, across a network write that may well fail and a fetch that may well
 * land afterwards.
 *
 * Three failure modes, all of which look identical to a working feature right
 * up until they don't:
 *
 *   - **A late response resurrects a snoozed page.** The user taps "hide these
 *     for 30 days", the cards vanish, and then the serve request that was
 *     already in flight resolves and puts six of them back. The guard is a ref
 *     rather than state because the check has to see the *current* value, not
 *     the one captured when the effect ran.
 *
 *   - **A failed write un-hides the card.** The preference did not save, but
 *     the honest thing to show the person who is looking at the screen is what
 *     they asked for. Reverting turns a lost preference into a visible lie.
 *
 *   - **A refetch inherits the old page's dismissals forever.** They are about
 *     cards that are no longer on screen; the server holds the durable copy.
 *     Keeping them here only grows two sets for the life of the process.
 */
import { act, renderHook, waitFor } from "@testing-library/react-native";
import { fetchCommercePlacements, recordCommerceFeedback } from "../../api/commerceDiscovery";
import type { CommercePlacement, CommerceServeResult } from "../../api/commerceDiscovery";
import { useFeedCommerce } from "../useFeedCommerce";
import { __resetCommerceSessionId, commerceSessionId } from "../session";

jest.mock("../../api/commerceDiscovery", () => ({
  fetchCommercePlacements: jest.fn(),
  recordCommerceFeedback: jest.fn(() => Promise.resolve(true))
}));

const fetchPlacements = fetchCommercePlacements as jest.MockedFunction<typeof fetchCommercePlacements>;
const feedback = recordCommerceFeedback as jest.MockedFunction<typeof recordCommerceFeedback>;

function placement(id: string, sellerUserId = 42): CommercePlacement {
  return {
    placementId: id,
    impressionToken: `tok-${id}`,
    surface: "feed",
    slot: 0,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason: "popular",
    rankingVersion: "v1",
    priceMinor: 1000,
    priceCurrency: "USD",
    product: {
      listingId: Number(id.replace(/\D/g, "")) || 1,
      title: `Product ${id}`,
      priceLabel: "$10.00",
      coverImageUrl: "https://cdn.example/x.jpg",
      sellerUserId,
      sellerStoreName: "Store",
      category: "shoes",
      rating: 4.5,
      ratingCount: 10
    }
  };
}

/** What the server sends when no operator has retuned the feed rhythm. */
const DEFAULT_CADENCE = { leadIn: 6, interval: 8, maxPerPage: 2 };

function served(placements: CommercePlacement[]): CommerceServeResult {
  return {
    placements,
    visiblePercentThreshold: 60,
    visibleDwellMs: 1000,
    cadence: DEFAULT_CADENCE
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  __resetCommerceSessionId();
  fetchPlacements.mockResolvedValue(served([placement("p1"), placement("p2")]));
});

describe("useFeedCommerce — fetching", () => {
  it("asks for more placements than the feed will place, so a hidden card leaves headroom", async () => {
    renderHook(() => useFeedCommerce({}));
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalled());
    const [surface, options] = fetchPlacements.mock.calls[0];
    expect(surface).toBe("feed");
    expect(options?.limit).toBeGreaterThan(2);
  });

  it("fetches nothing and reports nothing while disabled", async () => {
    const { result } = renderHook(() => useFeedCommerce({ enabled: false }));
    expect(fetchPlacements).not.toHaveBeenCalled();
    expect(result.current.placements).toEqual([]);
  });

  it("adopts the server's viewability contract rather than keeping its own defaults", async () => {
    fetchPlacements.mockResolvedValue({
      placements: [placement("p1")],
      visiblePercentThreshold: 75,
      visibleDwellMs: 2500,
      cadence: DEFAULT_CADENCE
    });
    const { result } = renderHook(() => useFeedCommerce({}));
    await waitFor(() => expect(result.current.visibleDwellMs).toBe(2500));
    expect(result.current.visiblePercentThreshold).toBe(75);
  });

  it("surfaces an empty page rather than an error when the engine is off", async () => {
    fetchPlacements.mockResolvedValue(served([]));
    const { result } = renderHook(() => useFeedCommerce({}));
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalled());
    expect(result.current.placements).toEqual([]);
  });

  it("never lets a recommendation failure reach Home as a rejection", async () => {
    fetchPlacements.mockRejectedValue(new Error("engine down"));
    const { result } = renderHook(() => useFeedCommerce({}));
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalled());
    expect(result.current.placements).toEqual([]);
  });

  it("refetches when Home pulls to refresh", async () => {
    const { rerender } = renderHook((props: { refreshToken: number }) => useFeedCommerce(props), {
      initialProps: { refreshToken: 0 }
    });
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(1));
    rerender({ refreshToken: 1 });
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(2));
  });
});

describe("useFeedCommerce — dismissal", () => {
  it("hides the card on the tap, not on the response", async () => {
    // A write that never settles. The card still has to be gone.
    feedback.mockReturnValue(new Promise(() => undefined) as never);
    const { result } = renderHook(() => useFeedCommerce({}));
    await waitFor(() => expect(result.current.placements).toHaveLength(2));

    act(() => {
      result.current.onFeedback(placement("p1"), "hide");
    });
    expect(result.current.dismissedPlacementIds.has("p1")).toBe(true);
  });

  it("keeps the card hidden when the preference fails to save", async () => {
    feedback.mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => useFeedCommerce({}));
    await waitFor(() => expect(result.current.placements).toHaveLength(2));

    act(() => {
      result.current.onFeedback(placement("p1"), "not_interested");
    });
    // Deliberately not reverted. Someone who hid something and watched it stay
    // hidden has been told the truth by the surface they were looking at.
    await waitFor(() => expect(result.current.dismissedPlacementIds.has("p1")).toBe(true));
  });

  it("hides the seller and the card when the user says stop recommending this store", async () => {
    const { result } = renderHook(() => useFeedCommerce({}));
    await waitFor(() => expect(result.current.placements).toHaveLength(2));

    act(() => {
      result.current.onFeedback(placement("p1", 99), "hide_seller");
    });
    expect(result.current.dismissedSellerIds.has(99)).toBe(true);
    // Belt and braces: a placement whose seller id did not survive the wire
    // still has to disappear.
    expect(result.current.dismissedPlacementIds.has("p1")).toBe(true);
  });

  it("sends the verb the user chose, not a generic hide", async () => {
    const { result } = renderHook(() => useFeedCommerce({}));
    await waitFor(() => expect(result.current.placements).toHaveLength(2));

    act(() => {
      result.current.onFeedback(placement("p1"), "see_fewer");
    });
    expect(feedback).toHaveBeenCalledWith(expect.objectContaining({ placementId: "p1" }), "see_fewer");
  });
});

describe("useFeedCommerce — snooze", () => {
  it("empties the page immediately", async () => {
    const { result } = renderHook(() => useFeedCommerce({}));
    await waitFor(() => expect(result.current.placements).toHaveLength(2));

    act(() => {
      result.current.onFeedback(placement("p1"), "snooze");
    });
    expect(result.current.placements).toEqual([]);
  });

  it("cannot be undone by a response that was already in flight", async () => {
    // The shape of the bug: the serve request has not resolved when the user
    // snoozes. If the handler checked a captured state value instead of a ref,
    // this resolve would repopulate a page the user just turned off.
    let resolveServe: (value: ReturnType<typeof served>) => void = () => undefined;
    fetchPlacements.mockReturnValue(
      new Promise((resolve) => {
        resolveServe = resolve;
      }) as never
    );

    const { result } = renderHook(() => useFeedCommerce({}));
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalled());

    act(() => {
      result.current.onFeedback(placement("p1"), "snooze");
    });
    expect(result.current.placements).toEqual([]);

    await act(async () => {
      resolveServe(served([placement("p3"), placement("p4")]));
    });
    expect(result.current.placements).toEqual([]);
  });
});

describe("useFeedCommerce — refresh", () => {
  it("drops the previous page's dismissals, which the server still holds", async () => {
    const { result, rerender } = renderHook((props: { refreshToken: number }) => useFeedCommerce(props), {
      initialProps: { refreshToken: 0 }
    });
    await waitFor(() => expect(result.current.placements).toHaveLength(2));

    act(() => {
      result.current.onFeedback(placement("p1"), "hide");
    });
    expect(result.current.dismissedPlacementIds.size).toBe(1);

    rerender({ refreshToken: 1 });
    // Safe only because the write is the durable record: the next serve is what
    // keeps the product away, so clearing here cannot un-hide anything.
    await waitFor(() => expect(result.current.dismissedPlacementIds.size).toBe(0));
    expect(result.current.dismissedSellerIds.size).toBe(0);
  });
});

describe("useFeedCommerce — session", () => {
  it("tags the request with the launch's correlation id", async () => {
    const { result } = renderHook(() => useFeedCommerce({}));
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalled());
    expect(fetchPlacements.mock.calls[0][1]?.sessionId).toBe(commerceSessionId());
    expect(result.current.sessionId).toBe(commerceSessionId());
  });

  it("reuses one id across mounts rather than minting one per surface", async () => {
    const first = renderHook(() => useFeedCommerce({}));
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(1));
    const second = renderHook(() => useFeedCommerce({}));
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(2));
    expect(second.result.current.sessionId).toBe(first.result.current.sessionId);
  });

  it("does not carry anything derived from the device or the user", () => {
    // The point of the id is that it groups events, not that it identifies
    // anyone. A persistent identifier here would turn an analytics key into a
    // cross-session behavioural profile stamped on every product scrolled past.
    expect(commerceSessionId()).toMatch(/^cs_[a-z0-9]+$/);
    __resetCommerceSessionId();
    expect(commerceSessionId()).not.toBe("");
  });
});
