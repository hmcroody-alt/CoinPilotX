/**
 * Offer state machine tests.
 *
 * No React, no renderer, no fake timers — the module under test takes `now` as a
 * parameter precisely so that time can be a number in a test rather than a
 * mocked global. Each test names a rule from the brief.
 */

import {
  MARKETPLACE_BOOST_ENABLED,
  MARKETPLACE_CART_ENABLED,
  MARKETPLACE_OFFERS_ENABLED,
  OFFER_TTL_HOURS,
  allowedActions,
  applyOfferAction,
  applyOfferActionToList,
  beginOfferAction,
  endOfferAction,
  isOfferFresh,
  isTerminal,
  offerActionsDisabled,
  offerExpiresAt,
  offersAwaitingSeller,
  resolveExpiries,
  resolveExpiry,
  type MarketplaceOffer
} from "../marketplaceOffers";

const T0 = 1_700_000_000_000;
const HOUR = 60 * 60 * 1000;

function makeOffer(overrides: Partial<MarketplaceOffer> = {}): MarketplaceOffer {
  return {
    id: "offer-1",
    listingId: "listing-1",
    amountMinor: 9500,
    currency: "USD",
    listPriceMinor: 12000,
    direction: "buyer_to_seller",
    state: "open",
    createdAt: T0,
    updatedAt: T0,
    buyerName: "Dana",
    itemTitle: "Oak dining table",
    ...overrides
  };
}

const ids = () => {
  let n = 0;
  return () => `counter-${(n += 1)}`;
};

describe("feature flags", () => {
  it("matches each flag to whether a backend exists", () => {
    // The brief: build the UI behind a flag, do not fake a working checkout.
    // Offers and cart are ON because their route packs exist
    // (`services/marketplace_offers_routes.py`, `services/marketplace_cart_routes.py`,
    // registered in bot.py; clients in `../marketplaceCommerce`). Boost still
    // has no purchase backend — if someone flips it on without landing an
    // endpoint, this test is the thing that says so.
    expect(MARKETPLACE_OFFERS_ENABLED).toBe(true);
    expect(MARKETPLACE_CART_ENABLED).toBe(true);
    expect(MARKETPLACE_BOOST_ENABLED).toBe(false);
  });
});

describe("permissions", () => {
  it("lets the recipient accept, counter or decline", () => {
    expect(allowedActions(makeOffer(), "seller")).toEqual(["accept", "counter", "decline"]);
  });

  it("lets the sender only withdraw", () => {
    expect(allowedActions(makeOffer(), "buyer")).toEqual(["withdraw"]);
  });

  it("flips with direction, so a seller-to-buyer counter is the buyer's to answer", () => {
    const offer = makeOffer({ direction: "seller_to_buyer" });
    expect(allowedActions(offer, "buyer")).toEqual(["accept", "counter", "decline"]);
    expect(allowedActions(offer, "seller")).toEqual(["withdraw"]);
  });

  it("offers nothing on a settled offer", () => {
    expect(allowedActions(makeOffer({ state: "accepted" }), "seller")).toEqual([]);
  });

  it("refuses an action the actor may not take", () => {
    const result = applyOfferAction(makeOffer(), { action: "accept", actor: "buyer", now: T0 });
    expect(result).toMatchObject({ ok: false, reason: "not_permitted" });
  });
});

describe("terminal transitions", () => {
  it.each([
    ["accept", "seller", "accepted"],
    ["decline", "seller", "declined"],
    ["withdraw", "buyer", "withdrawn"]
  ] as const)("%s moves an open offer to %s", (action, actor, state) => {
    const result = applyOfferAction(makeOffer(), { action, actor, now: T0 + 60_000 });
    expect(result.ok).toBe(true);
    expect(result.offer.state).toBe(state);
    expect(result.offer.updatedAt).toBe(T0 + 60_000);
  });

  it("does not mutate its input", () => {
    const offer = makeOffer();
    applyOfferAction(offer, { action: "accept", actor: "seller", now: T0 });
    expect(offer.state).toBe("open");
  });

  it("marks every non-open state terminal", () => {
    expect(isTerminal("open")).toBe(false);
    (["accepted", "countered", "declined", "expired", "withdrawn"] as const).forEach((state) =>
      expect(isTerminal(state)).toBe(true)
    );
  });
});

describe("counter", () => {
  it("closes the original and opens a new offer the other way", () => {
    const result = applyOfferAction(makeOffer(), {
      action: "counter",
      actor: "seller",
      now: T0 + HOUR,
      counterAmountMinor: 11000,
      makeId: ids()
    });

    expect(result.ok).toBe(true);
    if (!result.ok) return;
    expect(result.offer.state).toBe("countered");
    expect(result.created).toBeDefined();
    expect(result.created).toMatchObject({
      id: "counter-1",
      state: "open",
      direction: "seller_to_buyer",
      amountMinor: 11000,
      counterOf: "offer-1",
      createdAt: T0 + HOUR
    });
  });

  it("keeps the buyer's amount intact on the closed row — a counter is not an edit", () => {
    const result = applyOfferAction(makeOffer(), {
      action: "counter",
      actor: "seller",
      now: T0,
      counterAmountMinor: 11000,
      makeId: ids()
    });
    expect(result.ok && result.offer.amountMinor).toBe(9500);
  });

  it("restarts the clock on the counter", () => {
    const result = applyOfferAction(makeOffer(), {
      action: "counter",
      actor: "seller",
      now: T0 + 10 * HOUR,
      counterAmountMinor: 11000,
      makeId: ids()
    });
    expect(result.ok && result.created && offerExpiresAt(result.created)).toBe(
      T0 + 10 * HOUR + OFFER_TTL_HOURS * HOUR
    );
  });

  it.each([[undefined], [0], [-500], [95.5], [Number.NaN]])(
    "rejects the counter amount %p rather than clamping it",
    (amount) => {
      const result = applyOfferAction(makeOffer(), {
        action: "counter",
        actor: "seller",
        now: T0,
        counterAmountMinor: amount as number | undefined
      });
      expect(result).toMatchObject({ ok: false, reason: "invalid_amount" });
    }
  );
});

describe("idempotence", () => {
  it("returns already_resolved instead of throwing on a repeat accept", () => {
    const accepted = makeOffer({ state: "accepted" });
    const result = applyOfferAction(accepted, { action: "accept", actor: "seller", now: T0 });
    expect(result).toMatchObject({ ok: false, reason: "already_resolved" });
    expect(result.offer.state).toBe("accepted");
  });

  it("never reopens a settled offer, whatever is applied to it", () => {
    (["accepted", "declined", "countered", "withdrawn", "expired"] as const).forEach((state) => {
      (["accept", "counter", "decline", "withdraw"] as const).forEach((action) => {
        const result = applyOfferAction(makeOffer({ state }), {
          action,
          actor: "seller",
          now: T0,
          counterAmountMinor: 10000
        });
        expect(result.ok).toBe(false);
        expect(result.offer.state).toBe(state);
      });
    });
  });
});

describe("expiry", () => {
  it("lapses an open offer exactly at the TTL boundary", () => {
    const offer = makeOffer();
    expect(resolveExpiry(offer, T0 + OFFER_TTL_HOURS * HOUR - 1).state).toBe("open");
    expect(resolveExpiry(offer, T0 + OFFER_TTL_HOURS * HOUR).state).toBe("expired");
  });

  it("dates the expiry at the lapse, not at the moment it was noticed", () => {
    const resolved = resolveExpiry(makeOffer(), T0 + 200 * HOUR);
    expect(resolved.updatedAt).toBe(T0 + OFFER_TTL_HOURS * HOUR);
  });

  it("does not expire an offer that was already settled", () => {
    const accepted = makeOffer({ state: "accepted" });
    expect(resolveExpiry(accepted, T0 + 500 * HOUR)).toBe(accepted);
  });

  it("refuses an accept on an offer that lapsed while the list sat on screen", () => {
    const result = applyOfferAction(makeOffer(), {
      action: "accept",
      actor: "seller",
      now: T0 + OFFER_TTL_HOURS * HOUR + 1
    });
    expect(result).toMatchObject({ ok: false, reason: "expired" });
    expect(result.offer.state).toBe("expired");
  });

  it("returns the same array when a sweep changes nothing", () => {
    const offers = [makeOffer(), makeOffer({ id: "offer-2" })];
    expect(resolveExpiries(offers, T0 + HOUR)).toBe(offers);
  });

  it("returns a new array when a sweep lapses something", () => {
    const offers = [makeOffer()];
    const swept = resolveExpiries(offers, T0 + 100 * HOUR);
    expect(swept).not.toBe(offers);
    expect(swept[0].state).toBe("expired");
  });
});

describe("freshness", () => {
  it("is fresh under 30 minutes and stale after", () => {
    expect(isOfferFresh(makeOffer(), T0 + 29 * 60_000)).toBe(true);
    expect(isOfferFresh(makeOffer(), T0 + 31 * 60_000)).toBe(false);
  });

  it("is never fresh once settled", () => {
    expect(isOfferFresh(makeOffer({ state: "accepted" }), T0)).toBe(false);
  });
});

describe("double-tap protection", () => {
  it("stamps the first press and refuses the second", () => {
    const first = beginOfferAction(makeOffer(), "accept");
    expect(first.ok).toBe(true);
    if (!first.ok) return;
    expect(first.offer.pending).toBe("accept");

    const second = beginOfferAction(first.offer, "accept");
    expect(second).toMatchObject({ ok: false, reason: "in_flight" });
  });

  it("disables all three buttons while any one action is in flight", () => {
    const pending = beginOfferAction(makeOffer(), "decline");
    expect(pending.ok && offerActionsDisabled(pending.offer)).toBe(true);
  });

  it("blocks a competing action, not just a repeat of the same one", () => {
    const pending = beginOfferAction(makeOffer(), "accept");
    expect(pending.ok).toBe(true);
    if (!pending.ok) return;
    const decline = applyOfferAction(pending.offer, {
      action: "decline",
      actor: "seller",
      now: T0
    });
    expect(decline).toMatchObject({ ok: false, reason: "in_flight" });
  });

  it("clears the stamp so a failed request can be retried", () => {
    const pending = beginOfferAction(makeOffer(), "accept");
    expect(pending.ok).toBe(true);
    if (!pending.ok) return;
    const cleared = endOfferAction(pending.offer);
    expect(cleared.pending).toBeNull();
    expect(offerActionsDisabled(cleared)).toBe(false);
  });

  it("leaves a settled row disabled even with no action in flight", () => {
    expect(offerActionsDisabled(makeOffer({ state: "accepted" }))).toBe(true);
  });
});

describe("list reducer", () => {
  it("inserts the counter directly after the offer it answers", () => {
    const offers = [
      makeOffer({ id: "a" }),
      makeOffer({ id: "b" }),
      makeOffer({ id: "c" })
    ];
    const { offers: next } = applyOfferActionToList(offers, "a", {
      action: "counter",
      actor: "seller",
      now: T0,
      counterAmountMinor: 11000,
      makeId: ids()
    });
    expect(next.map((o) => o.id)).toEqual(["a", "counter-1", "b", "c"]);
    expect(next[0].state).toBe("countered");
  });

  it("reports a missing id without altering the list", () => {
    const offers = [makeOffer()];
    const { offers: next, result } = applyOfferActionToList(offers, "nope", {
      action: "accept",
      actor: "seller",
      now: T0
    });
    expect(result).toBeNull();
    expect(next).toEqual(offers);
  });

  it("writes back an expiry discovered at action time even though the action failed", () => {
    const { offers: next, result } = applyOfferActionToList([makeOffer()], "offer-1", {
      action: "accept",
      actor: "seller",
      now: T0 + 100 * HOUR
    });
    expect(result).toMatchObject({ ok: false, reason: "expired" });
    expect(next[0].state).toBe("expired");
  });
});

describe("seller queue", () => {
  it("lists only open buyer offers, freshest first", () => {
    const offers = [
      makeOffer({ id: "old", createdAt: T0 - 2 * HOUR }),
      makeOffer({ id: "new", createdAt: T0 - 1 * HOUR }),
      makeOffer({ id: "settled", state: "declined" }),
      makeOffer({ id: "outbound", direction: "seller_to_buyer" }),
      makeOffer({ id: "lapsed", createdAt: T0 - 100 * HOUR })
    ];
    expect(offersAwaitingSeller(offers, T0).map((o) => o.id)).toEqual(["new", "old"]);
  });
});
