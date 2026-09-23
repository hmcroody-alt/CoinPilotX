/**
 * The state behind the one chip Reels is allowed to show.
 *
 * `reelSlots.test.ts` proves the binder puts the chip on the right reel.
 * `ReelsCommerceChip.test.tsx` proves the chip behaves once it is there. This is
 * the layer between them — the one that owns a lifecycle, and therefore owns the
 * races:
 *
 *   - **A snooze that a late response undoes.** The user asks for no Marketplace
 *     suggestions for 30 days, the chip goes, and then the serve request that
 *     was already in flight resolves and puts one back. The guard has to be a
 *     ref, because the check must see the value *now* and not the one the effect
 *     captured when it ran.
 *
 *   - **A dismissal that does not stick.** The network write may well fail. The
 *     honest thing to show the person looking at the screen is what they asked
 *     for; reverting turns a lost preference into a visible lie, and an
 *     undismissable chip on someone's video is the worst outcome on the list.
 *
 *   - **A re-bind on every frame of a scroll.** `reelIds` is rebuilt by the
 *     screen each render, so a memo keyed on its identity hands the renderer a
 *     brand new Map sixty times a second. Keyed on content, it re-binds when the
 *     list actually changes.
 *
 * And the thing that is not a race but matters more than any of them: when the
 * engine has nothing worth showing, this hook's answer is an empty map, and an
 * empty map means Reels renders exactly what it rendered before the feature
 * existed.
 */
import { act, renderHook, waitFor } from "@testing-library/react-native";
import { fetchCommercePlacements, recordCommerceFeedback } from "../../api/commerceDiscovery";
import type { CommercePlacement, CommerceServeResult } from "../../api/commerceDiscovery";
import { useReelsCommerce } from "../useReelsCommerce";
import { REELS_INTERVAL, REELS_LEAD_IN } from "../reelSlots";
import { __resetCommerceSessionId } from "../session";

jest.mock("../../api/commerceDiscovery", () => ({
  fetchCommercePlacements: jest.fn(),
  recordCommerceFeedback: jest.fn(() => Promise.resolve(true))
}));

const fetchPlacements = fetchCommercePlacements as jest.MockedFunction<typeof fetchCommercePlacements>;
const feedback = recordCommerceFeedback as jest.MockedFunction<typeof recordCommerceFeedback>;

/** What the server sends for reels when nothing has been retuned. */
const REELS_CADENCE = { leadIn: REELS_LEAD_IN, interval: REELS_INTERVAL, maxPerPage: 1 };

function placement(id: string, sellerUserId = 77): CommercePlacement {
  return {
    placementId: id,
    impressionToken: `tok_${id}`,
    surface: "reels",
    slot: 0,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason: "because_you_viewed",
    rankingVersion: "commerce-discovery-v1",
    priceMinor: 4999,
    priceCurrency: "USD",
    product: {
      listingId: 501,
      title: "Women's Casual Sneakers",
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

function served(placements: CommercePlacement[], overrides: Partial<CommerceServeResult> = {}): CommerceServeResult {
  return {
    placements,
    visiblePercentThreshold: 60,
    visibleDwellMs: 1000,
    cadence: REELS_CADENCE,
    ...overrides
  };
}

/** A list long enough that the lead-in is reachable. */
const REEL_IDS = Array.from({ length: 20 }, (_, index) => `r${index + 1}`);

/** The reel the default cadence puts the chip on. */
const CARRIER = REEL_IDS[REELS_LEAD_IN];

beforeEach(() => {
  jest.clearAllMocks();
  __resetCommerceSessionId();
  fetchPlacements.mockResolvedValue(served([placement("p1")]));
});

describe("useReelsCommerce — asking", () => {
  it("asks the reels surface for the reels budget, which is one", async () => {
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS }));
    const [surface, options] = fetchPlacements.mock.calls[0];
    expect(surface).toBe("reels");
    expect(options?.limit).toBe(1);
    // Let the in-flight response land before the test ends. Without this the
    // state it sets arrives outside `act`, and the resulting warning is noise
    // that would hide a real one later.
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
  });

  it("sends its own cadence as the fallback, so a failure cannot hand it the feed's", async () => {
    // Reels is the surface where a wrong rhythm is most expensive: the feed's
    // interval on a reels list would place a chip every eight videos.
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS }));
    const [, options] = fetchPlacements.mock.calls[0];
    expect(options?.cadence).toEqual(REELS_CADENCE);
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
  });

  it("fetches nothing and holds nothing while disabled", () => {
    // `enabled` is a gate on *existence*, not on visibility. The screen passes
    // false for a signed-out viewer and for an active call.
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS, enabled: false }));
    expect(fetchPlacements).not.toHaveBeenCalled();
    expect(result.current.chipByReelId.size).toBe(0);
  });

  it("carries one session id for every beacon the surface will send", async () => {
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS }));
    const [, options] = fetchPlacements.mock.calls[0];
    await waitFor(() => expect(result.current.sessionId).toBeTruthy());
    expect(options?.sessionId).toBe(result.current.sessionId);
  });
});

describe("useReelsCommerce — nothing is a valid answer", () => {
  it("binds nothing when the engine cleared no placement over the floor", async () => {
    // `min_score("reels")` is the highest in the system on purpose: no placement
    // is better than a bad placement. An empty response must be inert.
    fetchPlacements.mockResolvedValue(served([]));
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS }));
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalled());
    expect(result.current.chipByReelId.size).toBe(0);
  });

  it("never lets a recommendation failure reach Reels as a rejection", async () => {
    // A failed product recommendation must not be able to break someone's video.
    fetchPlacements.mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS }));
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalled());
    expect(result.current.chipByReelId.size).toBe(0);
  });

  it("does not even ask for a chip when no reel could carry one", async () => {
    // Stronger than the "binds nothing to an empty reel list" this replaced. A
    // list shorter than the lead-in has no slot, so a serve request would spend
    // a round trip and a server-side placement row on a chip that structurally
    // cannot render — and would have to go out with no context at all, since
    // there is no target reel to describe.
    const { result } = renderHook(() => useReelsCommerce({ reelIds: [] }));
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(0));
    expect(fetchPlacements).not.toHaveBeenCalled();

    // Same for a list that exists but stops one short of the lead-in.
    const short = renderHook(() =>
      useReelsCommerce({ reelIds: REEL_IDS.slice(0, REELS_LEAD_IN) })
    );
    await waitFor(() => expect(short.result.current.chipByReelId.size).toBe(0));
    expect(fetchPlacements).not.toHaveBeenCalled();
  });
});

describe("useReelsCommerce — §8 context matching", () => {
  it("describes the reel the chip will actually land on, not the list", async () => {
    // The whole point of deriving the target from `reelCommerceSlots`: the reel
    // we ask the ranker about and the reel the chip binds to are the same video.
    // If these two ever disagree, the engine is scoring against one reel and the
    // user is watching another.
    const resolveContext = jest.fn((reelId: string) => ({ topic: `about ${reelId}` }));
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS, resolveContext }));
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));

    expect(resolveContext).toHaveBeenCalledWith(CARRIER);
    const [, options] = fetchPlacements.mock.calls[0];
    expect(options?.context).toEqual({ topic: `about ${CARRIER}` });
    expect([...result.current.chipByReelId.keys()]).toEqual([CARRIER]);
  });

  it("omits the context entirely when the reel has no topic", async () => {
    // `{}` and "absent" are not the same request. `ranking.relevance` answers
    // NEUTRAL for an absent context, which is the honest score for a reel that
    // says nothing about itself — scoring it against an empty string instead
    // would push it under the reels floor and silence a perfectly good chip.
    const resolveContext = jest.fn(() => null);
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS, resolveContext }));
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));

    const [, options] = fetchPlacements.mock.calls[0];
    expect(options?.context).toBeUndefined();
  });

  it("still serves a chip when the screen passes no resolver at all", async () => {
    // §8 is additive. A caller that never opted in gets the pre-§8 behaviour
    // rather than an error or an empty surface.
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS }));
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
    expect(fetchPlacements.mock.calls[0][1]?.context).toBeUndefined();
  });

  it("does not refetch when the screen rebuilds the resolver every render", async () => {
    // The screen's resolver closes over the reel list, so it is a new function
    // identity on every frame of a scroll. Held in a ref for exactly this
    // reason: a resolver in the effect's dep array would refire the serve
    // request — and mint a fresh placement row — sixty times a second.
    const { result, rerender } = renderHook(
      ({ tick }: { tick: number }) =>
        useReelsCommerce({
          reelIds: REEL_IDS,
          resolveContext: () => ({ topic: `render ${tick}` })
        }),
      { initialProps: { tick: 0 } }
    );
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));

    rerender({ tick: 1 });
    rerender({ tick: 2 });
    rerender({ tick: 3 });

    expect(fetchPlacements).toHaveBeenCalledTimes(1);
    expect(fetchPlacements.mock.calls[0][1]?.context).toEqual({ topic: "render 0" });
  });

  it("asks again when a refresh puts a different reel in the carrying slot", async () => {
    // Pull-to-refresh replaces the list, so the reel the ranker scored against
    // may no longer be on screen. Re-asking with the new target is the point of
    // keying the effect on the target id rather than on the list's identity.
    const resolveContext = (reelId: string) => ({ topic: `about ${reelId}` });
    const { result, rerender } = renderHook(
      ({ ids }: { ids: string[] }) => useReelsCommerce({ reelIds: ids, resolveContext }),
      { initialProps: { ids: REEL_IDS } }
    );
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
    expect(fetchPlacements).toHaveBeenCalledTimes(1);

    // Appending pages does not move the carrier, so it must not refetch.
    rerender({ ids: [...REEL_IDS, "r21", "r22"] });
    expect(fetchPlacements).toHaveBeenCalledTimes(1);

    // Prepending does move it.
    const refreshed = ["n1", "n2", ...REEL_IDS];
    rerender({ ids: refreshed });
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(2));
    expect(fetchPlacements.mock.calls[1][1]?.context).toEqual({
      topic: `about ${refreshed[REELS_LEAD_IN]}`
    });
  });
});

describe("useReelsCommerce — binding", () => {
  it("puts the chip on a reel id, at the server's lead-in", async () => {
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS }));
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
    expect([...result.current.chipByReelId.keys()]).toEqual([CARRIER]);
  });

  it("obeys a cadence the server retuned rather than its own constants", async () => {
    fetchPlacements.mockResolvedValue(
      served([placement("p1")], { cadence: { leadIn: 9, interval: 12, maxPerPage: 1 } })
    );
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS }));
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
    expect([...result.current.chipByReelId.keys()]).toEqual([REEL_IDS[9]]);
  });

  it("adopts the server's dwell instead of keeping its own default", async () => {
    fetchPlacements.mockResolvedValue(served([placement("p1")], { visibleDwellMs: 2500 }));
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS }));
    await waitFor(() => expect(result.current.visibleDwellMs).toBe(2500));
  });

  it("hands back the identical map when the reel list is rebuilt with the same ids", async () => {
    // The screen rebuilds `reelIds` on every render, so a memo keyed on its
    // identity would hand the renderer a new Map on every frame of a scroll —
    // and a new Map means every chip remounts and re-reports its impression.
    const { result, rerender } = renderHook(({ ids }) => useReelsCommerce({ reelIds: ids }), {
      initialProps: { ids: [...REEL_IDS] }
    });
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
    const first = result.current.chipByReelId;
    rerender({ ids: [...REEL_IDS] });
    expect(result.current.chipByReelId).toBe(first);
  });

  it("re-binds when the list actually changes", async () => {
    const { result, rerender } = renderHook(({ ids }) => useReelsCommerce({ reelIds: ids }), {
      initialProps: { ids: [...REEL_IDS] }
    });
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
    rerender({ ids: ["fresh1", "fresh2", ...REEL_IDS] });
    await waitFor(() => expect([...result.current.chipByReelId.keys()]).toEqual([REEL_IDS[2]]));
  });
});

describe("useReelsCommerce — a dismissal sticks", () => {
  it("removes the chip on hide and does not put another one anywhere", async () => {
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS }));
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
    const chip = result.current.chipByReelId.get(CARRIER)!;
    act(() => result.current.onFeedback(chip, "hide"));
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(0));
  });

  it("keeps the chip hidden even when the preference failed to save", async () => {
    // Reverting would turn a lost preference into a visible lie, and leave an
    // undismissable product on someone's video.
    feedback.mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS }));
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
    const chip = result.current.chipByReelId.get(CARRIER)!;
    act(() => result.current.onFeedback(chip, "hide"));
    await waitFor(() => expect(feedback).toHaveBeenCalled());
    expect(result.current.chipByReelId.size).toBe(0);
  });

  it.each(["hide", "not_interested", "see_fewer", "hide_seller"] as const)(
    "sends %p through with its own verb rather than collapsing every hide into one",
    async (action) => {
      // Four different durable preferences. A menu that hides the chip for all
      // four but posts `hide` every time looks perfect and quietly discards the
      // signal the whole feedback loop is built on.
      const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS }));
      await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
      const chip = result.current.chipByReelId.get(CARRIER)!;
      act(() => result.current.onFeedback(chip, action));
      await waitFor(() => expect(feedback).toHaveBeenCalled());
      expect(feedback.mock.calls[0][1]).toBe(action);
    }
  );

  it("hides a whole seller, not just the one placement", async () => {
    const { result, rerender } = renderHook(({ ids }) => useReelsCommerce({ reelIds: ids }), {
      initialProps: { ids: [...REEL_IDS] }
    });
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
    const chip = result.current.chipByReelId.get(CARRIER)!;
    act(() => result.current.onFeedback(chip, "hide_seller"));
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(0));
    // A different placement from the same seller must not reappear on the next
    // list either — the mute is about the store, not about this one product.
    rerender({ ids: ["x1", ...REEL_IDS] });
    expect(result.current.chipByReelId.size).toBe(0);
  });
});

describe("useReelsCommerce — a snooze wins the race", () => {
  it("empties the surface and a response already in flight cannot undo it", async () => {
    // The user asked for no Marketplace suggestions. A serve request that was
    // already on the wire must not put one back a moment later.
    let resolveServe: (value: CommerceServeResult) => void = () => undefined;
    fetchPlacements.mockReturnValue(
      new Promise<CommerceServeResult>((resolve) => {
        resolveServe = resolve;
      })
    );
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS }));
    act(() => result.current.onFeedback(placement("p9"), "snooze"));
    await act(async () => {
      resolveServe(served([placement("p1")]));
    });
    expect(result.current.chipByReelId.size).toBe(0);
  });

  it("records the snooze so the server holds the durable copy", async () => {
    const { result } = renderHook(() => useReelsCommerce({ reelIds: REEL_IDS }));
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
    const chip = result.current.chipByReelId.get(CARRIER)!;
    act(() => result.current.onFeedback(chip, "snooze"));
    await waitFor(() => expect(feedback).toHaveBeenCalledWith(chip, "snooze"));
  });
});

describe("useReelsCommerce — refresh", () => {
  it("refetches when the screen pulls to refresh", async () => {
    const { result, rerender } = renderHook(
      ({ token }) => useReelsCommerce({ reelIds: REEL_IDS, refreshToken: token }),
      { initialProps: { token: 0 } }
    );
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(1));
    rerender({ token: 1 });
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
  });

  it("stops carrying session-local dismissals about reels that are gone", async () => {
    // Clearing cannot un-hide anything — the server holds the durable copy and
    // will not serve a hidden placement again. It only stops two sets growing
    // for the life of the process.
    const { result, rerender } = renderHook(
      ({ token }) => useReelsCommerce({ reelIds: REEL_IDS, refreshToken: token }),
      { initialProps: { token: 0 } }
    );
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
    const chip = result.current.chipByReelId.get(CARRIER)!;
    act(() => result.current.onFeedback(chip, "hide"));
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(0));

    fetchPlacements.mockResolvedValue(served([placement("p2")]));
    rerender({ token: 1 });
    await waitFor(() => expect(result.current.chipByReelId.size).toBe(1));
  });
});
