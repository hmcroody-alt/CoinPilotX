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

/**
 * `route` is optional because the screen is reached two ways: from the Business
 * hub with no params, and from Advertising or Orders with a context title. Both
 * paths have to be renderable here, since the header this screen draws is now
 * the *only* header — there is no stack header behind it to fall back on.
 */
async function renderScreen(route?: { params?: { title?: string; accountId?: number } }) {
  const view = render(<BusinessOsPaymentsScreen route={route} />);
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

  /**
   * Tier 0.2. The shell defect was structural, not cosmetic: this screen was the
   * only Business OS route left without `headerShown: false` while drawing its
   * own gradient header, so it rendered two titles and two back chevrons. The
   * error surface had the matching problem — one failure stated three times with
   * two retries attached. Both classes of defect are invisible to a test that
   * only checks that the right words appear *somewhere*, so these assert counts.
   */
  describe("one shell, one header, one error", () => {
    it("renders exactly one header title, and no second title behind it", async () => {
      const view = await renderScreen();
      expect(view.getAllByText("Payments")).toHaveLength(1);
      expect(view.getAllByRole("header").filter((n) => n.props.children === "Payments")).toHaveLength(
        1
      );
    });

    /**
     * Advertising and Orders arrive here with their own context. That title used
     * to be rendered by the stack header; now that the stack header is gone, the
     * screen has to render it or the context is silently lost.
     */
    it("carries the caller's context title instead of a hard-coded 'Payments'", async () => {
      const view = await renderScreen({ params: { title: "Ad wallet", accountId: 7 } });
      const headers = view.getAllByRole("header").filter((n) => n.props.children === "Ad wallet");
      expect(headers).toHaveLength(1);
      expect(view.queryAllByText("Payments")).toHaveLength(0);
    });

    it("states a failed balance read once, with one retry and not two", async () => {
      mockOverview.mockRejectedValue(new Error("Balances unavailable."));
      const view = await renderScreen();

      expect(view.getAllByText("Try again")).toHaveLength(1);
      expect(view.getAllByText("Balances unavailable")).toHaveLength(1);
      expect(
        view.queryAllByText(/we could not read your balance just now/i)
      ).toHaveLength(0);
      expect(
        view.getAllByText(/this is a display problem, not a change to your money/i)
      ).toHaveLength(1);
    });

    /**
     * "It was broken" is not something a seller can usefully tell support about
     * their own money. The reference is minted at failure time and shaped so it
     * can be read aloud over the phone.
     */
    it("gives the seller a quotable reference for the failure", async () => {
      mockOverview.mockRejectedValue(new Error("Balances unavailable."));
      const view = await renderScreen();

      const reference = view.getByText(/^Reference PAY-/);
      expect(String(reference.props.children)).toMatch(/^Reference PAY-\d{8}-\d{4}-[0-9A-Z]{2}$/);
      // Copyable rather than transcribable, and spelled out to assistive tech.
      expect(reference.props.selectable).toBe(true);
    });

    /** A reference with no live failure behind it would be a code for nothing. */
    it("clears the reference once the retry succeeds", async () => {
      mockOverview.mockRejectedValueOnce(new Error("Balances unavailable."));
      const view = await renderScreen();
      expect(view.getByText(/^Reference PAY-/)).toBeTruthy();

      await act(async () => {
        fireEvent.press(view.getByText("Try again"));
      });

      await waitFor(() => expect(view.getByLabelText(/Available for payout, \$42\.00/)).toBeTruthy());
      expect(view.queryByText(/^Reference PAY-/)).toBeNull();
    });
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
