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
 *   swipe left (the finger travels leftward, pulling the
 *     next reel in)                                     → hide
 *   swipe right                                         → reveal
 *   travel below the commit threshold (tap, jitter,
 *     out-and-back)                                     → unchanged
 *   anything not driven by a finger                     → unchanged
 *
 * Hiding on the *forward* gesture is what makes this feel like an immersive
 * pager rather than a toggle: moving on to the next reel clears the chrome, and
 * backing up brings it back. It is also the more forgiving half to get right,
 * because a leftward swipe moves the list for real at every position except the
 * very last reel, where it rubber-bands — and a rubber-band still travels
 * leftward, so the hide lands anyway.
 *
 * ## Why direction comes from the gesture and not from the page index
 *
 * This rule used to compare the page index the gesture started on with the one
 * the pager settled on. That is a defensible model and it is wrong on a device,
 * because a gesture that does not change the page still has a direction. The
 * clearest case is an edge: at the first reel a rightward swipe is already at
 * offset 0, so the list rubber-bands and settles back where it started, the
 * index comparison reads "no transition", and the dock does not move — no matter
 * how many times the user repeats it. That is indistinguishable from the feature
 * never having shipped, and under the current mapping it would strand a user who
 * cannot get the dock *back*.
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
 * finger *left* pushes the current reel off and pulls the next one on, so the
 * content offset *increases*. Hence positive travel = swipe left = hide, and
 * negative travel = swipe right = reveal.
 */
export function navigatorIntentForSwipe({ source, startOffsetX, endOffsetX }: HorizontalSwipe): NavigatorIntent {
  if (source !== "touch") return "unchanged";
  if (!Number.isFinite(startOffsetX) || !Number.isFinite(endOffsetX)) return "unchanged";
  const travel = endOffsetX - startOffsetX;
  // An out-and-back drag lands here too: the finger moved, the content did not,
  // and the user cancelled whatever they were starting.
  if (Math.abs(travel) < SWIPE_COMMIT_THRESHOLD_PX) return "unchanged";
  return travel > 0 ? "hide" : "reveal";
}
