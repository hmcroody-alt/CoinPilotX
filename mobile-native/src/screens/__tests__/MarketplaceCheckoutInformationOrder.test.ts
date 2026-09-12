import { readFileSync } from "fs";
import { join } from "path";

const source = readFileSync(join(__dirname, "..", "MarketplaceCheckoutScreen.tsx"), "utf8");

describe("checkout asks before it charges", () => {
  it("opens on the details step whenever the order type asks for anything", () => {
    expect(source).toContain('useState<Stage>(needsDetailsStep ? "details" : "review")');
    expect(source).toContain("const needsDetailsStep = mustChooseLane || fulfillmentFields(declaredKind, tickets).length");
  });

  it("no longer defers the delivery address to the payment page", () => {
    // The sentence this replaces was the architecture: the address was first
    // asked for by Stripe, after the buyer had committed to paying.
    expect(source).not.toContain("delivery address on the secure payment page");
    expect(source).toContain("collectAddress: false");
  });

  it("will not leave the details step with a required field blank", () => {
    expect(source).toContain("firstMissingFulfillmentField(kind, tickets, details)");
  });

  it("sends what the buyer typed to every checkout lane", () => {
    // Cart and single-listing, once per payment method. Derived from the call
    // sites rather than fixed at two, so adding a payment method cannot quietly
    // ship a lane that drops the details the buyer just typed.
    const lanes = source.match(/\b(?:checkoutCartGroup|openMarketplaceCheckout)\(/g) ?? [];
    const forwarded = source.match(/mustChooseLane \? lane : "",\s*paymentMode,\s*details/g) ?? [];
    expect(lanes.length).toBeGreaterThanOrEqual(2);
    expect(forwarded).toHaveLength(lanes.length);
  });

  it("sends how many the buyer chose to every buy-now lane", () => {
    // Counted the same way, and for the same reason. The card lane is currently
    // unreachable — `MARKETPLACE_CARD_PAYMENTS_PAUSED` returns before it — so no
    // test that drives the UI can reach its call site, and a quantity added to
    // the cash lane alone would look complete right up until card payments
    // resume. Buy Now is the only lane that carries a quantity as an argument:
    // the cart's quantities live on the cart lines.
    const buyNow = source.match(/\bopenMarketplaceCheckout\(/g) ?? [];
    const carried = source.match(/Number\(params\.quantity \|\| 1\)/g) ?? [];
    expect(buyNow.length).toBeGreaterThanOrEqual(2);
    expect(carried).toHaveLength(buyNow.length);
  });

  it("returns to the details step to edit rather than restarting checkout", () => {
    expect(source).toContain('setStage("details")');
    expect(source).toContain("Edit order details");
  });
});
