/**
 * The Marketplace offer state machine.
 *
 * ## Why this is a pure module
 *
 * There is no offers backend. A search of `bot.py` finds no `marketplace_offers`
 * table, no `/api/pulse/marketplace/offers*` route, and no offer column on
 * `marketplace_listings`. The entire negotiation domain — buyer makes an offer,
 * seller accepts, counters or declines — exists only in the design.
 *
 * That makes the temptation obvious: put the transitions in the screen, wire the
 * three buttons to `setState`, and ship something that looks right. This module
 * exists to refuse that. The rules below are the part that will still be correct
 * when a real endpoint appears, so they are written once, here, with no React,
 * no navigation and no locale in them. The screen renders whatever
 * `applyOfferAction` returns; when the backend lands, the same function becomes
 * the optimistic-update reducer and its tests keep passing.
 *
 * ## The machine
 *
 *     open ──accept───► accepted   (terminal)
 *          ──decline──► declined   (terminal)
 *          ──withdraw─► withdrawn  (terminal, buyer-initiated)
 *          ──counter──► countered  (terminal for THIS offer; see below)
 *          ──(clock)──► expired    (terminal)
 *
 * Four of the five exits are ordinary terminal states. `counter` is the one that
 * does something structural: it closes the original as `countered` *and* creates
 * a new `open` offer travelling the other way. A counter is not an edit of the
 * buyer's offer — the seller cannot rewrite what the buyer said. It is a fresh
 * proposal, and modelling it as one is what makes the thread auditable: every
 * amount either side ever named survives in the chain, each row pointing at its
 * parent through `counterOf`.
 *
 * ## Idempotence and double-taps
 *
 * The brief asks for both, and they are different problems.
 *
 * *Idempotence* is about the machine: applying `accept` to an already-accepted
 * offer must not throw, must not double-count, and must not silently reopen a
 * closed row. `applyOfferAction` therefore never mutates and never throws — it
 * returns a discriminated result whose `ok: false` branch carries a `reason` the
 * UI can show. A retry after a dropped response lands on `already_resolved` and
 * the screen shows the settled state, which is the correct outcome.
 *
 * *Double-tap protection* is about the gap between the tap and the answer. That
 * is what `pending` is for: `beginOfferAction` stamps the offer with the action
 * in flight, and `offerActionsDisabled` returns true for the whole row while it
 * is set. All three buttons go down together on the first press, not just the
 * one pressed, because "Accept" and "Decline" racing each other is worse than
 * either being pressed twice.
 *
 * ## Expiry
 *
 * There is no existing TTL constant anywhere in the app — not in `bot.py`, not
 * in the API modules. `OFFER_TTL_HOURS = 72` below is therefore a proposal, and
 * it is flagged as one in the report rather than presented as a discovered fact.
 * Expiry is computed, never stored: `resolveExpiry` derives the state from the
 * clock at read time, so an offer that lapsed while the app was closed is
 * already expired the moment the list renders, with no sweeper job needed.
 */

/* ------------------------------------------------------------------ *
 * Feature flag
 * ------------------------------------------------------------------ */

/**
 * Offers are UI-only until an endpoint exists.
 *
 * The brief is explicit for exactly this case: "If offers, cart, or boost have
 * no backend at all, build the UI behind a feature flag and say so — do not fake
 * a working checkout." So the surface is built, and this constant keeps it dark.
 *
 * When it is false the Marketplace screen renders no offers section, no offer
 * button on grid cards, and no offers-waiting summary chip. Flipping it to true
 * without an endpoint gives a fully interactive negotiation flow whose accepts
 * reach nothing — which is useful for design review and catastrophic in
 * production, hence the wording here rather than a bare boolean.
 */
export const MARKETPLACE_OFFERS_ENABLED = false;

/**
 * Cart and checkout for Marketplace items.
 *
 * `openMarketplaceCheckout` in `./marketplace` posts to the real
 * `/api/pulse/payments/checkout` and returns a URL, so single-item purchase
 * works today. What does not exist is a *cart*: no basket endpoint, no line
 * items, no persisted quantity. The header badge and "Add to cart" therefore sit
 * behind this flag, and the brief's rule that purchase must "go through existing
 * payment/cart infrastructure — never a new payment path" is honoured by leaving
 * the single-item checkout as the only live route to payment.
 */
export const MARKETPLACE_CART_ENABLED = false;

/**
 * Boost — the seller-side purchase that buys a FEATURED badge.
 *
 * `marketplace_listings.featured` exists in the database and already drives
 * `ORDER BY l.featured DESC`, so the *effect* of a boost is real. What has no
 * backend is the *purchase*: nothing prices a boost, takes payment for one, or
 * sets the column from a client action. The promo card renders behind this flag.
 */
export const MARKETPLACE_BOOST_ENABLED = false;

/* ------------------------------------------------------------------ *
 * Types
 * ------------------------------------------------------------------ */

/** Every state an offer can be in. */
export type OfferState =
  | "open"
  | "accepted"
  | "countered"
  | "declined"
  | "expired"
  | "withdrawn";

/** The transitions a participant can request. */
export type OfferAction = "accept" | "counter" | "decline" | "withdraw";

/** Which side of the negotiation an offer came from. */
export type OfferDirection = "buyer_to_seller" | "seller_to_buyer";

export type MarketplaceOffer = {
  id: string;
  listingId: string;
  /** Minor units — cents, not dollars. Formatting is the caller's job. */
  amountMinor: number;
  currency: string;
  /** The listing's asking price at the time the offer was made. */
  listPriceMinor: number;
  direction: OfferDirection;
  state: OfferState;
  /** Epoch ms. */
  createdAt: number;
  /** Epoch ms of the last transition, or `createdAt` if still open. */
  updatedAt: number;
  /** Set when this offer was created by countering another. */
  counterOf?: string;
  /** Optional buyer note attached at creation. */
  message?: string;
  buyerName: string;
  buyerAvatarUrl?: string | null;
  itemTitle: string;
  itemThumbnailUrl?: string | null;
  /** The action currently in flight, if any. Drives double-tap suppression. */
  pending?: OfferAction | null;
};

/* ------------------------------------------------------------------ *
 * Expiry
 * ------------------------------------------------------------------ */

/**
 * How long an open offer stands before it lapses.
 *
 * PROPOSED, NOT DISCOVERED. No TTL constant exists anywhere in this codebase to
 * inherit, so 72h is a judgement: long enough that a seller who checks the app
 * every other day does not lose offers, short enough that a buyer is not bound
 * to a price for a week. Listed as an open question in the report.
 */
export const OFFER_TTL_HOURS = 72;

const HOUR_MS = 60 * 60 * 1000;

/** When an offer made at `createdAt` lapses. Epoch ms. */
export function offerExpiresAt(offer: MarketplaceOffer): number {
  return offer.createdAt + OFFER_TTL_HOURS * HOUR_MS;
}

/**
 * The offer's state as of `now`, with expiry applied.
 *
 * Only `open` offers can lapse — an accepted offer does not become expired
 * because time passed. Everything else is returned untouched.
 */
export function resolveExpiry(offer: MarketplaceOffer, now: number): MarketplaceOffer {
  if (offer.state !== "open") return offer;
  if (now < offerExpiresAt(offer)) return offer;
  return { ...offer, state: "expired", updatedAt: offerExpiresAt(offer), pending: null };
}

/**
 * True while the offer is fresh enough to deserve the pinging dot and the green
 * left edge. Thirty minutes, per the brief.
 */
export const OFFER_FRESH_MS = 30 * 60 * 1000;

export function isOfferFresh(offer: MarketplaceOffer, now: number): boolean {
  return offer.state === "open" && now - offer.createdAt < OFFER_FRESH_MS;
}

/* ------------------------------------------------------------------ *
 * Legality
 * ------------------------------------------------------------------ */

/** Terminal states — nothing can be applied to an offer in one of these. */
const TERMINAL: ReadonlySet<OfferState> = new Set<OfferState>([
  "accepted",
  "countered",
  "declined",
  "expired",
  "withdrawn"
]);

export function isTerminal(state: OfferState): boolean {
  return TERMINAL.has(state);
}

/**
 * Which actions each side may take on an open offer.
 *
 * The asymmetry is the point: the recipient can accept, counter or decline; the
 * sender can only withdraw. A seller cannot "withdraw" a buyer's offer, and a
 * buyer cannot "accept" their own — modelling both sides through one action set
 * would let the UI offer a button that means nothing.
 */
export function allowedActions(offer: MarketplaceOffer, actor: "buyer" | "seller"): OfferAction[] {
  if (isTerminal(offer.state)) return [];
  const isRecipient =
    (offer.direction === "buyer_to_seller" && actor === "seller") ||
    (offer.direction === "seller_to_buyer" && actor === "buyer");
  return isRecipient ? ["accept", "counter", "decline"] : ["withdraw"];
}

/* ------------------------------------------------------------------ *
 * Transitions
 * ------------------------------------------------------------------ */

export type OfferActionFailure =
  /** The offer already reached a terminal state. A retry lands here. */
  | "already_resolved"
  /** The offer lapsed before the action was applied. */
  | "expired"
  /** This actor cannot take this action on this offer. */
  | "not_permitted"
  /** `counter` was called without a counter amount, or with a nonsensical one. */
  | "invalid_amount"
  /** Another action on this offer is in flight. */
  | "in_flight";

export type OfferActionResult =
  | {
      ok: true;
      /** The original offer, moved to its new state. */
      offer: MarketplaceOffer;
      /** Present only for `counter`: the new open offer travelling back. */
      created?: MarketplaceOffer;
    }
  | { ok: false; reason: OfferActionFailure; offer: MarketplaceOffer };

export type ApplyOfferActionOptions = {
  action: OfferAction;
  actor: "buyer" | "seller";
  now: number;
  /** Required for `counter`. Minor units. */
  counterAmountMinor?: number;
  /** Optional note on a counter. */
  counterMessage?: string;
  /** Supplies the new offer's id on `counter`. Injected so tests are stable. */
  makeId?: () => string;
};

let counterSequence = 0;
function defaultMakeId(): string {
  counterSequence += 1;
  return `local-counter-${counterSequence}-${Date.now()}`;
}

/**
 * Apply an action to an offer.
 *
 * Never mutates its input and never throws. Every rejection is a value, because
 * the caller is a render tree: an exception here would have to be caught at a
 * button handler and turned back into a value anyway, and the version that
 * forgets the try/catch takes the screen down.
 */
export function applyOfferAction(
  offer: MarketplaceOffer,
  options: ApplyOfferActionOptions
): OfferActionResult {
  const { action, actor, now } = options;

  // Expiry is evaluated first. An offer that lapsed at 03:00 was not available
  // to accept at 09:00, regardless of what the list on screen still showed.
  const current = resolveExpiry(offer, now);
  if (current.state === "expired" && offer.state === "open") {
    return { ok: false, reason: "expired", offer: current };
  }
  if (isTerminal(current.state)) {
    return { ok: false, reason: "already_resolved", offer: current };
  }
  if (current.pending) {
    return { ok: false, reason: "in_flight", offer: current };
  }
  if (!allowedActions(current, actor).includes(action)) {
    return { ok: false, reason: "not_permitted", offer: current };
  }

  if (action === "counter") {
    const amount = options.counterAmountMinor;
    // A counter of zero, a negative, or a non-integer is not a slow path to
    // handle later — it is a bug in the sheet, and returning a value rather
    // than clamping means the sheet has to notice.
    if (amount == null || !Number.isFinite(amount) || !Number.isInteger(amount) || amount <= 0) {
      return { ok: false, reason: "invalid_amount", offer: current };
    }
    const closed: MarketplaceOffer = {
      ...current,
      state: "countered",
      updatedAt: now,
      pending: null
    };
    const created: MarketplaceOffer = {
      ...current,
      id: (options.makeId ?? defaultMakeId)(),
      amountMinor: amount,
      // The counter travels back the way the original came.
      direction:
        current.direction === "buyer_to_seller" ? "seller_to_buyer" : "buyer_to_seller",
      state: "open",
      createdAt: now,
      updatedAt: now,
      counterOf: current.id,
      message: options.counterMessage,
      pending: null
    };
    return { ok: true, offer: closed, created };
  }

  const nextState: OfferState =
    action === "accept" ? "accepted" : action === "decline" ? "declined" : "withdrawn";
  return { ok: true, offer: { ...current, state: nextState, updatedAt: now, pending: null } };
}

/* ------------------------------------------------------------------ *
 * Double-tap protection
 * ------------------------------------------------------------------ */

/**
 * Stamp the action in flight, or refuse because one already is.
 *
 * Called on press, before any await. The refusal is what makes the second tap of
 * a double-tap a no-op rather than a second request.
 */
export function beginOfferAction(
  offer: MarketplaceOffer,
  action: OfferAction
): OfferActionResult {
  if (offer.pending) return { ok: false, reason: "in_flight", offer };
  if (isTerminal(offer.state)) return { ok: false, reason: "already_resolved", offer };
  return { ok: true, offer: { ...offer, pending: action } };
}

/** Clear the in-flight stamp — on success, on failure, on abort. */
export function endOfferAction(offer: MarketplaceOffer): MarketplaceOffer {
  return offer.pending ? { ...offer, pending: null } : offer;
}

/**
 * True when the row's three buttons should be disabled.
 *
 * All three, together, whenever *any* action is in flight — not just the one
 * pressed. Leaving Decline live while Accept is in flight is a race with money
 * on the end of it.
 */
export function offerActionsDisabled(offer: MarketplaceOffer): boolean {
  return Boolean(offer.pending) || isTerminal(offer.state);
}

/* ------------------------------------------------------------------ *
 * Reducer over a list
 * ------------------------------------------------------------------ */

/**
 * Apply an action within a list, keeping the chain intact.
 *
 * The screen holds an array, and a counter changes two rows at once — the
 * original closes and a new one appears. Doing that at the call site means every
 * call site has to remember the second half, so it is done here: the closed
 * offer is replaced in place and the counter is inserted directly after it, so
 * the chain reads top-to-bottom in the order it happened.
 */
export function applyOfferActionToList(
  offers: readonly MarketplaceOffer[],
  offerId: string,
  options: ApplyOfferActionOptions
): { offers: MarketplaceOffer[]; result: OfferActionResult | null } {
  const index = offers.findIndex((o) => o.id === offerId);
  if (index < 0) return { offers: [...offers], result: null };

  const result = applyOfferAction(offers[index], options);
  if (!result.ok) {
    // Even a failure can carry a state change — an expiry discovered at action
    // time — so the row is written back rather than left stale.
    const next = [...offers];
    next[index] = result.offer;
    return { offers: next, result };
  }

  const next = [...offers];
  next[index] = result.offer;
  if (result.created) next.splice(index + 1, 0, result.created);
  return { offers: next, result };
}

/**
 * Sweep a list for lapsed offers.
 *
 * Cheap enough to call on every render of the list. Returns the same array
 * reference when nothing changed, so it does not defeat memoisation.
 */
export function resolveExpiries(
  offers: readonly MarketplaceOffer[],
  now: number
): readonly MarketplaceOffer[] {
  let changed = false;
  const next = offers.map((offer) => {
    const resolved = resolveExpiry(offer, now);
    if (resolved !== offer) changed = true;
    return resolved;
  });
  return changed ? next : offers;
}

/** The offers a seller still owes an answer to, freshest first. */
export function offersAwaitingSeller(
  offers: readonly MarketplaceOffer[],
  now: number
): MarketplaceOffer[] {
  return resolveExpiries(offers, now)
    .filter((offer) => offer.state === "open" && offer.direction === "buyer_to_seller")
    .sort((a, b) => b.createdAt - a.createdAt);
}
