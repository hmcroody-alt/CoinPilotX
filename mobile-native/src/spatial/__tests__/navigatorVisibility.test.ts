/**
 * The directional navigator rule, tested as a table.
 *
 * The interesting half of the mission's contract is the half that must change
 * *nothing* — a gesture that failed its threshold, a tilt commit, a rotation, a
 * refresh, an auto-advance. Those cases are invisible in a screen test (nothing
 * happens, and nothing happening is also what a broken build looks like before
 * you wire the assertion), so they are pinned here against the pure rule where
 * "unchanged" is a value rather than an absence.
 *
 * Direction mapping, which reads backwards until you hold a phone: swiping right
 * drags content rightward and brings the *earlier* reel on screen, so
 * right = previous = hide, left = next = reveal.
 */
import { navigatorIntentForSettle, settledPageIndex } from "../navigatorVisibility";

describe("settledPageIndex", () => {
  it("rounds to the nearest page rather than truncating", () => {
    // The failure this prevents: RN reports device-pixel-rounded offsets, so a
    // settled page 3 routinely arrives a fraction short of 3 * pageSize.
    // Truncating would read that as page 2 — a backwards transition — and hide
    // the navigator on a forward swipe.
    expect(settledPageIndex(1124.67, 375)).toBe(3);
    expect(settledPageIndex(1125.33, 375)).toBe(3);
    expect(settledPageIndex(0, 375)).toBe(0);
    expect(settledPageIndex(375, 375)).toBe(1);
  });

  it("answers 0 for an unmeasured viewport instead of dividing by zero", () => {
    // A pre-layout pass must not be able to move the dock. Index 0 classifies as
    // "unchanged" against any origin of 0, which is where the pager starts.
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

describe("navigatorIntentForSettle", () => {
  it("reveals on a committed swipe to the next reel", () => {
    expect(navigatorIntentForSettle({ source: "touch", fromIndex: 0, toIndex: 1 })).toBe("reveal");
  });

  it("hides on a committed swipe back to the previous reel", () => {
    expect(navigatorIntentForSettle({ source: "touch", fromIndex: 3, toIndex: 2 })).toBe("hide");
  });

  it("leaves visibility alone when a gesture settles on the reel it started from", () => {
    // Covers the mission's named edge case — first reel, swipe right, spring
    // back — and every failed-threshold gesture with it. Direction alone is not
    // enough; a transition has to have been committed.
    expect(navigatorIntentForSettle({ source: "touch", fromIndex: 0, toIndex: 0 })).toBe("unchanged");
    expect(navigatorIntentForSettle({ source: "touch", fromIndex: 4, toIndex: 4 })).toBe("unchanged");
  });

  it("leaves visibility alone for anything not driven by a finger", () => {
    // Tilt commits, auto-advance and programmatic scrolls all arrive as
    // "motion". A tilt that moves backwards through the pager still must not
    // hide the dock, which is the case this asserts second.
    expect(navigatorIntentForSettle({ source: "motion", fromIndex: 0, toIndex: 1 })).toBe("unchanged");
    expect(navigatorIntentForSettle({ source: "motion", fromIndex: 3, toIndex: 2 })).toBe("unchanged");
    expect(navigatorIntentForSettle({ source: "motion", fromIndex: 2, toIndex: 2 })).toBe("unchanged");
  });

  it("treats a non-finite index as no transition", () => {
    expect(navigatorIntentForSettle({ source: "touch", fromIndex: Number.NaN, toIndex: 1 })).toBe("unchanged");
    expect(navigatorIntentForSettle({ source: "touch", fromIndex: 0, toIndex: Number.NaN })).toBe("unchanged");
  });

  it("hides only one step at a time in the same direction it was asked to", () => {
    // A multi-page jump is still a single decision, not a repeated one: the rule
    // is a function of direction, not of distance.
    expect(navigatorIntentForSettle({ source: "touch", fromIndex: 0, toIndex: 4 })).toBe("reveal");
    expect(navigatorIntentForSettle({ source: "touch", fromIndex: 4, toIndex: 0 })).toBe("hide");
  });
});
