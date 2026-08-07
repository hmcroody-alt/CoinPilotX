/**
 * Business OS data layer.
 *
 * Business OS is the single Profile OS entry point for everything an owner does
 * to run their business: store management, marketplace selling, advertising,
 * orders, payments and insights.
 *
 * Backend binding: the LIVE `/api/pulse/*` surface.
 *
 * There is a second, newer `/api/business-os/*` surface in the backend, but every
 * one of its `BUSINESS_OS_*` rollout flags is inert by default and none of them
 * are set in any environment file, so those routes return 404 in production
 * today. Binding Business OS to them would ship controls that cannot work.
 * When those flags are turned on, the section registry below is the single place
 * that has to change.
 *
 * Non-duplication: marketplace listings, seller orders and payouts already have
 * canonical modules (`./marketplace`, `./orders`). This module does NOT restate
 * them — it adds the advertiser surface, which had no native client at all, and
 * a capability registry the navigation shell uses to decide which sections are
 * real.
 */
import { readJsonCache, writeJsonCache } from "../core/cache";
import { PULSE_API_BASE_URL } from "./config";
import { pulseApi } from "./pulseApi";

const AD_ACCOUNTS_CACHE_KEY = "pulsesoc.native.businessos.adaccounts";
const AD_CAMPAIGNS_CACHE_KEY = "pulsesoc.native.businessos.adcampaigns";
const AD_ANALYTICS_CACHE_KEY = "pulsesoc.native.businessos.adanalytics";

/* ------------------------------------------------------------------ *
 * Section registry
 * ------------------------------------------------------------------ */

export type BusinessOsSectionKey =
  | "dashboard"
  | "profile"
  | "store"
  | "marketplace"
  | "advertising"
  | "orders"
  | "customers"
  | "messages"
  | "insights"
  | "payments"
  | "events"
  | "team"
  | "verification"
  | "settings";

/**
 * Card #9 destination config — the single knob for the "Events" card.
 *
 * DECISION (owner sign-off: PENDING): card #9 stays the seller's *hosted-events*
 * manager (workshops, live sales, pop-ups you run), NOT the notification/activity
 * feed. The activity feed is reachable from every seller header's bell instead,
 * because it is a cross-cutting inbox, not one of the business sections. Keeping
 * the two apart matches how sellers think ("my events" vs "what happened") and
 * avoids overloading a dashboard tile with an unrelated feed.
 *
 * Everything renamable about the card is driven from here, so re-labelling it or
 * repointing it (e.g. if an owner later decides the tile should open the feed) is
 * a one-line change with no edit to the section list below.
 */
export const EVENTS_CARD_CONFIG = {
  label: "Events",
  blurb: "Events you host and promote.",
  icon: "calendar-outline",
  /** The rebuilt hosted-events manager. Was `Events` (the live discovery feed). */
  route: "BusinessOsEvents"
} as const;

export type BusinessOsSection = {
  key: BusinessOsSectionKey;
  label: string;
  /** One-line description of what the owner does here. */
  blurb: string;
  icon: string;
  /**
   * Route in the root stack, or a bottom tab reached via `Tabs`. Every name here
   * is registered in `AppNavigator`; nothing points at an invented screen.
   */
  route?: string;
  /** Params passed with the route, when the destination needs them. */
  params?: Record<string, string | number | boolean>;
  /** True when the destination is a bottom tab rather than a root-stack screen. */
  tab?: boolean;
  /**
   * True when a live backend contract exists for this section today. Sections
   * without one are not rendered as controls, so the shell never shows a button
   * that cannot do anything.
   */
  backed: boolean;
};

/**
 * Whether the Business OS hub renders the light redesign with live per-card
 * state, or the original dark sections screen.
 *
 * OFF, and deliberately so. The redesign's data wiring worked — live states,
 * error retry and real zeros all rendered from the correct sources — but its
 * typography and layout did not survive real device font scales: card titles
 * truncated to "Busine…" and "Payme…", state lines wrapped mid-word
 * ("complet e", "campai gn"), and the varying line lengths broke the grid into
 * uneven, cramped rows. The product owner reverted the visuals.
 *
 * This flag governs presentation only. Every source the redesign consumed is
 * still live and still consumed by the section screens; the hub simply stops
 * reading them. `screens/BusinessHubRoute.tsx` is the only reader of this flag,
 * and it defers its import of the light screen so nothing behind this flag is
 * evaluated while it is false.
 *
 * Before turning it back on, read `docs/business_os/BUSINESS_HUB_REVERT.md`:
 * the failure was layout robustness, so any revisit has to be proven at maximum
 * font scale on a narrow device before it ships.
 */
export const HUB_LIVE_CARDS = false;

/**
 * Ordered Business OS sections. `backed` reflects verified live `/api/pulse/*`
 * coverage, not aspiration.
 *
 * Store and Payments resolve to `SellerStore`, the canonical seller surface,
 * differing by `mode` rather than by screen so Business OS unifies the existing
 * seller tools instead of cloning them. Orders is the exception: it now resolves
 * to the rebuilt two-sided `BusinessOsOrders` surface, while `SellerStore
 * mode="orders"` still resolves for any existing deep link.
 *
 * Business Profile is the exception. It used to be `SellerStore mode="profile"`
 * — three generic seller-tool panels — but the job is not seller tooling, it is
 * showing the operator their public face and what is missing from it. That
 * earned a dedicated screen. `SellerStore mode="profile"` still resolves, so any
 * existing deep link keeps working.
 */
export const BUSINESS_OS_SECTIONS: BusinessOsSection[] = [
  {
    key: "dashboard",
    label: "Dashboard",
    blurb: "Everything happening across your business right now.",
    icon: "grid-outline",
    route: "BusinessOs",
    backed: true
  },
  {
    key: "profile",
    label: "Business Profile",
    blurb: "How buyers see your business.",
    icon: "business-outline",
    route: "BusinessProfile",
    backed: true
  },
  {
    key: "store",
    label: "Store",
    blurb: "Your listings, inventory and storefront.",
    icon: "storefront-outline",
    route: "SellerStore",
    params: { mode: "dashboard" },
    backed: true
  },
  {
    key: "marketplace",
    label: "Marketplace",
    blurb: "List an item and manage what you sell.",
    icon: "pricetags-outline",
    // Repointed from `MarketplaceCreateGateway` to the manager screen. The
    // gateway goes straight to the composer, which answers only the "list an
    // item" half of this card's promise; the other half — "manage what you
    // sell" — had nowhere to go. Every other caller of the gateway wants the
    // composer and is untouched, and the manager's footer CTA still opens it.
    route: "MarketplaceManager",
    backed: true
  },
  {
    key: "advertising",
    label: "Advertising",
    blurb: "Ad accounts, campaigns, budgets and delivery.",
    icon: "megaphone-outline",
    route: "BusinessOsAdvertising",
    backed: true
  },
  {
    // Repointed from `SellerStore { mode: "orders" }` to the rebuilt two-sided
    // Orders surface. The old route still works as a deep link (its orders panel
    // is untouched); this card now opens the new seller/buyer manager on the
    // seller side. Every other `SellerStore` caller is unaffected.
    key: "orders",
    label: "Orders",
    blurb: "Orders buyers placed with you.",
    icon: "receipt-outline",
    route: "BusinessOsOrders",
    params: { perspective: "seller" },
    backed: true
  },
  {
    key: "customers",
    label: "Customers",
    blurb: "Customer records and segments.",
    icon: "people-outline",
    backed: false
  },
  {
    key: "messages",
    label: "Messages",
    blurb: "Conversations with buyers.",
    icon: "chatbubbles-outline",
    route: "BusinessOsMessages",
    backed: true
  },
  {
    key: "insights",
    label: "Insights",
    blurb: "Delivery, spend and store performance.",
    icon: "stats-chart-outline",
    route: "BusinessOsInsights",
    backed: true
  },
  {
    key: "payments",
    label: "Payments",
    blurb: "Payouts, ad wallet and billing.",
    icon: "card-outline",
    route: "BusinessOsPayments",
    backed: true
  },
  {
    // Repointed from `Events` (the live discovery feed) to the rebuilt hosted-
    // events manager. Everything renamable lives in EVENTS_CARD_CONFIG above, so
    // this entry never has to change to relabel or redirect the card. The old
    // `Events` route is untouched and still answers every existing deep link.
    key: "events",
    label: EVENTS_CARD_CONFIG.label,
    blurb: EVENTS_CARD_CONFIG.blurb,
    icon: EVENTS_CARD_CONFIG.icon,
    route: EVENTS_CARD_CONFIG.route,
    backed: true
  },
  {
    key: "team",
    label: "Team",
    blurb: "People who help run the business.",
    icon: "person-add-outline",
    backed: false
  },
  {
    key: "verification",
    label: "Verification",
    blurb: "Verify the business and unlock ad delivery.",
    icon: "shield-checkmark-outline",
    route: "VerificationCenter",
    params: { track: "business" },
    backed: true
  },
  {
    key: "settings",
    label: "Settings",
    blurb: "Business preferences and account controls.",
    icon: "settings-outline",
    route: "Settings",
    tab: true,
    backed: true
  }
];

/** Sections that have a live contract and can be presented as working controls. */
export function activeBusinessOsSections() {
  return BUSINESS_OS_SECTIONS.filter((section) => section.backed && Boolean(section.route));
}

/** Sections shown on the Business OS hub — everything except the hub itself. */
export function businessOsHubSections() {
  return activeBusinessOsSections().filter((section) => section.key !== "dashboard");
}

/**
 * Resolve a section to navigation arguments. Tabs are reached through the `Tabs`
 * navigator; everything else is a root-stack push.
 */
export function businessOsNavigationArgs(section: BusinessOsSection): [string, Record<string, unknown>?] {
  if (!section.route) throw new Error(`Business OS section "${section.key}" has no route.`);
  if (section.tab) return ["Tabs", { screen: section.route, params: section.params }];
  return [section.route, section.params];
}

export function businessOsSection(key: BusinessOsSectionKey) {
  return BUSINESS_OS_SECTIONS.find((section) => section.key === key);
}

/* ------------------------------------------------------------------ *
 * Advertising — accounts
 * ------------------------------------------------------------------ */

export type AdAccountStatus = "draft" | "pending_verification" | "active" | "suspended";
export type AdAccountVerification = "unverified" | "pending" | "verified" | "rejected" | string;

export type AdAccount = {
  id: number;
  business_name?: string;
  business_type?: string;
  business_email?: string;
  business_phone?: string;
  business_website?: string;
  status?: AdAccountStatus | string;
  verification_status?: AdAccountVerification;
  created_at?: string;
  updated_at?: string;
};

export type AdAccountsResponse = { ok?: boolean; accounts?: AdAccount[]; account?: AdAccount };

export type AdAccountCreatePayload = {
  business_name: string;
  business_email?: string;
  business_phone?: string;
  business_website?: string;
  business_type?: string;
};

export async function listAdAccounts() {
  const data = await pulseApi<AdAccountsResponse>("/api/pulse/ads/accounts");
  const accounts = normalizeAdAccounts(data.accounts || []);
  await cacheAdAccounts(accounts).catch(() => undefined);
  return { ...data, accounts };
}

export async function createAdAccount(payload: AdAccountCreatePayload) {
  const data = await pulseApi<AdAccountsResponse>("/api/pulse/ads/accounts", {
    method: "POST",
    body: JSON.stringify(payload)
  });
  return { ...data, account: data.account ? normalizeAdAccount(data.account) : undefined };
}

/**
 * Ask for the ad account to be reviewed.
 *
 * This is not the same thing as the Verification Center, and the difference is
 * the whole point of the call. The Verification Center posts to
 * `/api/dashboard/account/verification/request` and decides a *profile* badge;
 * `select_ads` reads `pulse_ad_accounts.status`, which that flow never touches.
 * An advertiser who completed the badge track and waited was doing real work
 * against the wrong record, and their campaigns stayed dark either way.
 *
 * Owner-only on the server, and it refuses a second submission while one is in
 * review — both surface here as the server's own sentence rather than a guess.
 */
export async function requestAdAccountVerification(accountId: number, note = "") {
  return pulseApi<{
    ok?: boolean;
    account_id?: number;
    verification_status?: AdAccountVerification;
    status?: AdAccountStatus | string;
    submitted_at?: string;
  }>(`/api/pulse/ads/accounts/${encodeURIComponent(String(accountId))}/verification`, {
    method: "POST",
    body: JSON.stringify(note ? { note } : {})
  });
}

export async function loadCachedAdAccounts() {
  return (await readJsonCache<AdAccount[]>(AD_ACCOUNTS_CACHE_KEY, normalizeAdAccounts)) || [];
}

export async function cacheAdAccounts(accounts: AdAccount[]) {
  await writeJsonCache(AD_ACCOUNTS_CACHE_KEY, normalizeAdAccounts(accounts).slice(0, 100));
}

export function normalizeAdAccounts(accounts: AdAccount[]) {
  return (Array.isArray(accounts) ? accounts : []).map(normalizeAdAccount).filter((account) => account.id > 0);
}

export function normalizeAdAccount(account: AdAccount): AdAccount {
  return {
    ...account,
    id: Number(account.id || 0),
    business_name: String(account.business_name || "Untitled business"),
    business_type: String(account.business_type || ""),
    status: String(account.status || "draft"),
    verification_status: String(account.verification_status || "unverified"),
    created_at: String(account.created_at || ""),
    updated_at: String(account.updated_at || "")
  };
}

/** True when the account may create and run campaigns. */
export function adAccountCanTransact(account?: AdAccount) {
  return String(account?.status || "") === "active";
}

/* ------------------------------------------------------------------ *
 * Advertising — campaigns
 * ------------------------------------------------------------------ */

/** Mirrors `VALID_OBJECTIVES` in services/pulse_ads_service.py. */
export const AD_CAMPAIGN_OBJECTIVES = [
  "awareness",
  "brand_awareness",
  "traffic",
  "website_traffic",
  "engagement",
  "creator_growth",
  "creator_promotion",
  "marketplace",
  "marketplace_sales",
  "radio",
  "pulse_radio",
  "music_promotion",
  "video_promotion",
  "app_promotion",
  "event_promotion",
  "hologram_campaign"
] as const;
export type AdCampaignObjective = (typeof AD_CAMPAIGN_OBJECTIVES)[number];

/** Mirrors `VALID_BUDGET_TYPES`. */
export const AD_BUDGET_TYPES = ["daily", "lifetime"] as const;
export type AdBudgetType = (typeof AD_BUDGET_TYPES)[number];

/** Mirrors `CAMPAIGN_ACTIONS` in services/pulse_advertiser_portal.py. */
export const AD_CAMPAIGN_ACTIONS = ["pause", "resume", "archive", "duplicate", "submit", "complete"] as const;
export type AdCampaignAction = (typeof AD_CAMPAIGN_ACTIONS)[number];

/** Server-side budget ceilings, enforced by `safe_int(..., 0, 0, N)`. */
export const AD_DAILY_BUDGET_MAX_CENTS = 10_000_000;
export const AD_LIFETIME_BUDGET_MAX_CENTS = 100_000_000;

export type AdCampaign = {
  id: number;
  ad_account_id?: number;
  campaign_name?: string;
  objective?: string;
  status?: string;
  budget_type?: string;
  daily_budget_cents?: number;
  lifetime_budget_cents?: number;
  spent_cents?: number;
  start_at?: string;
  end_at?: string;
  priority?: number;
  pacing_mode?: string;
  created_at?: string;
  updated_at?: string;
  placements?: string[];
};

export type AdCampaignsResponse = { ok?: boolean; campaigns?: AdCampaign[]; campaign?: AdCampaign };

export type AdCampaignCreatePayload = {
  ad_account_id: number;
  campaign_name: string;
  objective?: AdCampaignObjective | string;
  budget_type?: AdBudgetType | string;
  daily_budget_cents?: number;
  lifetime_budget_cents?: number;
  start_at?: string;
  end_at?: string;
  placements?: string[];
};

export type AdCampaignActionResponse = {
  ok?: boolean;
  campaign_id?: number;
  status?: string;
  action?: string;
  message?: string;
};

export async function listAdCampaigns() {
  const data = await pulseApi<AdCampaignsResponse>("/api/pulse/ads/campaigns");
  const campaigns = normalizeAdCampaigns(data.campaigns || []);
  await cacheAdCampaigns(campaigns).catch(() => undefined);
  return { ...data, campaigns };
}

export async function getAdCampaign(campaignId: number) {
  const data = await pulseApi<AdCampaignsResponse>(
    `/api/pulse/ads/campaigns/${encodeURIComponent(String(campaignId))}`
  );
  return { ...data, campaign: data.campaign ? normalizeAdCampaign(data.campaign) : undefined };
}

export async function createAdCampaign(payload: AdCampaignCreatePayload) {
  const data = await pulseApi<AdCampaignsResponse>("/api/pulse/ads/campaigns", {
    method: "POST",
    body: JSON.stringify(clampCampaignBudgets(payload))
  });
  return { ...data, campaign: data.campaign ? normalizeAdCampaign(data.campaign) : undefined };
}

export async function updateAdCampaign(campaignId: number, payload: Partial<AdCampaignCreatePayload>) {
  const data = await pulseApi<AdCampaignsResponse>(
    `/api/pulse/ads/campaigns/${encodeURIComponent(String(campaignId))}`,
    { method: "PATCH", body: JSON.stringify(clampCampaignBudgets(payload)) }
  );
  return { ...data, campaign: data.campaign ? normalizeAdCampaign(data.campaign) : undefined };
}

export async function runAdCampaignAction(campaignId: number, action: AdCampaignAction) {
  return pulseApi<AdCampaignActionResponse>(
    `/api/pulse/ads/campaigns/${encodeURIComponent(String(campaignId))}/action`,
    { method: "POST", body: JSON.stringify({ action }) }
  );
}

export async function loadCachedAdCampaigns() {
  return (await readJsonCache<AdCampaign[]>(AD_CAMPAIGNS_CACHE_KEY, normalizeAdCampaigns)) || [];
}

export async function cacheAdCampaigns(campaigns: AdCampaign[]) {
  await writeJsonCache(AD_CAMPAIGNS_CACHE_KEY, normalizeAdCampaigns(campaigns).slice(0, 100));
}

export function normalizeAdCampaigns(campaigns: AdCampaign[]) {
  return (Array.isArray(campaigns) ? campaigns : [])
    .map(normalizeAdCampaign)
    .filter((campaign) => campaign.id > 0);
}

export function normalizeAdCampaign(campaign: AdCampaign): AdCampaign {
  return {
    ...campaign,
    id: Number(campaign.id || 0),
    ad_account_id: Number(campaign.ad_account_id || 0),
    campaign_name: String(campaign.campaign_name || "Untitled campaign"),
    objective: String(campaign.objective || "awareness"),
    status: String(campaign.status || "draft"),
    budget_type: String(campaign.budget_type || "daily"),
    daily_budget_cents: Number(campaign.daily_budget_cents || 0),
    lifetime_budget_cents: Number(campaign.lifetime_budget_cents || 0),
    spent_cents: Number(campaign.spent_cents || 0),
    start_at: String(campaign.start_at || ""),
    end_at: String(campaign.end_at || ""),
    placements: Array.isArray(campaign.placements) ? campaign.placements.map(String) : []
  };
}

/**
 * Clamp budgets to the server ceilings before sending. The backend silently
 * clamps out-of-range values, so mirroring it here keeps what the owner sees in
 * the form identical to what is stored.
 */
export function clampCampaignBudgets<T extends Partial<AdCampaignCreatePayload>>(payload: T): T {
  const next: Record<string, unknown> = { ...payload };
  if (payload.daily_budget_cents !== undefined) {
    next.daily_budget_cents = clampCents(payload.daily_budget_cents, AD_DAILY_BUDGET_MAX_CENTS);
  }
  if (payload.lifetime_budget_cents !== undefined) {
    next.lifetime_budget_cents = clampCents(payload.lifetime_budget_cents, AD_LIFETIME_BUDGET_MAX_CENTS);
  }
  return next as T;
}

function clampCents(value: number | undefined, max: number) {
  const cents = Math.floor(Number(value || 0));
  if (!Number.isFinite(cents) || cents < 0) return 0;
  return Math.min(cents, max);
}

/**
 * Actions the backend will accept for a campaign in its current status. Used so
 * the UI only offers transitions that will succeed.
 */
export function availableAdCampaignActions(campaign?: AdCampaign): AdCampaignAction[] {
  const status = String(campaign?.status || "draft").toLowerCase();
  if (status === "archived" || status === "completed") return ["duplicate"];
  if (status === "active") return ["pause", "complete", "archive", "duplicate"];
  if (status === "paused") return ["resume", "archive", "duplicate"];
  if (status === "pending_review") return ["archive", "duplicate"];
  return ["submit", "archive", "duplicate"];
}

/* ------------------------------------------------------------------ *
 * Advertising — analytics
 * ------------------------------------------------------------------ */

/**
 * There is deliberately no revenue field on either of these types.
 *
 * `advertiser_analytics` counts `conversion` rows in `pulse_ad_events` and
 * stops there: no order is linked to an ad, no value is carried, and nothing
 * is adjusted when an order is refunded. §37 forbids attributed revenue left
 * unadjusted after a refund, and with no order link the only way to honour
 * that is to never claim revenue. If a `revenue_cents` ever appears on this
 * payload, the attribution model has to arrive with it.
 */
export type AdAnalyticsRow = {
  account_id?: number;
  business_name?: string;
  campaign_id?: number;
  campaign_name?: string;
  status?: string;
  impressions?: number;
  viewable_impressions?: number;
  clicks?: number;
  hides?: number;
  reports?: number;
  /**
   * Count of `conversion` rows in `pulse_ad_events`. Present so the payload is
   * typed honestly, *not* so it can be labelled "Conversions" on a card: until
   * something in the product actually records a post-tap outcome, this is a
   * count of an event nothing emits. See `attributionNote` in api/adsDelivery.
   */
  conversions?: number;
  spent_cents?: number;
  /** Server-formatted currency string, e.g. "$1,234.00". */
  spend?: string;
  ctr?: number;
  estimated_cpc?: number;
  estimated_cpm?: number;
};

export type AdAnalyticsTotals = {
  impressions?: number;
  viewable_impressions?: number;
  clicks?: number;
  hides?: number;
  reports?: number;
  conversions?: number;
  spend_cents?: number;
  spend?: string;
  ctr?: number;
  estimated_cpc?: number;
  estimated_cpm?: number;
};

export type AdAnalytics = { totals: AdAnalyticsTotals; campaigns: AdAnalyticsRow[] };
export type AdAnalyticsResponse = { ok?: boolean; analytics?: AdAnalytics };

export async function getAdAnalytics(params: { accountId?: number } = {}) {
  const query = new URLSearchParams();
  if (params.accountId) query.set("account_id", String(params.accountId));
  const suffix = query.toString() ? `?${query.toString()}` : "";
  const data = await pulseApi<AdAnalyticsResponse>(`/api/pulse/ads/analytics${suffix}`);
  const analytics = normalizeAdAnalytics(data.analytics);
  await cacheAdAnalytics(analytics).catch(() => undefined);
  return { ...data, analytics };
}

export async function loadCachedAdAnalytics() {
  return (await readJsonCache<AdAnalytics>(AD_ANALYTICS_CACHE_KEY, normalizeAdAnalytics)) || null;
}

export async function cacheAdAnalytics(analytics: AdAnalytics) {
  await writeJsonCache(AD_ANALYTICS_CACHE_KEY, normalizeAdAnalytics(analytics));
}

export function normalizeAdAnalytics(analytics?: AdAnalytics | null): AdAnalytics {
  const rows = Array.isArray(analytics?.campaigns) ? analytics!.campaigns : [];
  const campaigns = rows
    .map((row) => ({
      ...row,
      account_id: Number(row.account_id || 0),
      campaign_id: Number(row.campaign_id || 0),
      campaign_name: String(row.campaign_name || "Untitled campaign"),
      status: String(row.status || "draft"),
      impressions: Number(row.impressions || 0),
      viewable_impressions: Number(row.viewable_impressions || 0),
      clicks: Number(row.clicks || 0),
      hides: Number(row.hides || 0),
      reports: Number(row.reports || 0),
      conversions: Number(row.conversions || 0),
      spent_cents: Number(row.spent_cents || 0),
      ctr: Number(row.ctr || 0),
      estimated_cpc: Number(row.estimated_cpc || 0),
      estimated_cpm: Number(row.estimated_cpm || 0)
    }))
    // A campaign-less account produces a row with a NULL campaign id from the
    // LEFT JOIN; it is not a campaign and must not be rendered as one.
    .filter((row) => Number(row.campaign_id) > 0);
  const totals = analytics?.totals || {};
  return {
    campaigns,
    totals: {
      ...totals,
      impressions: Number(totals.impressions || 0),
      viewable_impressions: Number(totals.viewable_impressions || 0),
      clicks: Number(totals.clicks || 0),
      hides: Number(totals.hides || 0),
      reports: Number(totals.reports || 0),
      conversions: Number(totals.conversions || 0),
      spend_cents: Number(totals.spend_cents || 0),
      ctr: Number(totals.ctr || 0),
      estimated_cpc: Number(totals.estimated_cpc || 0),
      estimated_cpm: Number(totals.estimated_cpm || 0)
    }
  };
}

/* ------------------------------------------------------------------ *
 * Advertising — wallet and billing
 * ------------------------------------------------------------------ */

export type AdWalletTransaction = {
  transaction_type?: string;
  amount_cents?: number;
  amount?: string;
  currency?: string;
  status?: string;
  description?: string;
  created_at?: string;
};

export type AdWalletReceipt = {
  invoice_number?: string;
  receipt_number?: string;
  amount_cents?: number;
  amount?: string;
  currency?: string;
  status?: string;
  created_at?: string;
};

export type AdWallet = {
  account_id?: number;
  currency?: string;
  available_balance_cents?: number;
  pending_balance_cents?: number;
  promotional_credits_cents?: number;
  bonus_credits_cents?: number;
  refund_credits_cents?: number;
  reserved_budget_cents?: number;
  lifetime_funded_cents?: number;
  lifetime_spent_cents?: number;
  spendable_balance_cents?: number;
  available_balance?: string;
  /**
   * The shortfall this account owes, in cents.
   *
   * A refunded or disputed top-up debits the wallet even when the money has
   * already been spent, so `available_balance_cents` can be negative and
   * `spendable_balance_cents` correctly floors at 0. Those two together look
   * exactly like an account that simply never funded, which is why the server
   * names the debt rather than leaving it to be inferred from a minus sign.
   */
  amount_owed_cents?: number;
  amount_owed?: string;
  /**
   * Set by the server when the wallet could not be read at all.
   *
   * The figures on such a row are `null`, not `0`. Anything consuming this
   * must branch on the flag before touching a number — a wallet we failed to
   * load is not a wallet holding nothing.
   */
  unavailable?: boolean;
  unavailable_reason?: string;
  transactions?: AdWalletTransaction[];
  receipts?: AdWalletReceipt[];
};

export type AdWalletResponse = { ok?: boolean; wallet?: AdWallet };

export type AdBilling = {
  wallet_balance_cents?: number;
  spend_limit_cents?: number;
  billing_status?: string;
  funding_status?: string;
  updated_at?: string;
  wallet_balance?: string;
  spend_limit?: string;
  /** Backend hardcodes false; real charging is not live. */
  live_charging?: boolean;
  /** Reflects PULSE_ADS_BILLING_ENABLED on the server. */
  billing_enabled?: boolean;
  stripe_customer_visible?: boolean;
  wallet?: AdWallet;
};

export type AdBillingResponse = { ok?: boolean; billing?: AdBilling };

export async function getAdWallet(accountId: number) {
  const data = await pulseApi<AdWalletResponse>(
    `/api/pulse/ads/accounts/${encodeURIComponent(String(accountId))}/wallet`
  );
  return { ...data, wallet: normalizeAdWallet(data.wallet) };
}

export async function getAdBillingSummary(accountId: number) {
  const data = await pulseApi<AdBillingResponse>(
    `/api/pulse/ads/accounts/${encodeURIComponent(String(accountId))}/billing-summary`
  );
  const billing = data.billing || {};
  return {
    ...data,
    billing: {
      ...billing,
      wallet_balance_cents: Number(billing.wallet_balance_cents || 0),
      spend_limit_cents: Number(billing.spend_limit_cents || 0),
      billing_status: String(billing.billing_status || "not_configured"),
      funding_status: String(billing.funding_status || "prepared"),
      live_charging: Boolean(billing.live_charging),
      billing_enabled: Boolean(billing.billing_enabled),
      wallet: normalizeAdWallet(billing.wallet)
    }
  };
}

export function normalizeAdWallet(wallet?: AdWallet | null): AdWallet {
  const source = wallet || {};
  // A wallet the server could not read arrives with `unavailable: true` and
  // null figures. Running it through the coercions below would turn every one
  // of those nulls into a `0` indistinguishable from a real empty wallet —
  // manufacturing on the client exactly the fake zero the server refused to
  // send. The flag and the reason are kept; the numbers stay absent.
  if (source.unavailable) {
    return {
      ...source,
      account_id: Number(source.account_id || 0),
      currency: String(source.currency || "usd").toUpperCase(),
      unavailable: true,
      unavailable_reason: String(source.unavailable_reason || "").trim() ||
        "Wallet balance couldn't be loaded. This is a temporary error, not a zero balance.",
      available_balance_cents: undefined,
      spendable_balance_cents: undefined,
      reserved_budget_cents: undefined,
      amount_owed_cents: undefined,
      transactions: Array.isArray(source.transactions) ? source.transactions : [],
      receipts: Array.isArray(source.receipts) ? source.receipts : []
    };
  }
  return {
    ...source,
    unavailable: false,
    account_id: Number(source.account_id || 0),
    currency: String(source.currency || "usd").toUpperCase(),
    available_balance_cents: Number(source.available_balance_cents || 0),
    pending_balance_cents: Number(source.pending_balance_cents || 0),
    promotional_credits_cents: Number(source.promotional_credits_cents || 0),
    bonus_credits_cents: Number(source.bonus_credits_cents || 0),
    refund_credits_cents: Number(source.refund_credits_cents || 0),
    reserved_budget_cents: Number(source.reserved_budget_cents || 0),
    lifetime_funded_cents: Number(source.lifetime_funded_cents || 0),
    lifetime_spent_cents: Number(source.lifetime_spent_cents || 0),
    spendable_balance_cents: Number(source.spendable_balance_cents || 0),
    amount_owed_cents: Number(source.amount_owed_cents || 0),
    transactions: Array.isArray(source.transactions) ? source.transactions : [],
    receipts: Array.isArray(source.receipts) ? source.receipts : []
  };
}

/**
 * Whether funding controls should be shown. The backend reports
 * `billing_enabled` from PULSE_ADS_BILLING_ENABLED and hardcodes
 * `live_charging: false`; when funding is not live the UI must say so rather
 * than render an Add Funds button that cannot charge anything.
 */
export function adFundingIsLive(billing?: AdBilling) {
  return Boolean(billing?.billing_enabled) && Boolean(billing?.live_charging);
}

/* ------------------------------------------------------------------ *
 * Formatting
 * ------------------------------------------------------------------ */

export function formatCents(cents?: number, currency = "USD") {
  const amount = Number(cents || 0) / 100;
  const code = String(currency || "USD").toUpperCase();
  try {
    return new Intl.NumberFormat(undefined, { style: "currency", currency: code }).format(amount);
  } catch {
    return `${code} ${amount.toFixed(2)}`;
  }
}

export function formatCampaignBudget(campaign?: AdCampaign) {
  if (!campaign) return "";
  const lifetime = String(campaign.budget_type || "daily").toLowerCase() === "lifetime";
  const cents = lifetime ? campaign.lifetime_budget_cents : campaign.daily_budget_cents;
  if (!Number(cents || 0)) return "No budget set";
  return `${formatCents(cents)} ${lifetime ? "lifetime" : "per day"}`;
}

export function formatObjective(objective?: string) {
  return String(objective || "awareness")
    .split("_")
    .map((word) => (word ? word[0].toUpperCase() + word.slice(1) : word))
    .join(" ");
}

/** Deep link to the equivalent web surface, for support and escalation only. */
export function businessOsWebUrl(path = "/pulse/ads") {
  return `${PULSE_API_BASE_URL}${path.startsWith("/") ? "" : "/"}${path}`;
}
