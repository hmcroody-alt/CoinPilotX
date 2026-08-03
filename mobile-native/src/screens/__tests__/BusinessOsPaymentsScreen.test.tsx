/**
 * Payments is the screen where a wrong number is a lie about somebody's money,
 * so these tests are about provenance rather than layout. Each one pins a rule
 * that is cheap to break by accident and expensive to break in production:
 *
 *   1. Every figure is a direct render of a backend total. The screen never
 *      sums the ledger to produce a balance, so a ledger that disagrees with
 *      the server's aggregate must not change what the hero says.
 *   2. A failed *fresh* balance read shows "—", never a cached figure. "$42.00"
 *      and "we could not reach your account" are different claims, and a stale
 *      number presented as current is the specific failure this screen exists
 *      to prevent.
 *   3. Cached money appears on exactly one path — offline — and always carries
 *      the time it was true.
 *   4. A module with no backend behind it renders ABSENT, not disabled and not
 *      zeroed. A greyed-out "Pay out now" still tells the seller a payout is
 *      something they can nearly do.
 *   5. A hold is never rendered as a loss.
 */
import React from "react";
import { act, fireEvent, render, waitFor } from "@testing-library/react-native";

jest.mock("react-native-safe-area-context", () => ({
  useSafeAreaInsets: () => ({ top: 0, bottom: 0, left: 0, right: 0 })
}));
jest.mock("../../core/eventSync", () => ({
  registerSyncInvalidation: jest.fn(() => () => undefined)
}));

const mockOverview = jest.fn();
const mockLedger = jest.fn();
const mockAdWallet = jest.fn();
const mockCachedOverview = jest.fn();
const mockCachedActivity = jest.fn();

// The formatters, the flag predicates and `payoutMethodState` are deliberately
// left real. They encode the money rules under test — mocking them out would
// let the screen pass while the rules it depends on are broken.
jest.mock("../../api/paymentsHub", () => ({
  ...jest.requireActual("../../api/paymentsHub"),
  fetchMoneyOverview: (...args: unknown[]) => mockOverview(...args),
  fetchLedgerPage: (...args: unknown[]) => mockLedger(...args),
  fetchAdWallet: (...args: unknown[]) => mockAdWallet(...args),
  loadCachedOverview: (...args: unknown[]) => mockCachedOverview(...args),
  loadCachedActivity: (...args: unknown[]) => mockCachedActivity(...args)
}));

import { BusinessOsPaymentsScreen } from "../BusinessOsPaymentsScreen";

const PAYOUT_METHOD = {
  provider: "stripe",
  seller_type: "individual",
  onboarding_status: "complete",
  payouts_enabled: true,
  charges_enabled: true,
  connected: true,
  destination_kind: "stripe_connected_account",
  destination_masked: "····9999",
  bank_destination: "not_stored",
  missing_requirements: [],
  last_checked_at: null,
  updated_at: null
};

function overview(overrides: Record<string, unknown> = {}) {
  return {
    seller_user_id: 1,
    currency: "USD",
    as_of: "2026-08-01T12:00:00Z",
    available_cents: 4200,
    processing_cents: 1500,
    lifetime_fees_cents: 300,
    lifetime_earnings_cents: 9000,
    wallets: [],
    reconciled: true,
    has_wallet: true,
    payout_method: PAYOUT_METHOD,
    payout_in_flight: null,
    last_failed_payout: null,
    recent_payouts: [],
    release_path: "none_in_product",
    payout_initiation: "none_in_product",
    instant_payout: "none_in_product",
    statements: "none_in_product",
    tax_documents: "none_in_product",
    escrow: { supported: false, reason: "business_os_ledger_only" },
    ad_wallet_source: "pulse_ads",
    ...overrides
  };
}

function entry(overrides: Record<string, unknown> = {}) {
  return {
    id: 1,
    kind: "income",
    sign: "+",
    entry_type: "credit",
    status: "posted",
    amount_cents: 2500,
    currency: "USD",
    title: "Order #1041",
    reference: null,
    counterparty_user_id: null,
    provider: "stripe",
    provider_reference: null,
    trace_id: null,
    created_at: "2026-08-01T10:00:00Z",
    ...overrides
  };
}

function page(entries: unknown[], overrides: Record<string, unknown> = {}) {
  return {
    seller_user_id: 1,
    currency: "USD",
    entries,
    next_cursor: null,
    has_more: false,
    as_of: "2026-08-01T12:00:00Z",
    ...overrides
  };
}

const AD_WALLET = {
  available_balance_cents: 3000,
  pending_balance_cents: 0,
  spendable_balance_cents: 2500,
  reserved_budget_cents: 500,
  lifetime_spent_cents: 10000,
  currency: "USD",
  transactions: []
};

beforeEach(() => {
  jest.clearAllMocks();
  mockOverview.mockResolvedValue(overview());
  mockLedger.mockResolvedValue(page([entry()]));
  mockAdWallet.mockResolvedValue({ wallet: AD_WALLET, billing: null, accountId: 7 });
  mockCachedOverview.mockResolvedValue(null);
  mockCachedActivity.mockResolvedValue(null);
});

async function renderScreen() {
  const view = render(<BusinessOsPaymentsScreen />);
  await waitFor(() => expect(view.queryByLabelText("Loading your balances")).toBeNull());
  return view;
}

describe("Payments money hub", () => {
  it("renders the server's available total and does not derive it from the ledger", async () => {
    // The ledger deliberately does not add up to the balance. If the hero ever
    // starts summing rows, this figure changes and the test fails.
    mockLedger.mockResolvedValue(
      page([entry({ id: 1, amount_cents: 999999 }), entry({ id: 2, amount_cents: 111111 })])
    );
    const view = await renderScreen();
    expect(view.getByLabelText(/Available for payout, \$42\.00/)).toBeTruthy();
  });

  it("shows an em dash and a retry when the balance read fails, never a cached figure", async () => {
    mockOverview.mockRejectedValue(new Error("Balances unavailable."));
    mockCachedOverview.mockResolvedValue({
      overview: overview({ available_cents: 999900 }),
      cachedAt: null // no timestamp, so this cache is unusable as a display
    });

    const view = await renderScreen();
    // The stale $9,999.00 must not appear anywhere.
    expect(view.queryByText("$9,999.00")).toBeNull();
    expect(view.getByText("Balances unavailable")).toBeTruthy();
    expect(view.getByText(/this is a display problem, not a change to your money/i)).toBeTruthy();
  });

  it("retries the balance read when the seller asks", async () => {
    mockOverview.mockRejectedValueOnce(new Error("Balances unavailable."));
    const view = await renderScreen();
    await act(async () => {
      fireEvent.press(view.getByText("Try again"));
    });
    await waitFor(() => expect(view.getByLabelText(/Available for payout, \$42\.00/)).toBeTruthy());
    expect(mockOverview).toHaveBeenCalledTimes(2);
  });

  it("labels cached money with the time it was true", async () => {
    mockOverview.mockRejectedValue(new Error("Network request failed"));
    mockLedger.mockRejectedValue(new Error("Network request failed"));
    mockCachedOverview.mockResolvedValue({
      overview: overview({ available_cents: 4200 }),
      cachedAt: "2026-08-01T09:14:00Z"
    });
    mockCachedActivity.mockResolvedValue({
      page: page([entry()]),
      cachedAt: "2026-08-01T09:14:00Z"
    });

    const view = await renderScreen();
    expect(view.getByText(/Offline — showing your last synced activity as of/)).toBeTruthy();
    expect(view.getByText(/Money actions are paused until you reconnect/)).toBeTruthy();
  });

  it("leaves the ad wallet card absent rather than showing a zero when it cannot be read", async () => {
    mockAdWallet.mockResolvedValue(null);
    const view = await renderScreen();
    expect(view.queryByText("Ad wallet")).toBeNull();
    // Processing still renders — one failed card must not blank the others.
    expect(view.getByText("Processing")).toBeTruthy();
  });

  it("renders the ad wallet's spendable figure, which is Advertising's own number", async () => {
    const view = await renderScreen();
    expect(view.getByText("Ad wallet")).toBeTruthy();
    expect(view.getByText("$25.00")).toBeTruthy();
  });

  it("puts 'no payout method' in front of the seller rather than a quiet row", async () => {
    mockOverview.mockResolvedValue(overview({ payout_method: null }));
    const view = await renderScreen();
    expect(view.getByText("No payout method")).toBeTruthy();
    expect(view.getByText(/there is nowhere to send them yet/i)).toBeTruthy();
  });

  it("names the Stripe connection instead of inventing a bank mask", async () => {
    const view = await renderScreen();
    expect(view.getByText("Payouts connected")).toBeTruthy();
    // "•••• 4321 · Checking" has no data behind it — the platform stores no
    // account number — so the card says what it actually has.
    expect(view.getByText("Connection ····9999")).toBeTruthy();
    expect(view.queryByText(/checking/i)).toBeNull();
  });

  it("ships no payout controls, statements or tax documents while their flags are off", async () => {
    const view = await renderScreen();
    expect(view.queryByText(/pay out now/i)).toBeNull();
    expect(view.queryByText("Move your money")).toBeNull();
    expect(view.queryByText("Statements")).toBeNull();
    // An empty tax section would itself assert a threshold determination that
    // nothing in this system performs.
    expect(view.queryByText("Tax documents")).toBeNull();
  });

  it("keeps the escrow card absent while no escrow figure exists", async () => {
    mockOverview.mockResolvedValue(overview({ escrow: { supported: true, reason: "" } }));
    const view = await renderScreen();
    expect(view.queryByText("Held in escrow")).toBeNull();
  });

  it("renders a hold unsigned and says 'held', never as an outflow", async () => {
    mockLedger.mockResolvedValue(
      page([entry({ id: 9, kind: "escrow", sign: "none", entry_type: "hold", amount_cents: 1800 })])
    );
    const view = await renderScreen();
    expect(view.getByText("$18.00")).toBeTruthy();
    expect(view.queryByText("−$18.00")).toBeNull();
    expect(view.getByText("held")).toBeTruthy();
  });

  it("keeps a failed transaction visible with its real status", async () => {
    mockLedger.mockResolvedValue(
      page([entry({ id: 12, kind: "payout", sign: "-", entry_type: "payout", status: "failed" })])
    );
    const view = await renderScreen();
    expect(view.getByText(/failed/i)).toBeTruthy();
  });

  it("appends the next page without duplicating rows already on screen", async () => {
    mockLedger
      .mockResolvedValueOnce(page([entry({ id: 1 })], { next_cursor: "c1", has_more: true }))
      // The server repeats id 1 on the boundary; the screen must dedupe by id
      // rather than show one transaction twice.
      .mockResolvedValueOnce(page([entry({ id: 1 }), entry({ id: 2, title: "Order #1042" })]));

    const view = await renderScreen();
    await act(async () => {
      fireEvent.press(view.getByLabelText("Load older activity"));
    });
    await waitFor(() => expect(view.getByText("Order #1042")).toBeTruthy());
    expect(view.getAllByText("Order #1041")).toHaveLength(1);
  });

  it("leaves the ledger intact when a further page fails", async () => {
    mockLedger
      .mockResolvedValueOnce(page([entry({ id: 1 })], { next_cursor: "c1", has_more: true }))
      .mockRejectedValueOnce(new Error("Network request failed"));

    const view = await renderScreen();
    await act(async () => {
      fireEvent.press(view.getByLabelText("Load older activity"));
    });
    // A failed page is not a shorter ledger, and the control stays available.
    expect(view.getByText("Order #1041")).toBeTruthy();
    await waitFor(() => expect(view.getByLabelText("Load older activity")).toBeTruthy());
  });

  it("says a new seller has no movement yet rather than showing an empty error", async () => {
    mockLedger.mockResolvedValue(page([]));
    const view = await renderScreen();
    expect(view.getByText("No money movement yet")).toBeTruthy();
  });

  it("surfaces a failed payout with the provider's real reason", async () => {
    mockOverview.mockResolvedValue(
      overview({
        last_failed_payout: {
          payout_id: 3,
          amount_cents: 5000,
          currency: "USD",
          status: "failed",
          provider: "stripe",
          provider_payout_reference: "po_1",
          failure_reason: "Your bank rejected the transfer.",
          created_at: null,
          updated_at: null
        }
      })
    );
    const view = await renderScreen();
    expect(view.getByText("A payout did not go through")).toBeTruthy();
    expect(view.getByText("Your bank rejected the transfer.")).toBeTruthy();
  });
});
