/**
 * The seller payout rail moves real money, so the decisions with financial
 * meaning are pinned:
 *
 *   1. No arithmetic. The balance block passes through whole; the normalizers
 *      clamp and coerce but never derive one figure from another.
 *   2. The idempotency key travels in the POST body. A duplicate reply reads
 *      as `duplicate: true`, never as a second payout.
 *   3. Error codes map to specific sentences; an unknown code degrades to the
 *      generic one instead of guessing.
 *   4. The masked destination is a reference (last four of the account id),
 *      never a bank number.
 */
const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => {
  const actual = jest.requireActual("../pulseApi");
  return {
    ...actual,
    pulseApi: (...args: unknown[]) => mockPulseApi(...args)
  };
});

import { PulseApiError } from "../pulseApi";
import {
  SELLER_PAYOUT_STATUSES,
  fetchConnectStatus,
  fetchSellerPayouts,
  maskedConnectRef,
  mintPayoutKey,
  normalizeConnectStatus,
  normalizeSellerPayout,
  normalizeSellerPayoutBalance,
  normalizeSellerPayoutPage,
  payoutErrorKey,
  payoutStatusChip,
  requestSellerPayout
} from "../sellerPayouts";

beforeEach(() => {
  mockPulseApi.mockReset();
});

/* ------------------------------------------------------------------ *
 * Normalizers
 * ------------------------------------------------------------------ */

describe("normalizeSellerPayout", () => {
  it("clamps the amount non-negative and uppercases the currency", () => {
    const payout = normalizeSellerPayout({ id: 3, amount_cents: 2500, currency: "usd" });
    expect(payout.amount_cents).toBe(2500);
    expect(payout.currency).toBe("USD");
    expect(normalizeSellerPayout({ id: 3, amount_cents: -1 }).amount_cents).toBe(0);
    expect(normalizeSellerPayout({ id: 3 }).currency).toBe("USD");
  });

  it("keeps the failure message verbatim — it is what the row shows", () => {
    const payout = normalizeSellerPayout({
      id: 1,
      status: "failed",
      failure_code: "account_closed",
      failure_message: "The bank account has been closed."
    });
    expect(payout.failure_message).toBe("The bank account has been closed.");
    expect(payout.failure_code).toBe("account_closed");
  });

  it("passes an unknown status through raw rather than guessing", () => {
    expect(normalizeSellerPayout({ id: 1, status: "hovering" }).status).toBe("hovering");
  });
});

describe("normalizeSellerPayoutBalance", () => {
  it("passes the server's block through whole, without deriving anything", () => {
    const balance = normalizeSellerPayoutBalance({
      available_cents: 1200,
      payout_pending_cents: 300,
      processing_cents: 45,
      processing_source: "stripe",
      computed_at: "2026-08-01T00:00:00Z"
    });
    expect(balance).toEqual({
      available_cents: 1200,
      payout_pending_cents: 300,
      processing_cents: 45,
      processing_source: "stripe",
      computed_at: "2026-08-01T00:00:00Z"
    });
  });

  it("reads an absent block as null, not as a zero balance", () => {
    // Null means "the endpoint said nothing"; zero would be a fabricated figure.
    expect(normalizeSellerPayoutBalance(null)).toBeNull();
    expect(normalizeSellerPayoutBalance(undefined)).toBeNull();
  });
});

describe("normalizeSellerPayoutPage", () => {
  it("drops id-less rows and reads a junk cursor as the last page", () => {
    const page = normalizeSellerPayoutPage({
      payouts: [{ id: 8, amount_cents: 100 }, { id: 0 }, {}],
      next_before_id: 8,
      has_more: true
    });
    expect(page.payouts).toHaveLength(1);
    expect(page.next_before_id).toBe(8);
    expect(page.has_more).toBe(true);
    expect(normalizeSellerPayoutPage({ next_before_id: 0 }).next_before_id).toBeNull();
    expect(
      normalizeSellerPayoutPage({ next_before_id: "x" as never }).next_before_id
    ).toBeNull();
    expect(normalizeSellerPayoutPage(null).payouts).toEqual([]);
    expect(normalizeSellerPayoutPage(null).balance).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * Endpoints
 * ------------------------------------------------------------------ */

describe("fetchSellerPayouts", () => {
  it("builds the history URL with a clamped limit and the cursor", async () => {
    mockPulseApi.mockResolvedValue({ payouts: [] });
    await fetchSellerPayouts({ limit: 500, beforeId: 42 });
    expect(String(mockPulseApi.mock.calls[0][0])).toBe(
      "/api/pulse/payments/seller/payouts?limit=100&before_id=42"
    );
  });

  it("defaults to twenty rows and no cursor", async () => {
    mockPulseApi.mockResolvedValue({ payouts: [] });
    await fetchSellerPayouts();
    expect(String(mockPulseApi.mock.calls[0][0])).toBe(
      "/api/pulse/payments/seller/payouts?limit=20"
    );
  });
});

describe("requestSellerPayout", () => {
  it("POSTs the amount and the idempotency key in the body", async () => {
    mockPulseApi.mockResolvedValue({ payout: { id: 1 }, duplicate: false });
    await requestSellerPayout(2500, "payout-abc");
    const [path, options] = mockPulseApi.mock.calls[0] as [string, { method: string; body: string }];
    expect(path).toBe("/api/pulse/payments/seller/payouts");
    expect(options.method).toBe("POST");
    expect(JSON.parse(options.body)).toEqual({ amount_cents: 2500, payout_key: "payout-abc" });
  });

  it("reads a replayed key as duplicate, never as a second payout", async () => {
    mockPulseApi.mockResolvedValue({
      payout: { id: 1, status: "pending" },
      duplicate: true,
      stripe: { submitted: false, reason: "already processed" }
    });
    const result = await requestSellerPayout(2500, "payout-abc");
    expect(result.duplicate).toBe(true);
    expect(result.stripe.submitted).toBe(false);
    expect(result.stripe.reason).toBe("already processed");
  });
});

describe("mintPayoutKey", () => {
  it("mints a prefixed, unique key per confirmation", () => {
    const one = mintPayoutKey();
    const two = mintPayoutKey();
    expect(one).toMatch(/^payout-/);
    expect(one).not.toBe(two);
  });
});

describe("payoutErrorKey", () => {
  it("maps the endpoint's declared codes onto specific sentences", () => {
    expect(payoutErrorKey(new PulseApiError("x", 400, "insufficient_balance"))).toBe(
      "errInsufficient"
    );
    expect(payoutErrorKey(new PulseApiError("x", 400, "payouts_disabled"))).toBe(
      "errPayoutsDisabled"
    );
    expect(payoutErrorKey(new PulseApiError("x", 400, "no_connected_account"))).toBe(
      "errNoAccount"
    );
  });

  it("degrades an unknown code — or a non-API error — to the generic sentence", () => {
    expect(payoutErrorKey(new PulseApiError("x", 500, "mystery"))).toBe("errGeneric");
    expect(payoutErrorKey(new Error("network down"))).toBe("errGeneric");
    expect(payoutErrorKey(undefined)).toBe("errGeneric");
  });
});

/* ------------------------------------------------------------------ *
 * Connect status
 * ------------------------------------------------------------------ */

describe("fetchConnectStatus / normalizeConnectStatus", () => {
  it("reads the connect status route and only an explicit true enables payouts", async () => {
    mockPulseApi.mockResolvedValue({ connected: true, payouts_enabled: 1 as never });
    const status = await fetchConnectStatus();
    expect(String(mockPulseApi.mock.calls[0][0])).toBe(
      "/api/pulse/payments/seller/connect/status"
    );
    expect(status.connected).toBe(true);
    // `payouts_enabled` is the server's word — a truthy non-boolean is not it.
    expect(status.payouts_enabled).toBe(false);
  });

  it("keeps the state block null when the server sent none", () => {
    expect(normalizeConnectStatus({ connected: false }).state).toBeNull();
    expect(normalizeConnectStatus(null).connected).toBe(false);
  });
});

describe("maskedConnectRef", () => {
  it("shows the last four of the account id as a reference", () => {
    const status = normalizeConnectStatus({
      connected: true,
      state: { connected_account_id: "acct_1NXYZ9876" }
    });
    expect(maskedConnectRef(status)).toBe("····9876");
  });

  it("answers empty for a missing or too-short id rather than inventing digits", () => {
    expect(maskedConnectRef(null)).toBe("");
    expect(maskedConnectRef(normalizeConnectStatus({ state: { connected_account_id: "ab" } }))).toBe(
      ""
    );
  });
});

/* ------------------------------------------------------------------ *
 * Status chips
 * ------------------------------------------------------------------ */

describe("payoutStatusChip", () => {
  it("gives each of the seven server states a key, and covers them all", () => {
    for (const status of SELLER_PAYOUT_STATUSES) {
      expect(payoutStatusChip(status).key).not.toBeNull();
    }
  });

  it("puts terminal failures in the error tone and settled money in success", () => {
    expect(payoutStatusChip("paid")).toEqual({ key: "statusPaid", tone: "success" });
    expect(payoutStatusChip("failed")).toEqual({ key: "statusFailed", tone: "error" });
    expect(payoutStatusChip("returned")).toEqual({ key: "statusReturned", tone: "error" });
    expect(payoutStatusChip("in_transit").tone).toBe("progress");
  });

  it("gives an unknown status no key and the neutral tone — the raw word renders", () => {
    expect(payoutStatusChip("hovering")).toEqual({ key: null, tone: "neutral" });
  });
});
