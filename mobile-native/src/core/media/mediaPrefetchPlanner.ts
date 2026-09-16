/**
 * What to warm, at what priority, for a given position in a list.
 *
 * Pure. No fetching, no cache, no timers, no React. Given where the user is and
 * what the conditions are, it returns the set of warms that should exist right
 * now -- and, just as importantly, the set of keys that should still be alive,
 * so the caller can cancel everything else.
 *
 * THE SHAPE OF THE POLICY
 *
 * Distance from the active item decides priority; the surface decides how far
 * the window reaches and which rendition each band gets. That split is what
 * lets Reels, the feed, Statuses, Messenger and the profile grid share one
 * scheduler without one surface's appetite starving another's: a profile grid
 * asking for twelve thumbnails and Reels asking for one video are the same kind
 * of request at different priorities, and the queue orders them correctly
 * because they are in the same queue.
 *
 * WHY THE WINDOW IS SMALL
 *
 * The temptation is to warm deeply -- if two ahead is good, ten is better. It
 * is not. Every speculative fetch is a bet, and the hit rate falls off a cliff
 * past the second item because users stop, reverse, and leave. Ten-deep
 * prefetch mostly buys cache evictions and spent data, and on a slow connection
 * it buys them at the cost of the item actually on screen.
 */

import type { NetworkTier } from "./mediaPrefetchQueue";
import { isVideoMedia, mediaCacheKey, mediaIdentityOf, isMediaReady } from "./mediaIdentity";
import type { MediaDescriptor, MediaRendition } from "./mediaIdentity";
import { MEDIA_PRIORITY } from "./mediaPrefetchQueue";
import type { MediaPriority } from "./mediaPrefetchQueue";

export type MediaSurface = "reels" | "feed" | "status" | "messenger" | "grid";

export type ScrollVelocityBand = "idle" | "slow" | "fast";

export type ScrollDirection = "forward" | "backward" | "idle";

type SurfacePolicy = {
  /** Items to consider in the direction of travel. */
  ahead: number;
  /** Items to consider behind. Non-zero because users reverse. */
  behind: number;
  /** How many of the `ahead` items get the playable rendition, not just a poster. */
  aggressiveAhead: number;
  /** The rendition this surface actually paints at full size. */
  primary: MediaRendition;
  /** The cheap stand-in: what gets warmed for everything further out. */
  cheap: MediaRendition;
  /**
   * Whether this surface may warm video front matter at all. False for
   * Messenger and the profile grid: those show a still preview, so fetching a
   * manifest would be paying for a stream nobody asked to play (§26/§27).
   */
  warmVideo: boolean;
};

/**
 * Reels and Statuses are the same shape of problem -- a full-screen pager where
 * the next item is one gesture away -- so they get the same window. The feed
 * reaches further because its items are smaller and several are visible at
 * once. The grid reaches furthest and warms nothing but thumbnails.
 */
export const SURFACE_POLICIES: Readonly<Record<MediaSurface, SurfacePolicy>> = Object.freeze({
  reels: { ahead: 3, behind: 1, aggressiveAhead: 1, primary: "full", cheap: "poster", warmVideo: true },
  status: { ahead: 3, behind: 1, aggressiveAhead: 1, primary: "full", cheap: "poster", warmVideo: true },
  feed: { ahead: 4, behind: 1, aggressiveAhead: 2, primary: "feed", cheap: "thumb", warmVideo: true },
  messenger: { ahead: 4, behind: 2, aggressiveAhead: 0, primary: "thumb", cheap: "thumb", warmVideo: false },
  grid: { ahead: 12, behind: 6, aggressiveAhead: 0, primary: "thumb", cheap: "thumb", warmVideo: false }
});

/**
 * How much of the window survives the network.
 *
 * On a weak connection the window collapses to the item on screen and one
 * neighbour. Prefetching three items ahead over a bad link does not make them
 * arrive sooner; it makes the visible one arrive later.
 */
const DEPTH_SCALE: Readonly<Record<NetworkTier, number>> = Object.freeze({
  good: 1,
  fair: 0.6,
  weak: 0.25
});

export type PrefetchPlanInput = {
  surface: MediaSurface;
  /** Ordered exactly as the list renders them. */
  items: readonly (MediaDescriptor | null | undefined)[];
  activeIndex: number;
  direction?: ScrollDirection;
  velocity?: ScrollVelocityBand;
  networkTier?: NetworkTier;
  /** §19. The user asked us to stop guessing. */
  dataSaver?: boolean;
  /** Set false when the surface is blurred or backgrounded. */
  active?: boolean;
};

export type PlannedWarm = {
  key: string;
  index: number;
  priority: MediaPriority;
  rendition: MediaRendition;
  media: MediaDescriptor;
  /** True when this warm should fetch video front matter rather than an image. */
  video: boolean;
};

export type PrefetchPlan = {
  warm: PlannedWarm[];
  /** Cache keys the active item depends on; protected from eviction. */
  pin: string[];
  /** Every key this plan considers live. Anything else may be cancelled. */
  keep: Set<string>;
};

const EMPTY_PLAN: PrefetchPlan = { warm: [], pin: [], keep: new Set() };

/**
 * Priority purely as a function of signed distance from the active item.
 *
 * Forward distances are graded finely because that is where the user is going.
 * The single item behind gets P2 rather than P3 -- reverse swipes are common
 * enough in a pager that treating the previous item as "distant" produces a
 * visible reload on the one gesture users make when they missed something.
 */
export function priorityForDistance(distance: number, aggressiveAhead: number): MediaPriority | null {
  if (distance === 0) return MEDIA_PRIORITY.VISIBLE as MediaPriority;
  if (distance < 0) {
    return distance === -1 ? (MEDIA_PRIORITY.NEAR as MediaPriority) : (MEDIA_PRIORITY.DISTANT as MediaPriority);
  }
  if (distance <= aggressiveAhead) return MEDIA_PRIORITY.NEXT as MediaPriority;
  if (distance <= aggressiveAhead + 1) return MEDIA_PRIORITY.NEAR as MediaPriority;
  if (distance <= aggressiveAhead + 3) return MEDIA_PRIORITY.AHEAD as MediaPriority;
  return MEDIA_PRIORITY.DISTANT as MediaPriority;
}

export function planMediaPrefetch(input: PrefetchPlanInput): PrefetchPlan {
  const {
    surface,
    items,
    activeIndex,
    direction = "idle",
    velocity = "idle",
    networkTier = "good",
    dataSaver = false,
    active = true
  } = input;

  if (!active) return EMPTY_PLAN;
  if (!items.length) return EMPTY_PLAN;
  if (activeIndex < 0 || activeIndex >= items.length) return EMPTY_PLAN;

  const policy = SURFACE_POLICIES[surface];
  const scale = DEPTH_SCALE[networkTier];

  // A fling is not navigation. Everything the user is blurring past will be
  // wrong by the time it arrives, so during a fast scroll the window shrinks to
  // the item under the thumb and nothing is warmed above a poster (§24).
  const flinging = velocity === "fast";

  let ahead = Math.round(policy.ahead * scale);
  let behind = Math.round(policy.behind * scale);
  let aggressiveAhead = policy.aggressiveAhead;

  if (flinging) {
    ahead = Math.min(ahead, 1);
    behind = 0;
    aggressiveAhead = 0;
  }

  // Data Saver keeps the promise that nothing speculative happens. The active
  // item is still fetched -- it is not speculation, the user is looking at it.
  if (dataSaver) {
    ahead = 0;
    behind = 0;
    aggressiveAhead = 0;
  }

  const warm: PlannedWarm[] = [];
  const keep = new Set<string>();
  const pin: string[] = [];

  // §25: "ahead" means in the direction of travel, not toward higher indices.
  // Scrolling back up a feed should warm what is above the thumb; a planner
  // that always looked down would spend its whole budget on items the user has
  // already passed and rejected.
  const step = direction === "backward" ? -1 : 1;
  const from = Math.max(0, Math.min(items.length - 1, activeIndex - (step === 1 ? behind : ahead)));
  const to = Math.max(0, Math.min(items.length - 1, activeIndex + (step === 1 ? ahead : behind)));

  for (let index = from; index <= to; index += 1) {
    const media = items[index];
    if (!media) continue;
    const identity = mediaIdentityOf(media);
    if (!identity) continue;

    /** Positive = in the direction of travel. Negative = already passed. */
    const distance = (index - activeIndex) * step;
    const priority = priorityForDistance(distance, aggressiveAhead);
    if (priority === null) continue;

    const video = isVideoMedia(media);

    // A poster is warmed for every item in the window, including the active
    // one. It is the thing that removes the black box (§35) and it costs a
    // fraction of what the full asset does, so there is no band where skipping
    // it is the right trade.
    const posterKey = mediaCacheKey(identity, policy.cheap);
    keep.add(posterKey);
    warm.push({ key: posterKey, index, priority, rendition: policy.cheap, media, video: false });
    if (distance === 0) pin.push(posterKey);

    // Beyond the aggressive band, the poster is all there is. That is the §3
    // "next 3-5: metadata and poster only" rule, and it is what keeps a window
    // that reaches five items deep from costing five full assets.
    const playable = distance === 0 || (distance > 0 && distance <= aggressiveAhead);
    if (!playable || flinging || dataSaver) continue;

    // §56: a Mux asset that is still transcoding has a poster and no segments.
    // Warming its manifest is a guaranteed 404, so the poster above is the
    // whole plan for it.
    if (!isMediaReady(media)) continue;

    // §29. The attached track is warmed in the same band as the picture it
    // plays over, and only in that band: a reel three ahead has a poster and
    // nothing else, so fetching its music would be paying for a second stream
    // on a bet the poster rule already declined to make.
    //
    // It is keyed on its OWN identity, not on `identity#audio`. A trending
    // sound is attached to many reels, and keying it under each reel would
    // fetch the same bytes once per reel and then evict something still needed
    // to hold the duplicates.
    const attachedAudioUrl = String(media.attached_audio_url || "").trim();
    if (attachedAudioUrl) {
      const audioMedia: MediaDescriptor = { media_url: attachedAudioUrl, type: "audio" };
      const audioIdentity = mediaIdentityOf(audioMedia);
      if (audioIdentity) {
        const audioKey = mediaCacheKey(audioIdentity, "audio");
        keep.add(audioKey);
        warm.push({ key: audioKey, index, priority, rendition: "audio", media: audioMedia, video: false });
        // Pinned with the active reel's own media: the track is not a nice-to-
        // have for the reel on screen, it is half of its audio.
        if (distance === 0) pin.push(audioKey);
      }
    }

    if (video) {
      if (!policy.warmVideo) continue;
      const manifestKey = mediaCacheKey(identity, "manifest");
      keep.add(manifestKey);
      warm.push({ key: manifestKey, index, priority, rendition: "manifest", media, video: true });
      if (distance === 0) pin.push(manifestKey);
      continue;
    }

    const primaryKey = mediaCacheKey(identity, policy.primary);
    if (primaryKey === posterKey) continue;
    keep.add(primaryKey);
    warm.push({ key: primaryKey, index, priority, rendition: policy.primary, media, video: false });
    if (distance === 0) pin.push(primaryKey);
  }

  warm.sort((a, b) => a.priority - b.priority || Math.abs(a.index - activeIndex) - Math.abs(b.index - activeIndex));
  return { warm, pin, keep };
}
