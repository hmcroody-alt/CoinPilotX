/**
 * When a suggestion preview is allowed to play, and for how long.
 *
 * ## The 3.5 seconds
 *
 * A suggestion preview is a *sample*, not a viewing. It plays once, for about
 * three and a half seconds, and then holds on the frame it stopped at. It does
 * not loop, because a looping clip in a feed row competes with the post the user
 * is actually reading, and it does not run to completion, because a 60-second
 * reel playing out in a carousel is a Reels page nobody asked for.
 *
 * ## Why replay needs a memory
 *
 * The obvious implementation — play whenever the card becomes the active one —
 * has a failure the simulator will never show you. Viewability is not a clean
 * boolean: a card sitting near the threshold flickers in and out as the user's
 * thumb rests on the feed, and every flicker would restart the clip from zero.
 * The visible result is a preview that stutters between its first two frames
 * forever, which reads as a broken video rather than as a scroll artifact.
 *
 * So a completed preview is remembered. Re-activating that card inside the
 * cooldown holds the frozen frame — the jitter case — and re-activating it after
 * the cooldown replays it, which is the "scrolled away and came back later" case
 * §6 asks to distinguish. A preview that was *interrupted* before finishing is
 * never recorded, so it always gets another chance.
 *
 * The memory is module-scope because the card that owns it is unmounted by
 * FlatList windowing precisely when the user scrolls away — component state
 * would be destroyed by the very gesture it is supposed to survive.
 */

/** How long a preview plays before freezing. §4 asks for 3–4s; this is the middle. */
export const DISCOVERY_PREVIEW_DURATION_MS = 3500;

/**
 * How long a completed preview stays completed.
 *
 * Long enough to cover viewability jitter and a short scroll past and back;
 * short enough that a deliberate return to a card feels alive rather than dead.
 */
export const DISCOVERY_PREVIEW_REPLAY_COOLDOWN_MS = 8000;

/**
 * Bound on the memory. A long feed session touches unboundedly many suggestions,
 * and a Map that only ever grows is a leak that takes an hour to become visible.
 */
const MAX_TRACKED_PREVIEWS = 64;

const completedAt = new Map<string, number>();

/** True when this card should start (or restart) its preview right now. */
export function shouldStartPreview(key: string, now: number = Date.now()): boolean {
  if (!key) return false;
  const completed = completedAt.get(key);
  if (completed === undefined) return true;
  if (now - completed < DISCOVERY_PREVIEW_REPLAY_COOLDOWN_MS) return false;
  // The cooldown has expired: forget it, so this counts as a fresh first play.
  completedAt.delete(key);
  return true;
}

/** Record that this card played its full preview. Interrupted previews must not call this. */
export function markPreviewCompleted(key: string, now: number = Date.now()): void {
  if (!key) return;
  if (completedAt.size >= MAX_TRACKED_PREVIEWS && !completedAt.has(key)) {
    // Map preserves insertion order, so the first key is the oldest.
    const oldest = completedAt.keys().next();
    if (!oldest.done) completedAt.delete(oldest.value);
  }
  completedAt.set(key, now);
}

/** Test seam, and the reset a fresh feed session performs on pull-to-refresh. */
export function resetDiscoveryPreviewPlayback(): void {
  completedAt.clear();
}
