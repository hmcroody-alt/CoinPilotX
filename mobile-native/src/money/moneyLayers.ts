/**
 * The money layers — every decision the deeper payment screens branch on.
 *
 * Why this is a module and not five screens' worth of inline `if`s
 * ---------------------------------------------------------------
 * The Payments hub answers "how much". The layers underneath it answer "why",
 * and a "why" is a judgement: is this seller blocked or merely unverified, is
 * this money held or lost, is an empty list an empty history or a failed read.
 * Those judgements have to be made once. Two screens that each decide whether a
 * payout account is "pending" will eventually disagree, and a seller told they
 * are verified on one screen and unverified on the next has no way to know which
 * to believe — the same failure mode `api/paymentsHub.ts` exists to prevent for
 * figures, applied to states.
 *
 * So this file is pure: it takes the canonical payloads and returns discriminated
 * unions plus i18n key *suffixes*. It renders nothing, fetches nothing, and
 * formats no money. It is the part of the layers a test can hold still.
 *
 * The arithmetic rule is inherited whole
 * --------------------------------------
 * **Nothing here computes a balance.** Not by summing `recent_payouts`, not by
 * subtracting processing from lifetime, not by counting entries. Where the
 * mission asks for a figure the server does not send, this module declares the
 * gap (see `MONEY_LAYER_GAPS`) and the layer renders an em dash with a sentence,
 * because a client-side total that disagrees with the server's by one refunded
 * cent is worse than no total at all.
 *
 * i18n keys, not sentences
 * ------------------------
 * Every user-visible string is returned as a suffix under `commerce:money`. A
 * literal English sentence returned from here would pass typecheck, ship, and
 * then be untranslatable in ten locales — and the hardcoded-string gate does not
 * read return values.
 */

import type { LedgerEntry, LedgerKind, SellerMoneyOverview } from "../api/paymentsHub";
import type { ConnectStatus, SellerPayout } from "../api/sellerPayouts";

/* ------------------------------------------------------------------ *
 * The layers themselves.
 * ------------------------------------------------------------------ */

/**
 * One id per screen the hub can open.
 *
 * `MoneyDetail` is deliberately not in this union: a detail screen is opened
 * with a subject (a payout, a ledger row), not with a layer id, and giving it an
 * id here would let a caller navigate to a detail screen with nothing to detail.
 */
export type MoneyLayerId =
  | "payout_overview"
  | "processing"
  | "move_money"
  | "payout_history"
  | "activity";

export const MONEY_LAYER_IDS: readonly MoneyLayerId[] = [
  "payout_overview",
  "processing",
  "move_money",
  "payout_history",
  "activity"
] as const;

export function isMoneyLayerId(value: unknown): value is MoneyLayerId {
  return MONEY_LAYER_IDS.includes(value as MoneyLayerId);
}

/* ------------------------------------------------------------------ *
 * Payout readiness — the state the onboarding and Move-your-money layers
 * both branch on.
 * ------------------------------------------------------------------ */

/**
 * Where the seller is in getting paid.
 *
 * `unknown` is a real member and the most important one. It means the connect
 * status has not been read, or the read failed — and the honest screen for that
 * is "we could not check", with a retry, not "not set up". Telling a fully
 * verified seller they have no payout account because a request timed out is the
 * exact class of lie this codebase keeps writing tests against.
 */
export type PayoutSetupStage =
  | "unknown"
  | "not_started"
  | "in_progress"
  | "pending_verification"
  | "blocked"
  | "ready";

/** The single action the layer offers. One per stage — never a row of buttons. */
export type PayoutSetupAction = "start" | "resume" | "retry_status" | "manage";

export type PayoutReadiness = {
  stage: PayoutSetupStage;
  action: PayoutSetupAction;
  /**
   * i18n suffix under `commerce:money.payout` for the sentence that explains the
   * stage. Always present — a stage with no explanation is the "blocked, no
   * reason given" screen the mission forbids.
   */
  reasonKey: string;
  /**
   * Raw provider codes (`disabled_reason`, `missing_requirements`). Rendered
   * verbatim under a "reference" label and never as prose: `individual.
   * verification.document` is a support artefact, not a sentence, and dressing
   * it up as one produces confident nonsense.
   */
  codes: string[];
  /** True while payouts can actually happen. The layers gate money actions on
   *  this rather than re-reading `payouts_enabled` in five places. */
  payoutsEnabled: boolean;
};

/**
 * Stripe's `disabled_reason` vocabulary, mapped to sentences we are willing to
 * stand behind. Anything not listed falls to `reasonBlockedOther`, which says
 * the account is blocked and shows the code — true, and short of inventing a
 * cause for a string this build has never seen.
 */
const BLOCKER_REASON_KEY: Record<string, string> = {
  "requirements.past_due": "reasonPastDue",
  "requirements.pending_verification": "reasonPendingVerification",
  "under_review": "reasonUnderReview",
  "listed": "reasonUnderReview",
  "platform_paused": "reasonPaused",
  "rejected.fraud": "reasonRejected",
  "rejected.terms_of_service": "reasonRejected",
  "rejected.listed": "reasonRejected",
  "rejected.other": "reasonRejected",
  "action_required.requested_capabilities": "reasonActionRequired"
};

/** A blocked account is one the seller cannot unblock by finishing a form. */
const HARD_BLOCK_PREFIXES = ["rejected.", "platform_paused", "listed", "under_review"];

function isHardBlock(reason: string): boolean {
  return HARD_BLOCK_PREFIXES.some((prefix) => reason === prefix || reason.startsWith(prefix));
}

/**
 * Resolve the payout stage from the two sources that know about it.
 *
 * Both arguments are nullable because both reads can fail independently — the
 * screen uses `Promise.allSettled`, so one failing must not be allowed to
 * masquerade as the other's answer. With neither, the answer is `unknown`.
 *
 * The money overview's `payout_method` is preferred for `payouts_enabled`
 * because it is the field the hub already renders; the connect status supplies
 * the *why* (`details_submitted`, `disabled_reason`) that the overview does not
 * carry. Reading enablement from two places and trusting whichever said yes
 * would widen access on a partial read, so the two are OR'd only where both mean
 * the same thing and the negative case wins everywhere else.
 */
export function payoutReadiness(
  connect: ConnectStatus | null | undefined,
  overview: SellerMoneyOverview | null | undefined
): PayoutReadiness {
  const method = overview?.payout_method || null;
  const requirements = Array.isArray(method?.missing_requirements)
    ? method.missing_requirements.filter((item) => typeof item === "string" && item.trim())
    : [];
  const disabledReason = String(connect?.state?.disabled_reason || "").trim();
  const codes = [disabledReason, ...requirements].filter(Boolean);

  const payoutsEnabled = connect?.payouts_enabled === true || method?.payouts_enabled === true;

  // Enabled outranks every complaint. Stripe can carry a stale requirement on an
  // account that is paying out fine, and a screen that says "blocked" over a
  // working rail sends the seller to support for nothing.
  if (payoutsEnabled) {
    return { stage: "ready", action: "manage", reasonKey: "reasonReady", codes, payoutsEnabled: true };
  }

  // No answer from either source. Not "not started" — we did not look.
  if (!connect && !method) {
    return {
      stage: "unknown",
      action: "retry_status",
      reasonKey: "reasonUnknown",
      codes: [],
      payoutsEnabled: false
    };
  }

  if (disabledReason && isHardBlock(disabledReason)) {
    return {
      stage: "blocked",
      action: "retry_status",
      reasonKey: BLOCKER_REASON_KEY[disabledReason] || "reasonBlockedOther",
      codes,
      payoutsEnabled: false
    };
  }

  const connected = connect?.connected === true || method?.connected === true;
  if (!connected) {
    return {
      stage: "not_started",
      action: "start",
      reasonKey: "reasonNotStarted",
      codes,
      payoutsEnabled: false
    };
  }

  const detailsSubmitted = connect?.state?.details_submitted === true;
  if (!detailsSubmitted) {
    return {
      stage: "in_progress",
      action: "resume",
      reasonKey: "reasonInProgress",
      codes,
      payoutsEnabled: false
    };
  }

  // Everything filed, nothing enabled: the provider is still deciding. A
  // requirement code here is the useful part, so it stays in `codes` while the
  // sentence stays generic.
  return {
    stage: "pending_verification",
    action: "retry_status",
    reasonKey: BLOCKER_REASON_KEY[disabledReason] || "reasonPendingVerification",
    codes,
    payoutsEnabled: false
  };
}

/* ------------------------------------------------------------------ *
 * Processing — why money is not available yet.
 * ------------------------------------------------------------------ */

/**
 * The explanation the Processing layer leads with.
 *
 * Derived from the server's own `release_path` rather than from the figures, so
 * that when a release path is built the copy follows it instead of being left
 * behind asserting a limitation that no longer exists. `services/seller_money.py`
 * reports `none_in_product` when no payout-request schema is deployed and
 * `payout_request` when one is.
 */
export type ProcessingExplainer = {
  /** i18n suffix under `commerce:money.processing`. */
  key: "noWallet" | "nothingProcessing" | "noReleasePath" | "awaitingRelease";
  /** Echoed so the layer can show it as a diagnostic and a test can pin it. */
  releasePath: string;
  /** Whether a release is something the product can actually perform today. */
  releasable: boolean;
};

export function processingExplainer(
  overview: SellerMoneyOverview | null | undefined
): ProcessingExplainer {
  const releasePath = String(overview?.release_path || "").trim();
  const releasable = releasePath === "payout_request";
  if (!overview || !overview.has_wallet) {
    return { key: "noWallet", releasePath, releasable };
  }
  if (!(Number(overview.processing_cents) > 0)) {
    return { key: "nothingProcessing", releasePath, releasable };
  }
  if (!releasable) {
    // The honest version of "$0.00 available beside $240.00 processing": on this
    // deployment nothing moves a hold to available, and saying "it will clear
    // soon" would be a schedule this platform does not have.
    return { key: "noReleasePath", releasePath, releasable };
  }
  return { key: "awaitingRelease", releasePath, releasable };
}

/* ------------------------------------------------------------------ *
 * Activity filters.
 * ------------------------------------------------------------------ */

/**
 * The filter chips over the money-movement feed.
 *
 * These are the buckets the *seller ledger* actually writes, taken from
 * `_KIND_BY_ENTRY_TYPE` in `services/seller_money.py`. There is deliberately no
 * "ad spend" chip and no "rewards" chip: neither of those ever lands in this
 * table (ad money lives in the Pulse Ads wallet, rewards in the rewards ledger),
 * so a chip for either would filter a feed that structurally cannot contain them
 * and return empty every time — a control that teaches the seller the wrong
 * thing about where their money went. Those two live in their own layers and are
 * recorded in `MONEY_LAYER_GAPS`.
 */
/**
 * Every kind the ledger can return, restated here so a catalog test can assert
 * that all six have a word. A kind with no translation renders its own token at
 * the seller, which on a money row looks like a bug in their accounts.
 */
export const MONEY_LEDGER_KINDS: readonly LedgerKind[] = [
  "income",
  "spend",
  "escrow",
  "payout",
  "refund",
  "other"
] as const;

export type ActivityFilterId = "all" | "income" | "payout" | "held" | "refund" | "spend";

export const ACTIVITY_FILTERS: readonly ActivityFilterId[] = [
  "all",
  "income",
  "payout",
  "held",
  "refund",
  "spend"
] as const;

const FILTER_KIND: Record<Exclude<ActivityFilterId, "all">, LedgerKind> = {
  income: "income",
  payout: "payout",
  held: "escrow",
  refund: "refund",
  spend: "spend"
};

export function isActivityFilterId(value: unknown): value is ActivityFilterId {
  return ACTIVITY_FILTERS.includes(value as ActivityFilterId);
}

/**
 * Whether a row survives a filter.
 *
 * A row of kind `other` matches only `all`. That is intentional: `other` is the
 * server's word for "an entry type nobody has classified", and filing it under
 * the nearest-looking chip would assert a classification the server declined to
 * make. It stays visible in the unfiltered feed, so nothing disappears.
 */
export function activityFilterMatches(filter: ActivityFilterId, entry: LedgerEntry): boolean {
  if (filter === "all") return true;
  return entry.kind === FILTER_KIND[filter];
}

export function filterLedgerEntries(
  entries: LedgerEntry[],
  filter: ActivityFilterId
): LedgerEntry[] {
  if (filter === "all") return entries;
  return entries.filter((entry) => activityFilterMatches(filter, entry));
}

/**
 * Which empty state an emptied list should show.
 *
 * "No activity yet" and "nothing matches this filter" are different facts and
 * the difference is actionable — one says start selling, the other says tap
 * another chip. A single empty state for both sends a seller with a full history
 * looking for a bug.
 */
export function activityEmptyKey(
  totalLoaded: number,
  filter: ActivityFilterId
): "emptyFeed" | "emptyFilter" {
  return totalLoaded === 0 || filter === "all" ? "emptyFeed" : "emptyFilter";
}

/* ------------------------------------------------------------------ *
 * Payout detail.
 * ------------------------------------------------------------------ */

/**
 * The provider's payout reference, masked.
 *
 * Masked for the same reason `maskedConnectRef` masks the connected-account id:
 * the client has no business holding a whole provider identifier, and the last
 * four characters are enough for a seller and support to agree they are looking
 * at the same payout. Returns "" — not a partial mask — for anything too short
 * to mask, so the layer omits the row rather than rendering `····`.
 */
export function maskedPayoutReference(reference: string | null | undefined): string {
  const value = String(reference || "").trim();
  if (value.length < 4) return "";
  return `····${value.slice(-4)}`;
}

/**
 * Whether a payout's failure text is safe to show the seller.
 *
 * Stripe's `failure_message` is written for the account holder and is safe;
 * `failure_code` is a token. Both are shown, but the message is the sentence and
 * the code is the reference — never the other way round, which is how a screen
 * ends up telling a seller their payout failed because of
 * `account_closed`.
 */
export type PayoutFailure = { messageKey: "failureGeneric" | null; message: string; code: string };

export function payoutFailure(payout: SellerPayout | null | undefined): PayoutFailure | null {
  if (!payout) return null;
  const code = String(payout.failure_code || "").trim();
  const message = String(payout.failure_message || "").trim();
  if (!code && !message) return null;
  // A code with no message gets a translated generic sentence plus the code, so
  // the seller reads something in their own language either way.
  return { messageKey: message ? null : "failureGeneric", message, code };
}

/**
 * A payout is terminal when nothing further will happen to it on its own.
 *
 * The layer uses this to decide whether to offer "check again" — offering a
 * refresh on a payout that reached `paid` three weeks ago implies the state is
 * still in doubt.
 */
export function payoutIsTerminal(status: string): boolean {
  return status === "paid" || status === "canceled" || status === "returned" || status === "failed";
}

/* ------------------------------------------------------------------ *
 * Declared gaps.
 * ------------------------------------------------------------------ */

export type MoneyLayerGap = {
  /** What the design asked for. */
  field: string;
  /** Why no source exists. */
  why: string;
  /** What the layer renders instead. Never a zero, never a guess. */
  clientBehaviour: string;
};

/**
 * The figures these layers were asked for and cannot honestly source.
 *
 * This mirrors `PAYMENTS_MOCK_DATA_GAPS` in `api/paymentsHub.ts` and exists for
 * the same reason: a gap that is written down is a gap somebody can close, while
 * a gap that was quietly filled with plausible arithmetic is a number nobody
 * will ever question.
 */
export const MONEY_LAYER_GAPS: MoneyLayerGap[] = [
  {
    field: "lifetime paid-out total",
    why:
      "No endpoint reports it. `recent_payouts` is a page of the most recent " +
      "rows, and `SellerPayoutPage` is cursor-paginated, so summing either " +
      "yields a total that grows as the seller scrolls — the client-side " +
      "accounting api/paymentsHub.ts forbids in its opening rule.",
    clientBehaviour:
      "The Payout Overview shows Available, Processing and Lifetime earnings — " +
      "all server-computed — and omits the paid-out tile entirely rather than " +
      "showing a running subtotal labelled as a lifetime figure."
  },
  {
    field: "payout arrival date",
    why:
      "Stripe owns the schedule and this platform never reads it back. There " +
      "is no `arrival_date` on `seller_payouts` and no schedule anywhere.",
    clientBehaviour:
      "Payout rows and the payout detail show created/updated timestamps and " +
      "the status chip. No ETA, no 'expected by', no countdown."
  },
  {
    field: "earnings attributable to one payout",
    why:
      "`creator_ledger_entries` has no payout_id column and `seller_payouts` " +
      "stores no line items, so the join from a payout back to the sales it " +
      "paid out is not something these tables can express.",
    clientBehaviour:
      "Payout detail links to the Activity layer for the same period instead " +
      "of listing 'included earnings' it would have to guess at."
  },
  {
    field: "ad spend and wallet funding inside the unified activity feed",
    why:
      "Ad money never enters `creator_ledger_entries`. It lives in the Pulse " +
      "Ads wallet, which Advertising reads directly — the arrangement that " +
      "keeps the two screens from disagreeing about the ad balance.",
    clientBehaviour:
      "The Activity layer covers the seller ledger and says so; ad funding and " +
      "spend history are reached by handing off to Advertising, which already " +
      "owns the wallet's own transaction history. There is deliberately no " +
      "`MoneyLayerKind` for the ad wallet — a second reader of that balance is " +
      "the thing this hand-off exists to avoid."
  },
  {
    field: "rewards credits inside the unified activity feed",
    why:
      "Rewards and Pulse Credits are a separate ledger behind " +
      "/api/pulse/rewards, with its own units. Interleaving credits with " +
      "currency rows would produce a feed whose amounts are not all money.",
    clientBehaviour:
      "The Rewards & Credits hub owns that history in full. The Activity " +
      "layer does not claim to include it."
  }
];

/**
 * Written out rather than derived from the array above, for the reason the
 * payments hub gives for its own count: derived, it would only ever restate the
 * array; written down, closing a gap is an edit somebody has to mean.
 */
export const MONEY_LAYER_GAP_COUNT = 5;
