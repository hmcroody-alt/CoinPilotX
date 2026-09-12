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
      status: "shipped",
      fulfillment_kind: "shipping"
    } as never);
    const seller = unifySellerOrder({
      id: 2384,
      item_type: "marketplace_listing",
      amount_cents: 9500,
      currency: "USD",
      status: "shipped",
      fulfillment_kind: "shipping"
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

  it("agrees on a pickup order, which is the only case that could disagree", () => {
    // The test above passes a shipping order to both ends. That could not have
    // caught the real defect, because both ends were shipping-*only*: the buyer
    // path read `order.delivery_type`, which is never served, and the seller
    // path passed `item_type` — "marketplace_product" on every row — into a
    // parameter named `deliveryType`. Two broken derivations agreeing on the
    // wrong answer is what "cross-view consistency" was measuring.
    //
    // So this asserts the same property on the lane that distinguishes them.
    for (const kind of ["pickup", "shipping", "digital", "booking_in_person"]) {
      const buyer = unifyBuyerOrder({ id: 9, amount_cents: 100, status: "paid", fulfillment_kind: kind } as never);
      const seller = unifySellerOrder({
        id: 9, item_type: "marketplace_product", amount_cents: 100, status: "paid", fulfillment_kind: kind
      } as never);
      expect(seller.variant).toBe(buyer.variant);
      // ...and they agree because two separate derivations reached the same
      // answer, not because one delegated to the other. These fields are
      // perspective-specific by definition, so if they ever match, the
      // agreement above is measuring nothing.
      expect(seller.counterpartyName).toBe("Buyer");
      expect(buyer.counterpartyName).not.toBe("Buyer");
      expect(seller.raw.seller).toBeDefined();
      expect(seller.raw.buyer).toBeUndefined();
      expect(buyer.raw.buyer).toBeDefined();
      expect(buyer.raw.seller).toBeUndefined();
    }
  });

  it("speaks fulfilment kinds, not the listing's lane vocabulary", () => {
    // `deliveryLane` folds "local" and "meetup" onto pickup — that is the
    // vocabulary a *listing* uses. `order_kind` only ever emits a member of
    // `KINDS`, and neither word is one. Honouring them here would mean the
    // client still understands the old language, so a payload that regressed to
    // sending lane words would be silently accepted instead of failing loudly.
    for (const laneWord of ["local", "meetup", "both", "delivery", "pickup_or_shipping"]) {
      expect(unifyBuyerOrder({ id: 1, fulfillment_kind: laneWord } as never).variant).toBe("shipping");
    }
  });

  it("puts a collected order on the pickup timeline and a posted one on shipping", () => {
    // `variant` selects which strip of step labels the buyer reads. Getting it
    // wrong tells someone waiting to collect an item in person that it is "On
    // its way", and never that it is ready.
    expect(unifyBuyerOrder({ id: 1, fulfillment_kind: "pickup" } as never).variant).toBe("pickup");
    expect(unifyBuyerOrder({ id: 1, fulfillment_kind: "shipping" } as never).variant).toBe("shipping");
    // The in-person kinds are collected too — the goods change hands rather
    // than travelling — so they get the same strip.
    expect(unifyBuyerOrder({ id: 1, fulfillment_kind: "service_in_person" } as never).variant).toBe("pickup");
    expect(unifyBuyerOrder({ id: 1, fulfillment_kind: "booking_in_person" } as never).variant).toBe("pickup");
    expect(unifyBuyerOrder({ id: 1, fulfillment_kind: "event_in_person" } as never).variant).toBe("pickup");
  });

  it("does not invent a pickup from an order that declared no lane", () => {
    // Pickup unlocks the escrow/safety panel, so it is the worst thing to
    // guess. A legacy row the server could not resolve stays on shipping.
    for (const kind of [undefined, "", "  ", "marketplace_product", "shipping_or_pickup", "digital"]) {
      expect(unifyBuyerOrder({ id: 1, fulfillment_kind: kind } as never).variant).toBe("shipping");
      expect(unifySellerOrder({ id: 1, item_type: "marketplace_product", fulfillment_kind: kind } as never).variant)
        .toBe("shipping");
    }
  });

  it("no longer answers from a field the server does not send", () => {
    // Both of these were the old inputs. Honouring either now would mean the
    // lane could be set by something other than the order's own frozen kind.
    expect(unifyBuyerOrder({ id: 1, delivery_type: "pickup" } as never).variant).toBe("shipping");
    expect(unifyBuyerOrder({ id: 1, listing: { delivery_type: "pickup" } } as never).variant).toBe("shipping");
    // And the seller path can no longer be steered by the row kind.
    expect(unifySellerOrder({ id: 1, item_type: "pickup" } as never).variant).toBe("shipping");
  });

  it("keeps the human reference stable across perspectives", () => {
    const buyer = unifyBuyerOrder({ id: 2384, order_id: "#PL-2384", amount_cents: 100 } as never);
    const seller = unifySellerOrder({ id: 2384, item_type: "listing", amount_cents: 100 } as never);
    expect(buyer.reference).toBe("PL-2384");
    expect(seller.reference).toBe("PL-2384");
  });
});

describe("escrow gating (money-critical)", () => {
  // This fixture used to read `listing: { delivery_type: "pickup" }`. No order
  // endpoint has ever served that field — not at the top level and not on the
  // joined listing, whose columns are named explicitly — so the fixture
  // described a payload the server cannot produce, and the test passed on an
  // input production never sends. In production the argument was always
  // `undefined`, `variant` was always "shipping", and `escrowPresentable` was
  // therefore always false: this whole block was green while the feature it
  // gates was unreachable.
  //
  // `fulfillment_kind` is what the server sends: the settled lane, frozen onto
  // the transaction at checkout.
  const pickupBuyer = {
    id: 1,
    amount_cents: 100,
    status: "paid",
    fulfillment_kind: "pickup"
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

  it("is reachable at all, which it was not", () => {
    // `escrowPresentable` is `flag && variant === "pickup"`, and no served
    // payload could make `variant` pickup. So with the flag fully on, this was
    // false for every order the app had ever rendered: the escrow panel was an
    // unreachable screen, and the flag gating it gated nothing.
    process.env.EXPO_PUBLIC_ORDERS_ESCROW = "1";
    const seller = unifySellerOrder({
      id: 3, item_type: "marketplace_product", amount_cents: 100, status: "paid",
      fulfillment_kind: "pickup"
    } as never);
    expect(seller.escrowPresentable).toBe(true);
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

describe("cash settlement (money-critical)", () => {
  const cashOrder = unifySellerOrder({
    id: 77,
    item_type: "listing",
    amount_cents: 5000,
    status: "cash_pending"
  } as never);

  it("keeps the cash flag even though normalizeStatus collapses the status to pending", () => {
    // `cash_pending` is not one of normalizeStatus's known states, so it lands
    // on "pending" alongside every card order awaiting Stripe. Without the
    // separate flag the settle action would be unreachable.
    expect(cashOrder.status).toBe("pending");
    expect(cashOrder.awaitingCash).toBe(true);
  });

  it("offers a live settle action, not a flag-gated preview", () => {
    // The fulfillment flag is off in this build and must not suppress this one:
    // a cash order is unpaid forever until a seller taps it.
    delete process.env.EXPO_PUBLIC_ORDERS_FULFILLMENT;
    const settle = sellerActionsFor(cashOrder).find((a) => a.key === "collect_cash");
    expect(settle?.enabled).toBe(true);
    expect(settle?.preview).toBe(false);
  });

  it("does not offer pack or ship on an order that has not been paid for yet", () => {
    const keys = sellerActionsFor(cashOrder).map((a) => a.key);
    expect(keys).not.toContain("mark_packed");
    expect(keys).not.toContain("mark_shipped");
  });

  it("withdraws the settle action once the order is paid", () => {
    const paid = unifySellerOrder({ id: 77, item_type: "listing", amount_cents: 5000, status: "paid" } as never);
    expect(paid.awaitingCash).toBe(false);
    expect(sellerActionsFor(paid).map((a) => a.key)).not.toContain("collect_cash");
  });

  it("withdraws the settle action on a refunded or cancelled order", () => {
    const cancelled: UnifiedOrder = { ...cashOrder, overlay: "cancelled" };
    expect(sellerActionsFor(cancelled).map((a) => a.key)).not.toContain("collect_cash");
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
