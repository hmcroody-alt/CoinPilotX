/**
 * The advertiser portal, in one request.
 *
 * WHY THIS MODULE EXISTS
 * ----------------------
 * `GET /api/pulse/ads/portal` returns everything the Advertising screens need
 * and five things they currently cannot see at all. The app instead makes five
 * calls — accounts, campaigns, analytics, wallet, billing-summary — which
 * together fetch a strict subset of the same payload.
 *
 * The five things only the portal carries:
 *
 * * `review_board` — the policy decision per creative, with the reason, the
 *   risk score, and the separation between the automated and the human verdict.
 *   §37 names an inaccessible policy reason as a completion blocker; it has
 *   been one unmade HTTP call away this whole time.
 * * `roles` and per-account `role` — server-enforced permissions. The write
 *   endpoints answer 403 for `analyst` and `viewer`, so without this the client
 *   offers buttons the server refuses.
 * * `creatives` — the Creative Library, which the app has never listed.
 * * `notifications` — typed, with read state.
 * * `placements` — real placement metadata instead of a hardcoded list.
 *
 * BACKEND BINDING
 * ---------------
 * `/api/pulse/ads/portal` → `pulse_advertiser_portal.portal_summary`. It is
 * authenticated and CSRF-checked like every sibling route and is **not** behind
 * an environment flag.
 *
 * That last point is the reason this module targets the legacy surface rather
 * than the newer `/api/business-os/advertising/*` one, which is richer, has the
 * ad-set tier this surface lacks, and returns 404 on all 46 of its routes
 * unless the server variable `BUSINESS_OS_ADVERTISING` is set — blank in
 * `.env.example`, with no evidence in the repo that it was ever turned on. See
 * `docs/business_os/ADVERTISING_BACKEND_INVENTORY.md`. Building here is correct
 * whether or not that vertical is ever lit.
 *
 * WHAT THIS MODULE DOES NOT DO
 * ----------------------------
 * It does not replace `businessOs.ts`. The per-endpoint calls stay, because
 * they are the fallback: {@link loadAdsPortal} degrades to the five-call
 * fan-out when the portal request fails, so a portal outage costs the five new
 * sections and nothing that works today.
 */

import { readJsonCache, writeJsonCache } from "../core/cache";
import {
  AdAccount,
  AdAnalytics,
  AdBilling,
  AdCampaign,
  AdWallet,
  getAdAnalytics,
  getAdBillingSummary,
  getAdWallet,
  listAdAccounts,
  listAdCampaigns,
  normalizeAdAccounts,
  normalizeAdAnalytics,
  normalizeAdCampaigns,
  normalizeAdWallet
} from "./businessOs";
import { pulseApi } from "./pulseApi";

const ADS_PORTAL_CACHE_KEY = "pulsesoc.native.businessos.adsportal";

/* ------------------------------------------------------------------ *
 * Roles
 * ------------------------------------------------------------------ */

/** Mirrors `ACCOUNT_ROLES` in `services/pulse_advertiser_portal.py:17`. */
export const AD_ACCOUNT_ROLES = [
  "owner",
  "campaign_manager",
  "marketing_manager",
  "analyst",
  "viewer"
] as const;
export type AdAccountRole = (typeof AD_ACCOUNT_ROLES)[number];

/**
 * Mirrors `WRITE_ROLES`. `analyst` and `viewer` are absent by design: for them
 * `_require_account_role` raises 403 on every campaign and creative write.
 */
export const AD_WRITE_ROLES: readonly string[] = ["owner", "campaign_manager", "marketing_manager"];

/** Mirrors `ANALYTICS_ROLES` — everyone except `viewer`. */
export const AD_ANALYTICS_ROLES: readonly string[] = [
  "owner",
  "campaign_manager",
  "marketing_manager",
  "analyst"
];

/* ------------------------------------------------------------------ *
 * Types
 * ------------------------------------------------------------------ */

/** An account as the portal returns it: the base row plus the joined rollups. */
export type AdPortalAccount = AdAccount & {
  role?: AdAccountRole | string;
  health_score?: number;
  campaign_count?: number;
  active_campaigns?: number;
  pending_reviews?: number;
  total_spend_cents?: number;
  /** Server-formatted, e.g. "$1,234.00". */
  total_spend?: string;
  industry?: string;
  website?: string;
  contact_email?: string;
};

export type AdCreative = {
  id: number;
  ad_account_id?: number;
  campaign_id?: number;
  campaign_name?: string;
  business_name?: string;
  title?: string;
  body?: string;
  call_to_action?: string;
  creative_type?: string;
  destination_url?: string;
  media_url?: string;
  media_asset_id?: number;
  /**
   * The lifecycle column: `draft` | `pending_review` | `approved` | `archived`.
   *
   * Separate from `moderation_status`, and both matter. `creative_action`'s
   * `delete_draft` branch requires *both* to read `draft`
   * (`pulse_advertiser_portal.py:683`) and answers 409 otherwise, so a client
   * that offered Delete on `moderation_status` alone would show a button that
   * fails on a creative already submitted for review.
   */
  status?: string;
  /** `pending` | `approved` | `rejected` | `draft`. */
  moderation_status?: string;
  rejection_reason?: string;
  created_at?: string;
  updated_at?: string;
  /* Derived server-side by `_creative_public`. Recomputing these on the client
   * would be a second opinion about the same facts, which is how two surfaces
   * come to disagree. They are read, not calculated. */
  performance_state?: string;
  media_ready?: boolean;
  destination_safe?: boolean;
};

/**
 * One row of the review board.
 *
 * `automated_review_status` and `human_review_status` are separate fields and
 * must stay separate in the UI. "A machine flagged this" and "a person upheld
 * it" are different facts, and only one of them is worth contesting.
 */
export type AdReviewEntry = {
  review_id: number;
  review_status?: string;
  risk_score?: number;
  automated_review_status?: string;
  human_review_status?: string;
  review_reason?: string;
  reviewed_at?: string;
  created_at?: string;
  updated_at?: string;
  creative_id?: number;
  title?: string;
  moderation_status?: string;
  rejection_reason?: string;
  campaign_id?: number;
  campaign_name?: string;
};

export type AdNotification = {
  id: number;
  account_id?: number;
  campaign_id?: number;
  creative_id?: number;
  notification_type?: string;
  title?: string;
  body?: string;
  /** `unread` | `read`. */
  status?: string;
  created_at?: string;
  read_at?: string;
};

export type AdPlacementMeta = {
  placement_key?: string;
  display_name?: string;
  device_type?: string;
  placement_type?: string;
  max_frequency?: number;
  priority?: number;
  card_style?: string;
  supported_creative_types?: string[];
};

export type AdPortalMetrics = {
  account_count: number;
  campaign_count: number;
  creative_count: number;
  pending_reviews: number;
  active_campaigns: number;
  draft_campaigns: number;
  unread_notifications: number;
  total_spend_cents: number;
  total_spend?: string;
  wallet_balance_cents: number;
  wallet_balance?: string;
  reserved_budget_cents: number;
  reserved_budget?: string;
  spendable_balance_cents: number;
  spendable_balance?: string;
  /**
   * Summed shortfall across the accounts that owe money after a reversed
   * top-up. When this is non-zero, `spendable_balance_cents` reading 0 is not
   * "never funded" — it is "funded, spent, then charged back".
   */
  amount_owed_cents: number;
  amount_owed?: string;
  /**
   * Number of wallets `portal_summary` could not read. The money totals here
   * are summed over the readable wallets only, so any value above zero means
   * every figure in this block is a partial one.
   */
  wallets_unavailable: number;
};

export type AdPortalRoles = {
  /**
   * A rollup, and a lossy one — see {@link accountRole}. Useful for a coarse
   * "can this person do anything at all" check and for nothing finer.
   */
  current?: string;
  /**
   * The five role names the server recognises. Not a menu — four of them cannot
   * be held by anyone, because nothing in the product writes the membership
   * table they come from. Nothing renders this, and a role picker built from it
   * would offer grants no route can make. See the Permissions section below.
   */
  allowed?: string[];
};

export type AdsPortal = {
  accounts: AdPortalAccount[];
  campaigns: AdCampaign[];
  creatives: AdCreative[];
  wallets: AdWallet[];
  analytics: AdAnalytics;
  review_board: AdReviewEntry[];
  notifications: AdNotification[];
  billing: AdBilling;
  metrics: AdPortalMetrics;
  campaign_status_counts: Record<string, number>;
  placements: Record<string, AdPlacementMeta>;
  roles: AdPortalRoles;
  /**
   * True when this came from the five-call fallback rather than the portal.
   * The five new sections are empty in that case, and an empty array from a
   * request that was never made is not the same claim as an empty array from
   * one that was — §31 calls the first `unavailable` and the second `zero`.
   */
  degraded: boolean;
};

export type AdsPortalResponse = { ok?: boolean; portal?: AdsPortal };

/* ------------------------------------------------------------------ *
 * Fetch
 * ------------------------------------------------------------------ */

/** The portal in one request. Throws; callers that need a floor use {@link loadAdsPortal}. */
export async function getAdsPortal() {
  const data = await pulseApi<AdsPortalResponse>("/api/pulse/ads/portal");
  const portal = normalizeAdsPortal(data.portal);
  await cacheAdsPortal(portal).catch(() => undefined);
  return { ...data, portal };
}

/**
 * The portal, with the old five calls as a floor.
 *
 * A portal failure must not cost the app the things it could already do. When
 * the single request fails this falls back to the fan-out the screens used
 * before, assembles the same shape from it, and marks the result `degraded` so
 * the UI can say "policy decisions couldn't load" rather than render an empty
 * review board as though the answer were "none".
 *
 * The fan-out is deliberately tolerant: each of its five calls is allowed to
 * fail on its own. Losing the wallet should not also cost the campaign list.
 */
export async function loadAdsPortal(): Promise<AdsPortal> {
  try {
    const { portal } = await getAdsPortal();
    return portal;
  } catch {
    return fanOutAdsPortal();
  }
}

async function fanOutAdsPortal(): Promise<AdsPortal> {
  const [accountsResult, campaignsResult, analyticsResult] = await Promise.allSettled([
    listAdAccounts(),
    listAdCampaigns(),
    getAdAnalytics()
  ]);

  const accounts =
    accountsResult.status === "fulfilled" ? normalizeAdAccounts(accountsResult.value.accounts || []) : [];
  const campaigns =
    campaignsResult.status === "fulfilled" ? normalizeAdCampaigns(campaignsResult.value.campaigns || []) : [];
  const analytics =
    analyticsResult.status === "fulfilled" ? normalizeAdAnalytics(analyticsResult.value.analytics) : normalizeAdAnalytics(null);

  // Wallet and billing are per-account and the old screens only ever asked
  // about the first one, so the fallback does the same rather than inventing a
  // fan-out the previous behaviour never had.
  const primary = accounts[0]?.id;
  let wallets: AdWallet[] = [];
  let billing: AdBilling = {};
  if (primary) {
    const [walletResult, billingResult] = await Promise.allSettled([
      getAdWallet(primary),
      getAdBillingSummary(primary)
    ]);
    if (walletResult.status === "fulfilled") wallets = [walletResult.value.wallet];
    if (billingResult.status === "fulfilled") billing = billingResult.value.billing;
  }

  return normalizeAdsPortal({
    accounts,
    campaigns,
    analytics,
    wallets,
    billing,
    degraded: true
  } as Partial<AdsPortal> as AdsPortal);
}

export async function loadCachedAdsPortal() {
  return (await readJsonCache<AdsPortal>(ADS_PORTAL_CACHE_KEY, normalizeAdsPortal)) || null;
}

export async function cacheAdsPortal(portal: AdsPortal) {
  await writeJsonCache(ADS_PORTAL_CACHE_KEY, normalizeAdsPortal(portal));
}

/* ------------------------------------------------------------------ *
 * Normalisation
 * ------------------------------------------------------------------ */

export function normalizeAdsPortal(portal?: Partial<AdsPortal> | null): AdsPortal {
  const source = portal || {};
  return {
    accounts: normalizeAdPortalAccounts(source.accounts),
    campaigns: normalizeAdCampaigns(Array.isArray(source.campaigns) ? source.campaigns : []),
    creatives: normalizeAdCreatives(source.creatives),
    wallets: (Array.isArray(source.wallets) ? source.wallets : []).map(normalizeAdWallet),
    analytics: normalizeAdAnalytics(source.analytics),
    review_board: normalizeAdReviewBoard(source.review_board),
    notifications: normalizeAdNotifications(source.notifications),
    billing: normalizeAdPortalBilling(source.billing),
    metrics: normalizeAdPortalMetrics(source.metrics),
    campaign_status_counts: normalizeStatusCounts(source.campaign_status_counts),
    placements: normalizePlacements(source.placements),
    roles: {
      current: String(source.roles?.current || "none"),
      allowed: Array.isArray(source.roles?.allowed) ? source.roles!.allowed!.map(String) : [...AD_ACCOUNT_ROLES]
    },
    degraded: Boolean(source.degraded)
  };
}

function normalizeAdPortalAccounts(accounts?: AdPortalAccount[]): AdPortalAccount[] {
  const base = normalizeAdAccounts(Array.isArray(accounts) ? accounts : []);
  return base.map((account, index) => {
    const raw = (accounts || [])[index] || {};
    return {
      ...account,
      /*
       * Empty, not `"viewer"`, when the row arrived without one.
       *
       * This used to read `String(raw.role || "viewer")`, which meant the client
       * manufactured a permission the server never stated and then every reader
       * downstream treated it as something the server had said. A normaliser
       * inventing a fact is the §31 violation in its purest form — it is not a
       * safe default, it is a fabricated one, and it is indistinguishable from
       * a real answer by the time anything else sees it.
       *
       * Failing closed still happens, just where it belongs: `accountRole`
       * answers `"viewer"` for a role it cannot read, so every gate behaves
       * exactly as before. What changes is that `accountAccess` can now tell
       * "the server said viewer" from "no role came back", and the copy the
       * reader gets differs accordingly.
       */
      role: String(raw.role || ""),
      health_score: Number(raw.health_score || 0),
      campaign_count: Number(raw.campaign_count || 0),
      active_campaigns: Number(raw.active_campaigns || 0),
      pending_reviews: Number(raw.pending_reviews || 0),
      total_spend_cents: Number(raw.total_spend_cents || 0),
      industry: String(raw.industry || ""),
      website: String(raw.website || ""),
      contact_email: String(raw.contact_email || "")
    };
  });
}

export function normalizeAdCreatives(creatives?: AdCreative[]): AdCreative[] {
  return (Array.isArray(creatives) ? creatives : [])
    .map((creative) => ({
      ...creative,
      id: Number(creative.id || 0),
      ad_account_id: Number(creative.ad_account_id || 0),
      campaign_id: Number(creative.campaign_id || 0),
      campaign_name: String(creative.campaign_name || ""),
      title: String(creative.title || "Untitled creative"),
      creative_type: String(creative.creative_type || "text"),
      status: String(creative.status || "draft"),
      moderation_status: String(creative.moderation_status || "draft"),
      rejection_reason: String(creative.rejection_reason || ""),
      performance_state: String(creative.performance_state || ""),
      media_ready: Boolean(creative.media_ready),
      destination_safe: Boolean(creative.destination_safe)
    }))
    .filter((creative) => creative.id > 0);
}

export function normalizeAdReviewBoard(rows?: AdReviewEntry[]): AdReviewEntry[] {
  return (Array.isArray(rows) ? rows : [])
    .map((row) => ({
      ...row,
      review_id: Number(row.review_id || 0),
      review_status: String(row.review_status || "pending"),
      risk_score: Number(row.risk_score || 0),
      automated_review_status: String(row.automated_review_status || ""),
      human_review_status: String(row.human_review_status || ""),
      review_reason: String(row.review_reason || ""),
      creative_id: Number(row.creative_id || 0),
      title: String(row.title || "Untitled creative"),
      moderation_status: String(row.moderation_status || "pending"),
      rejection_reason: String(row.rejection_reason || ""),
      campaign_id: Number(row.campaign_id || 0),
      campaign_name: String(row.campaign_name || "")
    }))
    .filter((row) => row.review_id > 0);
}

export function normalizeAdNotifications(rows?: AdNotification[]): AdNotification[] {
  return (Array.isArray(rows) ? rows : [])
    .map((row) => ({
      ...row,
      id: Number(row.id || 0),
      account_id: Number(row.account_id || 0),
      campaign_id: Number(row.campaign_id || 0),
      creative_id: Number(row.creative_id || 0),
      notification_type: String(row.notification_type || ""),
      title: String(row.title || ""),
      body: String(row.body || ""),
      status: String(row.status || "unread")
    }))
    .filter((row) => row.id > 0);
}

/**
 * The portal and `/billing-summary` disagree about one key name.
 *
 * `portal_summary` emits `billing: { enabled, mode, live_charging, … }`;
 * `billing_summary` emits `billing_enabled`. `adFundingIsLive` reads
 * `billing_enabled`, so handing it the portal's block unmapped makes it answer
 * `false` — which is the right answer today for the wrong reason, and would
 * silently stay `false` on the day someone sets `PULSE_ADS_BILLING_ENABLED`.
 *
 * A funding control that never appears is a smaller bug than one that appears
 * when it cannot charge, which is why this direction of the mapping is the safe
 * one and why it is still worth fixing rather than relying on.
 */
export function normalizeAdPortalBilling(billing?: (AdBilling & { enabled?: boolean }) | null): AdBilling {
  const source = billing || {};
  return {
    ...source,
    billing_enabled: Boolean(source.billing_enabled ?? source.enabled),
    live_charging: Boolean(source.live_charging),
    stripe_customer_visible: Boolean(source.stripe_customer_visible),
    wallet_balance_cents: Number(source.wallet_balance_cents || 0),
    spend_limit_cents: Number(source.spend_limit_cents || 0)
  };
}

function normalizeAdPortalMetrics(metrics?: Partial<AdPortalMetrics>): AdPortalMetrics {
  const source = metrics || {};
  return {
    ...source,
    account_count: Number(source.account_count || 0),
    campaign_count: Number(source.campaign_count || 0),
    creative_count: Number(source.creative_count || 0),
    pending_reviews: Number(source.pending_reviews || 0),
    active_campaigns: Number(source.active_campaigns || 0),
    draft_campaigns: Number(source.draft_campaigns || 0),
    unread_notifications: Number(source.unread_notifications || 0),
    total_spend_cents: Number(source.total_spend_cents || 0),
    wallet_balance_cents: Number(source.wallet_balance_cents || 0),
    reserved_budget_cents: Number(source.reserved_budget_cents || 0),
    spendable_balance_cents: Number(source.spendable_balance_cents || 0),
    amount_owed_cents: Number(source.amount_owed_cents || 0),
    // How many wallets failed to load. The money totals above are summed only
    // over the ones that did, so any value here means they are partial.
    wallets_unavailable: Number(source.wallets_unavailable || 0)
  };
}

function normalizeStatusCounts(counts?: Record<string, number>): Record<string, number> {
  const source = counts && typeof counts === "object" ? counts : {};
  const out: Record<string, number> = {};
  for (const key of Object.keys(source)) out[String(key)] = Number(source[key] || 0);
  return out;
}

function normalizePlacements(placements?: Record<string, AdPlacementMeta>): Record<string, AdPlacementMeta> {
  const source = placements && typeof placements === "object" ? placements : {};
  const out: Record<string, AdPlacementMeta> = {};
  for (const key of Object.keys(source)) {
    const item = source[key] || {};
    out[String(key)] = {
      ...item,
      placement_key: String(item.placement_key || key),
      display_name: String(item.display_name || key),
      supported_creative_types: Array.isArray(item.supported_creative_types)
        ? item.supported_creative_types.map(String)
        : []
    };
  }
  return out;
}

/* ------------------------------------------------------------------ *
 * Permissions
 * ------------------------------------------------------------------ *
 *
 * A note on the role vocabulary, because the code below deliberately does not
 * use most of it.
 *
 * The server declares five roles (`ACCOUNT_ROLES` in
 * services/pulse_advertiser_portal.py:17) and `portal.roles.allowed` ships all
 * five to the client. Four of them cannot be held by anyone. A non-owner role
 * comes from `pulse_ad_team_members`, and that table has no writer: `bot.py`
 * creates it (:103400) and indexes it (:103637), `_role_for_account` (:72) and
 * `_account_ids_for_user` (:262) read it, and there is no INSERT anywhere in
 * the product — no invite route, no accept route, nothing that fills
 * `invited_email`. So `_role_for_account` resolves to `"owner"` or raises
 * `PulseAdsError("Ad account not found.", 404)`, and every advertiser reaching
 * this code is the owner of every account they can see.
 *
 * That is why `adWriteBlockedReason` no longer says "an account owner can
 * change your role": no route implements that, so it was an instruction to
 * pursue a remedy that does not exist — a dead end under §37. The role branches
 * are kept because the server enforces them and a membership writer may land
 * later, but the copy is now true whether or not it does.
 */

/**
 * The role for one account, defaulting closed.
 *
 * Prefer this over `portal.roles.current` for any decision about a specific
 * account. The rollup is computed as "owner if you own *any* account, otherwise
 * the first account's role" (`portal_summary`), so someone who owns account A
 * and merely views account B reads as `owner` for both. The server does not
 * make that mistake — `_require_account_role` re-derives the role per account
 * and answers 403 — so trusting the rollup produces a button that looks live
 * and fails.
 *
 * An account this cannot find answers `"viewer"`. That is the right default for
 * *gating* a write — the least privilege, so an unloaded portal never unlocks a
 * button — and the wrong thing to put in front of a reader, because "unknown"
 * and "viewer" are different facts. Use {@link accountAccess} for anything the
 * reader sees.
 */
export function accountRole(portal: AdsPortal | null | undefined, accountId?: number): string {
  const accounts = portal?.accounts || [];
  const match = accounts.find((account) => Number(account.id) === Number(accountId));
  return String(match?.role || "viewer");
}

/**
 * Why the app believes what it believes about an account, in §31's vocabulary.
 *
 * `accountRole` collapses three different situations into the string
 * `"viewer"`: the portal has not arrived, the portal arrived without this
 * account, and the account really did come back carrying a viewer role. Only
 * the last is a permission. The first is `Loading…`/`Unavailable`, and the
 * second is the server declining to acknowledge the account at all — a 404 from
 * `_role_for_account`, not the 403 that `_require_account_role` raises.
 *
 * - `"unknown"`   — no role was read. Either no payload, or a payload whose
 *                   account row carried no role.
 * - `"unlisted"`  — a trustworthy payload arrived and this account is not in it.
 * - `"granted"`   — the account is present and carries a role the server names.
 */
export type AdAccountAccess =
  | { state: "unknown"; role: null }
  | { state: "unlisted"; role: null }
  | { state: "granted"; role: string };

/** True when the payload came from the portal rather than the degraded fan-out. */
export function portalIsLoaded(portal: AdsPortal | null | undefined): portal is AdsPortal {
  return Boolean(portal) && !portal?.degraded;
}

/** Whether this specific account was in the payload. */
export function accountIsListed(portal: AdsPortal | null | undefined, accountId?: number): boolean {
  return (portal?.accounts || []).some((account) => Number(account.id) === Number(accountId));
}

/**
 * Note what this keys on, and what it deliberately does not.
 *
 * The obvious implementation is "degraded means unknown", and it is wrong in
 * both directions. The degraded fan-out still returns accounts — it is the old
 * `listAdAccounts` path — and `normalizeAdAccount` spreads the row through
 * without defaulting `role`, so a degraded payload can carry a perfectly real
 * `"owner"` (which should be honoured) or an account row with no role at all
 * (which must not become `"viewer"`). Keying on whether a *recognised role
 * string actually arrived* handles both, and only falls back to `degraded` for
 * the one question the account row cannot answer: whether an account's absence
 * from the list means anything.
 */
export function accountAccess(
  portal: AdsPortal | null | undefined,
  accountId?: number
): AdAccountAccess {
  const match = (portal?.accounts || []).find(
    (account) => Number(account.id) === Number(accountId)
  );
  if (match) {
    const role = String((match as { role?: string }).role || "");
    // A row that arrived without a role is a fact that did not arrive, not a
    // viewer. This is the whole point of the type.
    return AD_ACCOUNT_ROLES.includes(role as (typeof AD_ACCOUNT_ROLES)[number])
      ? { state: "granted", role }
      : { state: "unknown", role: null };
  }
  // Absence only means something if the list is complete. On the fan-out path
  // it may just be the call that failed.
  return portalIsLoaded(portal) ? { state: "unlisted", role: null } : { state: "unknown", role: null };
}

/** Whether this account may run campaign and creative writes. */
export function canWriteAds(portal: AdsPortal | null | undefined, accountId?: number): boolean {
  return AD_WRITE_ROLES.includes(accountRole(portal, accountId));
}

/** Whether this account may see spend and performance figures. */
export function canViewAdAnalytics(portal: AdsPortal | null | undefined, accountId?: number): boolean {
  return AD_ANALYTICS_ROLES.includes(accountRole(portal, accountId));
}

/**
 * Why a write control is unavailable, in the reader's terms, or `null` when it
 * is available.
 *
 * §31 forbids an active-looking unavailable control and §37 forbids a dead end,
 * so a hidden button is not sufficient: someone who cannot find the action
 * needs to know it exists and what, if anything, they can do about it.
 *
 * Every branch answers a different question, and the two that are not about
 * permission come first. A blocked control on an unloaded portal is not a
 * permission decision — it is a decision the app has not been able to make yet
 * — and calling it a role restriction tells the reader they lack access they
 * very likely have. That is the absence-as-evidence error in its most damaging
 * form: it sends someone to ask for a grant instead of to retry.
 *
 * The role branches carry no remedy, because none exists. See the note at the
 * top of this section: nothing in the product writes `pulse_ad_team_members`,
 * so "ask an owner to change your role" named an action no route implements.
 */
export function adWriteBlockedReason(
  portal: AdsPortal | null | undefined,
  accountId?: number
): string | null {
  if (canWriteAds(portal, accountId)) return null;
  const access = accountAccess(portal, accountId);
  if (access.state === "unknown") {
    return "Your permissions for this account couldn’t be loaded, so changes are held back. Try again.";
  }
  if (access.state === "unlisted") {
    return "This ad account isn’t on your portal, so changes would be rejected.";
  }
  if (access.role === "analyst") {
    return "Your analyst access can read reports but can’t change campaigns.";
  }
  return "Your viewer access is read-only, so campaign changes are turned off.";
}

/* ------------------------------------------------------------------ *
 * Review board
 * ------------------------------------------------------------------ */

/**
 * Whether a review decision is final, i.e. a human upheld it.
 *
 * This drives whether the Policy Center offers a contest path. An automated
 * flag that no person has reviewed is still moving; a human rejection is the
 * one worth escalating.
 */
export function reviewIsHumanDecided(entry: AdReviewEntry): boolean {
  const human = String(entry.human_review_status || "").toLowerCase();
  return human.length > 0 && human !== "pending" && human !== "none";
}

/** The decision, as one word, for a badge. */
export function reviewOutcome(entry: AdReviewEntry): "approved" | "rejected" | "pending" {
  const status = String(entry.moderation_status || entry.review_status || "").toLowerCase();
  if (status === "approved") return "approved";
  if (status === "rejected" || status === "blocked") return "rejected";
  return "pending";
}

/**
 * The stated reason, or an honest admission that there isn't one.
 *
 * The backend can reject without populating `review_reason` or
 * `rejection_reason`. §37 forbids an inaccessible policy reason, and a blank
 * line is inaccessible in the way that matters — so when there is no reason
 * this says there is no reason rather than rendering nothing.
 */
export function reviewReasonText(entry: AdReviewEntry): string {
  const reason = String(entry.review_reason || "").trim() || String(entry.rejection_reason || "").trim();
  if (reason) return reason;
  if (reviewOutcome(entry) === "rejected") return "No reason was recorded for this decision.";
  return "";
}

/* ------------------------------------------------------------------ *
 * Placements
 * ------------------------------------------------------------------ */

/** One place an ad can run, as the Audiences page presents it. */
export type PlacementCatalogueEntry = {
  key: string;
  /** The server's display name, e.g. "Marketplace sponsor". */
  name: string;
  /**
   * Which devices this placement exists on, in words. The placement row stores
   * `all` / `mobile` / `desktop` and `select_ads` enforces it in SQL
   * (`p.device_type='all' OR p.device_type=?`) — so unlike the targeting table,
   * this constraint is real and worth showing.
   */
  devices: string;
  /**
   * How many times one campaign may appear here to one viewer, per the row's
   * `max_frequency`, which `_frequency_allowed` enforces per placement. `0`
   * means the row carried no cap, not that the cap is zero.
   */
  maxFrequency: number;
};

const DEVICE_WORDS: Record<string, string> = {
  all: "Every device",
  mobile: "Mobile only",
  desktop: "Desktop only",
  tablet: "Tablet only"
};

/**
 * The placements a campaign can actually be attached to.
 *
 * Read from the portal rather than hardcoded, because the hardcoded list this
 * replaces was wrong in both directions. It named two placements, "Feed" and
 * "Reels", where `PLACEMENTS` (services/pulse_ads_service.py:22) seeds twelve —
 * and one of the two it named does not exist: there is no reels placement key
 * anywhere in the ads service. Ten real surfaces, Marketplace and Search and
 * Pulse Radio among them, were invisible to the advertiser deciding where to
 * spend. Sourcing the list from `portal.placements` means it moves when
 * `seed_placements` does.
 *
 * Sorted by name because the server sends a dict and dict order promises
 * nothing; an unstable list looks like the catalogue is changing when it isn't.
 */
export function placementCatalogue(portal: AdsPortal | null | undefined): PlacementCatalogueEntry[] {
  const source = portal?.placements;
  if (!source || typeof source !== "object") return [];
  return Object.keys(source)
    .map((key) => {
      const item = source[key] || {};
      const device = String(item.device_type || "all").toLowerCase();
      const frequency = Number(item.max_frequency);
      return {
        key: String(item.placement_key || key),
        name: String(item.display_name || key),
        devices: DEVICE_WORDS[device] || "Every device",
        maxFrequency: Number.isFinite(frequency) && frequency > 0 ? frequency : 0
      };
    })
    .sort((a, b) => a.name.localeCompare(b.name));
}
