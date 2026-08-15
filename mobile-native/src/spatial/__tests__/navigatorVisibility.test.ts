/**
 * The directional navigator rule, tested as a table.
 *
 * The interesting half of the mission's contract is the half that must change
 * *nothing* — a tap the pager saw as a one-pixel drag, a tilt commit, a
 * rotation, a refresh, an auto-advance. Those cases are invisible in a screen
 * test (nothing happens, and nothing happening is also what a broken build looks
 * like before you wire the assertion), so they are pinned here against the pure
 * rule where "unchanged" is a value rather than an absence.
 *
 * Sign convention, which reads backwards until you hold a phone: dragging the
 * finger left pushes the current reel off and pulls the next one on, so the
 * content offset *increases*. Positive travel = swipe left = hide.
 */
import { navigatorIntentForSwipe, settledPageIndex, SWIPE_COMMIT_THRESHOLD_PX } from "../navigatorVisibility";

/** A swipe right by `distance` points: the content offset falls. */
const swipeRight = (from: number, distance: number) =>
  ({ source: "touch", startOffsetX: from, endOffsetX: from - distance }) as const;

/** A swipe left by `distance` points: the content offset rises. */
const swipeLeft = (from: number, distance: number) =>
  ({ source: "touch", startOffsetX: from, endOffsetX: from + distance }) as const;

describe("settledPageIndex", () => {
  it("rounds to the nearest page rather than truncating", () => {
    // RN reports device-pixel-rounded offsets, so a settled page 3 routinely
    // arrives a fraction short of 3 * pageSize.
    expect(settledPageIndex(1124.67, 375)).toBe(3);
    expect(settledPageIndex(1125.33, 375)).toBe(3);
    expect(settledPageIndex(0, 375)).toBe(0);
    expect(settledPageIndex(375, 375)).toBe(1);
  });

  it("answers 0 for an unmeasured viewport instead of dividing by zero", () => {
    expect(settledPageIndex(0, 0)).toBe(0);
    expect(settledPageIndex(500, 0)).toBe(0);
    expect(settledPageIndex(500, -375)).toBe(0);
    expect(settledPageIndex(Number.NaN, 375)).toBe(0);
    expect(settledPageIndex(500, Number.NaN)).toBe(0);
  });

  it("never reports a negative page from an over-scrolled offset", () => {
    expect(settledPageIndex(-40, 375)).toBe(0);
  });
});

describe("navigatorIntentForSwipe", () => {
  it("hides on a swipe left", () => {
    expect(navigatorIntentForSwipe(swipeLeft(0, 300))).toBe("hide");
  });

  it("reveals on a swipe right", () => {
    expect(navigatorIntentForSwipe(swipeRight(1125, 300))).toBe("reveal");
  });

  it("reveals on a swipe right from the first reel, which cannot change the page", () => {
    // The recovery case, and the one that decides whether a user can get out of
    // a hidden dock. At offset 0 there is no earlier reel to move to, so the
    // list rubber-bands and settles back where it started; an index-derived rule
    // read "no transition" and left the dock hidden however many times the user
    // tried. A swipe has a direction whether or not the pager could act on it.
    expect(navigatorIntentForSwipe(swipeRight(0, 120))).toBe("reveal");
    // Over-scroll past the leading edge reports a negative offset. Still right.
    expect(navigatorIntentForSwipe({ source: "touch", startOffsetX: 0, endOffsetX: -80 })).toBe("reveal");
  });

  it("hides on a swipe left at the trailing edge, where the page also cannot change", () => {
    // The mirror of the case above: the user is on the last loaded reel and
    // swipes forward anyway. The content rubber-bands leftward and springs back,
    // so the page is unchanged but the gesture was unambiguously a swipe left.
    expect(navigatorIntentForSwipe(swipeLeft(3750, 120))).toBe("hide");
  });

  it("answers identically on every repeat of the same gesture", () => {
    // "Repeated swipes reliably hide the navigator" — the rule is a function of
    // the gesture alone, so it has no state to get stuck in and answers the same
    // however many times it is asked, in either direction.
    expect([swipeLeft(0, 90), swipeLeft(0, 90), swipeLeft(0, 90)].map(navigatorIntentForSwipe))
      .toEqual(["hide", "hide", "hide"]);
    expect([swipeRight(0, 90), swipeRight(0, 90), swipeRight(0, 90)].map(navigatorIntentForSwipe))
      .toEqual(["reveal", "reveal", "reveal"]);
  });

  it("leaves visibility alone for travel below the commit threshold", () => {
    // A tap that wobbles, and a press on a reel control that drags a little.
    expect(navigatorIntentForSwipe(swipeRight(750, 0))).toBe("unchanged");
    expect(navigatorIntentForSwipe(swipeRight(750, SWIPE_COMMIT_THRESHOLD_PX - 1))).toBe("unchanged");
    expect(navigatorIntentForSwipe(swipeLeft(750, SWIPE_COMMIT_THRESHOLD_PX - 1))).toBe("unchanged");
  });

  it("commits exactly at the threshold", () => {
    expect(navigatorIntentForSwipe(swipeLeft(750, SWIPE_COMMIT_THRESHOLD_PX))).toBe("hide");
    expect(navigatorIntentForSwipe(swipeRight(750, SWIPE_COMMIT_THRESHOLD_PX))).toBe("reveal");
  });

  it("treats an out-and-back drag as the cancelled gesture it is", () => {
    // The finger travelled 200pt right and came back before lifting. Net zero.
    expect(navigatorIntentForSwipe({ source: "touch", startOffsetX: 750, endOffsetX: 750 })).toBe("unchanged");
  });

  it("leaves visibility alone for anything not driven by a finger", () => {
    // Tilt commits, auto-advance and programmatic scrolls all arrive as
    // "motion" — and a tilt travelling backwards through the pager still must
    // not hide the dock, which is the case this asserts second.
    expect(navigatorIntentForSwipe({ source: "motion", startOffsetX: 0, endOffsetX: 375 })).toBe("unchanged");
    expect(navigatorIntentForSwipe({ source: "motion", startOffsetX: 375, endOffsetX: 0 })).toBe("unchanged");
    expect(navigatorIntentForSwipe({ source: "motion", startOffsetX: 375, endOffsetX: 375 })).toBe("unchanged");
  });

  it("treats a non-finite offset as no gesture", () => {
    expect(navigatorIntentForSwipe({ source: "touch", startOffsetX: Number.NaN, endOffsetX: 0 })).toBe("unchanged");
    expect(navigatorIntentForSwipe({ source: "touch", startOffsetX: 0, endOffsetX: Number.NaN })).toBe("unchanged");
  });

  it("does not scale its answer with distance", () => {
    // A four-page fling is one decision, not four.
    expect(navigatorIntentForSwipe(swipeLeft(0, 1500))).toBe("hide");
    expect(navigatorIntentForSwipe(swipeRight(1500, 1500))).toBe("reveal");
  });

  it("needs no page size, so an unmeasured viewport cannot silence it", () => {
    // The other half of the index-derived defect: `settledPageIndex` answers 0
    // for an unmeasured viewport, which collapsed every swipe to "unchanged".
    // Nothing in this rule divides by a page.
    expect(navigatorIntentForSwipe(swipeLeft(60, 60))).toBe("hide");
    expect(navigatorIntentForSwipe(swipeRight(60, 60))).toBe("reveal");
  });
});
