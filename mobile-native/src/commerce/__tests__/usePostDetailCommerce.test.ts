/**
 * The state behind the single product card a post's own screen may show.
 *
 * Most of this hook is deliberately the same as `useMessengerCommerce` — the
 * dismissal sets, the snooze race, the optimistic feedback write — and those
 * claims are tested there. What is tested *here* is what is different about this
 * surface, because each difference is load-bearing:
 *
 *   - **It waits for the post.** This is the only surface that always sends a
 *     ranking context, and `min_score("post_detail")` is a tenth above the feed's
 *     floor precisely for that reason. A request fired before the post arrived
 *     would be held to the raised floor while carrying none of the signal it was
 *     raised for, so it would mostly answer empty — and the card would be missing
 *     on exactly the slow connections where the screen sits visible longest. That
 *     failure is invisible: nothing throws, the response is merely emptier.
 *
 *   - **It omits the context rather than sending an empty one.** `relevance`
 *     answers NEUTRAL with no context and 0.0 for one that matches nothing. On a
 *     post with no readable subject those are the difference between a card
 *     chosen on history and quality alone, and no card at all.
 *
 *   - **It reads the master switch itself.** `post_detail` had to be added to
 *     `preferences.SOCIAL_SURFACES` server-side for the opt-out to cover it; this
 *     is the client half of the same promise, and it is the half a viewer would
 *     actually see fail.
 *
 *   - **It refetches per post, not per keystroke.** The context is derived from
 *     the post's text, so keying the fetch on the context's identity would refire
 *     on every edit — and on every render, since it is an object literal.
 */
import { act, renderHook, waitFor } from "@testing-library/react-native";
import { fetchCommercePlacements, recordCommerceFeedback } from "../../api/commerceDiscovery";
import type { CommercePlacement, CommerceServeResult } from "../../api/commerceDiscovery";
import type { PulsePost } from "../../api/feed";
import { registerCommercePauseWriter, setSocialDiscoveryAllowed } from "../consent";
import { usePostDetailCommerce } from "../usePostDetailCommerce";
import { __resetCommerceSessionId } from "../session";

jest.mock("../../api/commerceDiscovery", () => ({
  fetchCommercePlacements: jest.fn(),
  recordCommerceFeedback: jest.fn(() => Promise.resolve(true))
}));

const fetchPlacements = fetchCommercePlacements as jest.MockedFunction<typeof fetchCommercePlacements>;
const feedback = recordCommerceFeedback as jest.MockedFunction<typeof recordCommerceFeedback>;

/** The screen holds one post, so there is nothing for a card to be spaced against. */
const POST_DETAIL_CADENCE = { leadIn: 0, interval: 1, maxPerPage: 1 };

function post(overrides: Partial<PulsePost> = {}): PulsePost {
  return { id: 12, post_id: 12, body: "tried the new #sneakers today", ...overrides };
}

function placement(id: string, sellerUserId = 77): CommercePlacement {
  return {
    placementId: id,
    impressionToken: `tok_${id}`,
    surface: "post_detail",
    slot: 0,
    expiresAt: "",
    promotionClass: "organic",
    labelKey: "commerce:discovery.label.recommended",
    reason: "related_to_this_post",
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
    cadence: POST_DETAIL_CADENCE,
    ...overrides
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  __resetCommerceSessionId();
  // The module-level switch persists across tests in a file; a case that turns it
  // off would otherwise silently disable every case after it.
  setSocialDiscoveryAllowed(true);
  registerCommercePauseWriter(null);
  fetchPlacements.mockResolvedValue(served([placement("p1")]));
});

afterAll(() => {
  registerCommercePauseWriter(null);
});

describe("usePostDetailCommerce — asking", () => {
  it("asks the post_detail surface for its budget, which is one", async () => {
    const { result } = renderHook(() => usePostDetailCommerce({ post: post() }));
    const [surface, options] = fetchPlacements.mock.calls[0];
    expect(surface).toBe("post_detail");
    expect(options?.limit).toBe(1);
    expect(options?.cadence).toEqual(POST_DETAIL_CADENCE);
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));
  });

  it("carries one session id for every beacon this surface will send", async () => {
    const { result } = renderHook(() => usePostDetailCommerce({ post: post() }));
    const [, options] = fetchPlacements.mock.calls[0];
    await waitFor(() => expect(result.current.sessionId).toBeTruthy());
    expect(options?.sessionId).toBe(result.current.sessionId);
  });

  it("takes the server's dwell rather than keeping its own default", async () => {
    fetchPlacements.mockResolvedValue(served([placement("p1")], { visibleDwellMs: 2500 }));
    const { result } = renderHook(() => usePostDetailCommerce({ post: post() }));
    await waitFor(() => expect(result.current.visibleDwellMs).toBe(2500));
  });
});

describe("usePostDetailCommerce — waiting for the post", () => {
  it("sends nothing while the post is still loading", () => {
    // Null is also the answer for a post that failed to load or was deleted, and
    // a product suggestion under an error state is the worst version of this
    // surface.
    const { result } = renderHook(() => usePostDetailCommerce({ post: null }));
    expect(fetchPlacements).not.toHaveBeenCalled();
    expect(result.current.placement).toBeNull();
  });

  it("asks once the post arrives", async () => {
    const { result, rerender } = renderHook(
      ({ post: current }: { post: PulsePost | null }) => usePostDetailCommerce({ post: current }),
      { initialProps: { post: null as PulsePost | null } }
    );
    expect(fetchPlacements).not.toHaveBeenCalled();

    rerender({ post: post() });

    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));
    expect(fetchPlacements).toHaveBeenCalledTimes(1);
  });

  it("sends nothing for a post with no id", () => {
    // A post the normalizer could not identify cannot be the subject of a
    // `related_to_this_post` claim, and the impression would be unattributable.
    const { result } = renderHook(() =>
      usePostDetailCommerce({ post: post({ id: 0, post_id: 0 }) })
    );
    expect(fetchPlacements).not.toHaveBeenCalled();
    expect(result.current.placement).toBeNull();
  });

  it("fetches nothing and holds nothing while disabled", () => {
    const { result } = renderHook(() => usePostDetailCommerce({ post: post(), enabled: false }));
    expect(fetchPlacements).not.toHaveBeenCalled();
    expect(result.current.placement).toBeNull();
  });

  it("drops what it holds the moment it is disabled", async () => {
    const { result, rerender } = renderHook(
      ({ enabled }) => usePostDetailCommerce({ post: post(), enabled }),
      { initialProps: { enabled: true } }
    );
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));
    rerender({ enabled: false });
    expect(result.current.placement).toBeNull();
  });
});

describe("usePostDetailCommerce — the context", () => {
  it("sends what the post is about, derived from the post itself", async () => {
    const { result } = renderHook(() =>
      usePostDetailCommerce({ post: post({ title: "Sneaker haul", body: "the #sneakers arrived" }) })
    );
    const [, options] = fetchPlacements.mock.calls[0];
    expect(options?.context).toEqual({ topic: "Sneaker haul", tags: ["sneakers"] });
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));
  });

  it("omits the context rather than sending an empty one", async () => {
    // Not `context: {}`. An absent context scores NEUTRAL and an empty one scores
    // 0.0, and only one of those is an honest description of a post whose subject
    // we cannot read. On the surface with the highest floor in the app, the
    // difference is a card versus nothing.
    const { result } = renderHook(() => usePostDetailCommerce({ post: post({ body: "  " }) }));
    const [, options] = fetchPlacements.mock.calls[0];
    expect(options && "context" in options).toBe(false);
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));
  });

  it("never sends a category, because a post does not have one", async () => {
    const { result } = renderHook(() =>
      usePostDetailCommerce({ post: post({ title: "Sneaker haul" }) })
    );
    const [, options] = fetchPlacements.mock.calls[0];
    expect(Object.keys(options?.context || {}).sort()).toEqual(["tags", "topic"]);
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));
  });

  it("does not ask again when the same post re-renders with a new object", async () => {
    // The context is derived from the post's text, so keying the fetch on its
    // identity would refire on every edit to a caption -- and the dependency
    // would be an object literal that is a new reference on each render anyway.
    const { result, rerender } = renderHook(
      ({ post: current }: { post: PulsePost }) => usePostDetailCommerce({ post: current }),
      { initialProps: { post: post() } }
    );
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    rerender({ post: post({ body: "tried the new #sneakers today!" }) });

    expect(fetchPlacements).toHaveBeenCalledTimes(1);
  });

  it("asks again with the new context when the screen shows a different post", async () => {
    const { result, rerender } = renderHook(
      ({ post: current }: { post: PulsePost }) => usePostDetailCommerce({ post: current }),
      { initialProps: { post: post() } }
    );
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    fetchPlacements.mockResolvedValue(served([placement("p9")]));
    rerender({ post: post({ id: 13, post_id: 13, title: "Knife review", body: "" }) });

    await waitFor(() => expect(result.current.placement?.placementId).toBe("p9"));
    expect(fetchPlacements.mock.calls[1][1]?.context).toEqual({ topic: "Knife review" });
  });
});

describe("usePostDetailCommerce — the master switch", () => {
  it("sends nothing when the viewer has turned discovery off", () => {
    // The server half of this is `post_detail` being a member of
    // `preferences.SOCIAL_SURFACES`. This is the half the viewer would see fail:
    // a product card under a post after they switched suggestions off.
    setSocialDiscoveryAllowed(false);
    const { result } = renderHook(() => usePostDetailCommerce({ post: post() }));
    expect(fetchPlacements).not.toHaveBeenCalled();
    expect(result.current.placement).toBeNull();
  });

  it("drops the card when discovery is switched off mid-read", async () => {
    const { result } = renderHook(() => usePostDetailCommerce({ post: post() }));
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    act(() => setSocialDiscoveryAllowed(false));

    expect(result.current.placement).toBeNull();
  });
});

describe("usePostDetailCommerce — nothing is the normal answer", () => {
  it("holds nothing when the engine returned nothing", async () => {
    fetchPlacements.mockResolvedValue(served([]));
    const { result } = renderHook(() => usePostDetailCommerce({ post: post() }));
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(1));
    expect(result.current.placement).toBeNull();
  });

  it("holds nothing when the request fails outright", async () => {
    // Reading a post is not allowed to break because a recommendation lookup did.
    fetchPlacements.mockRejectedValue(new Error("engine down"));
    const { result } = renderHook(() => usePostDetailCommerce({ post: post() }));
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(1));
    expect(result.current.placement).toBeNull();
  });

  it("shows nothing rather than a broken card when the product is unrenderable", async () => {
    const broken = placement("p1");
    broken.product = { ...broken.product, title: "" };
    fetchPlacements.mockResolvedValue(served([broken]));
    const { result } = renderHook(() => usePostDetailCommerce({ post: post() }));
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(1));
    expect(result.current.placement).toBeNull();
  });
});

describe("usePostDetailCommerce — a dismissal is not an invitation", () => {
  it("does not promote the runner-up when the card is hidden", async () => {
    // The cap is one, so in practice there is no runner-up. This pins the
    // behaviour for the day an operator raises it: "hide this one, get the next
    // one immediately" is what teaches people the hide button is a lie.
    fetchPlacements.mockResolvedValue(served([placement("p1"), placement("p2")]));
    const { result } = renderHook(() => usePostDetailCommerce({ post: post() }));
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    act(() => result.current.onFeedback(result.current.placement!, "hide"));

    expect(result.current.placement).toBeNull();
  });

  it("does not promote a different seller's product when a seller is muted", async () => {
    // The runner-up belongs to someone else on purpose: with both from the muted
    // seller this would pass even if the hook promoted the next candidate.
    fetchPlacements.mockResolvedValue(served([placement("p1", 77), placement("p2", 91)]));
    const { result } = renderHook(() => usePostDetailCommerce({ post: post() }));
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    act(() => result.current.onFeedback(result.current.placement!, "hide_seller"));

    expect(result.current.placement).toBeNull();
  });

  it("sends the verb the user chose, not a generic hide", async () => {
    const { result } = renderHook(() => usePostDetailCommerce({ post: post() }));
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    act(() => result.current.onFeedback(result.current.placement!, "see_fewer"));

    expect(feedback).toHaveBeenCalledTimes(1);
    expect(feedback.mock.calls[0][1]).toBe("see_fewer");
    expect(feedback.mock.calls[0][0].placementId).toBe("p1");
  });

  it("keeps the card hidden when the write fails", async () => {
    // An undismissable card is a worse outcome than a preference that failed to
    // save, and reverting would turn a lost preference into a visible lie.
    feedback.mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => usePostDetailCommerce({ post: post() }));
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    act(() => result.current.onFeedback(result.current.placement!, "hide"));

    await waitFor(() => expect(feedback).toHaveBeenCalledTimes(1));
    expect(result.current.placement).toBeNull();
  });
});

describe("usePostDetailCommerce — snooze", () => {
  it("writes the pause to the preference document, not only to the server", async () => {
    // The server-side suppression row is invisible to the settings screen, which
    // would then report suggestions as on with no Resume anywhere -- a pause the
    // user started and cannot find.
    const writer = jest.fn();
    registerCommercePauseWriter(writer);
    const { result } = renderHook(() => usePostDetailCommerce({ post: post() }));
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    act(() => result.current.onFeedback(result.current.placement!, "snooze"));

    expect(result.current.placement).toBeNull();
    expect(feedback.mock.calls[0][1]).toBe("snooze");
    expect(writer).toHaveBeenCalledTimes(1);
    expect(Number.isFinite(new Date(writer.mock.calls[0][0]).getTime())).toBe(true);
  });

  it("cannot be undone by a response that was already in flight", async () => {
    // The race: the reader snoozes while the serve request is open, and the
    // response lands a moment later and puts a card back -- after they asked for
    // thirty days without one. The guard has to read the value *now*, which is
    // why it is a ref and not the state the effect closed over.
    let resolveServe: (result: CommerceServeResult) => void = () => undefined;
    fetchPlacements.mockReturnValue(
      new Promise<CommerceServeResult>((resolve) => {
        resolveServe = resolve;
      })
    );

    const { result } = renderHook(() => usePostDetailCommerce({ post: post() }));
    act(() => result.current.onFeedback(placement("p1"), "snooze"));
    expect(result.current.placement).toBeNull();

    await act(async () => {
      resolveServe(served([placement("p1")]));
    });

    expect(result.current.placement).toBeNull();
  });

  it("stays snoozed across a pull-to-refresh", async () => {
    // Refresh clears the session-local dismissal *sets* -- they are about cards
    // no longer on screen -- but a snooze is a statement about the next thirty
    // days, not about this read of this post.
    const { result, rerender } = renderHook(
      ({ refreshToken }) => usePostDetailCommerce({ post: post(), refreshToken }),
      { initialProps: { refreshToken: 0 } }
    );
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    act(() => result.current.onFeedback(result.current.placement!, "snooze"));
    expect(result.current.placement).toBeNull();

    rerender({ refreshToken: 1 });
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(2));
    expect(result.current.placement).toBeNull();
  });
});

describe("usePostDetailCommerce — refresh", () => {
  it("asks again when the post is pulled down", async () => {
    const { result, rerender } = renderHook(
      ({ refreshToken }) => usePostDetailCommerce({ post: post(), refreshToken }),
      { initialProps: { refreshToken: 0 } }
    );
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    fetchPlacements.mockResolvedValue(served([placement("p9")]));
    rerender({ refreshToken: 1 });

    await waitFor(() => expect(result.current.placement?.placementId).toBe("p9"));
  });

  it("does not resurrect a hidden product the server still sends", async () => {
    const { result, rerender } = renderHook(
      ({ refreshToken }) => usePostDetailCommerce({ post: post(), refreshToken }),
      { initialProps: { refreshToken: 0 } }
    );
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));
    act(() => result.current.onFeedback(result.current.placement!, "hide"));
    expect(result.current.placement).toBeNull();

    // A *new* product after the refresh is allowed -- that is the refresh working.
    fetchPlacements.mockResolvedValue(served([placement("p2")]));
    rerender({ refreshToken: 1 });
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p2"));
  });
});
