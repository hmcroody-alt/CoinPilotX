jest.mock("../../api/payments", () => ({ createPaymentIntent: jest.fn() }));
jest.mock("../../api/adsWallet", () => ({ createAdFundingSession: jest.fn() }));
jest.mock("../../api/stripePaymentSheet", () => ({
  isPaymentSheetAvailable: jest.fn(),
  presentPaymentSheet: jest.fn()
}));
jest.mock("../appleIapAdCredits", () => ({ purchaseAdCredits: jest.fn() }));
jest.mock("../appleIapPremium", () => ({ purchasePremium: jest.fn() }));

import { createPaymentIntent } from "../../api/payments";
import { createAdFundingSession } from "../../api/adsWallet";
import { isPaymentSheetAvailable, presentPaymentSheet } from "../../api/stripePaymentSheet";
import { PaymentController } from "../PaymentController";

describe("PaymentController Stripe Ad Wallet funding", () => {
  beforeEach(() => jest.resetAllMocks());

  it("asks the server for policy before presenting PaymentSheet", async () => {
    (createPaymentIntent as jest.Mock).mockResolvedValue({
      ok: true, provider: "stripe", flow: "payment_sheet"
    });
    (isPaymentSheetAvailable as jest.Mock).mockReturnValue(true);
    (createAdFundingSession as jest.Mock).mockResolvedValue({
      id: 44,
      sheet: { clientSecret: "pi_secret", transactionIds: [] }
    });
    (presentPaymentSheet as jest.Mock).mockResolvedValue({ result: "completed" });

    await expect(PaymentController.fundAdWallet(8, 2500)).resolves.toMatchObject({
      status: "completed", fundingSessionId: 44
    });
    expect(createPaymentIntent).toHaveBeenCalledWith(expect.objectContaining({
      purchaseContext: "ad_credits"
    }));
    expect(createAdFundingSession).toHaveBeenCalledWith(8, 2500, "payment_sheet");
  });

  it("never creates a Stripe intent when policy selects StoreKit", async () => {
    (createPaymentIntent as jest.Mock).mockResolvedValue({
      ok: true, provider: "apple_iap", flow: "storekit"
    });
    await expect(PaymentController.fundAdWallet(8, 2500)).resolves.toMatchObject({
      status: "wrong_provider"
    });
    expect(createAdFundingSession).not.toHaveBeenCalled();
  });
});
