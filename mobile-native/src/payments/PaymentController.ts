/** One client orchestrator for server-selected payment policy. */
import { Platform } from "react-native";
import { createPaymentIntent, PaymentInstruction } from "../api/payments";
import { purchaseAdCredits } from "./appleIapAdCredits";
import { PremiumPlan, purchasePremium } from "./appleIapPremium";

export async function paymentInstruction(
  purchaseContext: string,
  options: { resourceId?: number | string; quantity?: number; plan?: PremiumPlan; productId?: string } = {}
): Promise<PaymentInstruction> {
  return createPaymentIntent({ platform: Platform.OS, purchaseContext, ...options });
}

export const PaymentController = {
  instruction: paymentInstruction,
  purchaseAdCredits,
  purchasePremium
};
