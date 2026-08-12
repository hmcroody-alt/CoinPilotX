import type { MarketplacePaymentOrder } from "./marketplaceCommerce";

export type MarketplaceCheckoutStage = "processing" | "confirmed" | "failed";

/** Pure interpretation of authoritative PulseSoc order states. */
export function marketplaceCheckoutStage(
  orders: readonly MarketplacePaymentOrder[]
): MarketplaceCheckoutStage {
  if (orders.length > 0 && orders.every((order) => order.paymentStatus === "paid" && order.fulfilled)) {
    return "confirmed";
  }
  if (orders.some((order) => ["failed", "canceled"].includes(order.paymentStatus))) {
    return "failed";
  }
  return "processing";
}
