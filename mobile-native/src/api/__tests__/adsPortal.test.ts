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

import { adFundingIsLive } from "../businessOs";
import {
  AD_WRITE_ROLES,
  AdReviewEntry,
  AdsPortal,
  accountRole,
  adWriteBlockedReason,
  canViewAdAnalytics,
  canWriteAds,
  getAdsPortal,
  loadAdsPortal,
  normalizeAdCreatives,
  normalizeAdPortalBilling,
  normalizeAdReviewBoard,
  normalizeAdsPortal,
  reviewIsHumanDecided,
  reviewOutcome,
  reviewReasonText
} from "../adsPortal";

beforeEach(() => {
  mockPulseApi.mockReset();
  mockReadJsonCache.mockReset();
  mockWriteJsonCache.mockReset();
  mockWriteJsonCache.mockResolvedValue(undefined);
});

function portalWithAccounts(accounts: { id: number; role: string }[]): AdsPortal {
  return normalizeAdsPortal({ accounts: accounts as never });
}

/* ------------------------------------------------------------------ *
 * Endpoint
 * ------------------------------------------------------------------ */

describe("portal endpoint", () => {
  it("reads the ungated legacy route, not the dark canonical one", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, portal: {} });
    await getAdsPortal();
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/ads/portal");
    // The Business OS surface 404s on every route until BUSINESS_OS_ADVERTISING
    // is set on the server. Nothing here may point at it.
    const path = String(mockPulseApi.mock.calls[0][0]);
    expect(path).not.toContain("business-os");
  });

  it("still resolves when the response carries no portal key", async () => {
    mockPulseApi.mockResolvedValue({ ok: true });
    const { portal } = await getAdsPortal();
    expect(portal.accounts).toEqual([]);
    expect(portal.degraded).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 * Degradation
 * ------------------------------------------------------------------ *
 * A portal outage must cost the five new sections and nothing the app
 * could already do.
 */

describe("fallback to the five-call fan-out", () => {
  it("degrades rather than throwing, and says so", async () => {
    mockPulseApi.mockImplementation((path: string) => {
      if (path === "/api/pulse/ads/portal") return Promise.reject(new Error("500"));
      if (path === "/api/pulse/ads/accounts") {
        return Promise.resolve({ ok: true, accounts: [{ id: 8, business_name: "Acme" }] });
      }
      if (path === "/api/pulse/ads/campaigns") {
        return Promise.resolve({ ok: true, campaigns: [{ id: 3, campaign_name: "Spring" }] });
      }
      if (path === "/api/pulse/ads/analytics") return Promise.resolve({ ok: true, analytics: {} });
      return Promise.resolve({ ok: true, wallet: {}, billing: {} });
    });

    const portal = await loadAdsPortal();
    expect(portal.degraded).toBe(true);
    expect(portal.accounts).toHaveLength(1);
    expect(portal.campaigns).toHaveLength(1);
  });

  it("keeps the campaign list when the wallet call fails", async () => {
    mockPulseApi.mockImplementation((path: string) => {
      if (path === "/api/pulse/ads/portal") return Promise.reject(new Error("500"));
      if (path === "/api/pulse/ads/accounts") {
        return Promise.resolve({ ok: true, accounts: [{ id: 8, business_name: "Acme" }] });
      }
      if (path === "/api/pulse/ads/campaigns") {
        return Promise.resolve({ ok: true, campaigns: [{ id: 3, campaign_name: "Spring" }] });
      }
      if (path === "/api/pulse/ads/analytics") return Promise.resolve({ ok: true, analytics: {} });
      return Promise.reject(new Error("503"));
    });

    const portal = await loadAdsPortal();
    expect(portal.campaigns).toHaveLength(1);
    expect(portal.wallets).toEqual([]);
  });

  it("survives every call failing", async () => {
    mockPulseApi.mockRejectedValue(new Error("offline"));
    const portal = await loadAdsPortal();
    expect(portal.degraded).toBe(true);
    expect(portal.accounts).toEqual([]);
    expect(portal.review_board).toEqual([]);
  });

  it("marks a successful portal load as not degraded", async () => {
    mockPulseApi.mockResolvedValue({ ok: true, portal: { accounts: [] } });
    const portal = await loadAdsPortal();
    expect(portal.degraded).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 * Billing key mapping
 * ------------------------------------------------------------------ */

describe("billing key mapping", () => {
  it("maps the portal's `enabled` onto `billing_enabled`", () => {
    const billing = normalizeAdPortalBilling({ enabled: true, live_charging: true });
    expect(billing.billing_enabled).toBe(true);
    // The whole point: adFundingIsLive reads billing_enabled and would have
    // answered false forever on an unmapped portal block.
    expect(adFundingIsLive(billing)).toBe(true);
  });

  it("does not invent funding when the server says charging is off", () => {
    const billing = normalizeAdPortalBilling({ enabled: true, live_charging: false });
    expect(adFundingIsLive(billing)).toBe(false);
  });

  it("prefers an explicit billing_enabled over enabled", () => {
    const billing = normalizeAdPortalBilling({ billing_enabled: false, enabled: true });
    expect(billing.billing_enabled).toBe(false);
  });

  it("treats a missing block as off", () => {
    expect(normalizeAdPortalBilling(null).billing_enabled).toBe(false);
  });
});

/* ------------------------------------------------------------------ *
 * Permissions
 * ------------------------------------------------------------------ */

describe("permissions", () => {
  it("excludes analyst and viewer from the write roles", () => {
    expect(AD_WRITE_ROLES).not.toContain("analyst");
    expect(AD_WRITE_ROLES).not.toContain("viewer");
  });

  it("reads the per-account role rather than the roles.current rollup", () => {
    // The rollup says "owner" because this person owns account 1. The server
    // re-derives per account and answers 403 on account 2.
    const portal = normalizeAdsPortal({
      accounts: [
        { id: 1, role: "owner" },
        { id: 2, role: "viewer" }
      ] as never,
      roles: { current: "owner", allowed: ["owner"] }
    });
    expect(accountRole(portal, 1)).toBe("owner");
    expect(accountRole(portal, 2)).toBe("viewer");
    expect(canWriteAds(portal, 1)).toBe(true);
    expect(canWriteAds(portal, 2)).toBe(false);
  });

  it("refuses writes for analyst and viewer", () => {
    const portal = portalWithAccounts([
      { id: 1, role: "analyst" },
      { id: 2, role: "viewer" }
    ]);
    expect(canWriteAds(portal, 1)).toBe(false);
    expect(canWriteAds(portal, 2)).toBe(false);
  });

  it("lets analyst read reports but not viewer", () => {
    const portal = portalWithAccounts([
      { id: 1, role: "analyst" },
      { id: 2, role: "viewer" }
    ]);
    expect(canViewAdAnalytics(portal, 1)).toBe(true);
    expect(canViewAdAnalytics(portal, 2)).toBe(false);
  });

  it("assumes the least privilege for an unknown account", () => {
    const portal = portalWithAccounts([{ id: 1, role: "owner" }]);
    expect(accountRole(portal, 999)).toBe("viewer");
    expect(canWriteAds(portal, 999)).toBe(false);
    expect(canWriteAds(null, 1)).toBe(false);
  });

  it("gives a blocked reader a reason and a route out, not silence", () => {
    const portal = portalWithAccounts([
      { id: 1, role: "owner" },
      { id: 2, role: "analyst" },
      { id: 3, role: "viewer" }
    ]);
    expect(adWriteBlockedReason(portal, 1)).toBeNull();
    expect(adWriteBlockedReason(portal, 2)).toContain("analyst");
    // §37: no dead end. A viewer must learn who can change this.
    expect(adWriteBlockedReason(portal, 3)).toContain("owner");
  });
});

/* ------------------------------------------------------------------ *
 * Review board
 * ------------------------------------------------------------------ */

describe("review board", () => {
  const rejected: AdReviewEntry = {
    review_id: 1,
    moderation_status: "rejected",
    automated_review_status: "flagged",
    human_review_status: "upheld",
    review_reason: "Prohibited health claim."
  };

  it("keeps the automated and human verdicts separate", () => {
    expect(reviewIsHumanDecided(rejected)).toBe(true);
    expect(reviewIsHumanDecided({ review_id: 2, automated_review_status: "flagged" })).toBe(false);
    expect(reviewIsHumanDecided({ review_id: 3, human_review_status: "pending" })).toBe(false);
  });

  it("reads the outcome as one word", () => {
    expect(reviewOutcome(rejected)).toBe("rejected");
    expect(reviewOutcome({ review_id: 2, moderation_status: "approved" })).toBe("approved");
    expect(reviewOutcome({ review_id: 3, moderation_status: "blocked" })).toBe("rejected");
    expect(reviewOutcome({ review_id: 4 })).toBe("pending");
  });

  it("never leaves a rejection without a readable reason", () => {
    expect(reviewReasonText(rejected)).toBe("Prohibited health claim.");
    // §37 forbids an inaccessible policy reason; a blank line is inaccessible.
    const bare = reviewReasonText({ review_id: 5, moderation_status: "rejected" });
    expect(bare.length).toBeGreaterThan(0);
    const whitespaceOnly = reviewReasonText({
      review_id: 6,
      moderation_status: "rejected",
      review_reason: "   "
    });
    expect(whitespaceOnly.length).toBeGreaterThan(0);
  });

  it("falls back to rejection_reason when review_reason is empty", () => {
    expect(
      reviewReasonText({ review_id: 7, moderation_status: "rejected", rejection_reason: "Bad link." })
    ).toBe("Bad link.");
  });

  it("says nothing about a decision that has not been made", () => {
    expect(reviewReasonText({ review_id: 8 })).toBe("");
  });

  it("drops rows with no review id", () => {
    expect(normalizeAdReviewBoard([{ review_id: 0 }, { review_id: 4 }])).toHaveLength(1);
    expect(normalizeAdReviewBoard(undefined)).toEqual([]);
  });
});

/* ------------------------------------------------------------------ *
 * Normalisation
 * ------------------------------------------------------------------ */

describe("normalisation", () => {
  it("reads the server's derived creative state instead of recomputing it", () => {
    const [creative] = normalizeAdCreatives([
      { id: 9, media_ready: true, destination_safe: false, performance_state: "learning" }
    ]);
    expect(creative.media_ready).toBe(true);
    expect(creative.destination_safe).toBe(false);
    expect(creative.performance_state).toBe("learning");
  });

  it("names an untitled creative rather than rendering an empty row", () => {
    expect(normalizeAdCreatives([{ id: 9 }])[0].title).toBe("Untitled creative");
  });

  it("survives a portal payload of the wrong shape entirely", () => {
    const portal = normalizeAdsPortal({
      accounts: null as never,
      creatives: "nope" as never,
      placements: 7 as never,
      campaign_status_counts: null as never
    });
    expect(portal.accounts).toEqual([]);
    expect(portal.creatives).toEqual([]);
    expect(portal.placements).toEqual({});
    expect(portal.campaign_status_counts).toEqual({});
    expect(portal.roles.current).toBe("none");
  });

  it("keeps placement keys and names addressable", () => {
    const portal = normalizeAdsPortal({
      placements: { feed_primary: { display_name: "Feed" } } as never
    });
    expect(portal.placements.feed_primary.placement_key).toBe("feed_primary");
    expect(portal.placements.feed_primary.display_name).toBe("Feed");
    expect(portal.placements.feed_primary.supported_creative_types).toEqual([]);
  });

  it("coerces status counts to numbers", () => {
    const portal = normalizeAdsPortal({
      campaign_status_counts: { active: "3", draft: null } as never
    });
    expect(portal.campaign_status_counts).toEqual({ active: 3, draft: 0 });
  });
});
