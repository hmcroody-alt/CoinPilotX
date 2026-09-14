/**
 * Whether a given item may play right now, and whether its sound is on.
 *
 * A pure predicate, separated from the players deliberately. Every surface had
 * grown its own version of this condition -- Reels checks focus and app state,
 * the feed checks focus and a motion preference, Statuses check something else
 * again -- and the divergence is why one surface could keep playing through a
 * route change that silenced another. One predicate, five call sites.
 *
 * WHAT THIS DOES NOT DO
 *
 * It does not claim the audio session, does not touch a player, and does not
 * know what a player is. Ownership of playback is already arbitrated by
 * mediaPlaybackCoordinator and the audio session is already configured by the
 * six modules allowed to configure it. This answers a question; the caller
 * decides what to do with the answer.
 */

import { isSufficientlyVisible } from "./mediaViewportSignals";
import type { ScrollVelocityBand } from "./mediaPrefetchPlanner";

export type AutoplayRefusal =
  | "not_visible"
  | "not_active_item"
  | "route_blurred"
  | "app_backgrounded"
  | "overlay_open"
  | "media_not_ready"
  | "scrolling_fast"
  | "reduce_motion"
  | "surface_disallows_autoplay";

export type AutoplayInput = {
  /** 0-100. The share of the cell inside the viewport. */
  visiblePercent: number;
  /** Whether the list has designated this the one active cell. */
  isActiveItem: boolean;
  /** The screen owns the current route. */
  routeFocused: boolean;
  /** AppState is "active". */
  appActive: boolean;
  /** A comment sheet, share sheet, or modal is covering the media. */
  overlayOpen?: boolean;
  /** isMediaReady(media) -- a still-transcoding asset has nothing to play. */
  mediaReady: boolean;
  velocity?: ScrollVelocityBand;
  /** OS "Reduce Motion", or the in-app equivalent. */
  reduceMotion?: boolean;
  /**
   * §26/§27. Messenger previews and profile grids never autoplay regardless of
   * how visible they are; that is a product decision, not a condition.
   */
  surfaceAllowsAutoplay?: boolean;
  /** The user's explicit mute toggle for this surface, if they set one. */
  userMuted?: boolean;
};

export type AutoplayDecision =
  | { play: true; muted: boolean }
  | { play: false; muted: boolean; reason: AutoplayRefusal };

/**
 * §7/§49: sound is on unless the user turned it off.
 *
 * This is the one default in the whole foundation that is a product position
 * rather than an engineering one, so it is a named constant instead of an
 * inline `false`: a silent change from a later refactor should have to edit
 * something that says what it is.
 */
export const AUTOPLAY_STARTS_UNMUTED = true;

export function shouldAutoplay(input: AutoplayInput): AutoplayDecision {
  const muted = input.userMuted ?? !AUTOPLAY_STARTS_UNMUTED;

  // Ordered cheapest-and-most-decisive first, so the refusal reason that comes
  // back is the one a reader would consider the real cause.
  if (input.surfaceAllowsAutoplay === false) return { play: false, muted, reason: "surface_disallows_autoplay" };
  if (!input.appActive) return { play: false, muted, reason: "app_backgrounded" };
  if (!input.routeFocused) return { play: false, muted, reason: "route_blurred" };
  if (input.overlayOpen) return { play: false, muted, reason: "overlay_open" };
  if (!input.isActiveItem) return { play: false, muted, reason: "not_active_item" };
  if (!isSufficientlyVisible(input.visiblePercent)) return { play: false, muted, reason: "not_visible" };
  if (!input.mediaReady) return { play: false, muted, reason: "media_not_ready" };

  // §24. Starting playback on every cell that flies past during a fling is the
  // failure this refuses: a burst of players, a burst of audio sessions, and
  // nothing the user could have watched.
  if (input.velocity === "fast") return { play: false, muted, reason: "scrolling_fast" };

  if (input.reduceMotion) return { play: false, muted, reason: "reduce_motion" };

  return { play: true, muted };
}

/**
 * §23: warming is not playing.
 *
 * The distinction is easy to lose in a refactor -- both "prepare" and "play"
 * end up calling into the same player object -- and losing it means every
 * prefetched item starts producing audio off screen. Exists so a test can pin
 * it: this returns true in cases where shouldAutoplay returns false.
 */
export function mayPrefetchWithoutPlaying(input: Pick<AutoplayInput, "appActive" | "routeFocused">) {
  return input.appActive && input.routeFocused;
}
