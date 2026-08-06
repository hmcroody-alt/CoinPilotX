/**
 * The Advertising screen's load path, after it moved onto the portal.
 *
 * Two properties matter enough to pin:
 *
 *   1. The single request must actually replace the fan-out, or the change is
 *      five calls plus one rather than one instead of five.
 *   2. A portal failure must cost the five new sections and nothing else. The
 *      old path is still there and still has to work, and the model must say
 *      `portal: null` rather than presenting an empty review board as "none" —
 *      §31 distinguishes `Unavailable` from a real zero.
 */

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

import { loadAdsMarketplace, loadAdsMarketplaceFanOut } from "../adsDashboard";

beforeEach(() => {
  mockPulseApi.mockReset();
  mockReadJsonCache.mockReset();
  mockReadJsonCache.mockResolvedValue(null);
  mockWriteJsonCache.mockReset();
  mockWriteJsonCache.mockResolvedValue(undefined);
});

const ACCOUNT = {
  id: 8,
  business_name: "Acme",
  status: "active",
  verification_status: "verified"
};

function portalPayload(overrides: Record<string, unknown> = {}) {
  return {
    ok: true,
    portal: {
      accounts: [{ ...ACCOUNT, role: "owner" }],
      campaigns: [{ id: 3, campaign_name: "Spring", status: "active" }],
      creatives: [{ id: 11, title: "Hero" }],
      wallets: [{ account_id: 8, spendable_balance_cents: 2500, currency: "USD" }],
      analytics: { totals: { spend_cents: 1000, clicks: 4, impressions: 900 } },
      review_board: [{ review_id: 2, moderation_status: "rejected" }],
      notifications: [{ id: 5, status: "unread" }],
      billing: { enabled: false, live_charging: false },
      roles: { current: "owner", allowed: ["owner"] },
      ...overrides
    }
  };
}

/* ------------------------------------------------------------------ *
 * One request, not five
 * ------------------------------------------------------------------ */

describe("the portal path", () => {
  it("loads the whole screen from a single request", async () => {
    mockPulseApi.mockResolvedValue(portalPayload());
    const model = await loadAdsMarketplace();

    expect(mockPulseApi).toHaveBeenCalledTimes(1);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/ads/portal");
    expect(model.accounts).toHaveLength(1);
    expect(model.campaigns).toHaveLength(1);
    expect(model.offline).toBe(false);
    expect(model.accountsStatus).toBe("ok");
  });

  it("carries the five sections the fan-out could never see", async () => {
    mockPulseApi.mockResolvedValue(portalPayload());
    const model = await loadAdsMarketplace();

    expect(model.portal).not.toBeNull();
    expect(model.portal?.review_board).toHaveLength(1);
    expect(model.portal?.creatives).toHaveLength(1);
    expect(model.portal?.notifications).toHaveLength(1);
    expect(model.portal?.roles.current).toBe("owner");
  });

  it("reads the wallet balance the server computed", async () => {
    mockPulseApi.mockResolvedValue(portalPayload());
    const model = await loadAdsMarketplace();
    expect(model.wallet?.balanceCents).toBe(2500);
    expect(model.wallet?.accountId).toBe(8);
    // Billing is off on the server, so the funding control must not claim to charge.
    expect(model.wallet?.fundingLive).toBe(false);
  });

  it("matches the wallet by account, not by position", async () => {
    // Wallets arriving out of order is the failure that shows one advertiser
    // another's balance. Matching on account_id is what prevents it.
    mockPulseApi.mockResolvedValue(
      portalPayload({
        accounts: [
          { ...ACCOUNT, id: 8, role: "owner" },
          { ...ACCOUNT, id: 9, business_name: "Beta", role: "owner" }
        ],
        wallets: [
          { account_id: 9, spendable_balance_cents: 99900 },
          { account_id: 8, spendable_balance_cents: 2500 }
        ]
      })
    );
    const model = await loadAdsMarketplace();
    expect(model.primaryAccount?.id).toBe(8);
    expect(model.wallet?.balanceCents).toBe(2500);
  });

  it("shows no wallet rather than a wrong one when the account has none", async () => {
    mockPulseApi.mockResolvedValue(portalPayload({ wallets: [] }));
    const model = await loadAdsMarketplace();
    expect(model.wallet).toBeNull();
  });
});

/* ------------------------------------------------------------------ *
 * Analytics scope
 * ------------------------------------------------------------------ */

describe("analytics scope", () => {
  it("uses the portal's figure when there is only one account", async () => {
    mockPulseApi.mockResolvedValue(portalPayload());
    const model = await loadAdsMarketplace();
    expect(mockPulseApi).toHaveBeenCalledTimes(1);
    expect(model.analytics?.totals?.clicks).toBe(4);
    expect(model.analyticsStatus).toBe("ok");
  });

  it("re-scopes to the primary account when there is more than one", async () => {
    // portal.analytics sums every account this person can see. The KPI row is
    // about one of them.
    mockPulseApi.mockImplementation((path: string) => {
      if (path === "/api/pulse/ads/portal") {
        return Promise.resolve(
          portalPayload({
            accounts: [
              { ...ACCOUNT, id: 8, role: "owner" },
              { ...ACCOUNT, id: 9, role: "owner" }
            ]
          })
        );
      }
      return Promise.resolve({ ok: true, analytics: { totals: { clicks: 1 } } });
    });

    const model = await loadAdsMarketplace();
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/ads/analytics?account_id=8");
    expect(model.analytics?.totals?.clicks).toBe(1);
  });

  it("keeps the wider figure but flags it when re-scoping fails", async () => {
    mockPulseApi.mockImplementation((path: string) => {
      if (path === "/api/pulse/ads/portal") {
        return Promise.resolve(
          portalPayload({
            accounts: [
              { ...ACCOUNT, id: 8, role: "owner" },
              { ...ACCOUNT, id: 9, role: "owner" }
            ]
          })
        );
      }
      return Promise.reject(new Error("500"));
    });

    const model = await loadAdsMarketplace();
    expect(model.analytics?.totals?.clicks).toBe(4);
    expect(model.analyticsStatus).toBe("error");
  });
});

/* ------------------------------------------------------------------ *
 * Fallback
 * ------------------------------------------------------------------ */

describe("the fallback path", () => {
  function fanOutResponses(path: string) {
    if (path === "/api/pulse/ads/accounts") {
      return Promise.resolve({ ok: true, accounts: [ACCOUNT] });
    }
    if (path === "/api/pulse/ads/campaigns") {
      return Promise.resolve({ ok: true, campaigns: [{ id: 3, campaign_name: "Spring" }] });
    }
    if (path.startsWith("/api/pulse/ads/analytics")) {
      return Promise.resolve({ ok: true, analytics: { totals: { clicks: 4 } } });
    }
    if (path.endsWith("/wallet")) {
      return Promise.resolve({ ok: true, wallet: { spendable_balance_cents: 700 } });
    }
    return Promise.resolve({ ok: true, billing: { billing_enabled: false } });
  }

  it("falls back to the five calls when the portal fails", async () => {
    mockPulseApi.mockImplementation((path: string) => {
      if (path === "/api/pulse/ads/portal") return Promise.reject(new Error("500"));
      return fanOutResponses(path);
    });

    const model = await loadAdsMarketplace();
    expect(model.accounts).toHaveLength(1);
    expect(model.campaigns).toHaveLength(1);
    expect(model.wallet?.balanceCents).toBe(700);
    expect(model.offline).toBe(false);
  });

  it("reports the new sections as absent, not as empty", async () => {
    mockPulseApi.mockImplementation((path: string) => {
      if (path === "/api/pulse/ads/portal") return Promise.reject(new Error("500"));
      return fanOutResponses(path);
    });

    const model = await loadAdsMarketplace();
    // Null is the honest answer: nobody asked. An empty review_board here would
    // mean "no policy decisions exist", which is a claim this path cannot make.
    expect(model.portal).toBeNull();
  });

  it("still signals offline when nothing reaches the network", async () => {
    mockPulseApi.mockRejectedValue(new Error("offline"));
    const model = await loadAdsMarketplace();
    expect(model.offline).toBe(true);
    expect(model.accountsStatus).toBe("error");
    expect(model.portal).toBeNull();
  });

  it("keeps the old loader callable on its own", async () => {
    mockPulseApi.mockImplementation((path: string) => fanOutResponses(path));
    const model = await loadAdsMarketplaceFanOut();
    expect(mockPulseApi).not.toHaveBeenCalledWith("/api/pulse/ads/portal");
    expect(model.accounts).toHaveLength(1);
  });
});
