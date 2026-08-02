/**
 * Data layer for the two-sided Advertising manager.
 *
 * This module is where the mission's money rules are enforced, so the split it
 * draws is the important thing about it:
 *
 *   • MARKETPLACE ADS are fully backed. Accounts, campaigns, analytics, wallet
 *     and billing all come from live `/api/pulse/ads/*` contracts via
 *     `./businessOs`. Nothing on that side is invented, and no balance or spend
 *     figure is computed on the client — every number is read from the server
 *     response and formatted, never derived.
 *
 *   • POST ADS, the seven-day spend series, and the post-performance
 *     suggestion are NOT backed by any endpoint today. They are gated behind a
 *     feature flag (off by default) and, when on, every value they show is
 *     tagged `MOCK-DATA` and visibly labelled in the UI as a preview. They never
 *     present a fabricated balance or spend as truth — the wallet chip in both
 *     modes reads the one real wallet.
 *
 * `ADS_MOCK_DATA_GAPS` lists every unsourced field and the backend work it
 * needs, so the completion report is generated from code rather than memory and
 * a test can assert the count has not silently grown.
 */

import {
  AdAccount,
  AdAnalytics,
  AdBilling,
  AdCampaign,
  AdCampaignAction,
  AdWallet,
  adAccountCanTransact,
  adFundingIsLive,
  availableAdCampaignActions,
  formatCents,
  getAdAnalytics,
  getAdBillingSummary,
  getAdWallet,
  listAdAccounts,
  listAdCampaigns,
  loadCachedAdAccounts,
  loadCachedAdAnalytics,
  loadCachedAdCampaigns
} from "./businessOs";

/* ------------------------------------------------------------------ *
 * Modes
 * ------------------------------------------------------------------ */

export type AdsMode = "marketplace" | "post";
export const ADS_MODES: readonly AdsMode[] = ["marketplace", "post"];

/* ------------------------------------------------------------------ *
 * Feature flag
 * ------------------------------------------------------------------ */

/**
 * Name of the env flag that turns on the unbacked preview surfaces (Post ads,
 * the seven-day spend chart, and the suggestion). Off by default. Kept as a
 * constant so the completion report and the settings copy can name it exactly.
 */
export const ADS_POST_MODE_FLAG = "EXPO_PUBLIC_ADS_POST_MODE";

/**
 * Whether the unbacked preview surfaces are enabled for this build. Read from
 * the environment at call time (not cached at module load) so tests can toggle
 * it. When false, the ModeToggle still shows Post ads but the mode explains it
 * is coming rather than rendering invented promotions, and the spend chart
 * falls back to an honest unsourced state.
 */
export function adsPostModeEnabled(): boolean {
  const raw = String(process.env[ADS_POST_MODE_FLAG] || "").trim().toLowerCase();
  return raw === "1" || raw === "true" || raw === "on" || raw === "yes";
}

/* ------------------------------------------------------------------ *
 * Unsourced fields
 * ------------------------------------------------------------------ */

export type AdsDataGap = {
  /** What the reference design asks for. */
  field: string;
  /** Where it would have to come from. */
  needs: string;
  /** The mode the gap belongs to, for the report. */
  mode: AdsMode | "both";
};

/**
 * Everything the Advertising design shows that this app has no live source for.
 * Exported so a test can assert the count: if someone later fakes one of these
 * without a real endpoint, the number changes and the test says so.
 */
export const ADS_MOCK_DATA_GAPS: readonly AdsDataGap[] = [
  // MOCK-DATA: per-day spend. `/api/pulse/ads/analytics` returns totals and
  // per-campaign rows only; there is no per-day breakdown, so the seven-day
  // spend chart's daily series is not sourced. The real total spend IS shown.
  {
    field: "Spend — last 7 days (per day)",
    needs: "analytics endpoint returning daily spend buckets (spend_cents per day)",
    mode: "marketplace"
  },
  // MOCK-DATA: campaign "learning" and "limited" phases. The backend campaign
  // status is one of draft|pending_review|active|paused|archived|completed. It
  // does not distinguish a delivering campaign that is still in its learning
  // window, nor one whose delivery is limited. Both collapse into "active".
  {
    field: "Campaign learning / limited delivery phase",
    needs: "campaign status extended with a delivery-phase field (learning, limited)",
    mode: "marketplace"
  },
  // MOCK-DATA: post promotions. There is no endpoint to promote a feed post,
  // Reel or live replay, list promotions, or read their review status and
  // delivered metrics. The entire Post-ads product is a flag-gated preview.
  {
    field: "Post / Reel / Live promotions",
    needs: "a post-promotion service (create, list, review status, delivered metrics)",
    mode: "post"
  },
  // MOCK-DATA: post-performance suggestion. "This Reel is outperforming —
  // promote it?" needs an organic-performance signal per post and a ranking of
  // which post is worth promoting. Nothing produces either.
  {
    field: "Outperforming-post suggestion",
    needs: "per-post organic reach/engagement feed + a promote-worthiness ranking",
    mode: "post"
  },
  // MOCK-DATA: promoted-post picker rail. The chooser of which of the user's
  // recent posts to promote needs the user's own recent post list with reach;
  // no feed-authored-by-me-with-metrics endpoint is wired here.
  {
    field: "Promote-a-post picker (your recent posts)",
    needs: "authored-posts endpoint with per-post reach for the picker rail",
    mode: "post"
  },
  // MOCK-DATA: the seven-day *window* itself. `getAdAnalytics` takes only an
  // account id — there is no date range — so its totals are lifetime, not the
  // last seven days. The KPI tiles therefore say "to date" rather than "· 7d".
  // Labelling a lifetime total as a weekly one would be the exact failure this
  // list exists to prevent, so the label was changed rather than the number.
  {
    field: "Spend / clicks windowed to the last 7 days",
    needs: "analytics endpoint accepting a date range (from/to) and windowing its totals",
    mode: "marketplace"
  },
  // MOCK-DATA: KPI trend arrows. A trend needs the same metric over a prior
  // comparable period. With no windowing there is no baseline to compare
  // against, so no tile shows an arrow — including the "▼ cheaper" treatment
  // the design specifies for cost-per-click.
  {
    field: "KPI period-over-period trend (▲/▼ vs previous period)",
    needs: "windowed analytics, so the same metric can be read for the prior period",
    mode: "marketplace"
  },
  // MOCK-DATA: ad-specific notifications. The design's header bell with an
  // unread badge needs notifications scoped to advertising — a campaign
  // approved, a card declined, delivery limited. The app's notification feed is
  // global, and putting a DM count on an ads bell would misreport it, so the
  // bell is omitted rather than wired to the wrong number.
  // MOCK-DATA: the Post-ads KPI trio. Reach, new followers and engagements for
  // promoted content need the promotion service that does not exist, plus a
  // follower-attribution signal that says which follows came from a promotion.
  // With the flag off no tile is drawn at all; with it on they are labelled
  // Preview and carry sample figures.
  {
    field: "Post-ads KPIs (reach, new followers, engagements)",
    needs: "post-promotion delivered metrics + follower attribution per promotion",
    mode: "post"
  },
  {
    field: "Advertising notification bell + unread badge",
    needs: "notifications filtered to an advertising category, with an unread count",
    mode: "both"
  }
] as const;

/* ------------------------------------------------------------------ *
 * Campaign state machine (Marketplace ads) — backed
 * ------------------------------------------------------------------ */

/**
 * The campaign phase the UI shows. `blocked_verification` is an overlay, not a
 * status the backend stores: it is derived from the owning account being unable
 * to transact while the campaign wants to deliver.
 *
 * `learning` and `limited` are declared here for completeness but are NOT
 * distinguishable from `delivering` given the current backend (see
 * ADS_MOCK_DATA_GAPS); `campaignPhase` never returns them from live data.
 */
export type CampaignPhase =
  | "draft"
  | "in_review"
  | "learning"
  | "delivering"
  | "limited"
  | "paused"
  | "ended";

export type CampaignTone = "neutral" | "info" | "success" | "warning" | "error";

/** Backend campaign status → the phase the UI shows. Backed, deterministic. */
export function campaignPhase(campaign?: AdCampaign): CampaignPhase {
  const status = String(campaign?.status || "draft").toLowerCase();
  if (status === "pending_review") return "in_review";
  if (status === "active") return "delivering";
  if (status === "paused") return "paused";
  if (status === "archived" || status === "completed") return "ended";
  return "draft";
}

/**
 * True when a campaign is meant to deliver but its account cannot transact —
 * the state that must show the blocked-verification overlay with a deep link to
 * the Verification center. Backed by `account.status`.
 */
export function campaignBlockedByVerification(campaign: AdCampaign, account?: AdAccount): boolean {
  const phase = campaignPhase(campaign);
  const wantsDelivery = phase === "delivering" || phase === "in_review";
  return wantsDelivery && !adAccountCanTransact(account);
}

export function campaignPhaseLabel(phase: CampaignPhase): string {
  switch (phase) {
    case "in_review":
      return "In review";
    case "delivering":
      return "Delivering";
    case "learning":
      return "Learning";
    case "limited":
      return "Limited";
    case "paused":
      return "Paused";
    case "ended":
      return "Ended";
    default:
      return "Draft";
  }
}

export function campaignPhaseTone(phase: CampaignPhase): CampaignTone {
  switch (phase) {
    case "delivering":
      return "success";
    case "in_review":
    case "learning":
      return "info";
    case "limited":
      return "warning";
    case "paused":
      return "neutral";
    case "ended":
      return "neutral";
    default:
      return "neutral";
  }
}

/** Re-exported so the screen imports action availability from one module. */
export { availableAdCampaignActions };
export type { AdCampaignAction };

/* ------------------------------------------------------------------ *
 * Promotion state machine (Post ads) — NOT backed, flag-gated preview
 * ------------------------------------------------------------------ */

export type PromotionPhase =
  | "submitted"
  | "in_review"
  | "promoting"
  | "completed"
  | "rejected"
  | "paused";

export type PromotedContentType = "post" | "reel" | "live";

/**
 * A promoted post. MOCK-DATA: no endpoint backs this. Every instance is a
 * preview and is tagged as such. `mock` is always true today; it is on the type
 * so a real backend value can later arrive with `mock: false` and the UI can
 * drop the preview labelling per record rather than per build.
 */
export type PostPromotion = {
  id: string;
  contentType: PromotedContentType;
  title: string;
  phase: PromotionPhase;
  /** Preview-only figures. Never a real balance or spend. */
  reach?: number;
  spendCents?: number;
  budgetCents?: number;
  rejectionReason?: string;
  mock: true;
};

export function promotionPhaseLabel(phase: PromotionPhase): string {
  switch (phase) {
    case "submitted":
      return "Submitted";
    case "in_review":
      return "In review";
    case "promoting":
      return "Promoting";
    case "completed":
      return "Completed";
    case "rejected":
      return "Rejected";
    case "paused":
      return "Paused";
    default:
      return "Submitted";
  }
}

export function promotionPhaseTone(phase: PromotionPhase): CampaignTone {
  switch (phase) {
    case "promoting":
      return "success";
    case "in_review":
    case "submitted":
      return "info";
    case "rejected":
      return "error";
    case "paused":
    case "completed":
      return "neutral";
    default:
      return "neutral";
  }
}

/**
 * Preview promotions, only when the flag is on. Returns an empty list when the
 * flag is off so no invented content is ever rendered. The fixtures exist to
 * demonstrate the surface's states, not to imply a running campaign — each is
 * marked `mock: true` and the screen labels the whole mode as a preview.
 */
export function loadMockPostPromotions(): PostPromotion[] {
  if (!adsPostModeEnabled()) return [];
  return [
    {
      id: "mock-reel-1",
      contentType: "reel",
      title: "Behind the scenes — studio tour",
      phase: "promoting",
      reach: 12400,
      spendCents: 1800,
      budgetCents: 5000,
      mock: true
    },
    {
      id: "mock-post-1",
      contentType: "post",
      title: "New drop announcement",
      phase: "in_review",
      budgetCents: 3000,
      mock: true
    },
    {
      id: "mock-live-1",
      contentType: "live",
      title: "Launch night — live replay",
      phase: "completed",
      reach: 8800,
      spendCents: 4000,
      budgetCents: 4000,
      mock: true
    }
  ];
}

/* ------------------------------------------------------------------ *
 * Suggestion (Post ads) — NOT backed, flag-gated preview
 * ------------------------------------------------------------------ */

export type PostSuggestion = {
  id: string;
  contentType: PromotedContentType;
  title: string;
  /** Human reason, e.g. "3× your usual reach in 24h". Preview copy. */
  reason: string;
  mock: true;
};

/** The single top suggestion, only when the flag is on. */
export function loadMockSuggestion(): PostSuggestion | null {
  if (!adsPostModeEnabled()) return null;
  return {
    id: "mock-suggestion-1",
    contentType: "reel",
    title: "Behind the scenes — studio tour",
    reason: "Reaching 3× your usual audience in its first day",
    mock: true
  };
}

/* ------------------------------------------------------------------ *
 * Seven-day spend series — partial. Total is real; per-day is MOCK-DATA.
 * ------------------------------------------------------------------ */

export type SpendSeries = {
  /** Seven daily spend figures in cents, oldest first. Last item is "today". */
  daysCents: number[];
  /** True when `daysCents` is a preview shape, not real per-day data. */
  mock: boolean;
  /** The real, backend-sourced total spend in cents (always trustworthy). */
  totalCents: number;
};

/**
 * Build the spend chart series. The total is always the real analytics total.
 * The per-day breakdown is MOCK-DATA: with the flag off it is empty and the
 * chart shows an unsourced state; with the flag on it is a preview distribution
 * of the real total across seven days, explicitly marked `mock: true` so the
 * chart can badge it. The distribution never invents spend beyond the real
 * total — it only spreads a true number for shape.
 */
export function buildSpendSeries(analytics: AdAnalytics | null): SpendSeries {
  const totalCents = Number(analytics?.totals?.spend_cents || 0);
  if (!adsPostModeEnabled()) {
    return { daysCents: [], mock: true, totalCents };
  }
  // A fixed, deterministic weighting. This is shape only — it sums to the real
  // total, so no cent is invented, but which day carried which share is not
  // known and this is flagged mock.
  const weights = [0.08, 0.11, 0.13, 0.12, 0.16, 0.18, 0.22];
  const daysCents = weights.map((w) => Math.round(totalCents * w));
  return { daysCents, mock: true, totalCents };
}

/* ------------------------------------------------------------------ *
 * Wallet — backed. The one source of money truth.
 * ------------------------------------------------------------------ */

export type WalletSummary = {
  /** Formatted spendable balance, e.g. "$142.00". Read from the server. */
  balanceLabel: string;
  balanceCents: number;
  currency: string;
  /** Whether funding can actually charge. Backend hardcodes false today. */
  fundingLive: boolean;
  /** The account this wallet belongs to. */
  accountId: number;
};

/**
 * Reduce a wallet + billing response into what the chip needs. The balance is
 * the server's `spendable_balance_cents` (falling back to available), never a
 * client computation. `fundingLive` is `adFundingIsLive`, so the Add-funds
 * control can say "not live yet" rather than pretend to charge.
 */
export function walletSummary(
  accountId: number,
  wallet: AdWallet | null,
  billing: AdBilling | null
): WalletSummary {
  const currency = String(wallet?.currency || "USD").toUpperCase();
  const balanceCents = Number(
    wallet?.spendable_balance_cents ?? wallet?.available_balance_cents ?? 0
  );
  return {
    accountId,
    currency,
    balanceCents,
    balanceLabel: formatCents(balanceCents, currency),
    fundingLive: adFundingIsLive(billing || undefined)
  };
}

/* ------------------------------------------------------------------ *
 * Marketplace-ads screen model — backed, with offline fallback
 * ------------------------------------------------------------------ */

export type AdsSectionStatus = "ok" | "error";

export type AdsMarketplaceModel = {
  accounts: AdAccount[];
  /** The account whose wallet/analytics are shown. First active, else first. */
  primaryAccount: AdAccount | null;
  campaigns: AdCampaign[];
  analytics: AdAnalytics | null;
  wallet: WalletSummary | null;
  spend: SpendSeries;
  /** True when the primary account cannot transact (drives the unverified state). */
  needsVerification: boolean;
  /** Per-source liveness so the screen can show section errors, not a whole-page one. */
  accountsStatus: AdsSectionStatus;
  campaignsStatus: AdsSectionStatus;
  analyticsStatus: AdsSectionStatus;
  /** True when nothing reached the network and this is cached/empty data. */
  offline: boolean;
};

/** Pick the account the dashboard centres on: prefer one that can transact. */
export function primaryAdAccount(accounts: AdAccount[]): AdAccount | null {
  if (accounts.length === 0) return null;
  return accounts.find(adAccountCanTransact) || accounts[0];
}

/**
 * Load everything the Marketplace-ads mode shows. Accounts and campaigns reject
 * on network failure (so they signal offline); analytics/wallet/billing are
 * best-effort and degrade to a section error rather than taking down the page.
 */
export async function loadAdsMarketplace(): Promise<AdsMarketplaceModel> {
  const [accountsRes, campaignsRes] = await Promise.allSettled([
    listAdAccounts(),
    listAdCampaigns()
  ]);

  let accounts: AdAccount[] = [];
  let accountsStatus: AdsSectionStatus = "ok";
  if (accountsRes.status === "fulfilled") {
    accounts = accountsRes.value.accounts;
  } else {
    accounts = await loadCachedAdAccounts().catch(() => []);
    accountsStatus = "error";
  }

  let campaigns: AdCampaign[] = [];
  let campaignsStatus: AdsSectionStatus = "ok";
  if (campaignsRes.status === "fulfilled") {
    campaigns = campaignsRes.value.campaigns;
  } else {
    campaigns = await loadCachedAdCampaigns().catch(() => []);
    campaignsStatus = "error";
  }

  const primaryAccount = primaryAdAccount(accounts);

  // Analytics, wallet and billing are keyed to the primary account. Only fetch
  // them when there is one; a user with no ad account sees the empty state, not
  // a spinner over calls that would 404.
  let analytics: AdAnalytics | null = null;
  let analyticsStatus: AdsSectionStatus = "ok";
  let wallet: WalletSummary | null = null;

  if (primaryAccount) {
    const [analyticsRes, walletRes, billingRes] = await Promise.allSettled([
      getAdAnalytics({ accountId: primaryAccount.id }),
      getAdWallet(primaryAccount.id),
      getAdBillingSummary(primaryAccount.id)
    ]);

    if (analyticsRes.status === "fulfilled") {
      analytics = analyticsRes.value.analytics;
    } else {
      analytics = await loadCachedAdAnalytics().catch(() => null);
      analyticsStatus = "error";
    }

    const walletValue = walletRes.status === "fulfilled" ? walletRes.value.wallet : null;
    const billingValue = billingRes.status === "fulfilled" ? billingRes.value.billing : null;
    // Wallet is money truth: only present it when the wallet call itself
    // succeeded. A failed wallet shows no chip rather than a stale or zero one.
    if (walletValue) {
      wallet = walletSummary(primaryAccount.id, walletValue, billingValue);
    }
  }

  const offline =
    accountsRes.status === "rejected" && campaignsRes.status === "rejected";

  return {
    accounts,
    primaryAccount,
    campaigns,
    analytics,
    wallet,
    spend: buildSpendSeries(analytics),
    needsVerification: Boolean(primaryAccount) && !adAccountCanTransact(primaryAccount || undefined),
    accountsStatus,
    campaignsStatus,
    analyticsStatus,
    offline
  };
}

/* ------------------------------------------------------------------ *
 * Derivations
 *
 * Everything below is pure and returns numbers, never strings. The screen
 * formats them through `useFormatters`, so a locale change is a render, not a
 * refetch. Keeping these here rather than inline in the component is what lets
 * the money rules be tested: `deliverySwitchState` in particular is the single
 * place that decides whether a pause switch may be pressed, so the mission's
 * "a switch that silently no-ops is forbidden" rule has one implementation
 * instead of one per call site.
 * ------------------------------------------------------------------ */

export type AdsKpis = {
  /** Real, from analytics totals. Lifetime — see ADS_MOCK_DATA_GAPS. */
  spendCents: number;
  clicks: number;
  impressions: number;
  /**
   * Server-computed cost per click, in cents. Null when there are no clicks:
   * dividing by zero and showing "$0.00 per click" would read as "clicks are
   * free" rather than "nobody has clicked".
   */
  cpcCents: number | null;
  /**
   * Sum of the daily budgets of campaigns that are currently delivering, in
   * cents. This is a budget, not money moved, so summing it client-side is
   * safe; it is the "of $X budgets" context line, never presented as a balance.
   */
  dailyBudgetCents: number;
  /** Whether any campaign contributed to `dailyBudgetCents`. */
  hasDailyBudget: boolean;
};

export function adsKpis(model: Pick<AdsMarketplaceModel, "analytics" | "campaigns">): AdsKpis {
  const totals = model.analytics?.totals;
  const clicks = Number(totals?.clicks || 0);
  // `estimated_cpc` is dollars (the service divides spend_cents/100 by clicks
  // and rounds to 2dp). Read it rather than recomputing, so the app and the
  // web portal never disagree about the same number.
  const cpcDollars = Number(totals?.estimated_cpc || 0);

  let dailyBudgetCents = 0;
  let hasDailyBudget = false;
  for (const campaign of model.campaigns) {
    if (campaignPhase(campaign) !== "delivering") continue;
    const budget = campaignBudget(campaign);
    if (!budget || budget.type !== "daily") continue;
    dailyBudgetCents += budget.budgetCents;
    hasDailyBudget = true;
  }

  return {
    spendCents: Number(totals?.spend_cents || 0),
    clicks,
    impressions: Number(totals?.impressions || 0),
    cpcCents: clicks > 0 && cpcDollars > 0 ? Math.round(cpcDollars * 100) : null,
    dailyBudgetCents,
    hasDailyBudget
  };
}

export type CampaignBudgetType = "daily" | "lifetime";

export type CampaignBudget = {
  budgetCents: number;
  spentCents: number;
  /** 0..1, clamped. 0 when the budget is unknown. */
  fraction: number;
  /** At or past 90% of budget — the bar turns amber. */
  hot: boolean;
  type: CampaignBudgetType;
};

/**
 * Null when the campaign has no budget set. That is a real state on this
 * backend (a draft can exist with zero budget) and the card says "No budget
 * set" rather than drawing a 0% bar, which would imply a budget of nothing.
 */
export function campaignBudget(campaign?: AdCampaign): CampaignBudget | null {
  if (!campaign) return null;
  const type: CampaignBudgetType =
    String(campaign.budget_type || "daily").toLowerCase() === "lifetime" ? "lifetime" : "daily";
  const budgetCents = Number(
    (type === "lifetime" ? campaign.lifetime_budget_cents : campaign.daily_budget_cents) || 0
  );
  if (budgetCents <= 0) return null;
  const spentCents = Number(campaign.spent_cents || 0);
  const fraction = Math.max(0, Math.min(1, spentCents / budgetCents));
  return { budgetCents, spentCents, fraction, hot: fraction >= 0.9, type };
}

/**
 * Spend for one campaign, preferring the analytics row over the campaign
 * record. Both are server figures; the analytics row is the one the web portal
 * shows, so it wins when present and the campaign's own counter is the
 * fallback rather than a second opinion.
 */
export function campaignSpendCents(campaign: AdCampaign, analytics: AdAnalytics | null): number {
  const row = analytics?.campaigns.find((entry) => Number(entry.campaign_id) === Number(campaign.id));
  if (row) return Number(row.spent_cents || 0);
  return Number(campaign.spent_cents || 0);
}

/* ------------------------------------------------------------------ *
 * Campaign tabs
 * ------------------------------------------------------------------ */

export const CAMPAIGN_TAB_KEYS = ["active", "paused", "ended", "drafts"] as const;
export type CampaignTabKey = (typeof CAMPAIGN_TAB_KEYS)[number];

export function campaignTabLabel(key: CampaignTabKey): string {
  if (key === "active") return "Active";
  if (key === "paused") return "Paused";
  if (key === "ended") return "Ended";
  return "Drafts";
}

/**
 * `in_review` campaigns live under Active: the advertiser submitted them and is
 * waiting, and burying them in Drafts would suggest they still need work.
 */
export function campaignTabFor(campaign: AdCampaign): CampaignTabKey {
  const phase = campaignPhase(campaign);
  if (phase === "delivering" || phase === "learning" || phase === "limited" || phase === "in_review") {
    return "active";
  }
  if (phase === "paused") return "paused";
  if (phase === "ended") return "ended";
  return "drafts";
}

export function filterCampaigns(campaigns: AdCampaign[], tab: CampaignTabKey): AdCampaign[] {
  return campaigns.filter((campaign) => campaignTabFor(campaign) === tab);
}

export type CampaignTabSummary = {
  key: CampaignTabKey;
  label: string;
  count: number;
  /** True when a campaign in this tab is blocked by verification. */
  needsAttention: boolean;
};

export function campaignTabs(campaigns: AdCampaign[], account?: AdAccount | null): CampaignTabSummary[] {
  return CAMPAIGN_TAB_KEYS.map((key) => {
    const inTab = filterCampaigns(campaigns, key);
    return {
      key,
      label: campaignTabLabel(key),
      count: inTab.length,
      needsAttention: inTab.some((campaign) =>
        campaignBlockedByVerification(campaign, account || undefined)
      )
    };
  });
}

/**
 * The campaigns the verification banner speaks for. The banner only appears
 * when this is non-empty AND the account is unverified — an unverified account
 * with nothing to deliver is told about verification by the empty state's
 * invitation instead, so the user never reads the same warning twice.
 */
export function blockedCampaigns(campaigns: AdCampaign[], account?: AdAccount | null): AdCampaign[] {
  return campaigns.filter((campaign) => campaignBlockedByVerification(campaign, account || undefined));
}

/* ------------------------------------------------------------------ *
 * The delivery switch
 * ------------------------------------------------------------------ */

export type DeliverySwitchState = {
  /** False hides the switch entirely (a draft has nothing to pause). */
  show: boolean;
  /** Thumb position. */
  on: boolean;
  /** Rendered, announced, and inert — with `reason` explaining why. */
  disabled: boolean;
  /** Null when the switch works. Non-null text is shown next to it. */
  reason: string | null;
  /** The action to send when pressed. Null whenever `disabled`. */
  action: AdCampaignAction | null;
};

/**
 * The one place that decides whether a pause switch may be pressed.
 *
 * It is derived from `availableAdCampaignActions`, which mirrors the server's
 * own action table, so the switch is enabled exactly when the server would
 * accept the call. A switch that renders enabled and then no-ops — or worse,
 * fails silently — is forbidden by the brief; every disabled case here carries
 * a reason the card renders and a screen reader announces.
 */
export function deliverySwitchState(campaign: AdCampaign, account?: AdAccount | null): DeliverySwitchState {
  const phase = campaignPhase(campaign);
  const actions = availableAdCampaignActions(campaign);

  if (phase === "draft") {
    // Nothing to pause: a draft has never delivered. The card offers "Submit"
    // as an action instead of a switch that would mean nothing.
    return { show: false, on: false, disabled: true, reason: null, action: null };
  }

  if (phase === "ended") {
    return {
      show: true,
      on: false,
      disabled: true,
      reason: "This campaign has ended and can't be restarted. Duplicate it to run it again.",
      action: null
    };
  }

  if (phase === "in_review") {
    return {
      show: true,
      on: false,
      disabled: true,
      reason: "In review. You can pause it once it starts delivering.",
      action: null
    };
  }

  if (campaignBlockedByVerification(campaign, account || undefined)) {
    return {
      show: true,
      on: false,
      disabled: true,
      reason: "Verify your business to start delivery.",
      action: null
    };
  }

  const on = phase === "delivering" || phase === "learning" || phase === "limited";
  const wanted: AdCampaignAction = on ? "pause" : "resume";
  if (!actions.includes(wanted)) {
    return {
      show: true,
      on,
      disabled: true,
      reason: "This campaign can't be changed right now.",
      action: null
    };
  }

  return { show: true, on, disabled: false, reason: null, action: wanted };
}

/**
 * Whether a campaign's metrics describe live delivery. Spend and clicks are
 * never presented as moving for a campaign that is not delivering — a paused
 * campaign's numbers are historical and the card labels them so.
 */
export function campaignMetricsAreLive(campaign: AdCampaign): boolean {
  const phase = campaignPhase(campaign);
  return phase === "delivering" || phase === "learning" || phase === "limited";
}

/* ------------------------------------------------------------------ *
 * Chart axis
 * ------------------------------------------------------------------ */

/**
 * Weekday indices for the last `count` days ending today, oldest first. Indices
 * (not names) so the screen can name them through the locale's `weekdayNames`
 * rather than shipping English labels from the data layer.
 */
export function spendChartWeekdays(now: Date = new Date(), count = 7): number[] {
  const out: number[] = [];
  for (let i = count - 1; i >= 0; i -= 1) {
    const day = new Date(now.getTime());
    day.setDate(day.getDate() - i);
    out.push(day.getDay());
  }
  return out;
}

/** Index of "today" within the series `spendChartWeekdays` returns. */
export function spendChartTodayIndex(count = 7): number {
  return Math.max(0, count - 1);
}

/* ------------------------------------------------------------------ *
 * Post-ads KPIs — NOT backed, flag-gated preview
 * ------------------------------------------------------------------ */

export type PostKpis = {
  reach: number;
  newFollowers: number;
  engagements: number;
  /** Cost per follower in cents, or null when no followers were attributed. */
  costPerFollowerCents: number | null;
  mock: true;
};

/**
 * Null when the preview flag is off — the Post mode then explains the product
 * is coming rather than drawing three tiles of numbers nobody produced. When
 * the flag is on, reach is summed from the mock promotions so the tiles and the
 * cards below them agree, and the rest are fixtures tagged `mock`.
 */
export function loadMockPostKpis(): PostKpis | null {
  if (!adsPostModeEnabled()) return null;
  const promotions = loadMockPostPromotions();
  const reach = promotions.reduce((total, promotion) => total + Number(promotion.reach || 0), 0);
  const spendCents = promotions.reduce((total, promotion) => total + Number(promotion.spendCents || 0), 0);
  const newFollowers = 214;
  return {
    reach,
    newFollowers,
    engagements: 1863,
    costPerFollowerCents: newFollowers > 0 ? Math.round(spendCents / newFollowers) : null,
    mock: true
  };
}

/**
 * The promotion equivalent of `deliverySwitchState`. There is no backend to
 * accept these transitions yet, so `disabled` is true in every state and the
 * reason says so plainly — a preview switch that appeared to work would be the
 * exact "silently no-ops" failure the marketplace side is careful to avoid.
 */
export function promotionSwitchState(promotion: PostPromotion): DeliverySwitchState {
  const phase = promotion.phase;
  if (phase === "completed" || phase === "rejected") {
    return { show: false, on: false, disabled: true, reason: null, action: null };
  }
  if (phase === "submitted" || phase === "in_review") {
    return {
      show: true,
      on: false,
      disabled: true,
      reason: "In review. You'll be able to pause it once it starts.",
      action: null
    };
  }
  return {
    show: true,
    on: phase === "promoting",
    disabled: true,
    reason: "Preview — pausing a promotion isn't wired to a backend yet.",
    action: null
  };
}

/* ------------------------------------------------------------------ *
 * Promote-a-post rail — NOT backed, flag-gated preview
 * ------------------------------------------------------------------ */

export type RecentPost = {
  id: string;
  contentType: PromotedContentType;
  title: string;
  reach: number;
  /**
   * How far this post is outrunning the author's usual reach, e.g. 5 for "5×".
   * Null for an ordinary post. Only the flagged one wears the HOT badge.
   */
  hotMultiplier: number | null;
  mock: true;
};

export function loadMockRecentPosts(): RecentPost[] {
  if (!adsPostModeEnabled()) return [];
  return [
    { id: "mock-recent-1", contentType: "reel", title: "Sound check", reach: 31200, hotMultiplier: 5, mock: true },
    { id: "mock-recent-2", contentType: "post", title: "Rehearsal photos", reach: 6100, hotMultiplier: null, mock: true },
    { id: "mock-recent-3", contentType: "live", title: "Friday live", reach: 4400, hotMultiplier: null, mock: true },
    { id: "mock-recent-4", contentType: "post", title: "Merch restock", reach: 2900, hotMultiplier: null, mock: true }
  ];
}

/**
 * The rule the "5× HOT" badge follows, stated once so the report can quote it
 * and a real implementation can inherit it: a post is flagged when its reach is
 * at least `HOT_POST_MULTIPLE` times the author's median recent reach. Today the
 * multiplier arrives from the fixture; when an organic-performance feed exists,
 * this is where the comparison belongs.
 */
export const HOT_POST_MULTIPLE = 3;
