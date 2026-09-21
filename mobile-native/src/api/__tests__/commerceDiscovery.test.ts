/**
 * The wire contract, asserted in both directions.
 *
 * Every other test in this feature runs against mapped objects, so all of them
 * would stay green if this module read the wrong key off the response. That is
 * the failure this file exists for, and it has a particular shape here: the
 * server speaks snake_case and the client speaks camelCase, so a mismatch is
 * never a type error and never a crash — it is `undefined` coerced to a default,
 * which renders as a card with no rating, or a $0.00 price, or a beacon the
 * server files under a placement that does not exist.
 *
 * Three rules from the module header get their own blocks below, because each
 * one is a promise the rest of the system relies on:
 *
 *   - **Reads never throw.** "A recommendation failure must never break the
 *     feed" is only structural if the failure cannot escape this layer. Callers
 *     have no error branch precisely because there is nothing to branch on.
 *
 *   - **Writes never throw either**, and report a boolean instead. A dropped
 *     impression beacon is an analytics gap; a rejected promise in a `useEffect`
 *     is a red box.
 *
 *   - **`promotionClass` is carried, never inferred.** An unrecognised class
 *     falls back to organic labelling and never to "Sponsored". Calling unpaid
 *     reach an ad is the one mistake in this system that is a legal problem
 *     rather than a UX one — so the fallback direction is asserted explicitly.
 */
import { pulseApi } from "../pulseApi";
import {
  explainCommercePlacement,
  fetchCommerceModules,
  fetchCommercePlacements,
  isCommercePlacementExpired,
  recordCommerceEngagement,
  recordCommerceFeedback,
  recordCommerceImpression
} from "../commerceDiscovery";

jest.mock("../pulseApi", () => ({ pulseApi: jest.fn() }));

const api = pulseApi as jest.MockedFunction<typeof pulseApi>;

/** A complete server placement, in the server's own casing. */
function rawPlacement(overrides: Record<string, unknown> = {}) {
  return {
    placement_id: "pl_1",
    impression_token: "tok_1",
    surface: "feed",
    slot: 0,
    expires_at: "",
    promotion_class: "organic",
    label_key: "commerce:discovery.label.recommended",
    reason: "because_you_viewed",
    ranking_version: "commerce-discovery-v1",
    price_minor: 4999,
    price_currency: "USD",
    ...overrides,
    product: {
      listing_id: 77,
      title: "Women's Casual Sneakers",
      price_label: "$49.99",
      cover_image_url: "https://cdn.example/s.jpg",
      seller_user_id: 42,
      seller_store_name: "M&W Store",
      category: "shoes",
      rating: 4.8,
      rating_count: 120,
      ...((overrides.product as Record<string, unknown>) || {})
    }
  };
}

function serveResponse(placements: unknown[] = [rawPlacement()]) {
  return {
    ok: true,
    placements,
    visible_percent_threshold: 60,
    visible_dwell_ms: 1000
  };
}

/** The body of the nth call, parsed back out of the JSON the client sent. */
function sentBody(call = 0): Record<string, unknown> {
  const options = api.mock.calls[call][1] as { body?: string } | undefined;
  return JSON.parse(String(options?.body || "{}"));
}

beforeEach(() => {
  jest.clearAllMocks();
});

describe("fetchCommercePlacements — reading the server's casing", () => {
  it("maps every field of a placement rather than the ones that happen to match", async () => {
    api.mockResolvedValue(serveResponse() as never);
    const result = await fetchCommercePlacements("feed");

    expect(result.placements).toHaveLength(1);
    expect(result.placements[0]).toEqual({
      placementId: "pl_1",
      impressionToken: "tok_1",
      surface: "feed",
      slot: 0,
      expiresAt: "",
      promotionClass: "organic",
      labelKey: "commerce:discovery.label.recommended",
      reason: "because_you_viewed",
      rankingVersion: "commerce-discovery-v1",
      priceMinor: 4999,
      priceCurrency: "USD",
      product: {
        listingId: 77,
        title: "Women's Casual Sneakers",
        priceLabel: "$49.99",
        coverImageUrl: "https://cdn.example/s.jpg",
        sellerUserId: 42,
        sellerStoreName: "M&W Store",
        category: "shoes",
        rating: 4.8,
        ratingCount: 120
      }
    });
  });

  it("reads the viewability contract off the response, not off its own defaults", async () => {
    api.mockResolvedValue({ ...serveResponse(), visible_percent_threshold: 80, visible_dwell_ms: 2000 } as never);
    const result = await fetchCommercePlacements("feed");
    expect(result.visiblePercentThreshold).toBe(80);
    expect(result.visibleDwellMs).toBe(2000);
  });

  it("falls back to the documented defaults when the server omits them", async () => {
    api.mockResolvedValue({ ok: true, placements: [] } as never);
    const result = await fetchCommercePlacements("feed");
    expect(result.visiblePercentThreshold).toBe(60);
    expect(result.visibleDwellMs).toBe(1000);
  });

  it("sends context in the body, never in a query string", async () => {
    api.mockResolvedValue(serveResponse() as never);
    await fetchCommercePlacements("feed", {
      context: { postId: 9, authorId: 3 } as never,
      sessionId: "cs_abc",
      limit: 6
    });

    const [path, options] = api.mock.calls[0];
    // A query string is written to access logs, proxy caches and analytics
    // referrers. What the viewer is looking at this second does not belong in
    // any of them.
    expect(path).toBe("/api/pulse/commerce/discovery/feed");
    expect(path).not.toContain("?");
    expect(options?.method).toBe("POST");
    expect(sentBody()).toMatchObject({ session_id: "cs_abc", limit: 6 });
  });

  it("omits the limit entirely rather than sending a zero the server would honour", async () => {
    api.mockResolvedValue(serveResponse() as never);
    await fetchCommercePlacements("feed");
    expect(sentBody()).not.toHaveProperty("limit");
  });
});

describe("fetchCommercePlacements — dropping what cannot be rendered or reported", () => {
  it.each([
    ["no placement id", { placement_id: "" }],
    ["no impression token", { impression_token: "" }],
    ["no listing", { product: { listing_id: 0 } }],
    ["no title", { product: { title: "" } }],
    ["an expired ttl", { expires_at: new Date(Date.now() - 60_000).toISOString() }]
  ])("drops a placement with %s", async (_label, overrides) => {
    api.mockResolvedValue(serveResponse([rawPlacement(overrides)]) as never);
    const result = await fetchCommercePlacements("feed");
    expect(result.placements).toEqual([]);
  });

  it("keeps a placement whose ttl is in the future", async () => {
    api.mockResolvedValue(
      serveResponse([rawPlacement({ expires_at: new Date(Date.now() + 60_000).toISOString() })]) as never
    );
    const result = await fetchCommercePlacements("feed");
    expect(result.placements).toHaveLength(1);
  });

  it("keeps a placement whose ttl it cannot parse", () => {
    // Failing open here is deliberate: the server rejects a stale token anyway,
    // so the cost of a wrong guess is one failed beacon. Failing closed would
    // blank the whole surface on a date-format change.
    expect(isCommercePlacementExpired({ expiresAt: "not-a-date" })).toBe(false);
    expect(isCommercePlacementExpired({ expiresAt: "" })).toBe(false);
  });
});

describe("promotion class is carried, never inferred", () => {
  it("keeps a house promotion labelled as a house promotion", async () => {
    api.mockResolvedValue(
      serveResponse([
        rawPlacement({ promotion_class: "house", label_key: "commerce:discovery.label.trending" })
      ]) as never
    );
    const result = await fetchCommercePlacements("feed");
    expect(result.placements[0].promotionClass).toBe("house");
    expect(result.placements[0].labelKey).toBe("commerce:discovery.label.trending");
  });

  it.each(["paid", "sponsored", "", "SOMETHING_NEW"])(
    "falls back to organic, not to an ad label, for class %p",
    async (promotionClass) => {
      api.mockResolvedValue(serveResponse([rawPlacement({ promotion_class: promotionClass })]) as never);
      const result = await fetchCommercePlacements("feed");
      expect(result.placements[0].promotionClass).toBe("organic");
    }
  );

  it("defaults a missing label to a key that exists in the catalog", async () => {
    api.mockResolvedValue(serveResponse([rawPlacement({ label_key: "" })]) as never);
    const result = await fetchCommercePlacements("feed");
    // There is no `marketplace` i18n namespace — marketplace strings live under
    // `commerce.marketplace` — so a `marketplace:` default would render the raw
    // key string as the card's headline.
    expect(result.placements[0].labelKey).toBe("commerce:discovery.label.recommended");
    expect(result.placements[0].labelKey.startsWith("commerce:")).toBe(true);
  });
});

describe("reads never throw", () => {
  it.each([
    ["the request rejects", () => api.mockRejectedValue(new Error("offline"))],
    ["the server says not ok", () => api.mockResolvedValue({ ok: false } as never)],
    ["placements is missing", () => api.mockResolvedValue({ ok: true } as never)],
    ["placements is not a list", () => api.mockResolvedValue({ ok: true, placements: {} } as never)],
    ["the response is null", () => api.mockResolvedValue(null as never)]
  ])("returns an empty serve result when %s", async (_label, arrange) => {
    arrange();
    await expect(fetchCommercePlacements("feed")).resolves.toEqual({
      placements: [],
      visiblePercentThreshold: 60,
      visibleDwellMs: 1000,
      cadence: { leadIn: 6, interval: 8, maxPerPage: 2 }
    });
  });

  it("hands a failing surface back its own rhythm, not the feed's", async () => {
    // A caller that asked for the reels rhythm and got an error must not be
    // consoled with the feed's cadence — that is how a surface whose budget is
    // one ends up placing at feed density the moment the network hiccups. The
    // fallback is the one the caller supplied, on every failure path.
    api.mockRejectedValue(new Error("offline"));
    const reelsCadence = { leadIn: 4, interval: 10, maxPerPage: 1 };
    const result = await fetchCommercePlacements("reels", { cadence: reelsCadence });
    expect(result.placements).toEqual([]);
    expect(result.cadence).toEqual(reelsCadence);
  });

  it("returns no modules when the marketplace request fails", async () => {
    api.mockRejectedValue(new Error("offline"));
    await expect(fetchCommerceModules("cs_abc")).resolves.toEqual([]);
  });
});

describe("fetchCommerceModules", () => {
  it("drops a module that lost every item to filtering", async () => {
    api.mockResolvedValue({
      ok: true,
      modules: [
        { key: "trending", reason: "trending", title_key: "commerce:discovery.module.trending", placements: [rawPlacement()] },
        { key: "new_arrivals", reason: "new_arrival", title_key: "x", placements: [rawPlacement({ placement_id: "" })] },
        { key: "", reason: "", title_key: "", placements: [rawPlacement({ placement_id: "pl_2" })] }
      ]
    } as never);

    const modules = await fetchCommerceModules();
    // An empty shelf under a heading reads as a broken screen, and a module
    // with no key cannot be attributed in analytics.
    expect(modules.map((module) => module.key)).toEqual(["trending"]);
    expect(modules[0].titleKey).toBe("commerce:discovery.module.trending");
  });

  it("passes the session id as an encoded query on a GET", async () => {
    api.mockResolvedValue({ ok: true, modules: [] } as never);
    await fetchCommerceModules("cs a&b");
    const [path, options] = api.mock.calls[0];
    expect(options?.method).toBe("GET");
    expect(path).toBe("/api/pulse/commerce/discovery/marketplace/modules?session_id=cs%20a%26b");
  });
});

describe("writes speak the server's field names", () => {
  const identity = { placementId: "pl_1", impressionToken: "tok_1" };

  it("sends an impression as served, with no claim that it was seen", async () => {
    api.mockResolvedValue({ ok: true } as never);
    await recordCommerceImpression(identity, { visible: false });
    expect(api.mock.calls[0][0]).toBe("/api/pulse/commerce/discovery/events/impression");
    expect(sentBody()).toEqual({
      placement_id: "pl_1",
      impression_token: "tok_1",
      visible: false,
      view_duration_ms: 0
    });
  });

  it("rounds and floors the dwell rather than sending a fractional or negative one", async () => {
    api.mockResolvedValue({ ok: true } as never);
    await recordCommerceImpression(identity, { visible: true, viewDurationMs: 1234.7 });
    expect(sentBody()).toMatchObject({ visible: true, view_duration_ms: 1235 });

    api.mockClear();
    await recordCommerceImpression(identity, { visible: true, viewDurationMs: -5 });
    expect(sentBody()).toMatchObject({ view_duration_ms: 0 });
  });

  it("sends an engagement with its action and its optional value", async () => {
    api.mockResolvedValue({ ok: true } as never);
    await recordCommerceEngagement(identity, "purchase", {
      valueMinor: 4999,
      currency: "USD",
      orderRef: "ord_9"
    });
    expect(api.mock.calls[0][0]).toBe("/api/pulse/commerce/discovery/events/engagement");
    expect(sentBody()).toEqual({
      placement_id: "pl_1",
      impression_token: "tok_1",
      action: "purchase",
      value_minor: 4999,
      currency: "USD",
      order_ref: "ord_9"
    });
  });

  it("sends the feedback verb the user chose", async () => {
    api.mockResolvedValue({ ok: true, suppressed: true } as never);
    await recordCommerceFeedback(identity, "hide_seller");
    expect(api.mock.calls[0][0]).toBe("/api/pulse/commerce/discovery/events/feedback");
    expect(sentBody()).toMatchObject({ action: "hide_seller", category: "" });
  });

  it("reports feedback as unhonoured when the server accepted it but suppressed nothing", async () => {
    // `ok` alone means "the request was well-formed". The caller cares whether
    // the preference will actually keep the product away.
    api.mockResolvedValue({ ok: true, suppressed: false } as never);
    await expect(recordCommerceFeedback(identity, "hide")).resolves.toBe(false);
  });

  it.each([
    ["impression", () => recordCommerceImpression(identity)],
    ["engagement", () => recordCommerceEngagement(identity, "click")],
    ["feedback", () => recordCommerceFeedback(identity, "hide")]
  ])("returns false rather than throwing when the %s write fails", async (_label, write) => {
    api.mockRejectedValue(new Error("offline"));
    await expect(write()).resolves.toBe(false);
  });

  it.each([
    ["impression", () => recordCommerceImpression({ placementId: "", impressionToken: "" })],
    ["engagement", () => recordCommerceEngagement({ placementId: "", impressionToken: "" }, "click")],
    ["feedback", () => recordCommerceFeedback({ placementId: "", impressionToken: "" }, "hide")]
  ])("does not send a %s for a placement with no id", async (_label, write) => {
    await expect(write()).resolves.toBe(false);
    expect(api).not.toHaveBeenCalled();
  });
});

describe("explainCommercePlacement", () => {
  it("returns the reason and the factor names the server gave", async () => {
    api.mockResolvedValue({
      ok: true,
      reason: "because_you_viewed",
      factors: ["relevance", "freshness"],
      ranking_version: "commerce-discovery-v1"
    } as never);

    const explanation = await explainCommercePlacement({ placementId: "pl_1", impressionToken: "tok_1" });
    expect(explanation).toEqual({
      reason: "because_you_viewed",
      factors: ["relevance", "freshness"],
      rankingVersion: "commerce-discovery-v1"
    });
  });

  it("posts the token rather than putting a capability in a url", async () => {
    api.mockResolvedValue({ ok: true, reason: "", factors: [], ranking_version: "" } as never);
    await explainCommercePlacement({ placementId: "pl 1", impressionToken: "tok_1" });
    const [path, options] = api.mock.calls[0];
    expect(options?.method).toBe("POST");
    expect(path).toBe("/api/pulse/commerce/discovery/explain/pl%201");
    expect(sentBody()).toEqual({ impression_token: "tok_1" });
  });

  it.each([
    ["the server declines", () => api.mockResolvedValue({ ok: false } as never)],
    ["the request fails", () => api.mockRejectedValue(new Error("offline"))]
  ])("returns null when %s", async (_label, arrange) => {
    arrange();
    await expect(
      explainCommercePlacement({ placementId: "pl_1", impressionToken: "tok_1" })
    ).resolves.toBeNull();
  });
});
