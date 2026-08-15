/**
 * Directional navigator-visibility rules for the full-screen Reels pager.
 *
 * ## Why this is a pure module and not three `if`s inside the screen
 *
 * The product contract is not "hide the dock while browsing". It is a table of
 * causes, and the interesting half of that table is the causes that must change
 * *nothing*: a tap the pager saw as a one-pixel drag, a tilt commit, a rotation,
 * a data refresh, an auto-advance. Those are the cases nobody writes a test for
 * when the rule lives inline at the bottom of a scroll handler — which is
 * exactly how an earlier implementation hid the dock on tilt commits, because a
 * tilt commit animates `scrollToOffset` and momentum-end cannot tell the two
 * apart.
 *
 * Separating the decision from the plumbing means the plumbing only has to
 * answer two observable questions — what produced this gesture, and how far the
 * content travelled under the finger — and the rule itself is unit-testable
 * without a renderer, a scroll view, or a sensor.
 *
 * ## The rule
 *
 *   swipe right (content follows the finger rightward) → hide
 *   swipe left                                          → reveal
 *   travel below the commit threshold (tap, jitter,
 *     out-and-back)                                     → unchanged
 *   anything not driven by a finger                     → unchanged
 *
 * ## Why direction comes from the gesture and not from the page index
 *
 * This rule used to compare the page index the gesture started on with the one
 * the pager settled on. That is a defensible model and it is wrong on a device,
 * for one reason: **the first reel**. A user who opens Reels and swipes right —
 * the single most likely way anyone tries this feature — is already at offset 0.
 * The list rubber-bands and settles back on the reel it started from, the index
 * comparison reads "no transition", and the dock does not move. Repeated right
 * swipes did nothing at all, which is indistinguishable from the feature never
 * having shipped.
 *
 * Index-derived direction had two further failure modes that are invisible in a
 * JS test and unavoidable on glass: it could not resolve until the momentum
 * phase ended (so the dock lagged the finger by the length of a fling), and it
 * silently collapsed to "unchanged" whenever the viewport width was not yet
 * measured, because `settledPageIndex` answers 0 for an unmeasured page size.
 *
 * Reading the finger instead removes all three. A completed swipe has a
 * direction whether or not it changed the page, it is known the instant the
 * finger lifts, and it needs no notion of page size.
 */

/**
 * What produced a horizontal gesture.
 *
 * `"touch"` is the only value that may change visibility, and it is asserted by
 * the caller rather than inferred: a programmatic `scrollToOffset` — which is
 * how a tilt commit navigates — produces scroll events indistinguishable from a
 * finger's, but never an `onScrollBeginDrag`.
 */
export type SwipeSource = "touch" | "motion";

/** What a gesture should do to the bottom navigator. */
export type NavigatorIntent = "hide" | "reveal" | "unchanged";

export interface HorizontalSwipe {
  source: SwipeSource;
  /** Content offset when the finger went down. */
  startOffsetX: number;
  /** Content offset when the finger lifted — before any momentum. */
  endOffsetX: number;
}

/**
 * How far the content must travel under the finger before the gesture counts as
 * a direction rather than as noise.
 *
 * Sized against the pan slop a scroll view needs before it claims the touch at
 * all (~10pt), doubled so that a tap that wobbles, or a press on a reel control
 * that drags a little, cannot toggle navigation. It is deliberately far below a
 * page: committing a *page* is not the bar, and making it the bar is the defect
 * this threshold replaced.
 */
export const SWIPE_COMMIT_THRESHOLD_PX = 24;

/**
 * Which page a horizontal pager has come to rest on.
 *
 * Rounds rather than truncates: a settled scroll offset is routinely a fraction
 * of a point off the exact multiple (RN reports device-pixel-rounded values, and
 * `snapToInterval` lands within a pixel rather than on it).
 *
 * A non-positive `pageSize` means the viewport has not been measured yet; the
 * only safe answer is index 0. Note that no visibility decision reads this any
 * more — it exists for playback/index bookkeeping, where being wrong costs a
 * frame rather than a stuck dock.
 */
export function settledPageIndex(offset: number, pageSize: number): number {
  if (!Number.isFinite(offset) || !Number.isFinite(pageSize) || pageSize <= 0) return 0;
  return Math.max(0, Math.round(offset / pageSize));
}

/**
 * The single decision point for navigator visibility.
 *
 * Sign convention, which reads backwards until you hold a phone: dragging the
 * finger *right* pulls the earlier reel onto the screen, so the content offset
 * *decreases*. Hence negative travel = swipe right = hide.
 */
export function navigatorIntentForSwipe({ source, startOffsetX, endOffsetX }: HorizontalSwipe): NavigatorIntent {
  if (source !== "touch") return "unchanged";
  if (!Number.isFinite(startOffsetX) || !Number.isFinite(endOffsetX)) return "unchanged";
  const travel = endOffsetX - startOffsetX;
  // An out-and-back drag lands here too: the finger moved, the content did not,
  // and the user cancelled whatever they were starting.
  if (Math.abs(travel) < SWIPE_COMMIT_THRESHOLD_PX) return "unchanged";
  return travel > 0 ? "reveal" : "hide";
}
