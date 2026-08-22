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
import { envFlagOn } from "../core/envFlag";

/* ------------------------------------------------------------------ *
 * Feature flags — read at call time so tests can toggle them.
 *
 * Both go through `envFlagOn` in `core/envFlag.ts`, which accepts exactly what
 * the inline parser here used to accept.
 * ------------------------------------------------------------------ */

/**
 * Gates the pickup-escrow presentation: the "payment is held in escrow until you
 * confirm handoff" safety panel and the escrow-release step of the pickup
 * timeline. OFF by default and MUST stay off until the live surface actually
 * exposes escrow state — an enabled panel would assert a hold the reachable
 * backend does not confirm. When on, every escrow figure is tagged Preview.
 */
export function ordersEscrowIsLive(): boolean {
  return envFlagOn("EXPO_PUBLIC_ORDERS_ESCROW");
}

/**
 * Gates the seller write actions the live surface cannot perform — "Mark packed",
 * "Mark shipped", "Confirm handoff". These map to the canonical `fulfill`/
 * `complete` transitions, which are DARK (404) in production. Rather than offer a
 * button that can only ever no-op — the exact failure the mission forbids — the
 * actions render as a disabled Preview until the canonical order routes are live.
 */
export function ordersFulfillmentIsLive(): boolean {
  return envFlagOn("EXPO_PUBLIC_ORDERS_FULFILLMENT");
}

/* ------------------------------------------------------------------ *
 * Order phase model — one machine, two variants.
 * ------------------------------------------------------------------ */

export type OrderPerspective = "seller" | "buyer";
export type OrderTimelineVariant = "shipping" | "pickup";
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
 * Deterministic map from a normalized live status to a reached step index, per
 * variant. `-1` means the timeline has not started (or the overlay owns the
 * card). Only real, live-derivable statuses advance the index; the mock steps
 * are never "reached" from live data, so the surface can honestly draw them as
 * not-yet-confirmed even when a later real step is reached.
 */
export function reachedStepIndex(status: string, variant: OrderTimelineVariant): number {
  const s = normalizeStatus(status);
  const steps = variant === "pickup" ? PICKUP_STEPS : SHIPPING_STEPS;
  if (s === "cancelled" || s === "refunded" || s === "failed") return -1;
  if (variant === "shipping") {
    if (s === "delivered") return indexOfKey(steps, "delivered");
    if (s === "shipped") return indexOfKey(steps, "shipped");
    if (s === "paid" || s === "processing" || s === "pending") return indexOfKey(steps, "paid");
    return indexOfKey(steps, "paid");
  }
  // Pickup: the live surface only distinguishes paid vs delivered/complete.
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
   * True only when the escrow flag is on AND this is a pickup order. Drives the
   * safety panel. When false the panel is withheld entirely.
   */
  escrowPresentable: boolean;
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
 * Pickup vs shipping is a listing property (`delivery_type`) the live order
 * payloads do not always carry. When it is present we honour it; when it is not
 * we default to shipping and do not fabricate a pickup order (pickup unlocks the
 * escrow/safety presentation, so guessing it would be the worst place to guess).
 */
function variantOf(deliveryType?: string): OrderTimelineVariant {
  const d = String(deliveryType || "").toLowerCase();
  return d === "pickup" || d === "local" ? "pickup" : "shipping";
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
  const deliveryType = (order as BuyerOrder & { delivery_type?: string; listing?: { delivery_type?: string } })
    .delivery_type
    || (order.listing as { delivery_type?: string } | undefined)?.delivery_type;
  const variant = variantOf(deliveryType);
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
    escrowPresentable: ordersEscrowIsLive() && variant === "pickup",
    returnWindowClosesAt:
      (order as BuyerOrder & { return_window_closes_at?: string }).return_window_closes_at || undefined,
    raw: { buyer: order }
  };
}

export function unifySellerOrder(order: MarketplaceSellerOrder): UnifiedOrder {
  const id = Number(order.id || 0);
  const variant = variantOf(String(order.item_type || ""));
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
    escrowPresentable: ordersEscrowIsLive() && variant === "pickup",
    raw: { seller: order }
  };
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

export type SellerActionKey = "mark_packed" | "mark_shipped" | "confirm_handoff" | "view_payout";

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
 * shipping and handoff are previews until the canonical order routes are live
 * (`ordersFulfillmentIsLive`), and shipping additionally requires tracking per
 * policy. "View payout" is always a live navigation, never gated.
 */
export function sellerActionsFor(order: UnifiedOrder): SellerActionState[] {
  const live = ordersFulfillmentIsLive();
  const actions: SellerActionState[] = [];
  const overlayBlocks = order.overlay === "cancelled" || order.overlay === "refunded";

  if (!overlayBlocks) {
    if (order.variant === "pickup") {
      actions.push(previewOrDisabled("confirm_handoff", "Confirm handoff", live));
    } else {
      const idx = reachedStepIndex(order.status, "shipping");
      if (idx < indexOfKey(SHIPPING_STEPS, "shipped")) {
        actions.push(previewOrDisabled("mark_packed", "Mark packed", live));
        actions.push(shippedAction(live, order));
      }
    }
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
  }
];

/** Pinned so a test breaks if a gap is silently closed or added. */
export const ORDERS_MOCK_DATA_GAP_COUNT = ORDERS_MOCK_DATA_GAPS.length;
