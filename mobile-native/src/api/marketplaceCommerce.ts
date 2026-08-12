/**
 * Server clients for the Marketplace commerce domains: cart, offers, returns.
 *
 * The backend contracts are the three route packs registered in `bot.py`:
 *
 *   - `services/marketplace_cart_routes.py`    → `/api/pulse/marketplace/cart*`
 *   - `services/marketplace_offers_routes.py`  → `/api/pulse/marketplace/offers*`
 *   - `services/marketplace_returns_routes.py` → `/api/pulse/marketplace/returns*`
 *
 * Deploy ordering: these endpoints must be live before an app build with
 * `MARKETPLACE_CART_ENABLED` / `MARKETPLACE_OFFERS_ENABLED` on reaches users.
 * The clients below fail soft — a 404 from a not-yet-deployed pack reads as an
 * empty cart / no offers, never a crash — but soft failure is a safety net,
 * not a release strategy.
 *
 * Money is integer minor units (cents) everywhere, matching both the server
 * and the offer state machine in `./marketplaceOffers`. Formatting is UI work.
 */

import { pulseApi } from "./pulseApi";

/* ------------------------------------------------------------------ *
 * Cart
 * ------------------------------------------------------------------ */

/** Per-line state, derived server-side at read time — never stored, so it
 * cannot go stale. Everything except `available` affects checkout. */
export type CartLineState =
  | "available"
  | "price_changed"
  | "low_stock"
  | "sold"
  | "removed"
  | "restricted";

export type CartLine = {
  line_id: number;
  listing_id: number;
  qty: number;
  state: CartLineState;
  price_snapshot_minor: number;
  price_now_minor: number;
  currency: string;
  title: string;
  cover_image_url: string;
  seller_user_id: number;
  seller_name: string;
  fulfillment: "digital" | "pickup" | "shipping";
  added_at: string;
};

export type CartSnapshot = {
  lines: readonly CartLine[];
  badgeCount: number;
};

const EMPTY_CART: CartSnapshot = { lines: [], badgeCount: 0 };

function asCartSnapshot(payload: unknown): CartSnapshot {
  const data = (payload ?? {}) as { lines?: unknown; badge_count?: unknown };
  const lines = Array.isArray(data.lines) ? (data.lines as CartLine[]) : [];
  return {
    lines,
    badgeCount:
      typeof data.badge_count === "number" ? data.badge_count : lines.length
  };
}

/** A missing or failing cart backend must read as an empty cart, not a crash —
 * but only for reads. Writes (add, checkout) surface their errors, because a
 * silently dropped "Add to cart" is a lie told to the buyer. */
export async function fetchCart(): Promise<CartSnapshot> {
  try {
    return asCartSnapshot(await pulseApi("/api/pulse/marketplace/cart"));
  } catch {
    return EMPTY_CART;
  }
}

export async function addToCart(listingId: number, qty = 1): Promise<CartSnapshot> {
  return asCartSnapshot(
    await pulseApi("/api/pulse/marketplace/cart", {
      method: "POST",
      body: JSON.stringify({ listing_id: listingId, qty })
    })
  );
}

export async function updateCartLine(lineId: number, qty: number): Promise<void> {
  await pulseApi(`/api/pulse/marketplace/cart/${lineId}`, {
    method: "PATCH",
    body: JSON.stringify({ qty })
  });
}

export async function removeCartLine(lineId: number): Promise<void> {
  await pulseApi(`/api/pulse/marketplace/cart/${lineId}`, { method: "DELETE" });
}

/** The buyer has seen the new price and accepted it. */
export async function confirmCartLinePrice(lineId: number): Promise<void> {
  await pulseApi(`/api/pulse/marketplace/cart/${lineId}/confirm-price`, {
    method: "POST",
    body: JSON.stringify({})
  });
}

export type CartValidation = {
  lines: readonly CartLine[];
  canCheckout: boolean;
  blockingLineIds: readonly number[];
  priceChangedLineIds: readonly number[];
};

export async function validateCart(): Promise<CartValidation> {
  const data = (await pulseApi("/api/pulse/marketplace/cart/validate", {
    method: "POST",
    body: JSON.stringify({})
  })) as {
    lines?: CartLine[];
    can_checkout?: boolean;
    blocking_line_ids?: number[];
    price_changed_line_ids?: number[];
  };
  return {
    lines: data.lines ?? [],
    canCheckout: Boolean(data.can_checkout),
    blockingLineIds: data.blocking_line_ids ?? [],
    priceChangedLineIds: data.price_changed_line_ids ?? []
  };
}

/**
 * Check out one seller's group. The idempotency key belongs to the *intent*,
 * not the tap: generate it when the confirm sheet opens, reuse it for every
 * retry of that confirmation, discard it when the sheet closes. A duplicate
 * tap then replays the same session instead of creating a second charge.
 */
export async function checkoutCartGroup(
  sellerUserId: number,
  idempotencyKey: string
): Promise<{ checkoutUrl: string; transactionIds: readonly number[] }> {
  const data = (await pulseApi("/api/pulse/marketplace/cart/checkout", {
    method: "POST",
    body: JSON.stringify({
      seller_user_id: sellerUserId,
      idempotency_key: idempotencyKey
    })
  })) as { checkout_url?: string; transaction_ids?: number[] };
  return {
    checkoutUrl: data.checkout_url ?? "",
    transactionIds: data.transaction_ids ?? []
  };
}

export type MarketplacePaymentStatus = "pending" | "paid" | "failed" | "canceled" | "refunded";

export type MarketplacePaymentOrder = {
  id: number;
  status: string;
  paymentStatus: MarketplacePaymentStatus;
  fulfilled: boolean;
};

/** Read the canonical server transaction after Stripe hands control back.
 * The client never promotes a browser return into payment success. */
export async function getMarketplacePaymentOrder(transactionId: number): Promise<MarketplacePaymentOrder> {
  const data = (await pulseApi(`/api/pulse/payments/orders/${transactionId}`)) as {
    order?: { id?: number; status?: string };
    payment_status?: MarketplacePaymentStatus;
    fulfilled?: boolean;
  };
  return {
    id: Number(data.order?.id || transactionId),
    status: String(data.order?.status || data.payment_status || "pending"),
    paymentStatus: data.payment_status || "pending",
    fulfilled: Boolean(data.fulfilled)
  };
}

/** Group lines the way checkout charges them: one group per seller, split by
 * fulfillment inside the group so digital / pickup / shipping never blur. */
export function groupCartLines(
  lines: readonly CartLine[]
): readonly {
  sellerUserId: number;
  sellerName: string;
  fulfillments: readonly { fulfillment: CartLine["fulfillment"]; lines: readonly CartLine[] }[];
  totalMinor: number;
  currency: string;
}[] {
  const bySeller = new Map<number, CartLine[]>();
  for (const line of lines) {
    const bucket = bySeller.get(line.seller_user_id) ?? [];
    bucket.push(line);
    bySeller.set(line.seller_user_id, bucket);
  }
  return [...bySeller.entries()].map(([sellerUserId, sellerLines]) => {
    const byFulfillment = new Map<CartLine["fulfillment"], CartLine[]>();
    for (const line of sellerLines) {
      const bucket = byFulfillment.get(line.fulfillment) ?? [];
      bucket.push(line);
      byFulfillment.set(line.fulfillment, bucket);
    }
    return {
      sellerUserId,
      sellerName: sellerLines[0]?.seller_name ?? "",
      fulfillments: [...byFulfillment.entries()].map(([fulfillment, grouped]) => ({
        fulfillment,
        lines: grouped
      })),
      totalMinor: sellerLines.reduce(
        (sum, line) => sum + line.price_snapshot_minor * line.qty,
        0
      ),
      currency: sellerLines[0]?.currency ?? "USD"
    };
  });
}

/* ------------------------------------------------------------------ *
 * Offers
 * ------------------------------------------------------------------ */

import type { MarketplaceOffer, OfferState } from "./marketplaceOffers";

type ServerOffer = {
  id: number;
  listing_id: number;
  amount_minor: number;
  list_price_minor: number;
  currency: string;
  direction: "buyer_to_seller" | "seller_to_buyer";
  state: OfferState | "open";
  counter_of: number | null;
  note: string;
  created_at: string;
  updated_at: string;
  /** Hydrated server-side by `_hydrate` in marketplace_offers_routes.py. */
  item_title?: string;
  item_thumbnail_url?: string | null;
  buyer_name?: string;
};

/** Server rows use the wire state `open`; the mobile machine calls it `open`
 * too — the mapping is the id/date shape, not the vocabulary. The server
 * hydrates listing title/thumbnail and buyer name so the offers UI never has
 * to join client-side; fallbacks below keep the card renderable if an older
 * backend build predates hydration. */
export function offerFromServer(row: ServerOffer): MarketplaceOffer {
  return {
    id: String(row.id),
    listingId: String(row.listing_id),
    amountMinor: row.amount_minor,
    currency: row.currency || "USD",
    listPriceMinor: row.list_price_minor,
    direction: row.direction,
    state: row.state as OfferState,
    createdAt: Date.parse(row.created_at) || Date.now(),
    updatedAt: Date.parse(row.updated_at) || Date.now(),
    buyerName: row.buyer_name || "PulseSoc buyer",
    itemTitle: row.item_title || "Marketplace item",
    ...(row.item_thumbnail_url ? { itemThumbnailUrl: row.item_thumbnail_url } : {}),
    ...(row.counter_of ? { counterOf: String(row.counter_of) } : {}),
    ...(row.note ? { message: row.note } : {})
  };
}

export async function fetchOffers(
  role: "buyer" | "seller" = "buyer"
): Promise<readonly MarketplaceOffer[]> {
  try {
    const data = (await pulseApi(
      `/api/pulse/marketplace/offers?role=${role}`
    )) as { offers?: ServerOffer[] };
    return (data.offers ?? []).map(offerFromServer);
  } catch {
    return [];
  }
}

export async function createOffer(input: {
  listingId: number;
  amountMinor: number;
  qty?: number;
  note?: string;
}): Promise<MarketplaceOffer> {
  const data = (await pulseApi("/api/pulse/marketplace/offers", {
    method: "POST",
    body: JSON.stringify({
      listing_id: input.listingId,
      amount_minor: input.amountMinor,
      qty: input.qty ?? 1,
      note: input.note ?? ""
    })
  })) as { offer: ServerOffer };
  return offerFromServer(data.offer);
}

export async function actOnOffer(
  offerId: string,
  action: "accept" | "decline" | "withdraw"
): Promise<MarketplaceOffer> {
  const data = (await pulseApi(
    `/api/pulse/marketplace/offers/${offerId}/${action}`,
    { method: "POST", body: JSON.stringify({}) }
  )) as { offer: ServerOffer };
  return offerFromServer(data.offer);
}

export async function counterOffer(
  offerId: string,
  amountMinor: number,
  note = ""
): Promise<{ closed: MarketplaceOffer; counter: MarketplaceOffer }> {
  const data = (await pulseApi(
    `/api/pulse/marketplace/offers/${offerId}/counter`,
    {
      method: "POST",
      body: JSON.stringify({ amount_minor: amountMinor, note })
    }
  )) as { offer: ServerOffer; counter: ServerOffer };
  return { closed: offerFromServer(data.offer), counter: offerFromServer(data.counter) };
}

/** Accepted-offer checkout: charges the offered amount inside the acceptance
 * window, through the same Stripe surface as every other purchase. */
export async function checkoutOffer(
  offerId: string
): Promise<{ checkoutUrl: string; transactionId: number }> {
  const data = (await pulseApi(
    `/api/pulse/marketplace/offers/${offerId}/checkout`,
    { method: "POST", body: JSON.stringify({}) }
  )) as { checkout_url?: string; transaction_id?: number };
  return {
    checkoutUrl: data.checkout_url ?? "",
    transactionId: data.transaction_id ?? 0
  };
}

/* ------------------------------------------------------------------ *
 * Returns
 * ------------------------------------------------------------------ */

export type ReturnState =
  | "opened"
  | "awaiting_seller"
  | "awaiting_buyer"
  | "under_review"
  | "resolved_refund"
  | "resolved_replacement"
  | "resolved_rejected"
  | "closed";

export type MarketplaceReturn = {
  id: number;
  transaction_id: number;
  listing_id: number;
  reason: string;
  explanation: string;
  desired_resolution: string;
  state: ReturnState;
  viewer_role: "buyer" | "seller" | "";
  created_at: string;
  updated_at: string;
};

export async function fetchReturns(
  role: "buyer" | "seller" = "buyer"
): Promise<readonly MarketplaceReturn[]> {
  try {
    const data = (await pulseApi(
      `/api/pulse/marketplace/returns?role=${role}`
    )) as { returns?: MarketplaceReturn[] };
    return data.returns ?? [];
  } catch {
    return [];
  }
}

export async function openReturn(input: {
  transactionId: number;
  reason: string;
  explanation: string;
  desiredResolution?: "refund" | "replacement" | "partial_refund";
  media?: readonly string[];
}): Promise<MarketplaceReturn> {
  const data = (await pulseApi("/api/pulse/marketplace/returns", {
    method: "POST",
    body: JSON.stringify({
      transaction_id: input.transactionId,
      reason: input.reason,
      explanation: input.explanation,
      desired_resolution: input.desiredResolution ?? "refund",
      media: input.media ?? []
    })
  })) as { return: MarketplaceReturn };
  return data.return;
}
