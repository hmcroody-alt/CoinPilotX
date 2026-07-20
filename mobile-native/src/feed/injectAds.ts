import { SponsoredAd } from "../api/ads";

export type FeedRow<TPost> =
  | { type: "post"; key: string; post: TPost }
  | { type: "ad"; key: string; ad: SponsoredAd };

type InjectOptions = {
  // Number of organic posts between sponsored slots. The first ad appears after
  // this many posts, matching the web feed_inline cadence.
  interval?: number;
  // Do not place an ad before at least this many organic posts have rendered.
  leadIn?: number;
};

// Interleaves sponsored ads into an organic post list at a fixed cadence.
// Ads are consumed in order and never duplicated; hidden ads should be removed
// by the caller before calling this. Pure + deterministic for unit testing.
export function injectAds<TPost extends { id: number | string }>(
  posts: TPost[],
  ads: SponsoredAd[],
  options: InjectOptions = {}
): FeedRow<TPost>[] {
  const interval = Math.max(options.interval ?? 4, 1);
  const leadIn = Math.max(options.leadIn ?? interval, 1);
  const rows: FeedRow<TPost>[] = [];
  let adIndex = 0;

  posts.forEach((post, position) => {
    rows.push({ type: "post", key: `post:${post.id}`, post });
    const organicCount = position + 1;
    const eligible = organicCount >= leadIn && (organicCount - leadIn) % interval === 0;
    if (eligible && adIndex < ads.length) {
      const ad = ads[adIndex];
      rows.push({ type: "ad", key: `ad:${ad.campaignId}:${ad.creativeId}:${adIndex}`, ad });
      adIndex += 1;
    }
  });

  return rows;
}
