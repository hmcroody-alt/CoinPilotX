/**
 * The two environment signals the prefetch policy reads: how fast the list is
 * moving, and how good the network is.
 *
 * Both are deliberately crude. A scroll velocity in three bands and a network
 * in three tiers is enough to make the decisions that matter -- warm or do not
 * warm, one item ahead or three -- and a finer measurement would only produce a
 * policy that changes its mind more often without changing it more correctly.
 */

import type { NetworkTier } from "./mediaPrefetchQueue";
import type { ScrollDirection, ScrollVelocityBand } from "./mediaPrefetchPlanner";

/* -------------------------------------------------------------------------- */
/* VISIBILITY                                                                  */
/* -------------------------------------------------------------------------- */

/**
 * §6: a sliver of a cell at the edge of the screen is not "in view".
 *
 * 72% is the threshold both Reels and the feed already use for viewability, and
 * it is exported here so the number lives in one place -- an autoplay rule and
 * a viewability config that disagree produce a video that plays while its own
 * list does not consider it visible.
 */
export const VISIBLE_PERCENT_THRESHOLD = 72;

export function isSufficientlyVisible(visiblePercent: number) {
  return Number.isFinite(visiblePercent) && visiblePercent >= VISIBLE_PERCENT_THRESHOLD;
}

/* -------------------------------------------------------------------------- */
/* SCROLL VELOCITY                                                             */
/* -------------------------------------------------------------------------- */

/**
 * Pixels per millisecond. A deliberate fling on a phone runs 2-8 px/ms; a
 * reading scroll runs well under 1.
 */
export const VELOCITY_THRESHOLDS = Object.freeze({
  /** Below this the list is effectively still. */
  idle: 0.05,
  /** At or above this a scroll becomes a fling. */
  fastEnter: 2.0,
  /**
   * And stays one until it drops below this. The gap is what stops a scroll
   * hovering near the boundary from flipping the prefetch policy on every
   * frame, which would be worse than either policy applied consistently.
   */
  fastExit: 0.8
});

export type ScrollTracker = {
  offset: number;
  at: number;
  band: ScrollVelocityBand;
  direction: ScrollDirection;
};

export function createScrollTracker(offset = 0, at = 0): ScrollTracker {
  return { offset, at, band: "idle", direction: "idle" };
}

/**
 * Fold one scroll event into the tracker. Pure: the caller supplies the clock.
 *
 * Events closer together than a frame are ignored rather than divided by a
 * near-zero interval -- that division is how a stationary list reports a
 * velocity of several thousand px/ms and freezes the prefetcher in fling mode.
 */
export function trackScroll(previous: ScrollTracker, offset: number, at: number): ScrollTracker {
  const dt = at - previous.at;
  if (!Number.isFinite(offset) || !Number.isFinite(dt) || dt < 8) {
    return previous.at === 0 ? { ...previous, offset, at } : previous;
  }

  const delta = offset - previous.offset;
  const speed = Math.abs(delta) / dt;

  let band: ScrollVelocityBand;
  if (speed < VELOCITY_THRESHOLDS.idle) band = "idle";
  else if (previous.band === "fast") band = speed >= VELOCITY_THRESHOLDS.fastExit ? "fast" : "slow";
  else band = speed >= VELOCITY_THRESHOLDS.fastEnter ? "fast" : "slow";

  const direction: ScrollDirection =
    speed < VELOCITY_THRESHOLDS.idle ? previous.direction : delta >= 0 ? "forward" : "backward";

  return { offset, at, band, direction };
}

/* -------------------------------------------------------------------------- */
/* NETWORK                                                                     */
/* -------------------------------------------------------------------------- */

/**
 * Network quality inferred from how long our own media warms took.
 *
 * There is no NetInfo in this app and adding it would be a dependency change
 * against a watched manifest, so the estimate comes from the work already being
 * done. That is arguably the better signal anyway: a reachability API reports
 * that Wi-Fi is connected, not that the Wi-Fi is a hotel's.
 */
export const NETWORK_SAMPLE_SIZE = 8;

export const THROUGHPUT_THRESHOLDS = Object.freeze({
  /** Poster-sized warms completing under this are a healthy link. */
  goodMs: 400,
  /** Above this, assume the window should collapse. */
  weakMs: 1800
});

export type NetworkEstimator = {
  samples: number[];
  tier: NetworkTier;
};

export function createNetworkEstimator(tier: NetworkTier = "good"): NetworkEstimator {
  return { samples: [], tier };
}

/**
 * Median, not mean. One 9-second timeout in a window of eight otherwise fast
 * warms is an outlier, and a mean would let it drag the whole session down to
 * "weak" -- collapsing the prefetch window in response to a single stalled
 * request is exactly the overreaction hysteresis exists to prevent.
 */
export function observeWarmDuration(estimator: NetworkEstimator, durationMs: number): NetworkEstimator {
  if (!Number.isFinite(durationMs) || durationMs < 0) return estimator;
  const samples = [...estimator.samples, durationMs].slice(-NETWORK_SAMPLE_SIZE);
  if (samples.length < 3) return { samples, tier: estimator.tier };

  const sorted = [...samples].sort((a, b) => a - b);
  const median = sorted[Math.floor(sorted.length / 2)];

  let tier: NetworkTier;
  if (median <= THROUGHPUT_THRESHOLDS.goodMs) tier = "good";
  else if (median >= THROUGHPUT_THRESHOLDS.weakMs) tier = "weak";
  else tier = "fair";

  return { samples, tier };
}
