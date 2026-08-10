/**
 * The wallet module moves the advertiser's actual money picture through these
 * normalizers, so the mapping decisions with financial meaning are pinned:
 *
 *   1. Transaction amounts keep their sign — a spend row rendered positive
 *      would read as a top-up.
 *   2. `auto_charge` is pinned false even if a payload claims true. The product
 *      promise is that auto top-up prompts and never charges a card; a lying
 *      payload must not be able to change what the screen says.
 *   3. Pagination ends on a null cursor, and a junk cursor reads as the end
 *      rather than an infinite loop of the same page.
 */

import {
  normalizeAdAutoTopup,
  normalizeAdFundingSession,
  normalizeAdSpendingLimits,
  normalizeAdWalletInvoice,
  normalizeAdWalletTxn,
  normalizeAdWalletTxnPage
} from "../adsWallet";

describe("normalizeAdWalletTxn", () => {
  it("keeps the sign on the amount — a spend is not a top-up", () => {
    expect(normalizeAdWalletTxn({ id: 1, amount_cents: -450 }).amount_cents).toBe(-450);
    expect(normalizeAdWalletTxn({ id: 2, amount_cents: 2000 }).amount_cents).toBe(2000);
  });

  it("nulls absent campaign/creative references instead of inventing id 0", () => {
    const txn = normalizeAdWalletTxn({ id: 1, campaign_id: 0, creative_id: undefined });
    expect(txn.campaign_id).toBeNull();
    expect(txn.creative_id).toBeNull();
    expect(normalizeAdWalletTxn({ id: 1, campaign_id: 7 }).campaign_id).toBe(7);
  });

  it("uppercases the currency and defaults to USD", () => {
    expect(normalizeAdWalletTxn({ id: 1, currency: "usd" }).currency).toBe("USD");
    expect(normalizeAdWalletTxn({ id: 1 }).currency).toBe("USD");
  });
});

describe("normalizeAdWalletTxnPage", () => {
  it("drops id-less rows and passes the cursor through", () => {
    const page = normalizeAdWalletTxnPage({
      transactions: [{ id: 5, amount_cents: -100 }, { id: 0 }, {}],
      next_before_id: 5
    });
    expect(page.transactions).toHaveLength(1);
    expect(page.next_before_id).toBe(5);
  });

  it("reads a missing or junk cursor as the last page", () => {
    expect(normalizeAdWalletTxnPage({ transactions: [] }).next_before_id).toBeNull();
    expect(normalizeAdWalletTxnPage({ transactions: [], next_before_id: 0 }).next_before_id).toBeNull();
    expect(
      normalizeAdWalletTxnPage({ transactions: [], next_before_id: "x" as never }).next_before_id
    ).toBeNull();
    expect(normalizeAdWalletTxnPage(null).transactions).toEqual([]);
  });
});

describe("normalizeAdWalletInvoice", () => {
  it("clamps the amount non-negative and keeps the period strings", () => {
    const invoice = normalizeAdWalletInvoice({
      id: 3,
      invoice_number: "INV-3",
      amount_cents: 4200,
      period_start: "2026-07-01",
      period_end: "2026-07-31"
    });
    expect(invoice.amount_cents).toBe(4200);
    expect(invoice.period_start).toBe("2026-07-01");
    expect(normalizeAdWalletInvoice({ id: 3, amount_cents: -1 }).amount_cents).toBe(0);
  });
});

describe("normalizeAdSpendingLimits", () => {
  it("reads 0 as 'no limit set' — the backend has no null state", () => {
    expect(normalizeAdSpendingLimits({})).toEqual({ daily_limit_cents: 0, lifetime_limit_cents: 0 });
    expect(normalizeAdSpendingLimits({ daily_limit_cents: 5000, lifetime_limit_cents: 100000 })).toEqual({
      daily_limit_cents: 5000,
      lifetime_limit_cents: 100000
    });
    expect(normalizeAdSpendingLimits(null).daily_limit_cents).toBe(0);
  });
});

describe("normalizeAdAutoTopup", () => {
  it("pins auto_charge false even when a payload claims otherwise", () => {
    // The product promise: this setting prompts, it never charges a card. No
    // server payload may flip the screen's copy on that.
    expect(normalizeAdAutoTopup({ enabled: true, auto_charge: true as never }).auto_charge).toBe(false);
    expect(normalizeAdAutoTopup({}).auto_charge).toBe(false);
  });

  it("only an explicit true enables it", () => {
    expect(normalizeAdAutoTopup({ enabled: true }).enabled).toBe(true);
    expect(normalizeAdAutoTopup({ enabled: 1 as never }).enabled).toBe(false);
    expect(normalizeAdAutoTopup(undefined).enabled).toBe(false);
  });

  it("keeps the server's note verbatim for the screen to show", () => {
    expect(normalizeAdAutoTopup({ note: "We'll remind you, not charge you." }).note).toBe(
      "We'll remind you, not charge you."
    );
  });
});

describe("normalizeAdFundingSession", () => {
  it("carries the checkout URL through untouched", () => {
    const session = normalizeAdFundingSession({
      id: 9,
      amount_cents: 5000,
      status: "pending",
      checkout_url: "https://checkout.stripe.com/c/pay/x"
    });
    expect(session.checkout_url).toBe("https://checkout.stripe.com/c/pay/x");
    expect(session.amount_cents).toBe(5000);
  });

  it("degrades a missing session to an empty URL the screen can branch on", () => {
    expect(normalizeAdFundingSession(undefined).checkout_url).toBe("");
    expect(normalizeAdFundingSession(null).id).toBe(0);
  });
});
