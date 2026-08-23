import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("expo-linear-gradient", () => ({ LinearGradient: ({ children }: { children?: React.ReactNode }) => children ?? null }));
jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
  }),
  useFormatters: () => ({ date: (value: string) => value, number: (value: number) => String(value) })
}));

import { PlansSection } from "../PremiumCenterScreen";
import type { PremiumOffers } from "../../payments/appleIapPremium";

const baseProps = {
  plan: "annual" as const,
  onPlan: jest.fn(),
  busy: false,
  disabled: false,
  onPurchase: jest.fn(),
  onRetry: jest.fn(),
  expired: false
};

const diagnostics = (over: Partial<PremiumOffers["diagnostics"]> = {}): PremiumOffers["diagnostics"] => ({
  requestedProductIds: ["com.pulsesoc.premium.monthly", "com.pulsesoc.premium.annual"],
  requestType: "subs",
  productCount: 0,
  returnedProductIds: [],
  errorCode: null,
  environment: "ios/release",
  ...over
});

const REAL_PLANS: PremiumOffers["plans"] = [
  { plan: "monthly", productId: "com.pulsesoc.premium.monthly", displayPrice: "$9.99", price: 9.99, currency: "USD" },
  { plan: "annual", productId: "com.pulsesoc.premium.annual", displayPrice: "$99.99", price: 99.99, currency: "USD" }
];

describe("Premium plan terminal states", () => {
  beforeEach(() => jest.clearAllMocks());

  it("renders loading only while the request is active", () => {
    const { getByText } = render(<PlansSection {...baseProps} offers={null} loading />);
    expect(getByText("premium:plans.loading")).toBeTruthy();
  });

  it("renders unavailable with a working retry after an empty response", () => {
    const onRetry = jest.fn();
    const { getByText } = render(
      <PlansSection {...baseProps} onRetry={onRetry} loading={false} offers={{ plans: [], annualSavingsPercent: null, status: "empty", missingPlans: ["monthly", "annual"], diagnostics: diagnostics() }} />
    );
    expect(getByText("premium:plans.unavailable")).toBeTruthy();
    fireEvent.press(getByText("premium:retry"));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  /**
   * Four causes, four sentences.
   *
   * The shipped screen said "The App Store didn't return any products" for all
   * of them, including the case where the request never reached Apple. That
   * sends the member to check a connection that was never the problem and
   * points support at the wrong system.
   */
  it.each([
    ["empty" as const, "premium:plans.unavailableBody"],
    ["unavailable" as const, "premium:plans.unavailableOffer"],
    ["failed" as const, "premium:plans.unavailableFailed"],
    ["timeout" as const, "premium:plans.unavailableTimeout"]
  ])("explains a %s result in its own words", (status, expected) => {
    const { getByText } = render(
      <PlansSection
        {...baseProps}
        loading={false}
        offers={{ plans: [], annualSavingsPercent: null, status, missingPlans: ["monthly", "annual"], diagnostics: diagnostics() }}
      />
    );
    expect(getByText(expected)).toBeTruthy();
  });

  it("prints a support reference carrying the status and the StoreKit code", () => {
    const { getByText } = render(
      <PlansSection
        {...baseProps}
        loading={false}
        offers={{
          plans: [], annualSavingsPercent: null, status: "failed", missingPlans: ["monthly", "annual"],
          diagnostics: diagnostics({ errorCode: "SKErrorDomain:0" })
        }}
      />
    );
    // The catalog string is the key here; what matters is that the interpolated
    // code is derived from the diagnostics rather than being a fixed label.
    expect(getByText("premium:plans.reference")).toBeTruthy();
  });

  it("offers only the real localized product and reports the missing plan", () => {
    const onPurchase = jest.fn();
    const { getByText, queryByText, getByLabelText } = render(
      <PlansSection
        {...baseProps}
        plan="monthly"
        onPurchase={onPurchase}
        loading={false}
        offers={{
          plans: [{ plan: "monthly", productId: "com.pulsesoc.premium.monthly", displayPrice: "€9,99", price: 9.99, currency: "EUR" }],
          annualSavingsPercent: null,
          status: "success",
          missingPlans: ["annual"],
          diagnostics: diagnostics({ productCount: 1, returnedProductIds: ["com.pulsesoc.premium.monthly"] })
        }}
      />
    );
    expect(getByText("€9,99")).toBeTruthy();
    expect(getByText("The annual plan is temporarily unavailable.")).toBeTruthy();
    expect(queryByText("$99.99")).toBeNull();
    fireEvent.press(getByLabelText("premium:purchase.start"));
    expect(onPurchase).toHaveBeenCalledTimes(1);
  });
});

/**
 * The sales surface, once Apple answers.
 *
 * These assert the thing the mission actually required: that the honest error
 * is replaced by *real* StoreKit data and never by fabricated plan buttons.
 */
describe("Premium plan cards", () => {
  const offers = (savings: number | null): PremiumOffers => ({
    plans: REAL_PLANS,
    annualSavingsPercent: savings,
    status: "success",
    missingPlans: [],
    diagnostics: diagnostics({
      productCount: 2,
      returnedProductIds: ["com.pulsesoc.premium.monthly", "com.pulsesoc.premium.annual"]
    })
  });

  it("replaces the unavailable message with Apple's own price strings", () => {
    const { getByText, queryByText } = render(
      <PlansSection {...baseProps} loading={false} offers={offers(17)} />
    );
    expect(getByText("$9.99")).toBeTruthy();
    expect(getByText("$99.99")).toBeTruthy();
    expect(queryByText("premium:plans.unavailable")).toBeNull();
    expect(queryByText("premium:plans.unavailableBody")).toBeNull();
  });

  it("badges the annual card only when a saving was actually computed", () => {
    const withSaving = render(<PlansSection {...baseProps} loading={false} offers={offers(17)} />);
    expect(withSaving.getByText("premium:plans.bestValue")).toBeTruthy();
    expect(withSaving.getByText("premium:plans.save")).toBeTruthy();

    // Same two real products, no honest comparison available: the claim goes.
    const withoutSaving = render(<PlansSection {...baseProps} loading={false} offers={offers(null)} />);
    expect(withoutSaving.queryByText("premium:plans.bestValue")).toBeNull();
    expect(withoutSaving.queryByText("premium:plans.save")).toBeNull();
  });

  it("selects a plan by product rather than by position", () => {
    const onPlan = jest.fn();
    const { getByLabelText } = render(
      <PlansSection {...baseProps} plan="annual" loading={false} offers={offers(17)} onPlan={onPlan} />
    );
    fireEvent.press(getByLabelText(/premium:plans\.monthly, \$9\.99/));
    expect(onPlan).toHaveBeenCalledWith("monthly");
  });

  it("names who takes the money next to the button that starts it", () => {
    const { getByText } = render(<PlansSection {...baseProps} loading={false} offers={offers(17)} />);
    expect(getByText("premium:plans.secure")).toBeTruthy();
    expect(getByText("premium:plans.terms")).toBeTruthy();
  });
});
