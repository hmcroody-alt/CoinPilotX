/**
 * What a Pulse Radio playback failure means, and what to do about it.
 *
 * Radio's old answer to every interruption was the same: status `error`, message
 * "This track could not be played." A three-second tunnel and a corrupt file
 * produced identical UI, and both of them threw the playback position away — so
 * a listener who lost signal for a moment came back to a stopped player and a
 * song that would restart from zero. That is the §84 failure: the radio did not
 * survive a temporary loss, it merely reported one.
 *
 * The decision is pure and lives here, apart from the player, because the
 * interesting part is the *classification*, not the timer. Four things change
 * the answer and none of them are visible from inside a playback callback:
 *
 *   - whether the bytes were coming off disk or off the network. A local file
 *     that fails is corrupt; waiting for connectivity would be a lie dressed as
 *     patience.
 *   - what the connectivity authority says. OFFLINE means "stop retrying and
 *     wait to be told", not "try harder" — retry loops against a dead radio are
 *     how a phone's battery disappears in a tunnel.
 *   - how many attempts this track has already cost. A stream that is genuinely
 *     broken must eventually be allowed to fail.
 *   - where playback had reached. Every branch carries the position forward.
 *     Nothing here is permitted to return zero for a track that was playing.
 */

import type { ConnectivityState } from "../connectivity";

/** How the failure arrived. */
export type RadioFailureReason =
  /** The player loaded but is starved of data — the classic dying stream. */
  | "stall"
  /** The source failed while loaded, mid-track. */
  | "load_failed"
  /** The source never loaded at all. */
  | "start_failed";

export type RadioRecoveryAction =
  /** Try the same track again after `delayMs`. */
  | "retry"
  /** Hold, keep the intent to play, and resume when connectivity returns. */
  | "await_network"
  /** This will not recover by itself. Surface it. */
  | "give_up";

export type RadioRecoveryInput = {
  reason: RadioFailureReason;
  connectivity: ConnectivityState;
  /** Recovery attempts already spent on this track, starting at 0. */
  attempt: number;
  /** Where playback had reached. Carried through every branch. */
  positionMillis: number;
  /** True when the failing source was a file on disk rather than a URL. */
  fromCache: boolean;
};

export type RadioRecoveryPlan = {
  action: RadioRecoveryAction;
  /** Wait before retrying. Always 0 for the non-retry actions. */
  delayMs: number;
  /** Where to resume. Never loses the listener's place. */
  resumeAtMillis: number;
  /** The status the player should show while this plan is in effect. */
  status: "buffering" | "offline" | "error";
  /**
   * True when the cached copy is the thing that failed and should be dropped so
   * the next attempt re-fetches instead of replaying the same broken bytes.
   */
  discardCachedCopy: boolean;
};

/** Beyond this, retrying is a loop rather than resilience. */
export const MAX_RADIO_RECOVERY_ATTEMPTS = 4;

const BASE_BACKOFF_MS = 500;
const MAX_BACKOFF_MS = 8000;

export function radioRecoveryPlan(input: RadioRecoveryInput): RadioRecoveryPlan {
  const resumeAtMillis = Math.max(0, Math.floor(input.positionMillis) || 0);

  // A file on disk does not stop being readable because a tunnel arrived. This
  // is a damaged or truncated cache entry, and the only useful response is to
  // stop trusting it. Retrying the same bytes would fail identically, forever.
  if (input.fromCache) {
    return { action: "give_up", delayMs: 0, resumeAtMillis, status: "error", discardCachedCopy: true };
  }

  // The connectivity authority already knows. Asking it beats inferring from an
  // error string, which is what this module replaced: `/reach|network|offline/`
  // over a message the platform is free to reword at any OS release.
  if (input.connectivity === "offline") {
    return { action: "await_network", delayMs: 0, resumeAtMillis, status: "offline", discardCachedCopy: false };
  }

  if (input.attempt >= MAX_RADIO_RECOVERY_ATTEMPTS) {
    return { action: "give_up", delayMs: 0, resumeAtMillis, status: "error", discardCachedCopy: false };
  }

  // RECOVERING means the authority has seen the network come back but has not
  // confirmed it yet. Retrying immediately into a half-open connection is how a
  // recovery burns an attempt for nothing, so it waits like any other retry.
  return {
    action: "retry",
    delayMs: backoffFor(input.attempt),
    resumeAtMillis,
    // The track has not ended and the listener has not stopped it. From their
    // side this is a gap in the audio, which is what buffering is.
    status: "buffering",
    discardCachedCopy: false
  };
}

/**
 * Whether a recovering player should pick up where it left off.
 *
 * Separate from the plan because it is asked at a different moment — when
 * connectivity returns, not when playback failed — and because "the listener
 * pressed pause while we were waiting" has to win over a pending resume.
 */
export function shouldResumeRadio(input: {
  userWantsPlayback: boolean;
  connectivity: ConnectivityState;
  awaitingNetwork: boolean;
}): boolean {
  if (!input.awaitingNetwork) return false;
  // Intent is authority. A resume that overrides an explicit pause is the app
  // starting music in someone's pocket.
  if (!input.userWantsPlayback) return false;
  return input.connectivity === "online" || input.connectivity === "degraded";
}

function backoffFor(attempt: number): number {
  return Math.min(MAX_BACKOFF_MS, BASE_BACKOFF_MS * 2 ** Math.max(0, attempt));
}
