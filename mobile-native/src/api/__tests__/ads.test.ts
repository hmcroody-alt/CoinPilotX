const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import {
  fetchSponsoredAds,
  isAdExpired,
  recordAdClick,
  recordAdEvent,
  recordAdImpression,
  recordAdViewability
} from "../ads";

const rawAd = {
  ad_id: 5,
  creative_id: 5,
  campaign_id: 9,
  placement_key: "feed_inline",
  label: "Sponsored",
  creative_type: "image",
  title: "Buy widgets",
  body: "Best widgets",
  media_url: "https://cdn.example/w.png",
  thumbnail_url: "https://cdn.example/t.png",
  destination_url: "/pulse/premium",
  call_to_action: "Shop now",
  card_style: "signal-card",
  placement_type: "feed",
  delivery_token: "tok.sig",
  tracking_nonce: "nonce123",
  expires_at: "",
  reportable: true
};

describe("fetchSponsoredAds", () => {
  beforeEach(() => mockPulseApi.mockReset());

  it("requests mobile placements and maps the payload", async () => {
    mockPulseApi.mockResolvedValueOnce({ ok: true, ads: [rawAd] });
    const ads = await fetchSponsoredAds({ context: "home", feedContext: "for_you", limit: 3 });
    const [path] = mockPulseApi.mock.calls[0];
    expect(path).toContain("/api/pulse/ads/placements?");
    expect(path).toContain("device_type=mobile");
    expect(path).toContain("context=home");
    expect(path).toContain("feed_context=for_you");
    expect(ads).toHaveLength(1);
    expect(ads[0]).toMatchObject({
      creativeId: 5,
      campaignId: 9,
      placementKey: "feed_inline",
      callToAction: "Shop now",
      deliveryToken: "tok.sig",
      trackingNonce: "nonce123"
    });
  });

  it("drops ads missing identifiers or already expired", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      ads: [
        { ...rawAd, creative_id: 0 },
        { ...rawAd, expires_at: "2000-01-01T00:00:00Z" },
        rawAd
      ]
    });
    const ads = await fetchSponsoredAds();
    expect(ads).toHaveLength(1);
    expect(ads[0].creativeId).toBe(5);
  });

  it("returns an empty list when the request fails", async () => {
    mockPulseApi.mockRejectedValueOnce(new Error("down"));
    expect(await fetchSponsoredAds()).toEqual([]);
  });

  it("returns an empty list when the backend reports not ok", async () => {
    mockPulseApi.mockResolvedValueOnce({ ok: false });
    expect(await fetchSponsoredAds()).toEqual([]);
  });
});

describe("isAdExpired", () => {
  it("treats a past timestamp as expired and blank as valid", () => {
    expect(isAdExpired({ expiresAt: "2000-01-01T00:00:00Z" })).toBe(true);
    expect(isAdExpired({ expiresAt: "" })).toBe(false);
    expect(isAdExpired({ expiresAt: "not-a-date" })).toBe(false);
  });
});

describe("ad tracking", () => {
  beforeEach(() => mockPulseApi.mockReset());

  const identity = {
    creativeId: 5,
    campaignId: 9,
    placementKey: "feed_inline",
    deliveryToken: "tok.sig",
    trackingNonce: "nonce123"
  };

  it("records an impression and returns the impression id", async () => {
    mockPulseApi.mockResolvedValueOnce({ ok: true, impression_id: 77 });
    const id = await recordAdImpression(identity, { viewport: "390x844" });
    expect(id).toBe(77);
    const [path, options] = mockPulseApi.mock.calls[0];
    expect(path).toBe("/api/pulse/ads/impression");
    expect(JSON.parse((options as { body: string }).body)).toEqual({
      creative_id: 5,
      campaign_id: 9,
      placement_key: "feed_inline",
      delivery_token: "tok.sig",
      tracking_nonce: "nonce123",
      viewport: "390x844"
    });
  });

  it("reports viewability only when an impression id exists", async () => {
    expect(await recordAdViewability(0, 1500)).toBe(false);
    expect(mockPulseApi).not.toHaveBeenCalled();
    mockPulseApi.mockResolvedValueOnce({ ok: true, viewable: true });
    expect(await recordAdViewability(77, 1500)).toBe(true);
  });

  it("routes clicks through the server and returns the canonical destination", async () => {
    mockPulseApi.mockResolvedValueOnce({ ok: true, destination_url: "/pulse/premium" });
    expect(await recordAdClick(identity)).toBe("/pulse/premium");
  });

  it("returns empty destination when the click request fails", async () => {
    mockPulseApi.mockRejectedValueOnce(new Error("boom"));
    expect(await recordAdClick(identity)).toBe("");
  });

  it("sends the event type and reason for hide/report events", async () => {
    mockPulseApi.mockResolvedValueOnce({ ok: true });
    await recordAdEvent(identity, "report", "inappropriate");
    const [path, options] = mockPulseApi.mock.calls[0];
    expect(path).toBe("/api/pulse/ads/event");
    expect(JSON.parse((options as { body: string }).body)).toMatchObject({
      event_type: "report",
      reason: "inappropriate",
      creative_id: 5
    });
  });
});
