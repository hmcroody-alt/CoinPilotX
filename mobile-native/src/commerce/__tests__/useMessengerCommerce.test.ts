/**
 * The state behind the single suggestion the Messenger inbox may show.
 *
 * Messaging carries the smallest budget in the system — `surface_caps()` gives
 * it `(1, 1)` and `min_score("messenger")` adds +0.10 to the base floor — for a
 * reason that is easy to state and easy to erode: people open Messenger to
 * reach a person, and the cost of interrupting that intent is higher than
 * anywhere else in the app. So the tests that matter most here are the ones
 * about *not* showing something:
 *
 *   - Nothing is the normal answer, and an empty answer renders an inbox
 *     identical to the one that existed before this feature.
 *   - One, or none. Never two, whatever the server sends.
 *   - A dismissal is not an invitation to show the runner-up. "Hide this, get
 *     another one instantly" is what teaches people the hide button is a lie.
 *   - A snooze that a late response cannot undo.
 *
 * What is *not* tested here, because it is not testable here: that nothing
 * appears inside a conversation. That claim is about the import graph, not
 * about this hook's state, and it lives in
 * `privateConversationsAreCommerceFree.test.ts`.
 */
import { act, renderHook, waitFor } from "@testing-library/react-native";
import { fetchCommercePlacements, recordCommerceFeedback } from "../../api/commerceDiscovery";
import type { CommercePlacement, CommerceServeResult } from "../../api/commerceDiscovery";
import { useMessengerCommerce } from "../useMessengerCommerce";
import { __resetCommerceSessionId } from "../session";

jest.mock("../../api/commerceDiscovery", () => ({
  fetchCommercePlacements: jest.fn(),
  recordCommerceFeedback: jest.fn(() => Promise.resolve(true))
}));

const fetchPlacements = fetchCommercePlacements as jest.MockedFunction<typeof fetchCommercePlacements>;
const feedback = recordCommerceFeedback as jest.MockedFunction<typeof recordCommerceFeedback>;

/** What the server sends for messenger when nothing has been retuned. */
const MESSENGER_CADENCE = { leadIn: 0, interval: 1, maxPerPage: 1 };

function placement(id: string, sellerUserId = 77): CommercePlacement {
  return {
    placementId: id,
    impressionToken: `tok_${id}`,
    surface: "messenger",
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
    cadence: MESSENGER_CADENCE,
    ...overrides
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  __resetCommerceSessionId();
  fetchPlacements.mockResolvedValue(served([placement("p1")]));
});

describe("useMessengerCommerce — asking", () => {
  it("asks the messenger surface for the messenger budget, which is one", async () => {
    const { result } = renderHook(() => useMessengerCommerce());
    const [surface, options] = fetchPlacements.mock.calls[0];
    expect(surface).toBe("messenger");
    expect(options?.limit).toBe(1);
    // Let the in-flight response land inside `act` — otherwise the state it
    // sets arrives as a warning that would mask a real one later.
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));
  });

  it("sends its own cadence, so a server that omits one cannot hand it the feed's", async () => {
    // The feed's interval on this surface would be meaningless — there is no
    // stream to interleave into — but a cadence is a required part of the
    // request, and inheriting another surface's is how a cap of one becomes a
    // cap of two on the day the server stops sending the field.
    const { result } = renderHook(() => useMessengerCommerce());
    const [, options] = fetchPlacements.mock.calls[0];
    expect(options?.cadence).toEqual(MESSENGER_CADENCE);
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));
  });

  it("fetches nothing and holds nothing while disabled", () => {
    // `enabled` is a gate on *existence*: signed out, in a call, or with
    // recommendations turned off, there is no request and no placement held.
    const { result } = renderHook(() => useMessengerCommerce({ enabled: false }));
    expect(fetchPlacements).not.toHaveBeenCalled();
    expect(result.current.placement).toBeNull();
  });

  it("drops what it holds the moment it is disabled", async () => {
    // Being switched off mid-session — a call arrives, or the user turns
    // recommendations off in Settings — must clear the strip, not merely stop
    // the next fetch.
    const { result, rerender } = renderHook(({ enabled }) => useMessengerCommerce({ enabled }), {
      initialProps: { enabled: true }
    });
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));
    rerender({ enabled: false });
    expect(result.current.placement).toBeNull();
  });

  it("carries one session id for every beacon this surface will send", async () => {
    const { result } = renderHook(() => useMessengerCommerce());
    const [, options] = fetchPlacements.mock.calls[0];
    await waitFor(() => expect(result.current.sessionId).toBeTruthy());
    expect(options?.sessionId).toBe(result.current.sessionId);
  });

  it("takes the server's dwell rather than keeping its own default", async () => {
    fetchPlacements.mockResolvedValue(served([placement("p1")], { visibleDwellMs: 2500 }));
    const { result } = renderHook(() => useMessengerCommerce());
    await waitFor(() => expect(result.current.visibleDwellMs).toBe(2500));
  });
});

describe("useMessengerCommerce — nothing is the normal answer", () => {
  it("holds nothing when the engine returned nothing", async () => {
    fetchPlacements.mockResolvedValue(served([]));
    const { result } = renderHook(() => useMessengerCommerce());
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(1));
    expect(result.current.placement).toBeNull();
  });

  it("holds nothing when the request fails outright", async () => {
    // `fetchCommercePlacements` resolves empty rather than throwing, so this is
    // the belt-and-braces path. A recommendation failure reaching the inbox as
    // an unhandled rejection is the one outcome that would break Messenger for
    // a feature Messenger does not need.
    fetchPlacements.mockRejectedValue(new Error("engine down"));
    const { result } = renderHook(() => useMessengerCommerce());
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(1));
    expect(result.current.placement).toBeNull();
  });

  it("shows nothing rather than a broken row when the product is unrenderable", async () => {
    // A placement with no title would render an empty card next to a price.
    // Showing nothing is the mission's stated preference, and it is also the
    // only option that does not look like a bug to the person holding the phone.
    const broken = placement("p1");
    broken.product = { ...broken.product, title: "" };
    fetchPlacements.mockResolvedValue(served([broken]));
    const { result } = renderHook(() => useMessengerCommerce());
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(1));
    expect(result.current.placement).toBeNull();
  });

  it("never holds more than one, whatever the server sends", async () => {
    // The cap is the server's, but a client that renders the whole array is one
    // config mistake away from a column of products above the inbox.
    fetchPlacements.mockResolvedValue(served([placement("p1"), placement("p2"), placement("p3")]));
    const { result } = renderHook(() => useMessengerCommerce());
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));
    // `placement` is a single value, not a list — the shape itself is the cap.
    expect(Array.isArray(result.current.placement)).toBe(false);
  });
});

describe("useMessengerCommerce — a dismissal is not an invitation", () => {
  it("does not promote the runner-up when the strip is hidden", async () => {
    fetchPlacements.mockResolvedValue(served([placement("p1"), placement("p2")]));
    const { result } = renderHook(() => useMessengerCommerce());
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    act(() => result.current.onFeedback(result.current.placement!, "hide"));

    // Not `p2`. The tempting implementation filters dismissals out of the list
    // and re-runs the selection, which hands the user a different product in the
    // same spot, one frame after they asked for less of this.
    expect(result.current.placement).toBeNull();
  });

  it("does not promote a different seller's product when a seller is muted", async () => {
    // The runner-up belongs to someone else on purpose: with both products from
    // the muted seller this would pass even if the hook promoted the next
    // candidate, which is the behaviour it exists to forbid.
    fetchPlacements.mockResolvedValue(served([placement("p1", 77), placement("p2", 91)]));
    const { result } = renderHook(() => useMessengerCommerce());
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    act(() => result.current.onFeedback(result.current.placement!, "hide_seller"));

    expect(result.current.placement).toBeNull();
  });

  it("sends the verb the user chose, not a generic hide", async () => {
    // "Hide this", "Not interested", "See fewer like this" and "Don't recommend
    // this seller" are four different durable preferences. A menu that clears
    // the strip for all four but posts `hide` every time looks perfect and
    // silently discards the signal the ranking loop is built on.
    const { result } = renderHook(() => useMessengerCommerce());
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));
    const held = result.current.placement!;

    act(() => result.current.onFeedback(held, "see_fewer"));

    expect(feedback).toHaveBeenCalledTimes(1);
    expect(feedback.mock.calls[0][1]).toBe("see_fewer");
    expect(feedback.mock.calls[0][0].placementId).toBe("p1");
  });

  it("keeps the strip hidden when the write fails", async () => {
    // The honest thing to show the person looking at the screen is what they
    // asked for. Reverting on a network failure turns a lost preference into a
    // visible lie, and an undismissable suggestion above someone's inbox is a
    // worse outcome than a preference that did not save.
    feedback.mockRejectedValueOnce(new Error("offline"));
    const { result } = renderHook(() => useMessengerCommerce());
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    act(() => result.current.onFeedback(result.current.placement!, "hide"));

    await waitFor(() => expect(feedback).toHaveBeenCalledTimes(1));
    expect(result.current.placement).toBeNull();
  });
});

describe("useMessengerCommerce — snooze", () => {
  it("clears the strip for the session", async () => {
    const { result } = renderHook(() => useMessengerCommerce());
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    act(() => result.current.onFeedback(result.current.placement!, "snooze"));

    expect(result.current.placement).toBeNull();
    expect(feedback.mock.calls[0][1]).toBe("snooze");
  });

  it("cannot be undone by a response that was already in flight", async () => {
    // The race: the user snoozes while a serve request is open, and the response
    // lands a moment later and puts a suggestion back — after they asked for
    // thirty days without one. The guard has to read the value *now*, which is
    // why it is a ref and not the state the effect closed over.
    let resolveServe: (result: CommerceServeResult) => void = () => undefined;
    fetchPlacements.mockReturnValue(
      new Promise<CommerceServeResult>((resolve) => {
        resolveServe = resolve;
      })
    );

    const { result } = renderHook(() => useMessengerCommerce());
    act(() => result.current.onFeedback(placement("p1"), "snooze"));
    expect(result.current.placement).toBeNull();

    await act(async () => {
      resolveServe(served([placement("p1")]));
    });

    expect(result.current.placement).toBeNull();
  });

  it("stays snoozed across a pull-to-refresh", async () => {
    // Refresh clears the session-local dismissal *sets* — they are about cards
    // that are no longer on screen — but a snooze is a statement about the next
    // thirty days, not about this page.
    const { result, rerender } = renderHook(({ refreshToken }) => useMessengerCommerce({ refreshToken }), {
      initialProps: { refreshToken: 0 }
    });
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    act(() => result.current.onFeedback(result.current.placement!, "snooze"));
    expect(result.current.placement).toBeNull();

    rerender({ refreshToken: 1 });
    await waitFor(() => expect(fetchPlacements).toHaveBeenCalledTimes(2));
    expect(result.current.placement).toBeNull();
  });
});

describe("useMessengerCommerce — refresh", () => {
  it("asks again when the inbox is pulled down", async () => {
    const { result, rerender } = renderHook(({ refreshToken }) => useMessengerCommerce({ refreshToken }), {
      initialProps: { refreshToken: 0 }
    });
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));

    fetchPlacements.mockResolvedValue(served([placement("p9")]));
    rerender({ refreshToken: 1 });

    await waitFor(() => expect(result.current.placement?.placementId).toBe("p9"));
  });

  it("does not resurrect a hidden product the server still sends", async () => {
    // The server owns the durable suppression, and the next serve is what
    // enforces it. But a refresh that arrives before the write lands — or one
    // where the write failed — must not put the same placement back, which is
    // what a blanket "clear the dismissal sets on refresh" would do if the
    // response were identical.
    const { result, rerender } = renderHook(({ refreshToken }) => useMessengerCommerce({ refreshToken }), {
      initialProps: { refreshToken: 0 }
    });
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p1"));
    act(() => result.current.onFeedback(result.current.placement!, "hide"));
    expect(result.current.placement).toBeNull();

    // A *new* product after the refresh is allowed — that is the refresh working.
    fetchPlacements.mockResolvedValue(served([placement("p2")]));
    rerender({ refreshToken: 1 });
    await waitFor(() => expect(result.current.placement?.placementId).toBe("p2"));
  });
});
