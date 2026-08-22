/**
 * The preview loop: the first five seconds, repeating, for as long as the card
 * is the active one.
 *
 * ## Why a state machine and not `if (position >= 5000) seek(0)`
 *
 * The naive form has a bug that only appears on a real device. A seek is not
 * instantaneous: between issuing `playFromPositionAsync(0)` and the player
 * actually reporting a position near zero, more status callbacks arrive, and
 * every one of them still reads a position past the window. Each fires another
 * seek. The visible result is a preview that judders at the loop point instead
 * of wrapping cleanly — and it is worse on slower networks, which is exactly
 * where nobody tests.
 *
 * So the rewind is latched. One seek is issued per crossing, and no further
 * seek is issued until the player confirms it came back inside the window. That
 * confirmation is the only thing that clears the latch, which means a seek that
 * silently fails leaves the latch set and the loop simply runs on to the end of
 * the clip rather than machine-gunning seeks at the player.
 *
 * ## Why this is not a timer
 *
 * A `setInterval` that seeks every five seconds drifts against the actual
 * playback clock — it keeps counting while the video is buffering, so a stalled
 * clip gets rewound before it has shown five seconds of anything. Position is
 * the only honest clock here, and the player already reports it.
 *
 * ## Why there is no longer a replay memory
 *
 * A previous revision remembered completed previews and suppressed replays
 * inside a cooldown, because a preview that played once and froze must not
 * restart on viewability jitter. That reasoning does not survive the change to
 * a continuous loop: the loop restarts from zero every five seconds anyway, so
 * a jitter-induced restart is indistinguishable from an ordinary wrap. Keeping
 * the memory would only mean a card the user deliberately scrolled back to
 * refuses to play.
 */

/**
 * The loop window. Playback never advances past this point; it returns to zero.
 *
 * Looping the *first* five seconds specifically is also what makes the loop
 * cheap: the segment is already in the player's buffer from the pass just
 * finished, so the wrap is a seek into buffered data rather than a refetch.
 */
export const DISCOVERY_PREVIEW_LOOP_WINDOW_MS = 5000;

/**
 * How often the player reports its position.
 *
 * This is the loop's resolution, and therefore its worst-case overshoot: the
 * rewind can only be issued on a callback, so playback may reach the window
 * plus one interval before turning over. 125ms is under a typical four frames
 * and is not perceptible as "played past five seconds"; the default (500ms)
 * would be. It costs nothing extra, because the handler touches refs only and
 * never sets React state — see `DiscoveryPreviewMedia`.
 */
export const DISCOVERY_PREVIEW_PROGRESS_INTERVAL_MS = 125;

/** Whether a rewind has been issued and not yet confirmed by the player. */
export type PreviewLoopState = { rewinding: boolean };

/** What the caller should do with the player as a result of this position report. */
export type PreviewLoopAction = "none" | "rewind";

export function initialPreviewLoopState(): PreviewLoopState {
  return { rewinding: false };
}

/**
 * Fold one position report into the loop.
 *
 * Pure, so the awkward cases — a report that arrives mid-seek, a clip shorter
 * than the window, a position that goes backwards on its own — are testable
 * without a player, a device, or a five-second wait.
 */
export function advancePreviewLoop(
  state: PreviewLoopState,
  positionMillis: number
): { state: PreviewLoopState; action: PreviewLoopAction } {
  // A player that has not reported a usable position yet says nothing about
  // whether we have reached the window.
  if (!Number.isFinite(positionMillis) || positionMillis < 0) {
    return { state, action: "none" };
  }

  if (state.rewinding) {
    // Still waiting for the seek to land. Only the player coming back inside
    // the window clears the latch — nothing else can prove the seek took.
    if (positionMillis < DISCOVERY_PREVIEW_LOOP_WINDOW_MS) {
      return { state: { rewinding: false }, action: "none" };
    }
    return { state, action: "none" };
  }

  if (positionMillis >= DISCOVERY_PREVIEW_LOOP_WINDOW_MS) {
    return { state: { rewinding: true }, action: "rewind" };
  }

  return { state, action: "none" };
}
