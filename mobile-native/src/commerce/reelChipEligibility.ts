/**
 * Which reels may be *offered* a Marketplace chip.
 *
 * ## Why exclusion happens here and not at render time
 *
 * `reelSlots.ts` spends a slot the moment it binds one, and a spent slot is
 * gone — that is its stated rule for dismissed placements, and it is the right
 * rule: hiding a chip must not make another appear on the next reel a moment
 * later. The consequence is that anything which would suppress a chip has to
 * happen *before* binding, or the suppression silently costs the list a
 * placement it was entitled to show somewhere else.
 *
 * So this is a filter on the input ids, not a branch in `ReelPlayerCard`.
 *
 * ## The two exclusions
 *
 * **Live.** A Live is one of the brief's no-interruption zones. Nothing
 * commercial may share the frame with it.
 *
 * **A reel that already sells something.** A PulseDrop Reel carries its own
 * commerce overlay: the live price and stock of the product the video was
 * published to sell. A chip on top of that is two shopping surfaces in one
 * frame, and — because the chip is chosen by a ranker that never saw this
 * reel's product — it is almost always advertising *a different product* over
 * the one being demonstrated. Between the two, the overlay is the one the
 * viewer is actually watching, so the chip yields.
 *
 * Both are "not a candidate", not "candidate whose chip is hidden", and the
 * distinction is the whole point of the module.
 *
 * ## Why ids, and why strings
 *
 * The binder is keyed by id throughout, because pagination appends and a
 * refresh replaces — both shift every index after them, so an index-keyed chip
 * migrates to a video the ranker never scored. `String(reel.id)` is applied
 * here and again at lookup rather than shared through a helper: one normaliser
 * used on one side and not the other is exactly how a binding goes missing.
 */
import type { PulseReel } from "../api/reels";
import { isPulseCommerceOverlay } from "../api/pulseCommerceOverlay";

/** True when a reel is a Live broadcast rather than a recorded video. */
export function reelIsLive(reel: PulseReel): boolean {
  return Boolean(reel.live_session_id || reel.live?.live_session_id);
}

/** True when a reel already carries PulseDrop's own commerce overlay. */
export function reelSellsItsOwnProduct(reel: PulseReel): boolean {
  return isPulseCommerceOverlay(reel.commerce);
}

/**
 * The ids, in list order, of the reels a chip may be bound to.
 *
 * `"0"` and the empty string are dropped because they are the absent-id
 * sentinels: binding to one would key every such reel to the same slot.
 */
export function chipEligibleReelIds(reels: readonly PulseReel[]): string[] {
  return reels
    .filter((reel) => !reelIsLive(reel))
    .filter((reel) => !reelSellsItsOwnProduct(reel))
    .map((reel) => String(reel.id))
    .filter((id) => id && id !== "0");
}
