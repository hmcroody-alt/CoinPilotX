/**
 * Tests for the Orders dashboard derivation layer — the single order model both
 * perspectives render. The things worth pinning outright:
 *
 * 1. CROSS-VIEW CONSISTENCY. A buyer order and a seller order with the same id
 *    and status resolve to the SAME variant, status and overlay. This is the hard
 *    requirement of a dual-perspective surface: one order cannot read as two
 *    different facts depending on which end you look from.
 * 2. PHASE MAPPING is derived only from live status. The mock steps
 *    (packed / pickup_scheduled / handed_off) are never "reached", so the timeline
 *    can draw them provisionally without claiming false progress.
 * 3. MONEY & SAFETY ARE FLAG-GATED OFF BY DEFAULT. `escrowPresentable` is false
 *    unless the escrow flag is on AND the order is pickup; seller fulfillment
 *    writes are disabled previews unless the fulfillment flag is on; shipping
 *    additionally requires tracking. None of this can silently no-op.
 * 4. `ORDERS_MOCK_DATA_GAPS` length is asserted, so closing a gap by inventing a
 *    value (or adding one) is a deliberate, reviewed change.
 */

jest.mock("../orders", () => ({
  ...jest.requireActual("../orders"),
  listBuyerOrders: jest.fn(),
  loadCachedBuyerOrders: jest.fn()
}));
jest.mock("../marketplace", () => ({
  ...jest.requireActual("../marketplace"),
  loadSellerStoreSnapshot: jest.fn(),
  loadCachedSellerStore: jest.fn()
}));

import {
  ORDERS_MOCK_DATA_GAPS,
  ORDERS_MOCK_DATA_GAP_COUNT,
  PICKUP_STEPS,
  SHIPPING_STEPS,
  loadBuyerOrdersModel,
  loadSellerOrdersModel,
  orderOverlay,
  reachedStepIndex,
  sellerActionsFor,
  unifyBuyerOrder,
  unifySellerOrder,
  type UnifiedOrder
} from "../ordersDashboard";
import { listBuyerOrders, loadCachedBuyerOrders } from "../orders";
import { loadSellerStoreSnapshot, loadCachedSellerStore } from "../marketplace";

const mockListBuyer = listBuyerOrders as jest.Mock;
const mockCachedBuyer = loadCachedBuyerOrders as jest.Mock;
const mockSnapshot = loadSellerStoreSnapshot as jest.Mock;
const mockCachedSeller = loadCachedSellerStore as jest.Mock;

beforeEach(() => {
  jest.clearAllMocks();
  delete process.env.EXPO_PUBLIC_ORDERS_ESCROW;
  delete process.env.EXPO_PUBLIC_ORDERS_FULFILLMENT;
});

function shippingIndex(key: string) {
  return SHIPPING_STEPS.findIndex((s) => s.key === key);
}
function pickupIndex(key: string) {
  return PICKUP_STEPS.findIndex((s) => s.key === key);
}

describe("phase mapping", () => {
  it("advances the shipping timeline only on real, live-derivable statuses", () => {
    expect(reachedStepIndex("paid", "shipping")).toBe(shippingIndex("paid"));
    expect(reachedStepIndex("shipped", "shipping")).toBe(shippingIndex("shipped"));
    expect(reachedStepIndex("delivered", "shipping")).toBe(shippingIndex("delivered"));
  });

  it("never reports a mock step as reached from a terminal overlay status", () => {
    expect(reachedStepIndex("cancelled", "shipping")).toBe(-1);
    expect(reachedStepIndex("refunded", "pickup")).toBe(-1);
  });

  it("collapses the pickup lifecycle the live surface cannot distinguish", () => {
    // The live surface only knows paid vs delivered/complete for pickup, so the
    // scheduled / handed-off sub-phases are mock and never 'reached'.
    expect(reachedStepIndex("paid", "pickup")).toBe(pickupIndex("paid"));
    expect(reachedStepIndex("delivered", "pickup")).toBe(pickupIndex("complete"));
    expect(PICKUP_STEPS.find((s) => s.key === "pickup_scheduled")?.mock).toBe(true);
    expect(PICKUP_STEPS.find((s) => s.key === "handed_off")?.mock).toBe(true);
  });

  it("maps overlays without folding them into the linear timeline", () => {
    expect(orderOverlay("refunded")).toBe("refunded");
    expect(orderOverlay("cancelled")).toBe("cancelled");
    expect(orderOverlay("failed")).toBe("issue");
    expect(orderOverlay("shipped")).toBe("none");
  });
});

describe("cross-view consistency", () => {
  it("resolves the same order to the same facts from both perspectives", () => {
    const buyer = unifyBuyerOrder({
      id: 2384,
      order_id: "PL-2384",
      item_title: "Walnut side table",
      amount_cents: 9500,
      currency: "USD",
      status: "shipped"
    } as never);
    const seller = unifySellerOrder({
      id: 2384,
      item_type: "marketplace_listing",
      amount_cents: 9500,
      currency: "USD",
      status: "shipped"
    } as never);

    // One order, two ends: id, status, variant and overlay must agree.
    expect(seller.id).toBe(buyer.id);
    expect(seller.status).toBe(buyer.status);
    expect(seller.variant).toBe(buyer.variant);
    expect(seller.overlay).toBe(buyer.overlay);
    // And the reached step is identical, so both timelines fill to the same point.
    expect(reachedStepIndex(seller.status, seller.variant)).toBe(
      reachedStepIndex(buyer.status, buyer.variant)
    );
  });

  it("keeps the human reference stable across perspectives", () => {
    const buyer = unifyBuyerOrder({ id: 2384, order_id: "#PL-2384", amount_cents: 100 } as never);
    const seller = unifySellerOrder({ id: 2384, item_type: "listing", amount_cents: 100 } as never);
    expect(buyer.reference).toBe("PL-2384");
    expect(seller.reference).toBe("PL-2384");
  });
});

describe("escrow gating (money-critical)", () => {
  const pickupBuyer = {
    id: 1,
    amount_cents: 100,
    status: "paid",
    listing: { delivery_type: "pickup" }
  };

  it("withholds the escrow presentation by default", () => {
    const order = unifyBuyerOrder(pickupBuyer as never);
    expect(order.variant).toBe("pickup");
    expect(order.escrowPresentable).toBe(false);
  });

  it("presents escrow only when the flag is on AND the order is pickup", () => {
    process.env.EXPO_PUBLIC_ORDERS_ESCROW = "1";
    const pickup = unifyBuyerOrder(pickupBuyer as never);
    const shipping = unifyBuyerOrder({ id: 2, amount_cents: 100, status: "paid" } as never);
    expect(pickup.escrowPresentable).toBe(true);
    expect(shipping.escrowPresentable).toBe(false);
  });
});

describe("seller fulfillment actions", () => {
  const shippingPaid: UnifiedOrder = unifySellerOrder({
    id: 5,
    item_type: "listing",
    amount_cents: 100,
    status: "paid"
  } as never);

  it("offers only disabled previews when fulfillment is not live", () => {
    const actions = sellerActionsFor(shippingPaid);
    const pack = actions.find((a) => a.key === "mark_packed");
    const ship = actions.find((a) => a.key === "mark_shipped");
    expect(pack?.enabled).toBe(false);
    expect(pack?.preview).toBe(true);
    expect(ship?.enabled).toBe(false);
    expect(ship?.preview).toBe(true);
  });

  it("always offers View payout as a live, ungated action", () => {
    const payout = sellerActionsFor(shippingPaid).find((a) => a.key === "view_payout");
    expect(payout?.enabled).toBe(true);
    expect(payout?.preview).toBe(false);
  });

  it("requires tracking before shipping even when fulfillment is live", () => {
    process.env.EXPO_PUBLIC_ORDERS_FULFILLMENT = "1";
    const noTracking = sellerActionsFor(shippingPaid).find((a) => a.key === "mark_shipped");
    expect(noTracking?.enabled).toBe(false);
    expect(noTracking?.reason).toMatch(/tracking/i);

    const withTracking = sellerActionsFor({
      ...shippingPaid,
      tracking: { available: true, number: "1Z999" }
    }).find((a) => a.key === "mark_shipped");
    expect(withTracking?.enabled).toBe(true);
  });

  it("blocks fulfillment actions on a cancelled or refunded order", () => {
    const cancelled = unifySellerOrder({
      id: 6,
      item_type: "listing",
      amount_cents: 100,
      status: "cancelled"
    } as never);
    const keys = sellerActionsFor(cancelled).map((a) => a.key);
    expect(keys).not.toContain("mark_packed");
    expect(keys).not.toContain("mark_shipped");
    // Payout stays reachable — the seller may still need the receipt trail.
    expect(keys).toContain("view_payout");
  });
});

describe("loaders", () => {
  it("returns live orders and offline:false on success", async () => {
    mockListBuyer.mockResolvedValue({ orders: [{ id: 9, amount_cents: 100, status: "paid" }] });
    const model = await loadBuyerOrdersModel();
    expect(model.offline).toBe(false);
    expect(model.orders).toHaveLength(1);
    expect(model.orders[0].id).toBe(9);
  });

  it("falls back to cache with offline:true when the live read fails", async () => {
    mockSnapshot.mockRejectedValue(new Error("network"));
    mockCachedSeller.mockResolvedValue({
      orders: [{ id: 3, item_type: "listing", amount_cents: 100, status: "paid" }]
    });
    const model = await loadSellerOrdersModel();
    expect(model.offline).toBe(true);
    expect(model.orders).toHaveLength(1);
    expect(model.error).toBeTruthy();
  });

  it("degrades to an empty offline model when both live and cache fail", async () => {
    mockListBuyer.mockRejectedValue(new Error("network"));
    mockCachedBuyer.mockRejectedValue(new Error("no cache"));
    const model = await loadBuyerOrdersModel();
    expect(model.offline).toBe(true);
    expect(model.orders).toEqual([]);
  });
});

describe("MOCK-DATA gap ledger", () => {
  it("pins the declared gap count so closing or adding one is deliberate", () => {
    expect(ORDERS_MOCK_DATA_GAP_COUNT).toBe(7);
    expect(ORDERS_MOCK_DATA_GAPS).toHaveLength(7);
  });

  it("declares every gap with the backend work it needs", () => {
    ORDERS_MOCK_DATA_GAPS.forEach((gap) => {
      expect(gap.field).toBeTruthy();
      expect(gap.backendWork).toBeTruthy();
      expect(["seller", "buyer", "both"]).toContain(gap.perspective);
    });
  });
});
