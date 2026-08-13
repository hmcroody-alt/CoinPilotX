import { Platform } from "react-native";
import { createPaymentIntent, verifyApplePremiumPurchase } from "../api/payments";
import {
  extractSignedTransaction,
  loadStoreKitAdapter,
  StoreKitAdapter
} from "./appleIapAdCredits";

export type PremiumPlan = "monthly" | "annual";
export type PremiumPurchaseResult =
  | { status: "verified"; productId: string }
  | { status: "cancelled" | "unavailable" | "failed" | "verification_pending" };

function cancelled(error: unknown) {
  return /cancel/i.test(String((error as { code?: unknown; message?: unknown })?.code || "")) ||
    /cancel/i.test(String((error as { message?: unknown })?.message || ""));
}

export async function purchasePremium(
  plan: PremiumPlan,
  deps: { adapter?: StoreKitAdapter | null } = {}
): Promise<PremiumPurchaseResult> {
  const instruction = await createPaymentIntent({
    platform: Platform.OS,
    purchaseContext: "premium",
    plan
  });
  if (!instruction.ok || instruction.provider !== "apple_iap" ||
      instruction.flow !== "storekit" || !instruction.appleProductId) {
    return { status: "unavailable" };
  }
  const adapter = deps.adapter !== undefined ? deps.adapter : loadStoreKitAdapter();
  if (!adapter) return { status: "unavailable" };
  try {
    await adapter.initConnection();
    const purchase = await adapter.requestPurchase(
      instruction.appleProductId, "subs", instruction.appAccountToken
    );
    if (!purchase) return { status: "failed" };
    const signed = extractSignedTransaction(purchase);
    if (!signed) return { status: "verification_pending" };
    const verified = await verifyApplePremiumPurchase(signed);
    if (!verified.ok || !verified.verified) return { status: "verification_pending" };
    await adapter.finishTransaction(purchase, false);
    return { status: "verified", productId: instruction.appleProductId };
  } catch (error) {
    return { status: cancelled(error) ? "cancelled" : "verification_pending" };
  }
}
