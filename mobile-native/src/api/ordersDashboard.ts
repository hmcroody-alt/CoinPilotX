/**
 * Orders dashboard data layer — the single order model both perspectives render.
 *
 * Orders is one surface seen from two ends: the seller's fulfillment queue and
 * the buyer's "Your orders". The hard requirement is that a given order reads the
 * SAME facts from both sides — a state that is "shipped" on the seller card is
 * "shipped" on the buyer card, because it is one order. This module produces one
 * normalized `UnifiedOrder` from whichever live payload we have, and both screens
 * derive their view from it. Neither screen re-interprets status on its own.
 *
 * ── Backend binding (money truth) ──────────────────────────────────────────
 * Bound to the LIVE `/api/pulse/*` surface, exactly like every other shipped
 * Business OS screen:
 *
 *   • buyer  → `/api/pulse/orders`               (see ./orders)
 *   • seller → `/api/pulse/payments/seller/orders` (see ./marketplace)
 *
 * There IS a richer canonical order engine in the backend — `created → paid →
 * fulfilled → completed` with a real per-order escrow account
 * (`mkt_order_escrow:<id>`, funds held from pay until settle) exposed at
 * `/api/business-os/orders*`. But every `BUSINESS_OS_*` flag is inert in
 * production, so those routes return 404 today. Binding to them would ship a
 * safety panel that claims "your payment is held" against an endpoint that
 * cannot answer. So this module reads the live surface, and everything the live
 * surface does not carry — escrow hold/release, ship-by deadlines, packing and
 * pickup-handoff sub-phases, payout amounts, the return-window close date — is
 * declared in `ORDERS_MOCK_DATA_GAPS` and gated behind a flag, never invented.
 *
 * No client-side money arithmetic lives here. Amounts are the server's cents,
 * formatted for display and nothing more. There is no new payment path: the only
 * money movements the surface can trigger are the existing payout-connect and
 * receipt/support web flows.
 */

import {
  BuyerOrder,
  formatOrderMoney,
  listBuyerOrders,
  loadCachedBuyerOrders,
  normalizeStatus
} from "./orders";
import {
  MarketplaceSellerOrder,
  listMarketplaceSellerOrders,
  loadCachedSellerStore
} from "./marketplace";
import { MarketplaceFulfillmentKind, isInPersonKind } from "./marketplaceFulfillment";
import { isFlagValueOn } from "../core/envFlag";

/* ------------------------------------------------------------------ *
 * Feature flags — read at call time so tests can toggle them.
 *
 * Both go through `isFlagValueOn` in `core/envFlag.ts`, which accepts exactly
 * what the inline parser here used to accept.
 *
 * Both spell their variable literally rather than handing the name to
 * `envFlagOn`. `babel-preset-expo` substitutes `process.env.X` only when the key
 * is a StringLiteral, so a computed lookup is never inlined and reads undefined
 * in a release bundle — these gates would be off on device regardless.
 * ------------------------------------------------------------------ */

/**
 * Gates the pickup-escrow presentation: the "payment is held in escrow until you
 * confirm handoff" safety panel and the escrow-release step of the pickup
 * timeline. OFF by default and MUST stay off until the live surface actually
 * exposes escrow state — an enabled panel would assert a hold the reachable
 * backend does not confirm. When on, every escrow figure is tagged Preview.
 */
export function ordersEscrowIsLive(): boolean {
  return isFlagValueOn(process.env.EXPO_PUBLIC_ORDERS_ESCROW);
}

/**
 * Gates the seller write actions the live surface cannot perform — "Mark packed",
 * "Mark shipped", "Confirm handoff". These map to the canonical `fulfill`/
 * `complete` transitions, which are DARK (404) in production. Rather than offer a
 * button that can only ever no-op — the exact failure the mission forbids — the
 * actions render as a disabled Preview until the canonical order routes are live.
 */
export function ordersFulfillmentIsLive(): boolean {
  return isFlagValueOn(process.env.EXPO_PUBLIC_ORDERS_FULFILLMENT);
}

/* ------------------------------------------------------------------ *
 * Order phase model — one machine, two variants.
 * ------------------------------------------------------------------ */

export type OrderPerspective = "seller" | "buyer";

/**
 * Which progress strip an order gets.
 *
 * This was `"shipping" | "pickup"` — two strips for eleven fulfilment kinds, so
 * everything that was not handed over in person was described as a parcel. A
 * probe (`scripts/probe_order_timeline_kinds.py`) published one listing per lane
 * and bought each through the real checkout route: four of the nine — a digital
 * download, a remote service, an online event and a remote booking — came back
 * on the parcel strip, telling the buyer their file was "Being packed" and then
 * "On its way".
 *
 * The app already knew better one screen earlier. `marketplaceFulfillment`
 * summarises a digital checkout as "Delivered to your PulseSoc account" and an
 * online event as "Joined online". That module holds the whole vocabulary —
 * `MarketplaceFulfillmentKind`, `isScheduledKind`, `isInPersonKind` — and this
 * one now reads it instead of keeping a second, coarser copy.
 */
export type OrderTimelineVariant = "shipping" | "pickup" | "digital" | "scheduled";
export type OrderSource = "store" | "marketplace";

/** Overlays sit on top of the linear timeline, never inside it. */
export type OrderOverlay = "none" | "issue" | "cancelled" | "refunded" | "expired";

/**
 * The linear steps of each variant. `mock: true` marks a step the live surface
 * cannot confirm — it is drawn dimmed and tagged Preview rather than presented as
 * a known fact. The reached step is derived from the live status; the mock steps
 * exist so the timeline shape matches the design without claiming false progress.
 */
export type OrderStep = {
  key: string;
  /** Seller-facing label. */
  sellerLabel: string;
  /** Buyer-facing label for the same underlying step. */
  buyerLabel: string;
  /** True when this step is not derivable from the live surface. */
  mock: boolean;
};

export const SHIPPING_STEPS: OrderStep[] = [
  { key: "paid", sellerLabel: "Paid", buyerLabel: "Order placed", mock: false },
  { key: "packed", sellerLabel: "Packed", buyerLabel: "Being packed", mock: true },
  { key: "shipped", sellerLabel: "Shipped", buyerLabel: "On its way", mock: false },
  { key: "delivered", sellerLabel: "Delivered", buyerLabel: "Delivered", mock: false }
];

export const PICKUP_STEPS: OrderStep[] = [
  { key: "paid", sellerLabel: "Paid", buyerLabel: "Reserved", mock: false },
  { key: "pickup_scheduled", sellerLabel: "Pickup scheduled", buyerLabel: "Pickup scheduled", mock: true },
  { key: "handed_off", sellerLabel: "Handed off", buyerLabel: "Picked up", mock: true },
  { key: "complete", sellerLabel: "Complete", buyerLabel: "Complete", mock: false }
];

/**
 * A digital sale has no middle. `_validate_digital` in
 * `services/marketplace_listing_types.py` refuses any delivery mode but
 * `automatic`, and `pulse_buyer_order_response` attaches the download links to
 * the order the moment `payment_status` reads "paid". So there is nothing
 * between paying and having the file, and neither step is a Preview: both are
 * read straight off the live status.
 *
 * The buyer-facing wording is `fulfillmentDestinationSummary`'s, verbatim: the
 * checkout told this same buyer "Delivered to your PulseSoc account" a screen
 * ago, and the order should not then invent a different account of where their
 * purchase went. It also stops short of "Ready to download", which would imply a
 * control that does not exist yet — the links are on the wire and no surface
 * renders them. See the `digital_files` entry in `ORDERS_MOCK_DATA_GAPS`.
 */
export const DIGITAL_STEPS: OrderStep[] = [
  { key: "paid", sellerLabel: "Paid", buyerLabel: "Order placed", mock: false },
  { key: "available", sellerLabel: "Delivered", buyerLabel: "Delivered to your account", mock: false }
];

/**
 * Services, bookings and events: something happens at an agreed time. The live
 * surface knows the order was paid and, eventually, that it completed — the
 * appointment itself is not a state it tracks, so the middle step is a Preview
 * exactly like the pickup strip's.
 *
 * These used to be split between the two old strips by whether they were held in
 * person, which meant a video consultation was "On its way" and a haircut was
 * "Picked up". Neither describes an appointment.
 */
export const SCHEDULED_STEPS: OrderStep[] = [
  { key: "paid", sellerLabel: "Paid", buyerLabel: "Booked", mock: false },
  { key: "scheduled", sellerLabel: "Scheduled", buyerLabel: "Scheduled", mock: true },
  { key: "complete", sellerLabel: "Complete", buyerLabel: "Complete", mock: false }
];

/**
 * The steps for a variant. Exported because `OrderTimeline` needs exactly this
 * and used to carry its own `variant === "pickup" ? … : …` — a second copy of
 * the choice that `reachedStepIndex` makes, which is how a new variant gets
 * drawn with one strip's dots and another strip's reached index.
 */
export function stepsForVariant(variant: OrderTimelineVariant): OrderStep[] {
  if (variant === "pickup") return PICKUP_STEPS;
  if (variant === "digital") return DIGITAL_STEPS;
  if (variant === "scheduled") return SCHEDULED_STEPS;
  return SHIPPING_STEPS;
}

/**
 * Deterministic map from a normalized live status to a reached step index, per
 * variant. `-1` means the timeline has not started (or the overlay owns the
 * card). Only real, live-derivable statuses advance the index; the mock steps
 * are never "reached" from live data, so the surface can honestly draw them as
 * not-yet-confirmed even when a later real step is reached.
 */
export function reachedStepIndex(status: string, variant: OrderTimelineVariant): number {
  const s = normalizeStatus(status);
  const steps = stepsForVariant(variant);
  if (s === "cancelled" || s === "refunded" || s === "failed") return -1;
  if (variant === "shipping") {
    if (s === "delivered") return indexOfKey(steps, "delivered");
    if (s === "shipped") return indexOfKey(steps, "shipped");
    if (s === "paid" || s === "processing" || s === "pending") return indexOfKey(steps, "paid");
    return indexOfKey(steps, "paid");
  }
  if (variant === "digital") {
    // The server's own rule for attaching the files, mirrored rather than
    // reinvented: `payment_status` reads "paid" for exactly this set
    // (`bot.py`), and the files ride along whenever it does.
    if (s === "paid" || s === "processing" || s === "shipped" || s === "delivered") {
      return indexOfKey(steps, "available");
    }
    return indexOfKey(steps, "paid");
  }
  // Pickup and scheduled: the live surface only distinguishes paid vs
  // delivered/complete.
  if (s === "delivered") return indexOfKey(steps, "complete");
  return indexOfKey(steps, "paid");
}

function indexOfKey(steps: OrderStep[], key: string): number {
  return steps.findIndex((step) => step.key === key);
}

export function orderOverlay(status: string): OrderOverlay {
  const s = normalizeStatus(status);
  if (s === "refunded") return "refunded";
  if (s === "cancelled") return "cancelled";
  if (s === "failed") return "issue";
  return "none";
}

/* ------------------------------------------------------------------ *
 * Unified order — the shape both screens render.
 * ------------------------------------------------------------------ */

export type UnifiedOrder = {
  /** Stable id shared by both perspectives. */
  id: number;
  /** Human reference, e.g. "PL-2384". */
  reference: string;
  title: string;
  /** Formatted money string — server cents, never recomputed here. */
  amountLabel: string;
  amountCents: number;
  currency: string;
  source: OrderSource;
  variant: OrderTimelineVariant;
  status: string;
  overlay: OrderOverlay;
  quantity: number;
  createdAt?: string;
  updatedAt?: string;
  counterpartyName: string;
  thumbnailUrl?: string;
  tracking?: { number?: string; url?: string; available: boolean };
  /**
   * True only when the escrow flag is on AND buyer and seller meet in person —
   * a collection, an in-person service, a booked appointment at an address, a
   * ticket scanned at a venue. Drives the safety panel, which is advice about
   * meeting a stranger. When false the panel is withheld entirely.
   *
   * Read from the order's kind rather than from `variant`: the strip answers
   * "how do I describe this order's progress", which is a different question
   * and, since the scheduled strip exists, a differently-shaped one.
   */
  escrowPresentable: boolean;
  /**
   * The order was checked out with cash / local pickup and the seller has not
   * confirmed the money changed hands yet. Kept as its own field because
   * `normalizeStatus` collapses `cash_pending` into plain "pending" — the
   * distinction is what makes the settle action reachable.
   */
  awaitingCash: boolean;
  /**
   * The return-window close date, only if the backend surfaced one. Never a
   * hardcoded "+30 days" — absence renders nothing.
   */
  returnWindowClosesAt?: string;
  /** Original payloads, so a screen can reach a field this model did not lift. */
  raw: { buyer?: BuyerOrder; seller?: MarketplaceSellerOrder };
};

const DEFAULT_REFERENCE_PREFIX = "PL";

function referenceFor(id: number, explicit?: string): string {
  if (explicit && explicit.trim()) return explicit.trim().replace(/^#/, "");
  return `${DEFAULT_REFERENCE_PREFIX}-${id}`;
}

/**
 * Every fulfilment kind, mapped to the strip that describes it.
 *
 * Typed as a total `Record` over `MarketplaceFulfillmentKind` on purpose: a
 * twelfth kind added to that union fails the typecheck *here*, rather than
 * silently defaulting to the parcel strip the way an open `Set` membership test
 * does. An enumeration cannot notice what was never put on it.
 *
 * The two undecided kinds sit on the strip their settled form would most likely
 * take; in practice checkout narrows them via `resolve_choice` before anything
 * is frozen, so an order should never carry one.
 */
export const TIMELINE_VARIANT_BY_KIND: Record<MarketplaceFulfillmentKind, OrderTimelineVariant> = {
  shipping: "shipping",
  shipping_or_pickup: "shipping",
  pickup: "pickup",
  digital: "digital",
  service_remote: "scheduled",
  service_in_person: "scheduled",
  service_choice: "scheduled",
  event_online: "scheduled",
  event_in_person: "scheduled",
  booking_remote: "scheduled",
  booking_in_person: "scheduled"
};

const KNOWN_KINDS = new Set<string>(Object.keys(TIMELINE_VARIANT_BY_KIND));

/** The payload's `fulfillment_kind` as a kind, or null if it is not one. */
function asFulfillmentKind(value?: string): MarketplaceFulfillmentKind | null {
  const kind = String(value || "").trim().toLowerCase();
  return KNOWN_KINDS.has(kind) ? (kind as MarketplaceFulfillmentKind) : null;
}

/**
 * Which timeline an order gets, from the lane it was actually placed on.
 *
 * The input is `fulfillment_kind`: the settled kind checkout froze onto the
 * transaction, after the buyer answered for a listing that offered both lanes.
 * It is an order fact rather than a listing lookup, so it survives the seller
 * editing or delisting the item. (It replaced `delivery_type`, which no order
 * serializer has ever sent and which holds the *product type* anyway — see
 * `scripts/probe_order_lane.py` and `deliveryLane`.)
 *
 * An unrecognised kind falls to shipping. That is the safe end: shipping is the
 * only strip that promises the buyer nothing the surface cannot show, and it no
 * longer drags the safety panel along with it.
 */
export function timelineVariantOf(fulfillmentKind?: string): OrderTimelineVariant {
  const kind = asFulfillmentKind(fulfillmentKind);
  return kind ? TIMELINE_VARIANT_BY_KIND[kind] : "shipping";
}

/**
 * Whether this order puts the buyer and seller in the same room — the question
 * the escrow safety panel is actually about.
 *
 * Kept apart from `timelineVariantOf` because they are different questions that
 * happened to share an answer while there were only two strips. Fusing them cost
 * both sides: a video consultation could not be described as an appointment
 * without also being offered stranger-safety advice, and an in-person haircut
 * could not get that advice without being described as a parcel awaiting
 * collection.
 */
export function orderIsInPerson(fulfillmentKind?: string): boolean {
  const kind = asFulfillmentKind(fulfillmentKind);
  return kind ? isInPersonKind(kind) : false;
}

function sourceOf(order: BuyerOrder | MarketplaceSellerOrder): OrderSource {
  const table = String((order as BuyerOrder).source_table || "").toLowerCase();
  const type = String((order as MarketplaceSellerOrder).item_type || "").toLowerCase();
  if (table.includes("creator") || type.includes("creator") || type.includes("course")) return "store";
  if (table.includes("seller") || type.includes("marketplace") || type.includes("listing")) return "marketplace";
  return "marketplace";
}

export function unifyBuyerOrder(order: BuyerOrder): UnifiedOrder {
  const id = Number(order.id || order.transaction_id || 0);
  const variant = timelineVariantOf(order.fulfillment_kind);
  const status = normalizeStatus(order.status_group || order.status || order.payment_status);
  return {
    id,
    reference: referenceFor(id, order.order_id),
    title: String(order.item_title || order.title || "Order"),
    amountLabel: formatOrderMoney(order),
    amountCents: Number(order.amount_cents || order.gross_amount_cents || 0),
    currency: String(order.currency || "USD").toUpperCase(),
    source: sourceOf(order),
    variant,
    status,
    overlay: orderOverlay(status),
    quantity: Number((order as BuyerOrder & { quantity?: number }).quantity || 1) || 1,
    createdAt: order.created_at,
    updatedAt: order.updated_at,
    counterpartyName: String(order.seller?.display_name || "PulseSoc Seller"),
    thumbnailUrl:
      order.listing?.thumbnail_url || order.listing?.image_url || order.listing?.cover_image_url || undefined,
    tracking: {
      available: Boolean(order.tracking?.available),
      number: order.tracking?.tracking_number || undefined,
      url: order.tracking?.tracking_url || undefined
    },
    escrowPresentable: ordersEscrowIsLive() && orderIsInPerson(order.fulfillment_kind),
    awaitingCash: isAwaitingCash(order.status),
    returnWindowClosesAt:
      (order as BuyerOrder & { return_window_closes_at?: string }).return_window_closes_at || undefined,
    raw: { buyer: order }
  };
}

export function unifySellerOrder(order: MarketplaceSellerOrder): UnifiedOrder {
  const id = Number(order.id || 0);
  // This passed `item_type` into a parameter named `deliveryType`. The two are
  // different facts and the values never overlapped: `item_type` reads
  // "marketplace_product" on every marketplace row, which is neither "pickup"
  // nor "local", so the seller's copy of the timeline was shipping-only by
  // construction — a seller could not see that the buyer was coming to collect.
  const variant = timelineVariantOf(order.fulfillment_kind);
  const status = normalizeStatus(order.status);
  const cents = Number(order.amount_cents || order.gross_amount_cents || 0);
  const currency = String(order.currency || "USD").toUpperCase();
  return {
    id,
    reference: referenceFor(id),
    title: String(order.item_type || "Order").replace(/_/g, " "),
    amountLabel: moneyLabel(cents, currency),
    amountCents: cents,
    currency,
    source: sourceOf(order),
    variant,
    status,
    overlay: orderOverlay(status),
    quantity: 1,
    createdAt: order.created_at,
    counterpartyName: "Buyer",
    escrowPresentable: ordersEscrowIsLive() && orderIsInPerson(order.fulfillment_kind),
    awaitingCash: isAwaitingCash(order.status),
    raw: { seller: order }
  };
}

function isAwaitingCash(status?: string): boolean {
  return String(status || "").toLowerCase() === "cash_pending";
}

/** Display-only formatting of server cents. Mirrors formatOrderMoney's contract. */
function moneyLabel(cents: number, currency: string): string {
  const amount = Number(cents || 0) / 100;
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency }).format(amount);
  } catch {
    return `${currency} ${amount.toFixed(2)}`;
  }
}

/* ------------------------------------------------------------------ *
 * Loaders — live read with cached fallback + an offline flag, mirroring
 * loadAdsMarketplace. Write controls read `offline` and refuse rather than fail.
 * ------------------------------------------------------------------ */

export type BuyerOrdersModel = {
  orders: UnifiedOrder[];
  offline: boolean;
  error?: string;
};

export type SellerOrdersModel = {
  orders: UnifiedOrder[];
  offline: boolean;
  error?: string;
};

export async function loadBuyerOrdersModel(limit = 80): Promise<BuyerOrdersModel> {
  try {
    const result = await listBuyerOrders({ limit });
    return { orders: (result.orders || []).map(unifyBuyerOrder), offline: false };
  } catch (error) {
    const cached = await loadCachedBuyerOrders().catch(() => []);
    return {
      orders: cached.map(unifyBuyerOrder),
      offline: true,
      error: error instanceof Error ? error.message : "Your orders could not load."
    };
  }
}

export async function loadSellerOrdersModel(): Promise<SellerOrdersModel> {
  try {
    // Asks for orders, not for the store.
    //
    // This used to call `loadSellerStoreSnapshot()`, which fetches the seller's
    // orders *and* 80 full listing rows — description, gallery JSON and 20-odd
    // other columns each — and then used only `.orders`. The Orders screen
    // downloaded, parsed and normalised an entire storefront it never renders,
    // on every open. One request, and a much smaller one, answers the question
    // this screen actually asks.
    const result = await listMarketplaceSellerOrders();
    return { orders: (result.orders || []).map(unifySellerOrder), offline: false };
  } catch (error) {
    const cached = await loadCachedSellerStore().catch(() => null);
    return {
      orders: (cached?.orders || []).map(unifySellerOrder),
      offline: true,
      error: error instanceof Error ? error.message : "Orders could not load."
    };
  }
}

/* ------------------------------------------------------------------ *
 * Attention counts — the one place that decides how many orders are waiting
 * on someone.
 * ------------------------------------------------------------------ */

/**
 * Seller orders still awaiting the seller: live (no cancelled/refunded/expired
 * overlay) and not yet handed over to the buyer.
 *
 * This lived inline in `OrdersManagerScreen` as the number behind its urgency
 * strip. It moved here because the Business Hub needs the same figure on its
 * Orders card, and two copies of "how many orders need me" is exactly the kind
 * of duplicate that drifts until the hub and the section disagree in front of
 * the seller. The screen and the hub now read this function.
 *
 * Deliberately NOT a deadline count. The live seller-orders payload carries no
 * fulfillment SLA (see `ORDERS_MOCK_DATA_GAPS`), so "due today" and "ships by
 * 4 PM" cannot be computed from it. This counts what is genuinely knowable —
 * orders that are open and unfulfilled — and callers that want urgency phrasing
 * must say "to fulfill", never "due today".
 */
export function ordersAwaitingSeller(orders: readonly UnifiedOrder[]): number {
  return orders.filter(
    (order) => order.overlay === "none" && order.status !== "delivered" && order.status !== "complete"
  ).length;
}

/**
 * Buyer orders currently in motion — the count behind the buyer-side urgency
 * strip. Extracted alongside its seller twin so the pair stays symmetrical.
 */
export function ordersInTransitForBuyer(orders: readonly UnifiedOrder[]): number {
  return orders.filter((order) => order.status === "shipped" || order.status === "pickup_scheduled").length;
}

/* ------------------------------------------------------------------ *
 * Seller fulfillment action model — what the seller may do to an order, and
 * why a control is disabled when it is. Mirrors the advertising delivery-switch
 * discipline: never offer an action the backend will reject or cannot perform.
 * ------------------------------------------------------------------ */

export type SellerActionKey =
  | "collect_cash"
  | "mark_packed"
  | "mark_shipped"
  | "confirm_handoff"
  | "mark_completed"
  | "view_payout";

export type SellerActionState = {
  key: SellerActionKey;
  label: string;
  /** True when the action can actually run against a live endpoint. */
  enabled: boolean;
  /** Present whenever `enabled` is false — the human reason, always shown. */
  reason?: string;
  /** True when the control is a flag-gated preview rather than a live action. */
  preview: boolean;
};

/**
 * The next fulfillment action for a seller order, given its status. Packing,
 * shipping, handoff and completion are previews until the canonical order routes
 * are live (`ordersFulfillmentIsLive`), and shipping additionally requires
 * tracking per policy. "View payout" is always a live navigation, never gated.
 *
 * The branch is on the strip, so it follows the strip's correction: a digital
 * sale is offered no fulfilment action at all, because there is none to take.
 * It used to fall through to the shipping branch and be told "Add a tracking
 * number before marking this order shipped" — a control whose one precondition
 * a downloadable file can never meet, which is the same defect as a disabled
 * button with no reachable enabling path.
 */
export function sellerActionsFor(order: UnifiedOrder): SellerActionState[] {
  const live = ordersFulfillmentIsLive();
  const actions: SellerActionState[] = [];
  const overlayBlocks = order.overlay === "cancelled" || order.overlay === "refunded";

  if (!overlayBlocks && order.awaitingCash) {
    // Not gated on `ordersFulfillmentIsLive`: this one settles through a live
    // route, and a cash order stays unpaid forever until someone taps it.
    actions.push({ key: "collect_cash", label: "Mark cash collected", enabled: true, preview: false });
  }

  if (!overlayBlocks && !order.awaitingCash) {
    if (order.variant === "pickup") {
      actions.push(previewOrDisabled("confirm_handoff", "Confirm handoff", live));
    } else if (order.variant === "scheduled") {
      // An appointment is finished, not handed off. Same dark `complete`
      // transition behind it, so same Preview treatment.
      if (order.status !== "delivered") {
        actions.push(previewOrDisabled("mark_completed", "Mark completed", live));
      }
    } else if (order.variant === "shipping") {
      const idx = reachedStepIndex(order.status, "shipping");
      if (idx < indexOfKey(SHIPPING_STEPS, "shipped")) {
        actions.push(previewOrDisabled("mark_packed", "Mark packed", live));
        actions.push(shippedAction(live, order));
      }
    }
    // "digital" deliberately falls through with no action: delivery is
    // automatic by the listing validator's own rule, and the files are attached
    // to the buyer's order the moment it reads paid. There is no seller step to
    // offer, so none is drawn.
  }
  actions.push({ key: "view_payout", label: "View payout", enabled: true, preview: false });
  return actions;
}

function previewOrDisabled(key: SellerActionKey, label: string, live: boolean): SellerActionState {
  if (live) return { key, label, enabled: true, preview: false };
  return {
    key,
    label,
    enabled: false,
    preview: true,
    reason: "Preview — fulfillment actions arrive when the order service is enabled."
  };
}

function shippedAction(live: boolean, order: UnifiedOrder): SellerActionState {
  if (!live) {
    return {
      key: "mark_shipped",
      label: "Mark shipped",
      enabled: false,
      preview: true,
      reason: "Preview — fulfillment actions arrive when the order service is enabled."
    };
  }
  // Policy: shipping requires tracking. Without it the control is present but
  // disabled with its reason, never hidden.
  if (!order.tracking?.number) {
    return {
      key: "mark_shipped",
      label: "Mark shipped",
      enabled: false,
      preview: false,
      reason: "Add a tracking number before marking this order shipped."
    };
  }
  return { key: "mark_shipped", label: "Mark shipped", enabled: true, preview: false };
}

/* ------------------------------------------------------------------ *
 * MOCK-DATA — every field the design asks for that the live surface cannot
 * source. Each carries the backend work it needs. Pinned in tests so closing a
 * gap (or papering over one with invented data) is a deliberate, visible change.
 * ------------------------------------------------------------------ */

export type OrdersMockGap = {
  field: string;
  perspective: OrderPerspective | "both";
  backendWork: string;
};

export const ORDERS_MOCK_DATA_GAPS: OrdersMockGap[] = [
  {
    field: "Ship-by deadline (countdown / overdue)",
    perspective: "seller",
    backendWork: "a per-order fulfillment SLA on the live seller-orders payload"
  },
  {
    field: "Packed sub-phase",
    perspective: "both",
    backendWork: "the live status collapses processing/packed into 'paid'; needs a packed transition"
  },
  {
    field: "Pickup scheduled + handed-off sub-phases",
    perspective: "both",
    backendWork: "pickup lifecycle states on the live surface (only paid vs complete today)"
  },
  {
    field: "Escrow hold / release (safety panel)",
    perspective: "both",
    backendWork: "escrow state on the live payload; the canonical hold exists but /api/business-os is dark"
  },
  {
    field: "Payout amount per order",
    perspective: "seller",
    backendWork: "net-payable per order; today only payout-connect onboarding is exposed"
  },
  {
    field: "Return-window close date",
    perspective: "buyer",
    backendWork: "a return-eligibility deadline on the live order payload"
  },
  {
    field: "Buy-again availability",
    perspective: "buyer",
    backendWork: "a still-purchasable / relist signal per past order"
  },
  {
    // The odd one out: the live surface DOES source this. `/api/pulse/orders`
    // already returns `digital_files` — `[{name, download_url}]` — on every paid
    // digital order, and `/api/pulse/marketplace/digital-files/<id>/download`
    // streams the bytes after checking the requester actually bought it. A
    // backend test pins that the payload carries the links.
    //
    // Nothing reads them. Not this app, not a template, not a static script.
    // The buyer pays, the file sits on the wire, and the order screen used to
    // say "Being packed". The strip now says "Delivered to your account", which
    // is true, but there is still no control that fetches the file: the
    // download route authenticates through `api_account_user()` and the native
    // app holds its token in secure-store rather than a browser cookie, so
    // handing the URL to `Linking.openURL` would open a 401 in Safari.
    field: "Digital file download (links are served, nothing renders them)",
    perspective: "buyer",
    backendWork:
      "a token-authenticated download the native app can call — either a short-lived signed URL on the order payload, or the bearer accepted on the existing download route"
  }
];

/** Pinned so a test breaks if a gap is silently closed or added. */
export const ORDERS_MOCK_DATA_GAP_COUNT = ORDERS_MOCK_DATA_GAPS.length;
