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
  accountAccess,
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
  placementCatalogue,
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

  it("gives a blocked reader a reason, not silence", () => {
    const portal = portalWithAccounts([
      { id: 1, role: "owner" },
      { id: 2, role: "analyst" },
      { id: 3, role: "viewer" }
    ]);
    expect(adWriteBlockedReason(portal, 1)).toBeNull();
    expect(adWriteBlockedReason(portal, 2)).toContain("analyst");
    expect(adWriteBlockedReason(portal, 3)).toContain("read-only");
  });

  it("never promises a role change, because no route performs one", () => {
    /*
     * The old viewer copy read "An account owner can change your role." Nothing
     * in the product writes `pulse_ad_team_members` — bot.py creates and indexes
     * it, pulse_advertiser_portal reads it twice, and there is no INSERT and no
     * invite route anywhere — so that sentence sent the reader to pursue a
     * remedy that does not exist. §37 forbids exactly that.
     */
    const portal = portalWithAccounts([
      { id: 2, role: "analyst" },
      { id: 3, role: "viewer" }
    ]);
    for (const id of [2, 3]) {
      const reason = String(adWriteBlockedReason(portal, id));
      expect(reason).not.toMatch(/change your role/i);
      expect(reason).not.toMatch(/ask an owner|account owner can/i);
      expect(reason).not.toMatch(/invite/i);
    }
  });

  describe("what a blocked control is actually reporting", () => {
    /*
     * `accountRole` collapses three situations into "viewer": the portal never
     * loaded, the portal loaded without this account, and a real viewer role.
     * Only the third is a permission. Telling the first two that they lack
     * access is the absence-as-evidence error at its most costly — it sends
     * someone to request a grant when the honest instruction is "try again".
     */
    const listed = portalWithAccounts([{ id: 1, role: "viewer" }]);

    it("separates the three states", () => {
      expect(accountAccess(null, 1)).toEqual({ state: "unknown", role: null });
      expect(accountAccess(listed, 999)).toEqual({ state: "unlisted", role: null });
      expect(accountAccess(listed, 1)).toEqual({ state: "granted", role: "viewer" });
    });

    it("honours a real role on the degraded path but not a missing one", () => {
      /*
       * The degraded fan-out is the old `listAdAccounts` call, and
       * `normalizeAdAccount` passes the row through without defaulting `role`.
       * So a degraded payload can carry a real role — which should be believed
       * — or an account row with no role at all, which must not silently become
       * "viewer". Keying on `degraded` alone gets one of these two wrong.
       */
      const withRole = { ...portalWithAccounts([{ id: 1, role: "owner" }]), degraded: true };
      expect(accountAccess(withRole, 1)).toEqual({ state: "granted", role: "owner" });
      expect(canWriteAds(withRole, 1)).toBe(true);

      const withoutRole = { ...portalWithAccounts([{ id: 1 } as never]), degraded: true };
      expect(accountAccess(withoutRole, 1).state).toBe("unknown");
      // The gate still fails closed; only the explanation changes.
      expect(canWriteAds(withoutRole, 1)).toBe(false);
      expect(String(adWriteBlockedReason(withoutRole, 1))).toMatch(/couldn’t be loaded/);
    });

    it("won't call an account unlisted on a payload that may be incomplete", () => {
      const degraded = { ...portalWithAccounts([{ id: 1, role: "owner" }]), degraded: true };
      // Account 999 is missing, but on the fan-out path that may just be the
      // call that failed — absence only means something if the list is whole.
      expect(accountAccess(degraded, 999).state).toBe("unknown");
      expect(accountAccess(portalWithAccounts([{ id: 1, role: "owner" }]), 999).state).toBe("unlisted");
    });

    it("does not let the normaliser invent the role in the first place", () => {
      /*
       * The root of all of the above. `normalizeAdPortalAccounts` used to write
       * `role: String(raw.role || "viewer")`, so an account row that arrived
       * with no role left the normaliser carrying one — and by the time any
       * reader saw it, a value the client made up was indistinguishable from a
       * value the server sent. Every gate still fails closed, but it does so in
       * `accountRole`, where the default is a decision rather than a forgery.
       */
      const silent = normalizeAdsPortal({ accounts: [{ id: 1 }] as never });
      expect(silent.accounts[0].role).toBe("");
      expect(accountRole(silent, 1)).toBe("viewer");
      expect(canWriteAds(silent, 1)).toBe(false);
      expect(accountAccess(silent, 1).state).toBe("unknown");

      const stated = normalizeAdsPortal({ accounts: [{ id: 1, role: "owner" }] as never });
      expect(stated.accounts[0].role).toBe("owner");
    });

    it("does not accept a role the server never named", () => {
      const bogus = portalWithAccounts([{ id: 1, role: "superadmin" } as never]);
      expect(accountAccess(bogus, 1).state).toBe("unknown");
      expect(canWriteAds(bogus, 1)).toBe(false);
    });

    it("blames the request, not the reader, when nothing loaded", () => {
      const reason = String(adWriteBlockedReason(null, 1));
      expect(reason).toMatch(/couldn’t be loaded/);
      expect(reason).toMatch(/try again/i);
      // The one thing it must never say is that this is about their permissions.
      expect(reason).not.toMatch(/read-only|analyst|viewer/i);
    });

    it("says an unlisted account is absent rather than restricted", () => {
      const reason = String(adWriteBlockedReason(listed, 999));
      expect(reason).toMatch(/isn’t on your portal/);
      expect(reason).not.toMatch(/read-only|analyst/i);
    });
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

/**
 * The catalogue exists because the app had been telling advertisers their ads
 * ran in "Feed" and "Reels". `seed_placements` writes twelve rows and Reels is
 * not among them, so the list was simultaneously too short and partly invented.
 * Everything here is about not repeating that: read the server's list, present
 * only what the row actually carries, and return nothing rather than a guess.
 */
describe("placementCatalogue", () => {
  it("reads every placement the portal ships, ordered stably by name", () => {
    const portal = normalizeAdsPortal({
      placements: {
        status_interstitial: {
          display_name: "Status interstitial",
          device_type: "mobile",
          max_frequency: 3
        },
        feed_inline: { display_name: "Feed inline signal", device_type: "all", max_frequency: 6 },
        marketplace_sponsor: {
          display_name: "Marketplace sponsor",
          device_type: "all",
          max_frequency: 5
        }
      } as never
    });
    // Sorted by name, not by the dict order the server happened to send: a
    // catalogue that reshuffles between fetches looks like it is changing.
    expect(placementCatalogue(portal).map((entry) => entry.name)).toEqual([
      "Feed inline signal",
      "Marketplace sponsor",
      "Status interstitial"
    ]);
  });

  it("puts the device constraint into words, because select_ads enforces it in SQL", () => {
    const portal = normalizeAdsPortal({
      placements: {
        a: { display_name: "A", device_type: "mobile" },
        b: { display_name: "B", device_type: "desktop" },
        c: { display_name: "C", device_type: "all" }
      } as never
    });
    expect(placementCatalogue(portal).map((entry) => entry.devices)).toEqual([
      "Mobile only",
      "Desktop only",
      "Every device"
    ]);
  });

  /**
   * A row with no `device_type` is not a row with no devices. The server's
   * default is `all`, and inventing a narrower constraint would tell an
   * advertiser their ad reaches fewer people than it does.
   */
  it("treats a missing device_type as every device rather than as unknown", () => {
    const portal = normalizeAdsPortal({ placements: { a: { display_name: "A" } } as never });
    expect(placementCatalogue(portal)[0].devices).toBe("Every device");
  });

  /**
   * `0` means the row carried no cap. It must not be rendered as "at most 0
   * views", which reads as a placement that shows nothing — the screen checks
   * for falsy and omits the clause.
   */
  it("reports a missing or unusable frequency cap as zero rather than guessing one", () => {
    const portal = normalizeAdsPortal({
      placements: {
        a: { display_name: "A" },
        b: { display_name: "B", max_frequency: "not a number" },
        c: { display_name: "C", max_frequency: 4 }
      } as never
    });
    expect(placementCatalogue(portal).map((entry) => entry.maxFrequency)).toEqual([0, 0, 4]);
  });

  it("falls back to the dict key when the row carries no name", () => {
    const portal = normalizeAdsPortal({ placements: { pulse_radio_sponsor: {} } as never });
    expect(placementCatalogue(portal)[0]).toMatchObject({
      key: "pulse_radio_sponsor",
      name: "pulse_radio_sponsor"
    });
  });

  /**
   * The screen distinguishes an empty catalogue from a loaded one and calls the
   * first `Unavailable`, so this returning `[]` is the whole contract for a
   * portal that never arrived. It must never substitute a default list.
   */
  it("returns nothing for a portal that isn't there", () => {
    expect(placementCatalogue(null)).toEqual([]);
    expect(placementCatalogue(undefined)).toEqual([]);
    expect(placementCatalogue({ placements: null } as never)).toEqual([]);
  });
});
