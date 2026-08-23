/**
 * The judgements the money layers branch on.
 *
 * These tests are about *lies*, not about coverage. Each one pins a case where
 * the cheap implementation would tell a seller something untrue: that they have
 * no payout account when a request merely timed out, that money is arriving when
 * this deployment has no way to move it, that their history is empty when a
 * filter is on. The rendering can change; these answers cannot.
 */

import {
  normalizeConnectStatus,
  normalizeSellerPayout,
  type ConnectStatus,
  type SellerPayout
} from "../../api/sellerPayouts";
import type { LedgerEntry, LedgerKind, SellerMoneyOverview } from "../../api/paymentsHub";
import {
  ACTIVITY_FILTERS,
  activityEmptyKey,
  activityFilterMatches,
  filterLedgerEntries,
  isActivityFilterId,
  isMoneyLayerId,
  maskedPayoutReference,
  MONEY_LAYER_GAP_COUNT,
  MONEY_LAYER_GAPS,
  MONEY_LAYER_IDS,
  MONEY_LEDGER_KINDS,
  payoutFailure,
  payoutIsTerminal,
  payoutOnboardingFailure,
  payoutOnboardingOutcome,
  payoutOnboardingPrefersServerMessage,
  payoutReadiness,
  processingExplainer
} from "../moneyLayers";

/* ---------------------------------------------------------------- *
 * Fixtures. Built through the normalizers where they exist, so a
 * change to a payload shape breaks here rather than in production.
 * ---------------------------------------------------------------- */

function connect(overrides: {
  connected?: boolean;
  payouts_enabled?: boolean;
  details_submitted?: boolean;
  disabled_reason?: string;
}): ConnectStatus {
  return normalizeConnectStatus({
    connected: overrides.connected,
    payouts_enabled: overrides.payouts_enabled,
    state: {
      connected_account_id: "acct_1234567890",
      charges_enabled: true,
      details_submitted: overrides.details_submitted,
      disabled_reason: overrides.disabled_reason || "",
      last_synced_at: "2026-08-13T00:00:00Z"
    }
  });
}

function overview(overrides: Partial<SellerMoneyOverview> = {}): SellerMoneyOverview {
  return {
    seller_user_id: 1,
    currency: "USD",
    as_of: "2026-08-13T00:00:00Z",
    available_cents: 0,
    processing_cents: 0,
    lifetime_fees_cents: 0,
    lifetime_earnings_cents: 0,
    wallets: [],
    reconciled: true,
    has_wallet: true,
    payout_method: null,
    payout_in_flight: null,
    last_failed_payout: null,
    recent_payouts: [],
    release_path: "none_in_product",
    payout_initiation: "unavailable",
    instant_payout: "unavailable",
    statements: "unavailable",
    tax_documents: "unavailable",
    escrow: { supported: false, reason: "" },
    ad_wallet_source: "",
    ...overrides
  };
}

function entry(kind: LedgerKind, id: number): LedgerEntry {
  return {
    id,
    kind,
    sign: kind === "income" ? "+" : kind === "escrow" || kind === "other" ? "none" : "-",
    entry_type: kind,
    status: "posted",
    amount_cents: 1000,
    currency: "USD",
    title: "",
    reference: null,
    counterparty_user_id: null,
    provider: null,
    provider_reference: null,
    trace_id: null,
    created_at: "2026-08-13T00:00:00Z"
  };
}

function payout(overrides: Partial<SellerPayout> = {}): SellerPayout {
  return normalizeSellerPayout({
    id: 1,
    payout_key: "pk_1",
    amount_cents: 5000,
    currency: "USD",
    status: "paid",
    stripe_payout_id: "po_1AbCdEfGh9821",
    ...overrides
  });
}

/* ---------------------------------------------------------------- *
 * Layer ids
 * ---------------------------------------------------------------- */

describe("money layer ids", () => {
  it("accepts only the five layers that exist", () => {
    MONEY_LAYER_IDS.forEach((id) => expect(isMoneyLayerId(id)).toBe(true));
    // A route param arrives from JS and can be anything at all.
    [undefined, null, "", "rewards", "MoneyDetail", 0, {}].forEach((value) =>
      expect(isMoneyLayerId(value)).toBe(false)
    );
  });
});

/* ---------------------------------------------------------------- *
 * Payout readiness
 * ---------------------------------------------------------------- */

describe("payoutReadiness", () => {
  it("says unknown, not 'not set up', when neither source answered", () => {
    // The failure this exists to prevent: a verified seller whose status read
    // timed out being told they have no payout account.
    const readiness = payoutReadiness(null, null);
    expect(readiness.stage).toBe("unknown");
    expect(readiness.action).toBe("retry_status");
    expect(readiness.reasonKey).toBe("reasonUnknown");
    expect(readiness.payoutsEnabled).toBe(false);
    // No codes are invented for a read that never happened.
    expect(readiness.codes).toEqual([]);
  });

  it("lets an enabled account outrank a stale requirement", () => {
    const readiness = payoutReadiness(
      connect({ connected: true, payouts_enabled: true, details_submitted: true }),
      overview({
        payout_method: {
          connected: true,
          payouts_enabled: true,
          missing_requirements: ["individual.verification.document"]
        } as SellerMoneyOverview["payout_method"]
      })
    );
    expect(readiness.stage).toBe("ready");
    expect(readiness.action).toBe("manage");
    expect(readiness.payoutsEnabled).toBe(true);
    // The requirement is still surfaced as a code — it is just not a blockage.
    expect(readiness.codes).toContain("individual.verification.document");
  });

  it("reads enablement from the overview when connect did not answer", () => {
    const readiness = payoutReadiness(
      null,
      overview({
        payout_method: {
          connected: true,
          payouts_enabled: true
        } as SellerMoneyOverview["payout_method"]
      })
    );
    expect(readiness.stage).toBe("ready");
  });

  it("never widens access off a partial read", () => {
    // Neither source says enabled: the negative case wins even though one of
    // them is missing entirely.
    expect(payoutReadiness(connect({ connected: true }), null).payoutsEnabled).toBe(false);
    expect(payoutReadiness(null, overview()).payoutsEnabled).toBe(false);
  });

  it("separates a hard block from an unfinished form", () => {
    const rejected = payoutReadiness(
      connect({ connected: true, details_submitted: true, disabled_reason: "rejected.fraud" }),
      overview()
    );
    expect(rejected.stage).toBe("blocked");
    expect(rejected.reasonKey).toBe("reasonRejected");

    const unfinished = payoutReadiness(connect({ connected: true }), overview());
    expect(unfinished.stage).toBe("in_progress");
    expect(unfinished.action).toBe("resume");
  });

  it("treats every hard-block prefix as blocked", () => {
    ["rejected.other", "rejected.terms_of_service", "platform_paused", "listed", "under_review"].forEach(
      (reason) => {
        const readiness = payoutReadiness(
          connect({ connected: true, details_submitted: true, disabled_reason: reason }),
          overview()
        );
        expect(readiness.stage).toBe("blocked");
      }
    );
  });

  it("names a blocker it has never seen without inventing a cause", () => {
    const readiness = payoutReadiness(
      connect({ connected: true, details_submitted: true, disabled_reason: "rejected.new_stripe_word" }),
      overview()
    );
    expect(readiness.stage).toBe("blocked");
    expect(readiness.reasonKey).toBe("reasonBlockedOther");
    // The unrecognised token survives as a support reference.
    expect(readiness.codes).toContain("rejected.new_stripe_word");
  });

  it("calls an unconnected account not started", () => {
    const readiness = payoutReadiness(connect({ connected: false }), overview());
    expect(readiness.stage).toBe("not_started");
    expect(readiness.action).toBe("start");
  });

  it("calls a fully filed but not-yet-enabled account pending", () => {
    const readiness = payoutReadiness(
      connect({ connected: true, details_submitted: true }),
      overview()
    );
    expect(readiness.stage).toBe("pending_verification");
    expect(readiness.action).toBe("retry_status");
  });

  it("gives every stage a reason key and exactly one action", () => {
    const cases: Array<[ConnectStatus | null, SellerMoneyOverview | null]> = [
      [null, null],
      [connect({ connected: false }), overview()],
      [connect({ connected: true }), overview()],
      [connect({ connected: true, details_submitted: true }), overview()],
      [connect({ connected: true, details_submitted: true, disabled_reason: "under_review" }), overview()],
      [connect({ connected: true, payouts_enabled: true, details_submitted: true }), overview()]
    ];
    const stages = cases.map(([c, o]) => {
      const readiness = payoutReadiness(c, o);
      expect(readiness.reasonKey).toMatch(/^reason[A-Z]/);
      expect(["start", "resume", "retry_status", "manage"]).toContain(readiness.action);
      return readiness.stage;
    });
    // All six stages are reachable — a stage nothing can produce is dead copy.
    expect(new Set(stages).size).toBe(6);
  });
});

/* ---------------------------------------------------------------- *
 * Processing
 * ---------------------------------------------------------------- */

describe("processingExplainer", () => {
  it("does not promise a release this deployment cannot perform", () => {
    // $240 processing with release_path "none_in_product" is the real shape.
    // "It will clear soon" would be a schedule that does not exist.
    const explainer = processingExplainer(
      overview({ processing_cents: 24000, release_path: "none_in_product" })
    );
    expect(explainer.key).toBe("noReleasePath");
    expect(explainer.releasable).toBe(false);
  });

  it("promises release only when the server reports a release path", () => {
    const explainer = processingExplainer(
      overview({ processing_cents: 24000, release_path: "payout_request" })
    );
    expect(explainer.key).toBe("awaitingRelease");
    expect(explainer.releasable).toBe(true);
  });

  it("distinguishes no wallet from nothing processing", () => {
    expect(processingExplainer(overview({ has_wallet: false })).key).toBe("noWallet");
    expect(processingExplainer(null).key).toBe("noWallet");
    expect(processingExplainer(overview({ processing_cents: 0 })).key).toBe("nothingProcessing");
  });

  it("echoes the server's release path verbatim rather than restating it", () => {
    expect(processingExplainer(overview({ release_path: "none_in_product" })).releasePath).toBe(
      "none_in_product"
    );
    // Anything that is not the one known release path is not releasable.
    expect(processingExplainer(overview({ release_path: "some_future_path" })).releasable).toBe(false);
  });
});

/* ---------------------------------------------------------------- *
 * Activity filters
 * ---------------------------------------------------------------- */

describe("activity filters", () => {
  const all = MONEY_LEDGER_KINDS.map((kind, index) => entry(kind, index + 1));

  it("maps every filter onto a kind the ledger actually writes", () => {
    ACTIVITY_FILTERS.filter((id) => id !== "all").forEach((id) => {
      expect(filterLedgerEntries(all, id).length).toBe(1);
    });
  });

  it("keeps unclassified rows visible but unclaimed", () => {
    const other = entry("other", 99);
    expect(activityFilterMatches("all", other)).toBe(true);
    // `other` is the server declining to classify. No chip may claim it.
    ACTIVITY_FILTERS.filter((id) => id !== "all").forEach((id) => {
      expect(activityFilterMatches(id, other)).toBe(false);
    });
  });

  it("returns the same array identity for 'all'", () => {
    // Not a micro-optimisation: a new array every render would remount rows.
    expect(filterLedgerEntries(all, "all")).toBe(all);
  });

  it("maps the 'held' chip onto the escrow kind", () => {
    // The chip is worded for sellers, the kind is the server's. They differ, and
    // that mapping is the one thing a rename could silently break.
    expect(filterLedgerEntries(all, "held").map((row) => row.kind)).toEqual(["escrow"]);
  });

  it("rejects a filter id that is not one of ours", () => {
    ACTIVITY_FILTERS.forEach((id) => expect(isActivityFilterId(id)).toBe(true));
    ["escrow", "ads", "rewards", "", null, 3].forEach((value) =>
      expect(isActivityFilterId(value)).toBe(false)
    );
  });

  it("tells an empty history apart from an empty filter", () => {
    expect(activityEmptyKey(0, "all")).toBe("emptyFeed");
    expect(activityEmptyKey(0, "refund")).toBe("emptyFeed");
    // Rows loaded, none matching: the seller should tap another chip, not
    // conclude their history vanished.
    expect(activityEmptyKey(12, "refund")).toBe("emptyFilter");
    expect(activityEmptyKey(12, "all")).toBe("emptyFeed");
  });
});

/* ---------------------------------------------------------------- *
 * Payout detail
 * ---------------------------------------------------------------- */

describe("payout detail helpers", () => {
  it("masks a provider reference down to four characters", () => {
    expect(maskedPayoutReference("po_1AbCdEfGh9821")).toBe("····9821");
  });

  it("returns nothing rather than a mask with nothing behind it", () => {
    ["", "  ", "po", "abc", null, undefined].forEach((value) =>
      expect(maskedPayoutReference(value)).toBe("")
    );
  });

  it("prefers the provider's own sentence over a generic one", () => {
    const failure = payoutFailure(
      payout({ status: "failed", failure_code: "account_closed", failure_message: "The bank account has been closed." })
    );
    expect(failure).toEqual({
      messageKey: null,
      message: "The bank account has been closed.",
      code: "account_closed"
    });
  });

  it("never shows a bare code as the explanation", () => {
    // A code with no message still gets a translated sentence, so the seller is
    // not told their payout failed because of `account_closed`.
    const failure = payoutFailure(payout({ status: "failed", failure_code: "account_closed" }));
    expect(failure?.messageKey).toBe("failureGeneric");
    expect(failure?.code).toBe("account_closed");
  });

  it("reports no failure for a payout that did not fail", () => {
    expect(payoutFailure(payout())).toBeNull();
    expect(payoutFailure(null)).toBeNull();
  });

  it("only offers 'check again' while a payout can still change", () => {
    ["paid", "canceled", "returned", "failed"].forEach((status) =>
      expect(payoutIsTerminal(status)).toBe(true)
    );
    ["pending", "created", "in_transit", "", "something_new"].forEach((status) =>
      expect(payoutIsTerminal(status)).toBe(false)
    );
  });
});

/* ---------------------------------------------------------------- *
 * Payout onboarding outcomes
 * ---------------------------------------------------------------- */

/**
 * The lie available here is the most expensive one on the screen: telling a
 * seller their payouts are set up when no account exists.
 *
 * `POST /api/pulse/payouts/connect` answers 200 in two very different worlds. If
 * `STRIPE_SECRET_KEY` is set it creates the connected account and returns a
 * link. If it is not, it writes a profile row with `onboarding_status =
 * 'stripe_not_configured'` and returns `ok: true` with no link at all — a
 * success body for a setup that did not happen. Reading `ok`, or reading the
 * status code, produces a screen that congratulates a seller who still cannot
 * be paid. Only the presence of the link means anything.
 */
describe("payoutOnboardingOutcome", () => {
  it("treats a link as the only evidence that setup can continue", () => {
    const outcome = payoutOnboardingOutcome({
      ok: true,
      onboarding_url: "https://connect.stripe.com/setup/s/acct_1"
    });
    expect(outcome.kind).toBe("ready");
    expect(outcome.url).toBe("https://connect.stripe.com/setup/s/acct_1");
  });

  it("does not call an ok-with-no-link a success", () => {
    // The unconfigured-Stripe case: `ok` is true and the status is 200.
    const outcome = payoutOnboardingOutcome({ ok: true, message: "Saved." });
    expect(outcome.kind).toBe("not_configured");
    expect(outcome.url).toBe("");
  });

  it("does not trust a blank or whitespace link", () => {
    [
      { ok: true, onboarding_url: "" },
      { ok: true, onboarding_url: "   " },
      null,
      undefined
    ].forEach((response) => {
      const outcome = payoutOnboardingOutcome(response);
      expect(outcome.kind).toBe("not_configured");
      expect(outcome.url).toBe("");
    });
  });

  it("carries the server's own sentence without becoming it", () => {
    const outcome = payoutOnboardingOutcome({ ok: true, message: "Stripe is not configured." });
    // Kept for display *underneath* our explanation, never as the explanation:
    // a seller should not be handed backend vocabulary as the whole answer.
    expect(outcome.serverMessage).toBe("Stripe is not configured.");
    expect(outcome.messageKey).toBe("outcomeNotConfigured");
  });
});

describe("payoutOnboardingFailure", () => {
  it("separates the refusals a seller can act on differently", () => {
    // 403 is the approved-seller gate, and it is the only refusal with a next
    // step inside the app, so it must not collapse into the generic failure.
    expect(payoutOnboardingFailure(403, "Approved seller status is required.").kind).toBe(
      "needs_seller_approval"
    );
    expect(payoutOnboardingFailure(401, "").kind).toBe("signed_out");
    expect(payoutOnboardingFailure(500, "boom").kind).toBe("failed");
  });

  it("falls back to a plain failure when there was no status at all", () => {
    // A transport error carries status 0. That is a failure to ask, not a
    // refusal, and must never be reported as "not configured" — which would
    // tell a seller their account is missing when the request never landed.
    const outcome = payoutOnboardingFailure(0, "Network request failed");
    expect(outcome.kind).toBe("failed");
    expect(outcome.url).toBe("");
  });

  it("never returns a link on any failure path", () => {
    [401, 403, 404, 429, 500, 0].forEach((status) =>
      expect(payoutOnboardingFailure(status, "x").url).toBe("")
    );
  });

  it("gives each outcome its own message key", () => {
    const keys = [
      payoutOnboardingOutcome({ ok: true, onboarding_url: "https://x" }).messageKey,
      payoutOnboardingOutcome({ ok: true }).messageKey,
      payoutOnboardingFailure(403, "").messageKey,
      payoutOnboardingFailure(401, "").messageKey,
      payoutOnboardingFailure(500, "").messageKey
    ];
    expect(new Set(keys).size).toBe(5);
  });
});

describe("payoutOnboardingPrefersServerMessage", () => {
  // The seller who tested this on a device saw both sentences at once —
  // "Payout setup couldn't start. Please try again." above the server's
  // "Payout onboarding failed. Please try again." — and read one tap as two
  // separate failures. Exactly one sentence may win.
  it("lets the server explain a failure, because only it knows why", () => {
    const outcome = payoutOnboardingFailure(503, "Payout setup isn't open yet.");
    expect(payoutOnboardingPrefersServerMessage(outcome)).toBe(true);
  });

  it("keeps the translated sentence when the failure arrived without one", () => {
    // A dropped connection has no body to quote, so the local string is all
    // there is — and it is the one that is translated.
    expect(payoutOnboardingPrefersServerMessage(payoutOnboardingFailure(0, ""))).toBe(false);
    expect(payoutOnboardingPrefersServerMessage(payoutOnboardingFailure(500, "   "))).toBe(false);
  });

  it("keeps the translated sentence for every outcome that is not a failure", () => {
    // These are fully determined by the response shape, so the server adds no
    // knowledge — only an untranslated, operator-worded duplicate.
    const settled = [
      payoutOnboardingOutcome({ ok: true, onboarding_url: "https://connect.stripe.com/x" }),
      payoutOnboardingOutcome({ ok: true, message: "Stripe Connect is not configured yet." }),
      payoutOnboardingFailure(403, "Approved merchant status is required."),
      payoutOnboardingFailure(401, "Login required.")
    ];
    settled.forEach((outcome) =>
      expect(payoutOnboardingPrefersServerMessage(outcome)).toBe(false)
    );
  });
});

/* ---------------------------------------------------------------- *
 * Declared gaps
 * ---------------------------------------------------------------- */

describe("declared gaps", () => {
  it("keeps the count a deliberate edit", () => {
    // Written down rather than derived, so closing a gap is something somebody
    // has to mean — the same rule PAYMENTS_MOCK_DATA_GAP_COUNT follows.
    expect(MONEY_LAYER_GAPS.length).toBe(MONEY_LAYER_GAP_COUNT);
  });

  it("says what is missing, why, and what is rendered instead", () => {
    MONEY_LAYER_GAPS.forEach((gap) => {
      expect(gap.field.trim().length).toBeGreaterThan(0);
      expect(gap.why.trim().length).toBeGreaterThan(20);
      expect(gap.clientBehaviour.trim().length).toBeGreaterThan(20);
    });
  });
});
