/**
 * Directional navigator-visibility rules for the full-screen Reels pager.
 *
 * ## Why this is a pure module and not three `if`s inside the screen
 *
 * The product contract is not "hide the dock while browsing". It is a table of
 * causes, and the interesting half of that table is the causes that must change
 * *nothing*: a gesture that failed its threshold, a tilt commit, a rotation, a
 * data refresh, an auto-advance, a tap that a child control claimed. Those are
 * the cases nobody writes a test for when the rule lives inline as a call to
 * `notifySwipeSettled()` at the bottom of a scroll handler — which is exactly
 * how the previous implementation hid the dock on tilt commits, because a tilt
 * commit animates `scrollToOffset` and momentum-end cannot tell the two apart.
 *
 * Separating the decision from the plumbing means the plumbing only has to
 * answer two observable questions — what produced this settle, and which index
 * did it start and end on — and the rule itself is unit-testable without a
 * renderer, a scroll view, or a sensor.
 *
 * ## The rule
 *
 * Only a successfully committed *touch* swipe may change navigator visibility.
 *
 *   swipe left  → next reel     (index increases) → reveal
 *   swipe right → previous reel (index decreases) → hide
 *   no index change (threshold not met, edge of list) → unchanged
 *   anything not driven by a finger                   → unchanged
 *
 * The direction mapping reads backwards until you hold a phone: swiping *right*
 * drags content rightward, which brings the *earlier* reel onto the screen. So
 * "swipe right = previous = hide" and "swipe left = next = reveal".
 */

/**
 * What produced a settled page transition.
 *
 * `"touch"` is the only value that may change visibility, and it is asserted
 * rather than inferred: the caller sets it from a drag having actually begun,
 * because a programmatic `scrollToOffset` produces a byte-identical
 * momentum-end event to a finger swipe.
 */
export type PageSettleSource = "touch" | "motion";

/** What a settle should do to the bottom navigator. */
export type NavigatorIntent = "hide" | "reveal" | "unchanged";

export interface PageSettle {
  source: PageSettleSource;
  /** Index the gesture started from — NOT the index before the last frame. */
  fromIndex: number;
  /** Index the pager came to rest on. */
  toIndex: number;
}

/**
 * Which page a horizontal pager has come to rest on.
 *
 * Rounds rather than truncates: a settled scroll offset is routinely a fraction
 * of a point off the exact multiple (RN reports device-pixel-rounded values, and
 * `snapToInterval` lands within a pixel rather than on it). Truncating turns a
 * settled page 3 at offset 1124.67 into page 2, which reads as a backwards
 * transition and would *hide* the dock on a forward swipe.
 *
 * A non-positive `pageSize` means the viewport has not been measured yet; the
 * only safe answer is index 0, which classifies as "unchanged" and therefore
 * cannot move the dock on a layout pass.
 */
export function settledPageIndex(offset: number, pageSize: number): number {
  if (!Number.isFinite(offset) || !Number.isFinite(pageSize) || pageSize <= 0) return 0;
  return Math.max(0, Math.round(offset / pageSize));
}

/**
 * The single decision point for navigator visibility.
 *
 * Everything the table calls a non-toggle resolves here to `"unchanged"`,
 * including the edge case the mission calls out by name: a user on the first
 * reel who swipes right, travels, and springs back has committed no transition,
 * so the dock must stay exactly as it was rather than hiding on the strength of
 * the gesture's direction alone.
 */
export function navigatorIntentForSettle({ source, fromIndex, toIndex }: PageSettle): NavigatorIntent {
  if (source !== "touch") return "unchanged";
  if (!Number.isFinite(fromIndex) || !Number.isFinite(toIndex)) return "unchanged";
  if (toIndex > fromIndex) return "reveal";
  if (toIndex < fromIndex) return "hide";
  return "unchanged";
}
