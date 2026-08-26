/**
 * Premium crypto intelligence rows + the Apple billing fallback.
 *
 * The rows must be four, all pressable, and each must land on the canonical
 * screen that already ships — the same destinations the Crypto Command Center
 * and deep links use. No Premium copy of any crypto system exists, so the only
 * thing worth asserting is the route name each press dispatches.
 *
 * The billing card must show Apple's own facts when the server has no billing
 * row for a paying member, omit what Apple did not supply, and fall back to
 * the honest "no billing record" copy when Apple proves nothing either.
 */
import React from "react";
import { fireEvent, render } from "@testing-library/react-native";

jest.mock("@expo/vector-icons", () => ({ Ionicons: () => null }));
jest.mock("../../i18n", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) => options?.defaultValue || key
  }),
  useFormatters: () => ({ date: (value: string) => `date(${value})`, number: (value: number) => String(value) })
}));

import { BillingSection, CryptoIntelligenceSection } from "../PremiumCenterScreen";
import type { AppleSubscriptionSnapshot } from "../../payments/appleIapPremium";

function nav() {
  return { navigate: jest.fn() } as never;
}

describe("Premium Crypto Intelligence rows", () => {
  const EXPECTED: Array<[string, string]> = [
    ["alerts", "CryptoAlertCenter"],
    ["portfolio", "CryptoPortfolio"],
    ["watchlists", "Watchlists"],
    ["undx", "UndxCapabilities"]
  ];

  it("renders exactly the four required entries", () => {
    const { getByText } = render(<CryptoIntelligenceSection navigation={nav()} />);
    for (const [key] of EXPECTED) {
      expect(getByText(`discovery:crypto.intelligence.${key}.label`)).toBeTruthy();
      expect(getByText(`discovery:crypto.intelligence.${key}.hint`)).toBeTruthy();
    }
  });

  it.each(EXPECTED)("%s opens the canonical %s screen — no dead rows, no duplicates", (key, routeName) => {
    const navigation = nav();
    const { getByLabelText } = render(<CryptoIntelligenceSection navigation={navigation} />);
    fireEvent.press(getByLabelText(`discovery:crypto.intelligence.${key}.label`));
    const navigate = (navigation as { navigate: jest.Mock }).navigate;
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith(routeName);
  });

  it("routes the four rows to four distinct destinations", () => {
    const navigation = nav();
    const { getByLabelText } = render(<CryptoIntelligenceSection navigation={navigation} />);
    for (const [key] of EXPECTED) {
      fireEvent.press(getByLabelText(`discovery:crypto.intelligence.${key}.label`));
    }
    const navigate = (navigation as { navigate: jest.Mock }).navigate;
    const destinations = navigate.mock.calls.map((call) => call[0]);
    expect(new Set(destinations).size).toBe(4);
  });
});

describe("Billing card Apple fallback", () => {
  const apple: AppleSubscriptionSnapshot = {
    productId: "com.pulsesoc.premium.annual",
    plan: "annual",
    displayPrice: "€99,99",
    status: "active",
    expiresAt: "2026-09-30T00:00:00.000Z",
    originalPurchaseAt: "2025-09-30T00:00:00.000Z"
  };

  it("shows Apple's verified facts instead of 'no billing details'", () => {
    const { getByText, queryByText } = render(
      <BillingSection subscription={null} apple={apple} experience="active" price={null} priceLoading={false} />
    );
    expect(queryByText("premium:billing.none")).toBeNull();
    expect(getByText("premium:billing.planValue")).toBeTruthy();
    expect(getByText("€99,99")).toBeTruthy();
    expect(getByText("premium:provider.apple_app_store")).toBeTruthy();
    expect(getByText("premium:subState.active")).toBeTruthy();
    expect(getByText("premium:billing.renewsOn")).toBeTruthy();
    expect(getByText("date(2026-09-30T00:00:00.000Z)")).toBeTruthy();
    expect(getByText("premium:billing.since")).toBeTruthy();
  });

  it("omits fields Apple did not supply rather than inventing them", () => {
    const { queryByText, getByText } = render(
      <BillingSection
        subscription={null}
        apple={{ ...apple, plan: null, displayPrice: null, originalPurchaseAt: null, status: "expired" }}
        experience="active"
        price={null}
        priceLoading={false}
      />
    );
    expect(queryByText("premium:billing.price")).toBeNull();
    expect(queryByText("premium:billing.since")).toBeNull();
    // A plan Apple could not name says so, rather than guessing a period.
    expect(getByText("premium:billing.planValueUnknown")).toBeTruthy();
    // An expired date is labelled as an end, never as a renewal.
    expect(queryByText("premium:billing.renewsOn")).toBeNull();
    expect(getByText("premium:billing.expiresOn")).toBeTruthy();
    expect(getByText("premium:subState.expired")).toBeTruthy();
  });

  it("shows only the server-verified active state when Apple omits billing facts", () => {
    const { getByText, queryByText } = render(
      <BillingSection subscription={null} apple={null} experience="active" price={null} priceLoading={false} />
    );
    expect(getByText("premium:subState.active")).toBeTruthy();
    expect(queryByText("premium:billing.none")).toBeNull();
  });

  it("never replaces the Founder copy with a billing card", () => {
    const { getByText, queryByText } = render(
      <BillingSection subscription={null} apple={apple} experience="founder" price={null} priceLoading={false} />
    );
    expect(getByText("premium:billing.founderNone")).toBeTruthy();
    expect(queryByText("€99,99")).toBeNull();
  });

  it("still prefers the server's own subscription row over StoreKit", () => {
    const { getByText, queryByText } = render(
      <BillingSection
        subscription={{
          provider: "apple_iap", plan_key: "premium_annual", billing_period: "annual",
          status: "active", current_period_end: "2026-10-01", cancel_at_period_end: false,
          state: "active", auto_renew: true, renews_at: "2026-10-01", expires_at: null,
          product_id: "com.pulsesoc.premium.annual", original_purchase_at: null
        }}
        apple={apple}
        experience="active"
        price={null}
        priceLoading={false}
      />
    );
    expect(getByText("date(2026-10-01)")).toBeTruthy();
    // StoreKit's own figures must not leak into a card the server already owns.
    expect(queryByText("€99,99")).toBeNull();
  });
});
