import { injectAds } from "../injectAds";
import { SponsoredAd } from "../../api/ads";

function ad(id: number): SponsoredAd {
  return {
    adId: id,
    creativeId: id,
    campaignId: id * 10,
    placementKey: "feed_inline",
    label: "Sponsored",
    creativeType: "text",
    title: `Ad ${id}`,
    body: "",
    mediaUrl: "",
    thumbnailUrl: "",
    playbackUrl: "",
    mediaType: "",
    width: 0,
    height: 0,
    durationSeconds: 0,
    destinationUrl: "/pulse/premium",
    callToAction: "Learn more",
    cardStyle: "signal-card",
    placementType: "feed",
    deliveryToken: "tok",
    trackingNonce: "nonce",
    expiresAt: "",
    reportable: true
  };
}

const posts = (count: number) => Array.from({ length: count }, (_, index) => ({ id: index + 1 }));

describe("injectAds", () => {
  it("returns only post rows when no ads are supplied", () => {
    const rows = injectAds(posts(6), []);
    expect(rows).toHaveLength(6);
    expect(rows.every((row) => row.type === "post")).toBe(true);
  });

  it("places the first ad after leadIn posts and repeats at the interval", () => {
    const rows = injectAds(posts(12), [ad(1), ad(2)], { interval: 5, leadIn: 3 });
    const types = rows.map((row) => row.type);
    // posts 1-3, ad, posts 4-8, ad, posts 9-12
    expect(types).toEqual([
      "post",
      "post",
      "post",
      "ad",
      "post",
      "post",
      "post",
      "post",
      "post",
      "ad",
      "post",
      "post",
      "post",
      "post"
    ]);
  });

  it("never emits more ads than were supplied", () => {
    const rows = injectAds(posts(40), [ad(1)], { interval: 4, leadIn: 4 });
    expect(rows.filter((row) => row.type === "ad")).toHaveLength(1);
  });

  it("does not inject ads into a feed shorter than the lead-in", () => {
    const rows = injectAds(posts(2), [ad(1)], { interval: 5, leadIn: 3 });
    expect(rows.filter((row) => row.type === "ad")).toHaveLength(0);
  });

  it("assigns stable, unique keys to post and ad rows", () => {
    const rows = injectAds(posts(6), [ad(7)], { interval: 5, leadIn: 3 });
    const keys = rows.map((row) => row.key);
    expect(new Set(keys).size).toBe(keys.length);
    expect(rows.find((row) => row.type === "ad")?.key).toBe("ad:70:7:0");
  });
});
