/**
 * Payments is the screen where a wrong number is a lie about somebody's money,
 * so these tests are about provenance rather than layout:
 *
 *   1. Every figure shown comes from the backend or from real orders. A wallet
 *      endpoint that fails must leave the wallet absent, never zeroed — "$0.00
 *      available" and "we could not reach your wallet" are different claims.
 *   2. Order money is labelled gross, not payout. Summing what buyers were
 *      charged and calling it a payout overstates it by every fee and refund.
 *   3. No funding control renders while `adFundingIsLive` is false. The server
 *      hardcodes `live_charging: false`, so an Add Funds button could only ever
 *      be a control that charges nothing — the thing constraint 6 forbids.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("../../navigation/BottomNavVisibility", () => ({
  BOTTOM_NAV_CONTENT_CLEARANCE: 0,
  useBottomNavScrollVisibility: () => ({
    onScroll: jest.fn(),
    onScrollBeginDrag: jest.fn(),
    scrollEventThrottle: 16
  })
}));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));

const mockListAccounts = jest.fn();
const mockGetWallet = jest.fn();
const mockGetBilling = jest.fn();

jest.mock("../../api/businessOs", () => ({
  ...jest.requireActual("../../api/businessOs"),
  listAdAccounts: (...args: unknown[]) => mockListAccounts(...args),
  getAdWallet: (...args: unknown[]) => mockGetWallet(...args),
  getAdBillingSummary: (...args: unknown[]) => mockGetBilling(...args)
}));

const mockSnapshot = jest.fn();
const mockCachedStore = jest.fn();
const mockConnectPayout = jest.fn();

jest.mock("../../api/marketplace", () => ({
  ...jest.requireActual("../../api/marketplace"),
  loadSellerStoreSnapshot: (...args: unknown[]) => mockSnapshot(...args),
  loadCachedSellerStore: (...args: unknown[]) => mockCachedStore(...args),
  connectMarketplacePayout: (...args: unknown[]) => mockConnectPayout(...args)
}));

import { BusinessOsPaymentsScreen } from "../BusinessOsPaymentsScreen";

const ACCOUNT = { id: 7, business_name: "Roody Goods", status: "active", verified: true };

const WALLET = {
  available_balance_cents: 4200,
  pending_balance_cents: 1000,
  spendable_balance_cents: 3200,
  reserved_budget_cents: 1000,
  promotional_credits_cents: 500,
  bonus_credits_cents: 250,
  refund_credits_cents: 50,
  lifetime_spent_cents: 99900,
  currency: "USD",
  transactions: []
};

const BILLING = {
  wallet_balance_cents: 4200,
  spend_limit_cents: 50000,
  billing_status: "not_configured",
  funding_status: "prepared",
  billing_enabled: false,
  live_charging: false
};

beforeEach(() => {
  jest.clearAllMocks();
  mockListAccounts.mockResolvedValue({ accounts: [ACCOUNT] });
  mockGetWallet.mockResolvedValue({ wallet: WALLET });
  mockGetBilling.mockResolvedValue({ billing: BILLING });
  mockSnapshot.mockResolvedValue({ listings: [], orders: [] });
  mockCachedStore.mockResolvedValue(null);
  mockConnectPayout.mockResolvedValue({ message: "Payout onboarding checked." });
});

async function renderScreen() {
  const view = render(<BusinessOsPaymentsScreen />);
  await waitFor(() => expect(view.queryByText("Loading payments…")).toBeNull());
  return view;
}

describe("Business OS payments", () => {
  it("shows the wallet figures the backend returned for the ad account", async () => {
    const view = await renderScreen();
    expect(mockGetWallet).toHaveBeenCalledWith(7);
    expect(view.getByLabelText("Available: $42.00")).toBeTruthy();
    expect(view.getByLabelText("Spendable: $32.00")).toBeTruthy();
    expect(view.getByLabelText("Lifetime spent: $999.00")).toBeTruthy();
    // Promotional, bonus and refund credits are one idea split across three
    // fields; showing only one of them would understate what is available.
    expect(view.getByLabelText("Credits: $8.00")).toBeTruthy();
  });

  it("renders no funding control while the account cannot be charged", async () => {
    const view = await renderScreen();
    expect(view.queryByLabelText(/add funds/i)).toBeNull();
    expect(view.getByText(/no funds can be added here/i)).toBeTruthy();
  });

  it("sums real orders and calls the total gross rather than a payout", async () => {
    mockSnapshot.mockResolvedValue({
      listings: [],
      orders: [
        { id: 1, status: "paid", gross_amount_cents: 2500, currency: "USD" },
        { id: 2, status: "pending", gross_amount_cents: 1000, currency: "USD" },
        { id: 3, status: "paid", amount_cents: 500, currency: "USD" }
      ]
    });
    const view = await renderScreen();
    expect(view.getByLabelText("Orders: 3")).toBeTruthy();
    expect(view.getByLabelText("Paid orders: 2")).toBeTruthy();
    expect(view.getByLabelText("Gross from orders: $40.00")).toBeTruthy();
    expect(view.getByText(/It is not\s+your payout amount/i)).toBeTruthy();
  });

  it("leaves billing visible when only the wallet call fails", async () => {
    // Two endpoints, two failure modes. A missing wallet blanking out billing
    // would hide a spend limit the user is still subject to.
    mockGetWallet.mockRejectedValue(new Error("wallet unavailable"));
    const view = await renderScreen();
    expect(view.queryByText("Ad wallet")).toBeNull();
    expect(view.getByText("Billing")).toBeTruthy();
    expect(view.getByLabelText("Spend limit: $500.00")).toBeTruthy();
  });

  it("says the figures are unavailable rather than showing zeros when offline", async () => {
    mockListAccounts.mockRejectedValue(new Error("Network request failed"));
    mockSnapshot.mockRejectedValue(new Error("Network request failed"));
    mockCachedStore.mockResolvedValue({ orders: [{ id: 1, status: "paid", gross_amount_cents: 700 }] });

    const view = await renderScreen();
    expect(view.getByText("Showing saved data")).toBeTruthy();
    expect(view.queryByText("Ad wallet")).toBeNull();
    expect(view.queryByText("Billing")).toBeNull();
    // Cached orders still render, so the screen degrades rather than emptying.
    expect(view.getByLabelText("Gross from orders: $7.00")).toBeTruthy();
  });

  it("disables payout onboarding while offline instead of failing on tap", async () => {
    mockListAccounts.mockRejectedValue(new Error("Network request failed"));
    const view = await renderScreen();
    await act(async () => {
      fireEvent.press(view.getByLabelText("Check payout onboarding"));
    });
    expect(mockConnectPayout).not.toHaveBeenCalled();
  });

  it("reports what payout onboarding actually returned", async () => {
    mockConnectPayout.mockResolvedValue({ message: "Bank details still pending review." });
    const view = await renderScreen();
    await act(async () => {
      fireEvent.press(view.getByLabelText("Check payout onboarding"));
    });
    expect(view.getByText("Bank details still pending review.")).toBeTruthy();
  });

  it("explains the empty state instead of showing an empty wallet", async () => {
    mockListAccounts.mockResolvedValue({ accounts: [] });
    const view = await renderScreen();
    expect(mockGetWallet).not.toHaveBeenCalled();
    expect(view.getByText("No ad account yet")).toBeTruthy();
    expect(view.queryByText("Ad wallet")).toBeNull();
  });
});
