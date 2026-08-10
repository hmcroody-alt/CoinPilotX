/**
 * The creative library's risky mapping is `editable`: metadata edits reset
 * moderation to draft server-side, so a screen that offered Edit off a guessed
 * flag would either 409 or silently pull an approved ad out of review. The
 * verdict must come from the server and default to false. The moderation
 * history merge (two tables, two column names for the same idea) is the other
 * mapping worth pinning.
 */

import {
  normalizeAdLibraryAssetDetail,
  normalizeAdLibraryFilter,
  normalizeAdLibraryItem
} from "../adsLibrary";

describe("normalizeAdLibraryFilter", () => {
  it("accepts only the four buckets and falls back to all", () => {
    expect(normalizeAdLibraryFilter("videos")).toBe("videos");
    expect(normalizeAdLibraryFilter("IMAGES")).toBe("images");
    expect(normalizeAdLibraryFilter("reels")).toBe("all");
    expect(normalizeAdLibraryFilter(null)).toBe("all");
  });
});

describe("normalizeAdLibraryItem", () => {
  it("coerces the SQLite 0/1 media_ready flag", () => {
    expect(normalizeAdLibraryItem({ id: 1, media_ready: 1 }).media_ready).toBe(true);
    expect(normalizeAdLibraryItem({ id: 1, media_ready: 0 }).media_ready).toBe(false);
    expect(normalizeAdLibraryItem({ id: 1, media_ready: true }).media_ready).toBe(true);
  });

  it("nulls the campaign reference when there is no real campaign id", () => {
    expect(normalizeAdLibraryItem({ id: 1, campaign: { campaign_id: 0 } }).campaign).toBeNull();
    const linked = normalizeAdLibraryItem({
      id: 1,
      campaign: { campaign_id: 3, campaign_name: "Launch", campaign_status: "active", adset_id: 0 }
    });
    expect(linked.campaign).toEqual({
      campaign_id: 3,
      campaign_name: "Launch",
      campaign_status: "active",
      adset_id: null
    });
  });

  it("clamps performance figures non-negative and keeps ctr as a fraction", () => {
    const item = normalizeAdLibraryItem({
      id: 1,
      performance: { impressions: 4000, clicks: 50, ctr: 0.0125 }
    });
    expect(item.performance).toEqual({ impressions: 4000, clicks: 50, ctr: 0.0125 });
    const junk = normalizeAdLibraryItem({ id: 1, performance: { impressions: -5, ctr: -1 } });
    expect(junk.performance).toEqual({ impressions: 0, clicks: 0, ctr: 0 });
  });

  it("keeps policy flags with their server text", () => {
    const item = normalizeAdLibraryItem({
      id: 1,
      policy_flags: [{ flag_type: "text_overlay", severity: "warning", details: "Too much text" }]
    });
    expect(item.policy_flags[0].details).toBe("Too much text");
  });

  it("degrades an empty payload to draft defaults", () => {
    const bare = normalizeAdLibraryItem(undefined);
    expect(bare.id).toBe(0);
    expect(bare.status).toBe("draft");
    expect(bare.moderation_status).toBe("draft");
    expect(bare.bucket).toBe("posts");
  });
});

describe("normalizeAdLibraryAssetDetail", () => {
  it("takes editable only from an explicit server true", () => {
    // Guessing true would offer an Edit that 409s — or worse, one that pulls an
    // approved creative back to draft without the warning the flow depends on.
    expect(normalizeAdLibraryAssetDetail({ id: 1, editable: true }).editable).toBe(true);
    expect(normalizeAdLibraryAssetDetail({ id: 1, editable: 1 }).editable).toBe(false);
    expect(normalizeAdLibraryAssetDetail({ id: 1 }).editable).toBe(false);
  });

  it("merges the two history sources' differently named columns", () => {
    const detail = normalizeAdLibraryAssetDetail({
      id: 1,
      moderation_history: [
        { source: "moderation_queue", status: "rejected", notes: "Overlay text", created_at: "a" },
        { source: "review_board", review_status: "approved", review_reason: "Fine now", created_at: "b" }
      ]
    });
    expect(detail.moderation_history).toEqual([
      { source: "moderation_queue", status: "rejected", notes: "Overlay text", created_at: "a" },
      { source: "review_board", status: "approved", notes: "Fine now", created_at: "b" }
    ]);
  });

  it("carries the six copy fields through verbatim", () => {
    const detail = normalizeAdLibraryAssetDetail({
      id: 1,
      body: "b",
      headline: "h",
      primary_text: "p",
      call_to_action: "Shop now",
      destination_url: "https://example.com"
    });
    expect(detail.headline).toBe("h");
    expect(detail.call_to_action).toBe("Shop now");
    expect(detail.destination_url).toBe("https://example.com");
  });
});
