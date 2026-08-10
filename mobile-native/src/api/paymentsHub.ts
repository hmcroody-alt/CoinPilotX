/**
 * Payments money hub — the data layer for the screen where every number is money.
 *
 * The rule this module exists to keep
 * ------------------------------------
 * **No balance is ever computed here.** Not by adding ledger rows, not by
 * subtracting fees, not by summing wallets. Every figure the screen renders
 * arrives from the server as a finished total. The only arithmetic in this file
 * is `cents / 100` inside a formatter, and even that is display, not accounting.
 *
 * The reason is not tidiness. Two surfaces that each derive a balance will
 * eventually derive different balances, and a seller looking at two screens that
 * disagree about their own money has no way to tell which one is lying. So the
 * server owns the arithmetic and both screens render the same answer.
 *
 * Which backend this binds to
 * ---------------------------
 * There are two money systems in this codebase and only one is reachable.
 *
 *   • `/api/business-os/marketplace/money*` sits on the canonical double-entry
 *     ledger with real per-order escrow. It is richer and better tested. Every
 *     route in front of it is gated on `BUSINESS_OS_MARKETPLACE`, which is inert
 *     in production, so those routes answer 404 today. Binding the hero balance
 *     to an endpoint that 404s would ship a money screen that cannot load.
 *   • `/api/pulse/payments/seller/money*` reads `creator_wallets` /
 *     `creator_ledger_entries` — the tables the Stripe webhook handler actually
 *     writes. This is live. This is what we bind to.
 *
 * The ad wallet is deliberately NOT read from either of those. It comes from
 * `getAdWallet` in ./businessOs, which is the same call the Advertising screen
 * makes, because the mission's rule is that divergence between those two screens
 * is a bug. See docs/business_os/PAYMENTS_MONEY_SOURCE_MAP.md §5.
 *
 * What this platform cannot source
 * --------------------------------
 * The server reports its own gaps as machine-readable fields rather than
 * omitting them, and this module surfaces them as `PAYMENTS_MOCK_DATA_GAPS` and
 * a set of gate predicates. Payout initiation is now live (see
 * `payoutInitiationIsLive` and `./sellerPayouts`); every other gate still
 * answers false, and the screen's contract is to render an off module
 * **absent** — not disabled, not with a placeholder figure. See the constant
 * for the full list.
 */

import {
  AdBilling,
  AdWallet,
  adFundingIsLive,
  getAdBillingSummary,
  getAdWallet,
  listAdAccounts
} from "./businessOs";
import { readJsonCache, writeJsonCache } from "../core/cache";
import { envFlagOn } from "../core/envFlag";
import { pulseApi } from "./pulseApi";

/* ------------------------------------------------------------------ *
 * Wire types — mirrored from services/seller_money.py.
 * ------------------------------------------------------------------ */

export type PayoutMethod = {
  provider: string;
  seller_type: string;
  onboarding_status: string;
  payouts_enabled: boolean;
  charges_enabled: boolean;
  connected: boolean;
  /** Always "stripe_connected_account" — see `bank_destination`. */
  destination_kind: string;
  /** Masked server-side. The client never receives the full identifier. */
  destination_masked: string;
  /**
   * Always "not_stored". This platform holds no bank account number anywhere,
   * so the "•••• 4321 checking" the design asks for has no data behind it. The
   * screen shows the Stripe connection instead and says so.
   */
  bank_destination: string;
  missing_requirements: string[];
  last_checked_at: string | null;
  updated_at: string | null;
};

export type PayoutRecord = {
  payout_id: number;
  amount_cents: number;
  currency: string;
  status: string;
  provider: string;
  provider_payout_reference: string;
  failure_reason: string | null;
  created_at: string | null;
  updated_at: string | null;
};

export type SellerWalletPart = {
  wallet_id: number;
  wallet_type: string;
  currency: string;
  status: string;
  available_cents: number;
  processing_cents: number;
  lifetime_fees_cents: number;
  lifetime_earnings_cents: number;
  entry_count: number;
  stored_available_cents: number;
  stored_processing_cents: number;
  /** False when the cached wallet column disagrees with the ledger. */
  reconciled: boolean;
};

export type SellerMoneyOverview = {
  seller_user_id: number;
  currency: string;
  as_of: string;
  available_cents: number;
  processing_cents: number;
  lifetime_fees_cents: number;
  lifetime_earnings_cents: number;
  wallets: SellerWalletPart[];
  reconciled: boolean;
  has_wallet: boolean;
  payout_method: PayoutMethod | null;
  payout_in_flight: PayoutRecord | null;
  last_failed_payout: PayoutRecord | null;
  recent_payouts: PayoutRecord[];
  release_path: string;
  payout_initiation: string;
  instant_payout: string;
  statements: string;
  tax_documents: string;
  escrow: { supported: boolean; reason: string };
  ad_wallet_source: string;
};

/** The five row shapes the design specifies, plus `other` for entry types
 *  nobody has defined. `other` renders with no sign — see `LedgerEntry.sign`. */
export type LedgerKind = "income" | "spend" | "escrow" | "payout" | "refund" | "other";

export type LedgerEntry = {
  /** The ledger row's own primary key. Never synthesised, never positional. */
  id: number;
  kind: LedgerKind;
  /**
   * Decided server-side so two screens cannot decide it differently.
   * `"none"` means the amount renders unsigned — a hold is not an outflow, and
   * an unrecognised entry type gets no arrow rather than a guessed one.
   */
  sign: "+" | "-" | "none";
  entry_type: string;
  status: string;
  amount_cents: number;
  currency: string;
  title: string;
  reference: { type: string; id: string } | null;
  counterparty_user_id: number | null;
  provider: string | null;
  provider_reference: string | null;
  trace_id: string | null;
  created_at: string | null;
};

export type LedgerPage = {
  seller_user_id: number;
  currency: string;
  entries: LedgerEntry[];
  next_cursor: string | null;
  has_more: boolean;
  as_of: string;
};

/* ------------------------------------------------------------------ *
 * Feature flags.
 *
 * Every one of these gates a module that has no backend behind it today. They
 * default OFF and the screen's rule is that an off flag means the module is
 * ABSENT — not greyed out, not showing a placeholder. A disabled "Pay out now"
 * still tells the seller a payout is a thing they can nearly do; an absent one
 * tells the truth.
 *
 * `envFlagOn` is the shared reader in `core/envFlag.ts`. This module's own
 * `envFlag` was one of the four parsers it consolidated, and it was the one
 * whose behaviour the shared reader adopted — trim, lowercase, four accepted
 * values — so none of the six gates below changed when they moved onto it.
 * ------------------------------------------------------------------ */

/**
 * Gates the withdraw / request-payout flow.
 *
 * This answered false for as long as no endpoint initiated a payout. That is no
 * longer true: `POST /api/pulse/payments/seller/payouts` exists and moves money
 * to the seller's connected Stripe account, gated server-side on
 * `payouts_enabled` and the available balance, with a `payout_key` idempotency
 * contract. The gate now states that reality rather than reading a build flag —
 * a client flag that could hide a live money rail would be the same kind of lie
 * as a button over a dead one. The data layer for the flow lives in
 * `./sellerPayouts`.
 */
export function payoutInitiationIsLive(): boolean {
  return true;
}

/** Gates instant payout and its fee quote. Requires a backend quote endpoint,
 *  which does not exist; a client-computed fee is forbidden outright. */
export function instantPayoutIsLive(): boolean {
  return envFlagOn("EXPO_PUBLIC_PAYMENTS_INSTANT_PAYOUT");
}

/** Gates the statements list. No statement is generated anywhere. */
export function statementsAreLive(): boolean {
  return envFlagOn("EXPO_PUBLIC_PAYMENTS_STATEMENTS");
}

/** Gates the tax-document centre. No 1099-K or equivalent is ever issued, and
 *  a "no form this year" message would itself assert a threshold determination
 *  that nothing in this backend performs. */
export function taxDocumentsAreLive(): boolean {
  return envFlagOn("EXPO_PUBLIC_PAYMENTS_TAX_DOCUMENTS");
}

/** Gates the escrow balance card. Per-order escrow is Business OS only, and
 *  that vertical is dark in production. */
export function escrowCardIsLive(): boolean {
  return envFlagOn("EXPO_PUBLIC_PAYMENTS_ESCROW");
}

/**
 * Gates ad-wallet top-up and the auto-top-up switch.
 *
 * Two conditions, and the second is not ours. `adFundingIsLive(billing)` is
 * Advertising's own gate, evaluated against the same billing summary that screen
 * fetches — so if funding is dark there it is dark here, necessarily, rather than
 * by two files happening to agree. Our flag can only ever subtract from it.
 *
 * The billing argument is required rather than optional on purpose. The upstream
 * `adFundingIsLive` accepts `undefined` and answers false, which would let a
 * caller who simply forgot to fetch billing get a plausible-looking "funding is
 * off" instead of a compile error. On this screen a silent false is the wrong
 * kind of safe: it hides the top-up control for a reason nobody wrote down.
 */
export function adTopUpIsLive(billing: AdBilling | null | undefined): boolean {
  if (!billing) return false;
  return envFlagOn("EXPO_PUBLIC_PAYMENTS_AD_TOPUP") && adFundingIsLive(billing);
}

/* ------------------------------------------------------------------ *
 * Declared gaps. Rendered in the report and asserted in tests.
 * ------------------------------------------------------------------ */

export type MoneyGap = {
  field: string;
  why: string;
  clientBehaviour: string;
};

export const PAYMENTS_MOCK_DATA_GAPS: MoneyGap[] = [
  {
    field: "next payout date",
    why:
      "MOCK-DATA: no payout schedule is stored or computed anywhere. Stripe " +
      "Connect owns the schedule and this platform never reads it back.",
    clientBehaviour:
      "The hero sub-line states the payout destination and omits a date. The " +
      "pinging 'scheduled' dot renders only when payout_in_flight is a real row."
  },
  {
    field: "masked bank destination",
    why:
      "seller_payout_accounts stores a Stripe connected-account id, not an " +
      "account number. There is no '•••• 4321 checking' to render.",
    clientBehaviour:
      "The payout card names the Stripe connection with a masked reference and " +
      "labels it as such. No bank shape is implied."
  },
  {
    field: "instant payout fee and net",
    why: "No quote endpoint exists, and computing a fee client-side is forbidden.",
    clientBehaviour: "The instant-payout affordance is absent."
  },
  {
    field: "held in escrow",
    why:
      "Per-order escrow (mkt_order_escrow:<id>) is Business OS only and that " +
      "vertical is dark in production. A hold on the live surface is a " +
      "wallet-level pending balance, not money fenced per order.",
    clientBehaviour:
      "The escrow card is absent. Held money appears as Processing, which is " +
      "what it actually is."
  },
  {
    field: "statements and tax documents",
    why: "Nothing generates a statement or issues a tax form.",
    clientBehaviour:
      "Both sections are absent. No document name, year, or availability is " +
      "invented — including a 'no form this year' message, which would assert a " +
      "threshold determination nothing performs."
  },
  {
    field: "ad wallet top-up and auto top-up",
    why:
      "The Pulse Ads funding path exists in code behind three gates that all " +
      "deny: PULSE_ADS_BILLING_ENABLED, a hardcoded live_charging=false, and a " +
      "native-iOS 403. So adFundingIsLive() cannot return true.",
    clientBehaviour:
      "The balance renders; the top-up button and auto-top-up switch are absent."
  },
  {
    field: "refund response deadline",
    why:
      "No auto-approval policy, deadline field, or timer exists on either " +
      "money surface. 'Respond within N days or it is auto-approved' has no N.",
    clientBehaviour:
      "The refund banner states the dispute and its real status, and links to " +
      "Orders. It asserts no deadline and no consequence."
  },
  {
    field: "step-up authentication",
    why:
      "SECURITY GAP, not a data gap: this app has no re-authentication " +
      "primitive at all. Nothing can be gated behind one.",
    clientBehaviour:
      "Payout initiation now ships on server-side session auth alone. The " +
      "blast radius is bounded — the destination is locked server-side to the " +
      "seller's own connected Stripe account — but a re-authentication " +
      "primitive is still the right fix and remains unbuilt."
  }
];

/**
 * The row count, written out, so a test can hold this ledger to it.
 *
 * These are the money rows, and one of them is a declared security gap rather
 * than a data gap — yet this was the one table of the nine that no test read at
 * all, which made the most consequential line in the ledger the least protected.
 * A literal rather than `PAYMENTS_MOCK_DATA_GAPS.length`: derived from the array
 * it would only ever restate the array, and the point is to make closing a money
 * gap an edit somebody has to mean.
 *
 * Was 9. "Available balance becoming non-zero" left the ledger when the Stripe
 * webhook release path and `POST /api/pulse/payments/seller/payouts` shipped —
 * a gap closed by a real source, which is the only way a row leaves this list.
 */
export const PAYMENTS_MOCK_DATA_GAP_COUNT = 8;

/* ------------------------------------------------------------------ *
 * Reads.
 * ------------------------------------------------------------------ */

const OVERVIEW_CACHE_KEY = "payments.hub.overview.v1";
const ACTIVITY_CACHE_KEY = "payments.hub.activity.v1";

type CachedOverview = { overview: SellerMoneyOverview; cached_at: string };
type CachedActivity = { page: LedgerPage; cached_at: string };

export async function fetchMoneyOverview(currency = "USD"): Promise<SellerMoneyOverview> {
  const result = await pulseApi<{ ok?: boolean; money?: SellerMoneyOverview }>(
    `/api/pulse/payments/seller/money?currency=${encodeURIComponent(currency)}`
  );
  const money = result?.money;
  if (!money) throw new Error("Balances unavailable.");
  // Cached so the offline state can show "as of {time}". It is never used to
  // satisfy a fresh read — see loadCachedOverview's doc comment.
  await writeJsonCache<CachedOverview>(OVERVIEW_CACHE_KEY, {
    overview: money,
    cached_at: new Date().toISOString()
  }).catch(() => undefined);
  return money;
}

export async function fetchLedgerPage(options: {
  currency?: string;
  cursor?: string | null;
  limit?: number;
} = {}): Promise<LedgerPage> {
  const params = new URLSearchParams();
  params.set("currency", options.currency || "USD");
  params.set("limit", String(Math.max(1, Math.min(100, options.limit || 25))));
  if (options.cursor) params.set("cursor", options.cursor);
  const result = await pulseApi<{ ok?: boolean; activity?: LedgerPage }>(
    `/api/pulse/payments/seller/money/activity?${params.toString()}`
  );
  const page = result?.activity;
  if (!page) throw new Error("Activity unavailable.");
  if (!options.cursor) {
    // Only the first page is cached. Caching deeper pages would let an offline
    // reader scroll into a feed whose head is stale — the "last updated" label
    // would be attached to the wrong thing.
    await writeJsonCache<CachedActivity>(ACTIVITY_CACHE_KEY, {
      page,
      cached_at: new Date().toISOString()
    }).catch(() => undefined);
  }
  return page;
}

/**
 * The cached overview, for the offline state ONLY.
 *
 * A cached balance is not a balance. It is a statement about the past, and the
 * screen may render it only under an explicit "as of {time}" label with money
 * actions blocked. A failed *fresh* read must show "—" with a retry, never this.
 * Returning the timestamp alongside the figures is what makes that enforceable:
 * a caller cannot render these numbers without also holding the time they were
 * true.
 */
export async function loadCachedOverview(): Promise<{ overview: SellerMoneyOverview; cachedAt: string } | null> {
  const cached = await readJsonCache<CachedOverview>(OVERVIEW_CACHE_KEY, (value) => value);
  if (!cached?.overview) return null;
  return { overview: cached.overview, cachedAt: cached.cached_at };
}

export async function loadCachedActivity(): Promise<{ page: LedgerPage; cachedAt: string } | null> {
  const cached = await readJsonCache<CachedActivity>(ACTIVITY_CACHE_KEY, (value) => value);
  if (!cached?.page) return null;
  return { page: cached.page, cachedAt: cached.cached_at };
}

/**
 * The ad wallet, read from the same place the Advertising screen reads it.
 *
 * This is a re-export of Advertising's own path on purpose. The Payments card
 * and the Advertising header must render one object; the moment this file grows
 * its own wallet fetch, the two screens can drift, and a seller seeing two
 * different ad balances cannot tell which is real.
 *
 * Returns null — not zero — when the wallet cannot be read. A zero would look
 * like a measurement.
 */
export async function fetchAdWallet(): Promise<{
  wallet: AdWallet;
  billing: AdBilling | null;
  accountId: number;
} | null> {
  const response = await listAdAccounts().catch(() => null);
  const accountId = Number(response?.accounts?.[0]?.id || 0);
  // No ad account is not an error and not a zero balance — it is the absence of
  // an ad wallet, and the card is absent with it.
  if (!accountId) return null;
  const walletResponse = await getAdWallet(accountId).catch(() => null);
  const wallet = walletResponse?.wallet;
  if (!wallet) return null;
  // Billing is fetched alongside because it carries the funding gate, and the
  // top-up control's visibility is not decidable from the wallet alone. A failed
  // billing read yields null, which `adTopUpIsLive` treats as "not live" — the
  // safe direction, since the cost of wrongly hiding top-up is an extra tap and
  // the cost of wrongly showing it is a control that silently does nothing.
  const billingResponse = await getAdBillingSummary(accountId).catch(() => null);
  return { wallet, billing: billingResponse?.billing || null, accountId };
}

/**
 * The ad wallet's spendable figure, using the SAME field precedence
 * `adsDashboard.walletSummary` uses.
 *
 * Matching the fallback matters as much as matching the endpoint. If Advertising
 * preferred `spendable_balance_cents` and Payments preferred
 * `available_balance_cents`, the two screens would read one object and still
 * disagree — a slower, subtler way of producing exactly the divergence the
 * shared endpoint was meant to prevent.
 */
export function adWalletSpendableCents(wallet: AdWallet | null | undefined): number | null {
  if (!wallet) return null;
  const spendable = wallet.spendable_balance_cents;
  const available = wallet.available_balance_cents;
  if (typeof spendable === "number") return spendable;
  if (typeof available === "number") return available;
  return null;
}

/* ------------------------------------------------------------------ *
 * Presentation helpers. Formatting only — never accounting.
 * ------------------------------------------------------------------ */

export function formatMoney(cents: number | null | undefined, currency = "USD"): string {
  if (cents === null || cents === undefined || !Number.isFinite(Number(cents))) {
    // The design's rule for an unavailable balance: an em dash, never a zero.
    // A zero is a claim about the seller's money; a dash is an admission.
    return "—";
  }
  const amount = Number(cents) / 100;
  const code = String(currency || "USD").toUpperCase();
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: code }).format(amount);
  } catch {
    return `${code} ${amount.toFixed(2)}`;
  }
}

/**
 * A quotable reference for a money failure the seller is looking at.
 *
 * A seller staring at an em dash where their balance should be needs something
 * to say when they contact support beyond "it was broken". This is that thing.
 *
 * It is deliberately derived from the wall clock and nothing else. There is no
 * server-issued correlation id on these endpoints, and minting a random opaque
 * token would look like one — a code that appears to identify a record support
 * can look up, when in fact no record exists, is worse than no code at all. A
 * timestamp is the one identifier that is genuinely useful here: support can
 * correlate it against server logs for that seller at that minute, and it is
 * honest about being nothing more than "when this happened".
 *
 * Minute resolution, not second, so that a seller reading it aloud over the
 * phone gets it right and so that two failures inside the same minute do not
 * imply two different incidents. The two trailing characters are the local UTC
 * offset in quarter-hours, base-36, which is what makes a code from a seller in
 * Lagos distinguishable from one in Los Angeles without asking them.
 */
export function supportReferenceFor(when: Date = new Date()): string {
  if (Number.isNaN(when.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  const date = `${when.getFullYear()}${pad(when.getMonth() + 1)}${pad(when.getDate())}`;
  const time = `${pad(when.getHours())}${pad(when.getMinutes())}`;
  // `getTimezoneOffset` is minutes *behind* UTC, so it is negated to read the
  // way a person would say it. Offset by 48 quarter-hours keeps it non-negative
  // across the whole −12…+14 range so the code never contains a sign.
  const quarterHours = Math.round(-when.getTimezoneOffset() / 15) + 48;
  return `PAY-${date}-${time}-${quarterHours.toString(36).toUpperCase().padStart(2, "0")}`;
}

/** The amount as it appears on a ledger row, sign included. */
export function formatSignedAmount(entry: LedgerEntry): string {
  const magnitude = formatMoney(Math.abs(entry.amount_cents), entry.currency);
  if (entry.sign === "+") return `+${magnitude}`;
  if (entry.sign === "-") return `−${magnitude}`; // U+2212, not a hyphen
  return magnitude;
}

/**
 * What a screen reader says for a ledger row.
 *
 * Colour is never the only signal, so the word that colour encodes — "held",
 * "refund", "paid out" — is spoken. In particular a hold announces as *held*,
 * because an AT user hearing an escrow row must not understand it as money lost.
 */
export function describeEntryForAccessibility(entry: LedgerEntry): string {
  const magnitude = formatMoney(Math.abs(entry.amount_cents), entry.currency);
  const direction =
    entry.sign === "+" ? `${magnitude} in`
      : entry.sign === "-" ? `${magnitude} out`
        : entry.kind === "escrow" ? `${magnitude} held, still yours`
          : magnitude;
  const parts = [KIND_LABEL[entry.kind], entry.title, direction];
  if (entry.status && entry.status !== "posted") parts.push(`status ${entry.status}`);
  if (entry.reference) parts.push(`reference ${entry.reference.type} ${entry.reference.id}`);
  return parts.filter(Boolean).join(", ");
}

export const KIND_LABEL: Record<LedgerKind, string> = {
  income: "Income",
  spend: "Spend",
  escrow: "Held",
  payout: "Payout",
  refund: "Refund",
  other: "Ledger entry"
};

/**
 * The one honest sentence about where a seller's money is.
 *
 * Called by the hero sub-line. It exists because "$0.00 available" beside
 * "$240.00 processing" is technically true and practically a lie by omission —
 * the seller concludes something is broken. The explanation is derived from the
 * server's own `release_path` field, so if a release path is ever built, this
 * copy changes with it instead of being left behind.
 */
export function describeAvailability(overview: SellerMoneyOverview): string {
  if (!overview.has_wallet) return "No sales yet";
  if (overview.release_path === "none_in_product" && overview.processing_cents > 0) {
    return `${formatMoney(overview.processing_cents, overview.currency)} is still processing`;
  }
  if (overview.payout_method?.payouts_enabled) {
    return `To ${overview.payout_method.provider} ${overview.payout_method.destination_masked}`;
  }
  return "Add a payout method to get paid";
}

/**
 * Whether the hero's pinging dot should animate.
 *
 * Only while a payout is genuinely in flight. The design is explicit that the
 * dot means something; an always-on dot is decoration that reads as status.
 */
export function payoutIsScheduled(overview: SellerMoneyOverview | null): boolean {
  return Boolean(overview?.payout_in_flight);
}

/**
 * The payout-method state the screen branches on.
 *
 * `"none"` outranks every other prompt on the screen — a seller with no way to
 * be paid should be told that before anything else competes for the space.
 */
export function payoutMethodState(
  overview: SellerMoneyOverview | null
): "none" | "incomplete" | "ready" | "unknown" {
  if (!overview) return "unknown";
  const method = overview.payout_method;
  if (!method) return "none";
  if (method.payouts_enabled) return "ready";
  return "incomplete";
}
