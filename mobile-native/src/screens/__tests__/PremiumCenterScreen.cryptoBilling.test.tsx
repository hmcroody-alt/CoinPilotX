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
    ["premium-crypto-alerts", "CryptoAlertCenter"],
    ["premium-crypto-portfolio", "CryptoPortfolio"],
    ["premium-crypto-watchlist", "Watchlists"],
    ["premium-crypto-undx", "UndxCapabilities"]
  ];

  it("renders exactly the four required entries", () => {
    const { getByTestId, getByText } = render(<CryptoIntelligenceSection navigation={nav()} />);
    for (const [testID] of EXPECTED) expect(getByTestId(testID)).toBeTruthy();
    expect(getByText("discovery:crypto.intelligence.watchlist.label")).toBeTruthy();
    expect(getByText("discovery:crypto.intelligence.watchlist.hint")).toBeTruthy();
  });

  it.each(EXPECTED)("%s opens the canonical %s screen — no dead rows, no duplicates", (testID, routeName) => {
    const navigation = nav();
    const { getByTestId } = render(<CryptoIntelligenceSection navigation={navigation} />);
    fireEvent.press(getByTestId(testID));
    const navigate = (navigation as { navigate: jest.Mock }).navigate;
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith(routeName);
  });

  it("routes the four rows to four distinct destinations", () => {
    const navigation = nav();
    const { getByTestId } = render(<CryptoIntelligenceSection navigation={navigation} />);
    for (const [testID] of EXPECTED) fireEvent.press(getByTestId(testID));
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
      <BillingSection subscription={null} apple={apple} experience="active" />
    );
    expect(queryByText("premium:billing.none")).toBeNull();
    expect(getByText("premium:period.annual")).toBeTruthy();
    expect(getByText("€99,99")).toBeTruthy();
    expect(getByText("premium:provider.apple_iap")).toBeTruthy();
    expect(getByText("premium:subStatus.active")).toBeTruthy();
    expect(getByText("premium:billing.activeUntil")).toBeTruthy();
    expect(getByText("date(2026-09-30T00:00:00.000Z)")).toBeTruthy();
    expect(getByText("premium:billing.since")).toBeTruthy();
  });

  it("omits fields Apple did not supply rather than inventing them", () => {
    const { queryByText, getByText } = render(
      <BillingSection
        subscription={null}
        apple={{ ...apple, plan: null, displayPrice: null, originalPurchaseAt: null, status: "expired" }}
        experience="active"
      />
    );
    expect(queryByText("premium:billing.plan")).toBeNull();
    expect(queryByText("premium:billing.price")).toBeNull();
    expect(queryByText("premium:billing.since")).toBeNull();
    // An expired date is labelled as an end, never as an active period.
    expect(queryByText("premium:billing.activeUntil")).toBeNull();
    expect(getByText("premium:billing.expires")).toBeTruthy();
    expect(getByText("premium:subStatus.expired")).toBeTruthy();
  });

  it("keeps the honest none-state when Apple proves nothing either", () => {
    const { getByText } = render(<BillingSection subscription={null} apple={null} experience="active" />);
    expect(getByText("premium:billing.none")).toBeTruthy();
  });

  it("never replaces the Founder copy with a billing card", () => {
    const { getByText, queryByText } = render(
      <BillingSection subscription={null} apple={apple} experience="founder" />
    );
    expect(getByText("premium:billing.founderNone")).toBeTruthy();
    expect(queryByText("€99,99")).toBeNull();
  });

  it("still prefers the server's own subscription row over StoreKit", () => {
    const { getByText, queryByText } = render(
      <BillingSection
        subscription={{
          provider: "apple_iap", plan_key: "premium_annual", billing_period: "annual",
          status: "active", current_period_end: "2026-10-01", cancel_at_period_end: false
        }}
        apple={apple}
        experience="active"
      />
    );
    expect(getByText("premium:billing.renews")).toBeTruthy();
    expect(queryByText("premium:billing.activeUntil")).toBeNull();
  });
});
