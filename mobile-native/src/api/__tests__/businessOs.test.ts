const mockPulseApi = jest.fn();
const mockReadJsonCache = jest.fn();
const mockWriteJsonCache = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

jest.mock("../../core/cache", () => ({
  readJsonCache: (...args: unknown[]) => mockReadJsonCache(...args),
  writeJsonCache: (...args: unknown[]) => mockWriteJsonCache(...args)
}));

import {
  AD_CAMPAIGN_ACTIONS,
  AD_DAILY_BUDGET_MAX_CENTS,
  AD_LIFETIME_BUDGET_MAX_CENTS,
  activeBusinessOsSections,
  adAccountCanTransact,
  adFundingIsLive,
  availableAdCampaignActions,
  BUSINESS_OS_SECTIONS,
  businessOsHubSections,
  businessOsNavigationArgs,
  businessOsSection,
  clampCampaignBudgets,
  createAdAccount,
  createAdCampaign,
  formatCampaignBudget,
  formatCents,
  formatObjective,
  getAdAnalytics,
  getAdBillingSummary,
  getAdCampaign,
  getAdWallet,
  listAdAccounts,
  listAdCampaigns,
  normalizeAdAnalytics,
  normalizeAdCampaign,
  runAdCampaignAction,
  updateAdCampaign
} from "../businessOs";

beforeEach(() => {
  mockPulseApi.mockReset();
  mockReadJsonCache.mockReset();
  mockWriteJsonCache.mockReset();
  mockWriteJsonCache.mockResolvedValue(undefined);
});

/* ------------------------------------------------------------------ *
 * Endpoint paths — these must match the live Flask routes exactly.
 * ------------------------------------------------------------------ */

describe("Business OS advertising endpoints", () => {
  it("lists ad accounts from /api/pulse/ads/accounts", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, accounts: [] });
    await listAdAccounts();
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/ads/accounts");
  });

  it("creates an ad account with a POST body", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, account: { id: 3, business_name: "Roody Co" } });
    const result = await createAdAccount({ business_name: "Roody Co", business_email: "a@b.co" });
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/ads/accounts", {
      method: "POST",
      body: JSON.stringify({ business_name: "Roody Co", business_email: "a@b.co" })
    });
    expect(result.account?.id).toBe(3);
  });

  it("lists campaigns from /api/pulse/ads/campaigns", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, campaigns: [] });
    await listAdCampaigns();
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/ads/campaigns");
  });

  it("reads a single campaign by id", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, campaign: { id: 12 } });
    await getAdCampaign(12);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/ads/campaigns/12");
  });

  it("patches a campaign rather than re-posting it", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, campaign: { id: 12 } });
    await updateAdCampaign(12, { campaign_name: "Renamed" });
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/ads/campaigns/12", {
      method: "PATCH",
      body: JSON.stringify({ campaign_name: "Renamed" })
    });
  });

  it("posts lifecycle actions to the action route", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, campaign_id: 12, status: "paused" });
    await runAdCampaignAction(12, "pause");
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/ads/campaigns/12/action", {
      method: "POST",
      body: JSON.stringify({ action: "pause" })
    });
  });

  it("requests analytics without a filter when no account is given", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, analytics: { totals: {}, campaigns: [] } });
    await getAdAnalytics();
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/ads/analytics");
  });

  it("scopes analytics to an account when one is given", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, analytics: { totals: {}, campaigns: [] } });
    await getAdAnalytics({ accountId: 7 });
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/ads/analytics?account_id=7");
  });

  it("reads the wallet and billing summary from the account-scoped routes", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, wallet: {} });
    await getAdWallet(7);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/ads/accounts/7/wallet");

    mockPulseApi.mockResolvedValue({ ok: true, billing: {} });
    await getAdBillingSummary(7);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/ads/accounts/7/billing-summary");
  });

  it("never targets the dark /api/business-os/* surface", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, accounts: [], campaigns: [], analytics: { totals: {}, campaigns: [] } });
    await listAdAccounts();
    await listAdCampaigns();
    await getAdAnalytics();
    mockPulseApi.mock.calls.forEach(([path]) => {
      expect(String(path)).not.toContain("/api/business-os/");
    });
  });
});

/* ------------------------------------------------------------------ *
 * Parsing
 * ------------------------------------------------------------------ */

describe("normalizeAdCampaign", () => {
  it("coerces the documented field shapes and fills safe defaults", () => {
    const campaign = normalizeAdCampaign({
      id: "12" as unknown as number,
      ad_account_id: "7" as unknown as number,
      daily_budget_cents: "5000" as unknown as number,
      spent_cents: null as unknown as number
    });
    expect(campaign.id).toBe(12);
    expect(campaign.ad_account_id).toBe(7);
    expect(campaign.daily_budget_cents).toBe(5000);
    expect(campaign.spent_cents).toBe(0);
    expect(campaign.campaign_name).toBe("Untitled campaign");
    expect(campaign.objective).toBe("awareness");
    expect(campaign.status).toBe("draft");
    expect(campaign.budget_type).toBe("daily");
    expect(campaign.placements).toEqual([]);
  });
});

describe("normalizeAdAnalytics", () => {
  it("drops the LEFT JOIN row an account with no campaigns produces", () => {
    const analytics = normalizeAdAnalytics({
      totals: { impressions: 10, clicks: 1, spend_cents: 500 },
      campaigns: [
        { account_id: 7, business_name: "Roody Co", campaign_id: null as unknown as number },
        { account_id: 7, campaign_id: 12, campaign_name: "Spring", impressions: 10, clicks: 1 }
      ]
    });
    expect(analytics.campaigns).toHaveLength(1);
    expect(analytics.campaigns[0].campaign_id).toBe(12);
  });

  it("returns zeroed totals for an empty or missing payload", () => {
    const analytics = normalizeAdAnalytics(undefined);
    expect(analytics.campaigns).toEqual([]);
    expect(analytics.totals.impressions).toBe(0);
    expect(analytics.totals.spend_cents).toBe(0);
    expect(analytics.totals.ctr).toBe(0);
  });
});

/* ------------------------------------------------------------------ *
 * Server-contract mirroring
 * ------------------------------------------------------------------ */

describe("budget clamping", () => {
  it("clamps to the same ceilings the backend enforces", () => {
    const clamped = clampCampaignBudgets({
      ad_account_id: 7,
      campaign_name: "Big",
      daily_budget_cents: 99_000_000,
      lifetime_budget_cents: 999_000_000
    });
    expect(clamped.daily_budget_cents).toBe(AD_DAILY_BUDGET_MAX_CENTS);
    expect(clamped.lifetime_budget_cents).toBe(AD_LIFETIME_BUDGET_MAX_CENTS);
  });

  it("floors negatives and fractions to zero-safe integers", () => {
    const clamped = clampCampaignBudgets({ daily_budget_cents: -5, lifetime_budget_cents: 1234.7 });
    expect(clamped.daily_budget_cents).toBe(0);
    expect(clamped.lifetime_budget_cents).toBe(1234);
  });

  it("leaves untouched fields absent so PATCH stays partial", () => {
    const clamped = clampCampaignBudgets({ campaign_name: "Renamed" });
    expect(clamped).toEqual({ campaign_name: "Renamed" });
    expect("daily_budget_cents" in clamped).toBe(false);
  });

  it("applies clamping on the wire for create", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, campaign: { id: 1 } });
    await createAdCampaign({ ad_account_id: 7, campaign_name: "Big", daily_budget_cents: 99_000_000 });
    const [, options] = mockPulseApi.mock.calls[0];
    expect(JSON.parse(String((options as RequestInit).body))).toEqual({
      ad_account_id: 7,
      campaign_name: "Big",
      daily_budget_cents: AD_DAILY_BUDGET_MAX_CENTS
    });
  });
});

describe("availableAdCampaignActions", () => {
  it("only ever offers actions the backend accepts", () => {
    const statuses = ["draft", "pending_review", "active", "paused", "archived", "completed"];
    statuses.forEach((status) => {
      availableAdCampaignActions({ id: 1, status }).forEach((action) => {
        expect(AD_CAMPAIGN_ACTIONS).toContain(action);
      });
    });
  });

  it("offers pause for an active campaign and resume for a paused one", () => {
    expect(availableAdCampaignActions({ id: 1, status: "active" })).toContain("pause");
    expect(availableAdCampaignActions({ id: 1, status: "active" })).not.toContain("resume");
    expect(availableAdCampaignActions({ id: 1, status: "paused" })).toContain("resume");
    expect(availableAdCampaignActions({ id: 1, status: "paused" })).not.toContain("pause");
  });

  it("never offers a terminal campaign anything but duplicate", () => {
    expect(availableAdCampaignActions({ id: 1, status: "archived" })).toEqual(["duplicate"]);
    expect(availableAdCampaignActions({ id: 1, status: "completed" })).toEqual(["duplicate"]);
  });
});

/* ------------------------------------------------------------------ *
 * Guards against nonfunctional controls
 * ------------------------------------------------------------------ */

describe("capability guards", () => {
  it("only treats an active account as able to transact", () => {
    expect(adAccountCanTransact({ id: 1, status: "active" })).toBe(true);
    expect(adAccountCanTransact({ id: 1, status: "pending_verification" })).toBe(false);
    expect(adAccountCanTransact({ id: 1, status: "suspended" })).toBe(false);
    expect(adAccountCanTransact(undefined)).toBe(false);
  });

  it("treats funding as live only when the server says both flags are on", () => {
    expect(adFundingIsLive({ billing_enabled: true, live_charging: true })).toBe(true);
    expect(adFundingIsLive({ billing_enabled: true, live_charging: false })).toBe(false);
    expect(adFundingIsLive({ billing_enabled: false, live_charging: true })).toBe(false);
    expect(adFundingIsLive(undefined)).toBe(false);
  });

  it("exposes only sections that are backed and routable", () => {
    activeBusinessOsSections().forEach((section) => {
      expect(section.backed).toBe(true);
      expect(section.route).toBeTruthy();
    });
  });

  it("omits the hub itself from the hub grid", () => {
    expect(businessOsHubSections().map((section) => section.key)).not.toContain("dashboard");
  });

  it("routes tab sections through the Tabs navigator and stack sections directly", () => {
    const messages = businessOsSection("messages")!;
    expect(businessOsNavigationArgs(messages)).toEqual(["Tabs", { screen: "Messenger", params: undefined }]);

    const store = businessOsSection("store")!;
    expect(businessOsNavigationArgs(store)).toEqual(["SellerStore", { mode: "dashboard" }]);
  });

  it("refuses to produce navigation args for an unrouted section", () => {
    const customers = businessOsSection("customers")!;
    expect(() => businessOsNavigationArgs(customers)).toThrow(/no route/);
  });

  it("keeps every mission section present in the registry", () => {
    const keys = BUSINESS_OS_SECTIONS.map((section) => section.key);
    [
      "dashboard",
      "profile",
      "store",
      "marketplace",
      "advertising",
      "orders",
      "customers",
      "messages",
      "insights",
      "payments",
      "events",
      "team",
      "verification",
      "settings"
    ].forEach((key) => expect(keys).toContain(key));
  });

  it("does not route sections that have no live contract", () => {
    const unbacked = BUSINESS_OS_SECTIONS.filter((section) => !section.backed).map((section) => section.key);
    expect(unbacked).toEqual(["customers", "team"]);
  });
});

/* ------------------------------------------------------------------ *
 * Formatting
 * ------------------------------------------------------------------ */

describe("formatting", () => {
  it("renders cents as currency", () => {
    expect(formatCents(123456)).toContain("1,234.56");
    expect(formatCents(0)).toContain("0.00");
  });

  it("labels the budget by its type and says so when unset", () => {
    expect(formatCampaignBudget({ id: 1, budget_type: "daily", daily_budget_cents: 5000 })).toContain("per day");
    expect(formatCampaignBudget({ id: 1, budget_type: "lifetime", lifetime_budget_cents: 5000 })).toContain("lifetime");
    expect(formatCampaignBudget({ id: 1, budget_type: "daily", daily_budget_cents: 0 })).toBe("No budget set");
  });

  it("humanizes snake_case objectives", () => {
    expect(formatObjective("marketplace_sales")).toBe("Marketplace Sales");
    expect(formatObjective(undefined)).toBe("Awareness");
  });
});
