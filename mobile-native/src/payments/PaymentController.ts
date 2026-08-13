/** One client orchestrator for server-selected payment policy. */
import { Platform } from "react-native";
import { createPaymentIntent, PaymentInstruction } from "../api/payments";
import { purchaseAdCredits } from "./appleIapAdCredits";
import { PremiumPlan, purchasePremium } from "./appleIapPremium";
import { createAdFundingSession } from "../api/adsWallet";
import { isPaymentSheetAvailable, presentPaymentSheet } from "../api/stripePaymentSheet";

export async function paymentInstruction(
  purchaseContext: string,
  options: { resourceId?: number | string; quantity?: number; plan?: PremiumPlan; productId?: string } = {}
): Promise<PaymentInstruction> {
  return createPaymentIntent({ platform: Platform.OS, purchaseContext, ...options });
}

export const PaymentController = {
  instruction: paymentInstruction,
  purchaseAdCredits,
  purchasePremium,
  async fundAdWallet(accountId: number, amountCents: number) {
    const instruction = await paymentInstruction("ad_credits");
    if (!instruction.ok || instruction.provider !== "stripe" || instruction.flow !== "payment_sheet") {
      return { status: "wrong_provider" as const, instruction };
    }
    if (!isPaymentSheetAvailable()) return { status: "unavailable" as const, instruction };
    const session = await createAdFundingSession(accountId, amountCents, "payment_sheet");
    if (!session.sheet) return { status: "unavailable" as const, instruction };
    const outcome = await presentPaymentSheet(session.sheet);
    return { status: outcome.result, outcome, instruction, fundingSessionId: session.id };
  }
};
