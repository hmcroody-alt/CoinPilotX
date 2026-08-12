import { marketplaceCheckoutStage } from "../marketplaceCheckoutState";
import type { MarketplacePaymentOrder } from "../marketplaceCommerce";

function order(paymentStatus: MarketplacePaymentOrder["paymentStatus"], fulfilled = false): MarketplacePaymentOrder {
  return { id: 1, status: paymentStatus, paymentStatus, fulfilled };
}

describe("marketplaceCheckoutStage", () => {
  it("never treats an empty or pending provider response as success", () => {
    expect(marketplaceCheckoutStage([])).toBe("processing");
    expect(marketplaceCheckoutStage([order("pending")])).toBe("processing");
  });

  it("requires every server transaction to be paid and fulfilled", () => {
    expect(marketplaceCheckoutStage([order("paid", true), order("paid", true)])).toBe("confirmed");
    expect(marketplaceCheckoutStage([order("paid", true), order("pending")])).toBe("processing");
    expect(marketplaceCheckoutStage([order("paid", false)])).toBe("processing");
  });

  it("surfaces authoritative failure and cancellation", () => {
    expect(marketplaceCheckoutStage([order("failed")])).toBe("failed");
    expect(marketplaceCheckoutStage([order("canceled")])).toBe("failed");
  });
});
