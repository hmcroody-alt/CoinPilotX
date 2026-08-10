/**
 * Audiences carry two client-side guards the server does not provide, so they
 * are pinned here:
 *
 *   1. `audienceArchiveWarning` — the server archives an in-use audience
 *      without checking. This helper is the only thing standing between the
 *      advertiser and silently untargeting a delivering campaign, so its filter
 *      (live campaigns count, closed ones do not) must not drift.
 *   2. `eligibleLookalikeSeeds` — the lookalike endpoint rejects archived,
 *      lookalike-kind and sub-100-member seeds with a 400. Offering one anyway
 *      would be a picker full of options the server refuses.
 */

import {
  AD_LOOKALIKE_MIN_SEED,
  audienceArchiveWarning,
  audienceSizeBand,
  eligibleLookalikeSeeds,
  normalizeAdAudience,
  normalizeAdAudienceDetail
} from "../adsAudiences";
import type { AdAudience } from "../adsAudiences";

const audience = (overrides: Partial<AdAudience> = {}): AdAudience =>
  normalizeAdAudience({
    id: 4,
    account_id: 7,
    name: "Engaged 30d",
    kind: "custom",
    definition: { source: "engaged_with_content", window_days: 30 },
    estimated_size: 5400,
    archived: false,
    ...overrides
  });

describe("normalizeAdAudience", () => {
  it("coerces the SQLite 0/1 archived flag to a boolean", () => {
    expect(audience({ archived: 1 as never }).archived).toBe(true);
    expect(audience({ archived: 0 as never }).archived).toBe(false);
    expect(audience({ archived: true }).archived).toBe(true);
  });

  it("keeps the definition object and defaults a missing one to empty", () => {
    expect(audience().definition).toEqual({ source: "engaged_with_content", window_days: 30 });
    expect(normalizeAdAudience({ id: 1, definition: "junk" as never }).definition).toEqual({});
  });

  it("degrades a missing payload to safe defaults", () => {
    const bare = normalizeAdAudience(undefined);
    expect(bare.id).toBe(0);
    expect(bare.kind).toBe("saved");
    expect(bare.estimated_size).toBe(0);
  });
});

describe("audienceSizeBand", () => {
  it("mirrors the backend's thresholds: narrow < 1,000 < good < broad", () => {
    expect(audienceSizeBand(999)).toBe("narrow");
    expect(audienceSizeBand(1000)).toBe("good");
    expect(audienceSizeBand(5_000_000)).toBe("good");
    expect(audienceSizeBand(5_000_001)).toBe("broad");
  });
});

describe("normalizeAdAudienceDetail", () => {
  it("reads the server's band and falls back to good on junk", () => {
    const narrow = normalizeAdAudienceDetail({ id: 4, estimate: { estimated_size: 200, band: "narrow" } });
    expect(narrow.estimate).toEqual({ estimated_size: 200, band: "narrow" });
    const junk = normalizeAdAudienceDetail({ id: 4, estimate: { estimated_size: 5000, band: "weird" } });
    expect(junk.estimate.band).toBe("good");
  });

  it("keeps server warnings verbatim and drops empty ones", () => {
    const detail = normalizeAdAudienceDetail({
      id: 4,
      warnings: ["This audience is very narrow.", "", null]
    });
    expect(detail.warnings).toEqual(["This audience is very narrow."]);
  });

  it("keeps only campaign references with real ids", () => {
    const detail = normalizeAdAudienceDetail({
      id: 4,
      referenced_by_campaigns: [
        { campaign_id: 3, campaign_name: "Launch", status: "active", roles: ["included", ""] },
        { campaign_id: 0, campaign_name: "ghost" }
      ]
    });
    expect(detail.referenced_by_campaigns).toHaveLength(1);
    expect(detail.referenced_by_campaigns[0].roles).toEqual(["included"]);
  });
});

describe("audienceArchiveWarning", () => {
  it("warns about live campaigns and ignores closed ones", () => {
    const detail = normalizeAdAudienceDetail({
      id: 4,
      referenced_by_campaigns: [
        { campaign_id: 1, campaign_name: "Delivering", status: "active", roles: ["included"] },
        { campaign_id: 2, campaign_name: "Waiting", status: "pending_review", roles: ["included"] },
        { campaign_id: 3, campaign_name: "Done", status: "Completed", roles: ["included"] },
        { campaign_id: 4, campaign_name: "Gone", status: "archived", roles: ["excluded"] }
      ]
    });
    const warned = audienceArchiveWarning(detail);
    expect(warned.map((ref) => ref.campaign_id)).toEqual([1, 2]);
  });

  it("is empty when nothing live references the audience", () => {
    expect(audienceArchiveWarning(normalizeAdAudienceDetail({ id: 4 }))).toEqual([]);
  });
});

describe("eligibleLookalikeSeeds", () => {
  it("offers only seeds the server would accept", () => {
    const seeds = eligibleLookalikeSeeds([
      audience({ id: 1, estimated_size: AD_LOOKALIKE_MIN_SEED }),
      audience({ id: 2, estimated_size: AD_LOOKALIKE_MIN_SEED - 1 }),
      audience({ id: 3, archived: true }),
      audience({ id: 4, kind: "lookalike" })
    ]);
    expect(seeds.map((seed) => seed.id)).toEqual([1]);
  });
});
