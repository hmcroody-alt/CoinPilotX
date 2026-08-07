/**
 * Delivery truth, spend truth, and wallet authority.
 *
 * WHY THIS MODULE EXISTS
 * ----------------------
 * The app currently shows `campaign.status` as though it meant something about
 * delivery. It does not. `status='active'` is one of eight conditions the
 * selector requires, and it is the only one the client has been reading.
 *
 * `pulse_ads_service.select_ads` (services/pulse_ads_service.py:1213–1224) will
 * only return a creative when **all** of these hold:
 *
 * ```
 *   p.is_active = 1
 *   c.status    = 'active'
 *   a.status    = 'active'          <- the ad ACCOUNT, not the campaign
 *   cr.moderation_status = 'approved'
 *   cr.status            = 'approved'
 *   creative_type in (image,video,audio) implies media_asset_id IS NOT NULL
 *   media_asset_id  is null or its asset is moderation_status='approved'
 *   thumbnail_asset_id is null or its asset is approved
 *   c.start_at empty or <= now, and c.end_at empty or >= now
 *   placement device_type = 'all' or matches the viewer
 * ```
 *
 * and then four more runtime filters pass: targeting match, creative/placement
 * type compatibility, `_campaign_budget_available`, and the frequency cap.
 *
 * So an advertiser can sit in front of a campaign labelled "Active", with money
 * in the wallet and an approved ad, and reach nobody — forever, silently. That
 * is the contradictory state `NO_DEAD_ENDS` exists to forbid, and it is what
 * ships today.
 *
 * THE FINDING THAT MATTERS MOST — AND WHAT WAS DONE ABOUT IT
 * ----------------------------------------------------------
 * `a.status = 'active'` is required for every impression, and when this module
 * was written **no route in the product ever set it**. `create_ad_account`
 * inserts `'pending_verification'` with `verification_status='unverified'`; the
 * only write to that column in `bot.py` was the admin disable route, which sets
 * `'suspended'`. Every `status='active'` write in the repo lived in
 * `scripts/*_audit.py` — one-off tools, not a product path. A self-serve
 * advertiser could not reach delivery at all, ever, and the client could only
 * name the wall.
 *
 * The wall now has a door. `POST /api/pulse/ads/accounts/:id/verification` lets
 * the advertiser ask for review; `POST /api/admin/pulse/ads/accounts/verify`
 * writes `status='active'` and `verification_status='verified'` together,
 * because they describe one decision and an account that is verified but not
 * active still serves nothing. Rejection stores a reason and the account can
 * resubmit.
 *
 * So the account-level blockers below are no longer one undifferentiated "not
 * approved". Four states, and only two of them are things the reader waits on.
 *
 * WHY "DELIVERING" IS NOT A CLAIM THIS MODULE MAKES LIGHTLY
 * --------------------------------------------------------
 * Four of the selector's conditions are evaluated per request against data the
 * client never sees: today's impression count, the viewer's targeting context,
 * frequency history, and the wallet. So "every gate I can see is clear" is not
 * the same statement as "this is delivering", and conflating them would be the
 * same category of error the module exists to remove.
 *
 * Hence two separate states. {@link deliveryState} answers `"eligible"` when
 * every visible gate is clear, and `"delivering"` only when the ledger has
 * already proved it — `spent_cents > 0`, money that moved. One is a forecast,
 * the other is a receipt, and the reader deserves to know which they are
 * looking at.
 *
 * WALLET AUTHORITY
 * ----------------
 * `portal_summary` wraps each `wallet_summary` call in a bare `except` and, on
 * failure, appends a row of literal zeroes with `"$0.00"` strings already
 * formatted (services/pulse_advertiser_portal.py:440–466). Those rows are then
 * summed into `metrics.wallet_balance_cents` and `metrics.reserved_budget_cents`.
 * A fabricated zero is indistinguishable from a real one in the payload.
 *
 * Except in one case, which is the whole lever: `wallet_summary` opens with
 * `_owner_account(conn, user_id, account_id)` (services/pulse_ad_payments.py:135),
 * so it raises for **every non-owner**. A campaign manager with full write
 * access takes the except branch on every request. For those accounts the
 * `"$0.00"` is guaranteed fabricated, and the client knows it for certain
 * without needing to detect anything.
 *
 * {@link walletAuthority} therefore reports `"confirmed"` only for accounts the
 * reader owns, and `"restricted"` otherwise — §31's word for access
 * intentionally blocked, which is exactly what an owner-only endpoint is. §37
 * forbids a client-authoritative wallet balance; this module never computes
 * one, it only decides whether the server's is worth repeating.
 */

import type { CampaignTone } from "./adsDashboard";
import { AdCampaign, AdWallet } from "./businessOs";
import {
  AdCreative,
  AdsPortal,
  accountIsListed,
  accountRole,
  portalIsLoaded
} from "./adsPortal";

/* ------------------------------------------------------------------ *
 * Delivery state
 * ------------------------------------------------------------------ */

export const DELIVERY_STATES = [
  "delivering",
  "eligible",
  "active_unconfirmed",
  "blocked",
  "scheduled",
  "ended",
  "paused",
  "in_review",
  "draft",
  "archived",
  "suspended"
] as const;

export type DeliveryState = (typeof DELIVERY_STATES)[number];

/**
 * The specific gate a campaign is stuck behind, in the reader's terms.
 *
 * One code, not a list. A reader given six simultaneous reasons does not know
 * which one to act on, and five of the six are usually consequences of the
 * first. {@link deliveryBlocker} returns the earliest unmet gate in the order
 * the reader must clear them.
 */
export type DeliveryBlockerCode =
  | "account_suspended"
  | "account_verification_pending"
  | "account_verification_rejected"
  | "account_not_verified"
  | "account_not_active"
  | "no_creative"
  | "creative_rejected"
  | "creative_in_review"
  | "creative_media_missing"
  | "no_placement"
  | "no_budget"
  | "budget_exhausted";

export type DeliveryBlocker = {
  code: DeliveryBlockerCode;
  /** One sentence naming the gate. */
  title: string;
  /** What the reader can do about it, or who can. Never blank. */
  detail: string;
  /**
   * False when nothing the advertiser does will clear this — a suspension, or a
   * review that is already sitting with someone else.
   *
   * This flag used to be false for every account-level gate, because account
   * activation genuinely had no product path: nothing in the product ever wrote
   * `pulse_ad_accounts.status`, so "wait for verification" was a dead end
   * dressed as a next step and the honest thing was to say so. That is no longer
   * true — `POST /api/pulse/ads/accounts/:id/verification` exists, so an
   * unverified or rejected account is now something the advertiser can act on
   * and the flag says so.
   */
  advertiserCanClear: boolean;
};

const BLOCKERS: Record<DeliveryBlockerCode, Omit<DeliveryBlocker, "code">> = {
  account_suspended: {
    title: "This ad account is suspended",
    detail: "Suspended accounts don't deliver. Contact support to find out why and what's needed to lift it.",
    advertiserCanClear: false
  },
  account_verification_pending: {
    title: "Your account verification is in review",
    detail:
      "Nothing in your campaign is wrong and there's nothing to do. Campaigns can run as soon as the review is decided.",
    advertiserCanClear: false
  },
  account_verification_rejected: {
    title: "Account verification was declined",
    detail:
      "The decision says what to fix. Update your business details, then request verification again — a declined account can be resubmitted.",
    advertiserCanClear: true
  },
  account_not_verified: {
    title: "This ad account isn't verified yet",
    detail:
      "Ads only deliver from a verified account. Request verification from the account's settings; it's a one-time review of the business behind the account.",
    advertiserCanClear: true
  },
  account_not_active: {
    title: "This ad account isn't approved to deliver yet",
    detail:
      "The account is verified but isn't marked active, which shouldn't happen — approval writes both. Contact support so it can be corrected.",
    advertiserCanClear: false
  },
  no_creative: {
    title: "This campaign has no ad in it",
    detail: "Add at least one creative in the campaign editor, then submit it for review.",
    advertiserCanClear: true
  },
  creative_rejected: {
    title: "Every ad in this campaign was rejected",
    detail: "Open the Policy Center to read the decision, then duplicate the ad and fix what it names.",
    advertiserCanClear: true
  },
  creative_in_review: {
    title: "Waiting on review",
    detail: "No ad here has been approved yet. Reviews are decided by our team; the Policy Center shows where each one stands.",
    advertiserCanClear: true
  },
  creative_media_missing: {
    title: "The approved ad has no uploaded media",
    detail:
      "An image, video or audio ad only delivers once its file is uploaded and approved. Re-upload the media in the campaign editor.",
    advertiserCanClear: true
  },
  no_placement: {
    title: "This campaign has nowhere to show",
    detail: "Choose at least one placement in the campaign editor.",
    advertiserCanClear: true
  },
  no_budget: {
    title: "This campaign has no budget",
    detail: "Set a daily or lifetime budget in the campaign editor. A campaign with a zero budget never spends.",
    advertiserCanClear: true
  },
  budget_exhausted: {
    title: "The lifetime budget is spent",
    detail: "This campaign has spent its full lifetime budget. Raise the budget to keep it running.",
    advertiserCanClear: true
  }
};

/**
 * Whether the portal payload may be used to rule things *out*.
 *
 * `portal` is `null` on the fan-out path: creatives, placements, roles and
 * wallets were never requested. `degraded` is the portal path admitting some of
 * it failed. In both cases an empty `portal.creatives` means "not asked", not
 * "none exist".
 *
 * This distinction is the whole reason the function exists. §31 forbids
 * rendering an unmade request as an all-clear; the mirror error — rendering an
 * unmade request as a *fault* — is worse, because it sends the reader into the
 * campaign editor to fix a creative that is already there. Every gate below
 * that reads portal-only data is guarded by this, and the gates that read the
 * campaign row itself are not, because that row did arrive.
 */
export function portalIsAuthoritative(portal: AdsPortal | null | undefined): portal is AdsPortal {
  return portalIsLoaded(portal);
}

/**
 * Whether this specific account was in the payload.
 *
 * `accountRole` answers `"viewer"` for an account it cannot find, which is the
 * right default for gating a write but the wrong basis for telling someone what
 * their role is. Callers that put a role in front of the reader check this
 * first, or use `accountAccess` in api/adsPortal, which folds both checks and
 * the role into one answer.
 */
export function accountIsKnown(portal: AdsPortal | null | undefined, accountId: number): boolean {
  return accountIsListed(portal, accountId);
}

/** The creatives belonging to one campaign. */
export function campaignCreatives(portal: AdsPortal | null | undefined, campaignId: number): AdCreative[] {
  return (portal?.creatives || []).filter((creative) => Number(creative.campaign_id) === Number(campaignId));
}

/**
 * A creative the selector would accept, as far as the payload can tell.
 *
 * Both columns must read `approved` — the selector tests `cr.status` and
 * `cr.moderation_status` separately, and the portal's own action rules treat
 * them as independent, so checking one would let a half-approved creative pass.
 */
export function creativeIsDeliverable(creative: AdCreative): boolean {
  return (
    String(creative.status || "").toLowerCase() === "approved" &&
    String(creative.moderation_status || "").toLowerCase() === "approved"
  );
}

/**
 * Whether an approved creative actually has the media the selector demands.
 *
 * The portal's `media_ready` is not this test. `_creative_public` sets it from
 * `media_asset_id OR media_url OR creative_type == 'text'`
 * (services/pulse_advertiser_portal.py:390), while the selector requires
 * `media_asset_id IS NOT NULL` for image, video and audio. A creative carrying
 * only a `media_url` reports ready and is never selected — a true field with a
 * false implication, which is worse than a missing one.
 */
export function creativeMediaSatisfiesSelector(creative: AdCreative): boolean {
  const type = String(creative.creative_type || "text").toLowerCase();
  if (type !== "image" && type !== "video" && type !== "audio") return true;
  return Number(creative.media_asset_id || 0) > 0;
}

/**
 * The gate this campaign is stuck behind, or `null` when every visible one is
 * clear. Ordered the way the reader has to clear them: the account first,
 * because nothing downstream matters while it is closed.
 */
export function deliveryBlocker(
  portal: AdsPortal | null | undefined,
  campaign: AdCampaign
): DeliveryBlocker | null {
  const accountId = Number(campaign.ad_account_id || 0);
  const account = (portal?.accounts || []).find((row) => Number(row.id) === accountId);
  const accountStatus = String(account?.status || "").toLowerCase();

  if (accountStatus === "suspended") return blocker("account_suspended");
  // An account we cannot find is not asserted to be broken; the selector's
  // requirement is only claimed against a status we actually read.
  if (account && accountStatus !== "active") {
    // Verification, in the same order and with the same distinctions the server
    // makes in `_account_activation_blocker`. Collapsing these into one
    // "not approved yet" is how the reader ends up waiting on a review nobody
    // asked for, or requesting one that is already in progress.
    switch (accountVerificationState(account)) {
      case "pending":
        return blocker("account_verification_pending");
      case "rejected":
        return blocker("account_verification_rejected");
      case "unverified":
        return blocker("account_not_verified");
      default:
        return blocker("account_not_active");
    }
  }

  // Creatives only exist in the portal payload. Without it, "this campaign has
  // no ad in it" would be a statement about a request that was never made.
  if (portalIsAuthoritative(portal)) {
    const creatives = campaignCreatives(portal, campaign.id);
    const live = creatives.filter(creativeIsDeliverable);
    if (live.length === 0) {
      if (creatives.length === 0) return blocker("no_creative");
      const rejected = creatives.filter(
        (creative) => String(creative.moderation_status || "").toLowerCase() === "rejected"
      );
      return blocker(rejected.length === creatives.length ? "creative_rejected" : "creative_in_review");
    }
    if (!live.some(creativeMediaSatisfiesSelector)) return blocker("creative_media_missing");
  }

  // `Array.isArray` rather than `|| []`: an absent field is a campaign row that
  // didn't carry placements, and an empty array is a campaign that has none.
  // Only the second is a finding.
  if (Array.isArray(campaign.placements) && campaign.placements.length === 0) {
    return blocker("no_placement");
  }

  const lifetime = Number(campaign.lifetime_budget_cents || 0);
  const daily = Number(campaign.daily_budget_cents || 0);
  const spent = Number(campaign.spent_cents || 0);
  if (lifetime <= 0 && daily <= 0) return blocker("no_budget");
  if (lifetime > 0 && spent >= lifetime) return blocker("budget_exhausted");

  return null;
}

function blocker(code: DeliveryBlockerCode): DeliveryBlocker {
  return { code, ...BLOCKERS[code] };
}

export type AccountVerificationState = "unverified" | "pending" | "verified" | "rejected";

/**
 * The account's verification state, read the way the server reads it.
 *
 * The wording is not one vocabulary. `verification_status` predates the
 * lifecycle that now writes it, so rows exist carrying `"approved"` from the
 * one-off audit scripts and `"submitted"`/`"in_review"` from nowhere in
 * particular. This mirrors `pulse_ads_service.account_verification_state` so the
 * two surfaces cannot disagree about which of four states an advertiser is in —
 * and an unrecognised value reads as `"unverified"`, which is the safe answer
 * because it points at an action rather than at a wait.
 */
export function accountVerificationState(account: {
  verification_status?: unknown;
}): AccountVerificationState {
  const raw = String(account?.verification_status || "").toLowerCase();
  if (raw === "approved" || raw === "verified") return "verified";
  if (raw === "pending" || raw === "submitted" || raw === "in_review") return "pending";
  if (raw === "rejected" || raw === "declined" || raw === "needs_more_info") return "rejected";
  return "unverified";
}

/**
 * Whether the campaign's own schedule window has opened, and whether it closed.
 *
 * The selector compares against server time; the client compares against the
 * device clock. That is a real difference of a few seconds to a few minutes on
 * a badly set phone, so this is used only to explain a campaign that is not
 * delivering — never to claim one is.
 */
export function campaignWindow(campaign: AdCampaign, now: Date = new Date()): "before" | "open" | "after" {
  const start = String(campaign.start_at || "").trim();
  const end = String(campaign.end_at || "").trim();
  const at = now.toISOString();
  if (start && start > at) return "before";
  if (end && end < at) return "after";
  return "open";
}

/**
 * What is actually true of this campaign.
 *
 * `blocked` is the state the whole module is for: the campaign says active, the
 * server would not serve it, and until now the app said "Active" anyway.
 */
export function deliveryState(
  portal: AdsPortal | null | undefined,
  campaign: AdCampaign,
  now: Date = new Date()
): DeliveryState {
  const status = String(campaign.status || "draft").toLowerCase();
  if (status === "archived") return "archived";
  if (status === "suspended") return "suspended";
  if (status === "paused") return "paused";
  if (status === "pending_review") return "in_review";
  if (status === "completed") return "ended";
  if (status !== "active") return "draft";

  const window = campaignWindow(campaign, now);
  if (window === "after") return "ended";
  if (deliveryBlocker(portal, campaign)) return "blocked";
  if (window === "before") return "scheduled";
  // Spend is a receipt from the server's own ledger, so it proves delivery with
  // or without the portal. "Eligible" is a forecast built from the visible
  // gates, so it may only be claimed when those gates were actually loaded.
  if (Number(campaign.spent_cents || 0) > 0) return "delivering";
  return portalIsAuthoritative(portal) ? "eligible" : "active_unconfirmed";
}

export function deliveryStateLabel(state: DeliveryState): string {
  switch (state) {
    case "delivering":
      return "Delivering";
    case "eligible":
      return "Ready to deliver";
    // Deliberately the plain word. The campaign is active and nothing is known
    // to be wrong; claiming "Ready to deliver" would forecast from gates that
    // were never read.
    case "active_unconfirmed":
      return "Active";
    case "blocked":
      return "Not delivering";
    case "scheduled":
      return "Scheduled";
    case "ended":
      return "Ended";
    case "paused":
      return "Paused";
    case "in_review":
      return "In review";
    case "archived":
      return "Archived";
    case "suspended":
      return "Suspended";
    default:
      return "Draft";
  }
}

export function deliveryStateTone(state: DeliveryState): CampaignTone {
  switch (state) {
    case "delivering":
      return "success";
    case "eligible":
    case "scheduled":
    case "in_review":
      return "info";
    case "blocked":
      return "warning";
    case "suspended":
      return "error";
    // Not `success`: a green pill is a claim about delivery, and this state is
    // the absence of one.
    case "active_unconfirmed":
    default:
      return "neutral";
  }
}

/**
 * The line under the status, explaining it. Never blank — a pill with no
 * explanation is the "generic error with no recovery action" §31 forbids,
 * arriving one word at a time.
 */
export function deliveryStateDetail(
  portal: AdsPortal | null | undefined,
  campaign: AdCampaign,
  now: Date = new Date()
): string {
  const state = deliveryState(portal, campaign, now);
  if (state === "blocked") {
    const reason = deliveryBlocker(portal, campaign);
    return reason ? reason.title : "This campaign isn't reaching anyone.";
  }
  switch (state) {
    case "delivering":
      return "This campaign has spent, so it is reaching people.";
    case "eligible":
      return "Everything checks out. Nothing has been spent yet, so delivery isn't confirmed.";
    case "active_unconfirmed":
      return "Marked active with nothing spent yet. Open the campaign to check its ads and budget.";
    case "scheduled":
      return "Everything checks out. It starts on its scheduled date.";
    case "ended":
      return "Its end date has passed. It no longer delivers.";
    case "paused":
      return "You paused this. Resuming reserves budget from the wallet.";
    case "in_review":
      return "Submitted and waiting on a decision.";
    case "archived":
      return "Archived. It no longer delivers and can't be resumed.";
    case "suspended":
      return "Suspended by our team. Contact support for the reason.";
    default:
      return "Not submitted yet.";
  }
}

/* ------------------------------------------------------------------ *
 * Spend
 * ------------------------------------------------------------------ *
 *
 * There is deliberately no spend formatter here. `spent_cents` is incremented
 * only by `record_spend_event` (services/pulse_ad_payments.py:432), one cent per
 * counted impression, and `adsDashboard.campaignSpendCents` already reads it —
 * preferring the analytics row, falling back to the campaign counter, never
 * summing the two. Adding a second renderer would be the thing this module
 * exists to prevent: two places in the app with an opinion about one number.
 */

/* ------------------------------------------------------------------ *
 * Wallet authority
 * ------------------------------------------------------------------ */

export type WalletAuthorityState = "confirmed" | "restricted" | "unavailable";

export type WalletAuthority = {
  state: WalletAuthorityState;
  /** The balance to show, already in §31's vocabulary. */
  display: string;
  /** The reserved figure, same rules. */
  reservedDisplay: string;
  /** Why the reader is seeing that word, or `null` when it's a real figure. */
  note: string | null;
  /**
   * Cents, only when confirmed. `null` everywhere else, so a caller cannot
   * accidentally do arithmetic on a number the server never stood behind.
   */
  spendableCents: number | null;
  /**
   * What this wallet owes, in cents, and `null` when that is not knowable.
   *
   * A reversed top-up debits the wallet after the money is gone, so the
   * balance goes negative and `spendableCents` floors at 0. Zero-and-solvent
   * and zero-and-in-debt then render identically, and only one of them
   * explains why the campaigns underneath just stopped. Always 0 or positive
   * when confirmed — never a negative dressed up as a balance.
   */
  owedCents: number | null;
  /** Formatted debt for display, `null` when there is nothing owed to show. */
  owedDisplay: string | null;
};

/**
 * Whether the wallet figures for this account can be believed.
 *
 * The rule is `role === "owner"`, and it is exact rather than heuristic:
 * `wallet_summary` calls `_owner_account` first, so a non-owner's request
 * raises and `portal_summary` substitutes a hand-written row of zeroes. There
 * is nothing in the substituted row to detect — it is well-formed, it carries
 * `"$0.00"` strings, and it sums into `metrics` alongside real ones. The role
 * is the only signal that distinguishes them, and it is a reliable one.
 *
 * A team member who reads `"$0.00"` and concludes the account is out of money
 * has been misled by the product about money. §31 names this exact failure —
 * "fake zero after service failure" — and §37 names the ban on a
 * client-authoritative wallet balance. Both are answered by refusing to repeat
 * the figure rather than by computing a better one.
 */
export function walletAuthority(
  portal: AdsPortal | null | undefined,
  accountId: number
): WalletAuthority {
  if (!portal || portal.degraded) {
    return {
      state: "unavailable",
      display: "Unavailable",
      reservedDisplay: "Unavailable",
      note: "Wallet figures didn't load. This doesn't mean the balance is zero.",
      spendableCents: null,
      owedCents: null,
      owedDisplay: null
    };
  }

  const role = accountRole(portal, accountId);
  if (role !== "owner") {
    return {
      state: "restricted",
      display: "Restricted",
      reservedDisplay: "Restricted",
      note: "Only an account owner can see this wallet. Your role can still run campaigns against it.",
      spendableCents: null,
      owedCents: null,
      owedDisplay: null
    };
  }

  const wallet = (portal.wallets || []).find((row) => Number(row.account_id) === Number(accountId));
  if (!wallet) {
    return {
      state: "unavailable",
      display: "Unavailable",
      reservedDisplay: "Unavailable",
      note: "This account's wallet wasn't in the response. Pull to refresh.",
      spendableCents: null,
      owedCents: null,
      owedDisplay: null
    };
  }

  // The server says outright when it could not read the wallet. Its figures
  // are absent rather than zero on that row, and treating them as a balance
  // would put a fabricated "$0.00" in front of an advertiser whose real
  // balance is simply unknown.
  if (wallet.unavailable) {
    return {
      state: "unavailable",
      display: "Unavailable",
      reservedDisplay: "Unavailable",
      note:
        String(wallet.unavailable_reason || "").trim() ||
        "Wallet balance couldn't be loaded. This doesn't mean the balance is zero.",
      spendableCents: null,
      owedCents: null,
      owedDisplay: null
    };
  }

  const owedCents = Math.max(0, Number(wallet.amount_owed_cents || 0));
  return {
    state: "confirmed",
    display: walletFigure(wallet.available_balance, wallet.available_balance_cents),
    reservedDisplay: walletFigure(
      (wallet as AdWallet & { reserved_budget?: string }).reserved_budget,
      wallet.reserved_budget_cents
    ),
    note: null,
    spendableCents: Number(wallet.spendable_balance_cents || 0),
    owedCents,
    owedDisplay: owedCents > 0 ? owedFigure(wallet.amount_owed, owedCents) : null
  };
}

function walletFigure(formatted: string | undefined, cents: number | undefined): string {
  const text = String(formatted || "").trim();
  if (text) return text;
  // `undefined` cents is a figure the server did not send, not a zero one.
  // Only an explicit 0 earns "$0.00".
  if (cents === undefined || cents === null) return "Unavailable";
  return Number(cents) === 0 ? "$0.00" : "Unavailable";
}

/**
 * The debt, formatted. Falls back to formatting the cents itself because a
 * debt that renders as an empty string is a debt the reader never learns about
 * — the one failure mode worse than an ugly number.
 */
function owedFigure(formatted: string | undefined, cents: number): string {
  const text = String(formatted || "").trim();
  if (text) return text;
  return `$${(cents / 100).toFixed(2)}`;
}

/**
 * The portal's wallet rollup, believed only when every account behind it can be.
 *
 * `metrics.wallet_balance_cents` sums one row per account, and any non-owned
 * account contributes a fabricated zero. A total that silently omits an
 * account's real balance is not a smaller number — it is a wrong one, and it is
 * the headline figure on the Advertising home screen.
 */
export function walletRollupAuthority(portal: AdsPortal | null | undefined): WalletAuthority {
  if (!portal || portal.degraded) {
    return {
      state: "unavailable",
      display: "Unavailable",
      reservedDisplay: "Unavailable",
      note: "Wallet figures didn't load. This doesn't mean the balance is zero.",
      spendableCents: null,
      owedCents: null,
      owedDisplay: null
    };
  }
  const accounts = portal.accounts || [];
  if (accounts.length === 0) {
    return {
      state: "unavailable",
      display: "Unavailable",
      reservedDisplay: "Unavailable",
      note: "No ad accounts to total.",
      spendableCents: null,
      owedCents: null,
      owedDisplay: null
    };
  }
  const unowned = accounts.filter((account) => accountRole(portal, Number(account.id)) !== "owner");
  if (unowned.length > 0) {
    return {
      state: "restricted",
      display: "Restricted",
      reservedDisplay: "Restricted",
      note:
        unowned.length === accounts.length
          ? "Only an account owner can see wallet balances."
          : `A combined total would leave out ${unowned.length === 1 ? "1 account" : `${unowned.length} accounts`} you don't own. Open an account to see its wallet.`,
      spendableCents: null,
      owedCents: null,
      owedDisplay: null
    };
  }
  const metrics = portal.metrics;
  // The server sums these over the wallets it could actually read. If any
  // failed, the total is short by an unknown amount, and a short total shown
  // as a whole one is the same lie as a fake zero — just harder to spot.
  const missing = Number(metrics.wallets_unavailable || 0);
  if (missing > 0) {
    return {
      state: "unavailable",
      display: "Unavailable",
      reservedDisplay: "Unavailable",
      note: `${missing === 1 ? "1 wallet" : `${missing} wallets`} couldn't be loaded, so a combined total would be short by an unknown amount.`,
      spendableCents: null,
      owedCents: null,
      owedDisplay: null
    };
  }
  const owedCents = Math.max(0, Number(metrics.amount_owed_cents || 0));
  return {
    state: "confirmed",
    display: walletFigure(metrics.wallet_balance, metrics.wallet_balance_cents),
    reservedDisplay: walletFigure(metrics.reserved_budget, metrics.reserved_budget_cents),
    note: null,
    spendableCents: Number(metrics.spendable_balance_cents || 0),
    owedCents,
    owedDisplay: owedCents > 0 ? owedFigure(metrics.amount_owed, owedCents) : null
  };
}

/* ------------------------------------------------------------------ *
 * The activation gate
 * ------------------------------------------------------------------ */

export type ResumeCheck = {
  /** Whether to offer Resume at all. */
  allowed: boolean;
  /** Why not, in the reader's terms. `null` when allowed. */
  reason: string | null;
};

/**
 * Whether Resume can succeed, checked before it is offered.
 *
 * §37 forbids campaign activation without verification, policy, eligibility and
 * funding. `campaign_action("resume")` used to check exactly one of those four
 * and get the authorisation wrong on the way: it called
 * `reserve_campaign_budget`, which begins
 * `if not campaign or owner_user_id != user_id: raise "Campaign not found." 404`
 * — so a campaign manager, a write role the same route had authorised four lines
 * earlier, was told the campaign did not exist. It does, they are looking at it,
 * and they were allowed to pause it a moment ago.
 *
 * All four gates are now enforced server-side, in this order, before any budget
 * is reserved, and the role refusal names the wallet instead of denying the
 * campaign. This function's job is therefore no longer to compensate for a
 * missing server check but to agree with one: it declines to offer a button
 * whose only outcome would be a refusal the reader was given no warning about.
 * Same rule, `NO_DEAD_ENDS`, applied to a control rather than to a page — but
 * now both halves are telling the same story.
 */
export function resumeCheck(
  portal: AdsPortal | null | undefined,
  campaign: AdCampaign
): ResumeCheck {
  const accountId = Number(campaign.ad_account_id || 0);
  // Only when the role was actually read. `accountRole` answers `"viewer"` for
  // an account it cannot find, so on the fan-out path — where no roles are
  // fetched at all — an unguarded check would disable every Resume button in
  // the app and tell each reader they don't own an account they do. A wrong
  // reason attached to a dead control is worse than the control failing loudly.
  if (accountIsKnown(portal, accountId) && accountRole(portal, accountId) !== "owner") {
    return {
      allowed: false,
      reason: "Only the account owner can resume a campaign, because resuming reserves budget from the wallet."
    };
  }
  const budget = Number(campaign.lifetime_budget_cents || 0) || Number(campaign.daily_budget_cents || 0);
  if (budget <= 0) {
    return { allowed: false, reason: "Set a budget before resuming. The server rejects a resume on a zero-budget campaign." };
  }
  const wallet = walletAuthority(portal, accountId);
  // Only a *confirmed* balance may block. Refusing on an unconfirmed figure
  // would be the fabricated zero doing damage through a different door.
  if (wallet.state === "confirmed" && wallet.spendableCents !== null) {
    const required = Math.min(budget, 50_000);
    if (wallet.spendableCents < required) {
      return {
        allowed: false,
        reason: "Your wallet balance is too low to reserve this campaign's budget. Add funds first."
      };
    }
  }
  const gate = deliveryBlocker(portal, campaign);
  if (gate && SERVER_ENFORCED_BLOCKERS.has(gate.code)) {
    return { allowed: false, reason: gate.detail };
  }
  return { allowed: true, reason: null };
}

/**
 * The blockers `activation_blocker` refuses a resume for, server-side.
 *
 * This used to be `!gate.advertiserCanClear`, on the reasoning that a gate the
 * advertiser could clear themselves was one the server would let them resume
 * through and simply not deliver on. That reasoning was correct about the
 * server as it stood and is wrong about the server as it stands: resume now
 * enforces all four of §37's gates before any money is reserved, so offering a
 * button for `no_creative` would produce a refusal the reader did not expect
 * from a control the app said was available.
 *
 * `advertiserCanClear` still means what it meant — whether there is anything for
 * the reader to do — and that is a different question from whether the server
 * will accept the request. Conflating the two is what put the button there.
 *
 * `budget_exhausted` is absent deliberately: the server's activation gate checks
 * that a budget exists, not that it has room left. Spend against the lifetime
 * cap is evaluated per impression by `_campaign_budget_available`, so a resume
 * on an exhausted campaign is accepted and simply doesn't deliver — which the
 * campaign row then says.
 */
const SERVER_ENFORCED_BLOCKERS: ReadonlySet<DeliveryBlockerCode> = new Set([
  "account_suspended",
  "account_verification_pending",
  "account_verification_rejected",
  "account_not_verified",
  "account_not_active",
  "no_creative",
  "creative_in_review",
  "creative_rejected",
  "creative_media_missing",
  "no_placement",
  "no_budget"
]);

/* ------------------------------------------------------------------ *
 * Attribution
 * ------------------------------------------------------------------ *
 *
 * There is none, and this is where the app says so.
 *
 * `advertiser_analytics` now returns a `conversions` count, and that count is
 * an accurate count of `conversion` rows in `pulse_ad_events`. It is not a
 * count of conversions. The only code in the product that ever wrote that
 * event type was `SponsoredAdCard.flushViewability`, which fired it the moment
 * an ad had been on screen for one second — beside the call that already
 * records the same fact as `impressions.viewable = 1`. That write is now
 * removed, which leaves the platform with no post-tap instrumentation at all:
 * no pixel, no SDK callback, no order link, no value.
 *
 * So the number is zero going forward and meaningless going backward, and the
 * mission's rule for this situation is explicit — say what is missing rather
 * than render an empty report (§37: no fake report; §31: `"Not configured"`
 * for setup that does not exist). A "Conversions: 0" cell would be the fake
 * report. A sentence is the honest version, because the reader's real question
 * is not "what is the number" but "why is there no number", and that question
 * has an answer.
 *
 * It is deliberately not shown on every campaign. An awareness campaign's
 * objective *is* impressions; telling its owner that conversions aren't
 * tracked is noise about something they never asked. It appears only where the
 * objective implies an outcome after the tap, which is the exact case §37 has
 * in mind when it forbids an advertiser being shown clicks and spend with no
 * evidence the objective was met.
 */

/**
 * Objectives whose success happens somewhere the platform cannot see.
 *
 * Drawn from `VALID_OBJECTIVES` (services/pulse_ads_service.py:53–70). The
 * omitted ones — awareness, brand_awareness, traffic, website_traffic,
 * engagement — are fully measured by impressions and clicks, which the card
 * already shows truthfully.
 */
const OUTCOME_OBJECTIVES = new Set([
  "marketplace",
  "marketplace_sales",
  "app_promotion",
  "event_promotion",
  "music_promotion",
  "video_promotion",
  "creator_growth",
  "creator_promotion"
]);

/**
 * One line naming what this campaign's reporting cannot tell the advertiser,
 * or `null` when nothing is missing.
 *
 * Returns `null` for objectives the platform genuinely measures, so the line
 * never appears as boilerplate. Callers render it under the metric strip; it
 * carries no number and must never be presented next to one, because pairing
 * it with a figure would reintroduce the claim it exists to withdraw.
 */
export function attributionNote(campaign: Pick<AdCampaign, "objective">): string | null {
  const objective = String(campaign.objective || "").toLowerCase();
  if (!OUTCOME_OBJECTIVES.has(objective)) return null;
  return "Conversions aren’t tracked. Impressions and clicks above are measured; what happens after the tap isn’t.";
}
