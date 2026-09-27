/**
 * The header fits, on every iPhone, asserted as arithmetic.
 *
 * This is the one part of the Marketplace entry point that cannot be checked by
 * looking at it. Home's wordmark does not shrink — its underline is a fixed 120pt
 * and its letters are `flexShrink: 0` — so when the trailing cluster grows past
 * what a width can afford, the wordmark *overflows underneath the buttons* rather
 * than truncating. On a simulator at Pro Max width, which is where a header gets
 * eyeballed, four full-size actions fit and there is nothing to see. The failure
 * only exists at 375–402pt.
 *
 * So the property under test is stated directly: chrome plus the wordmark's own
 * exported minimum must not exceed the window, at four actions, at every width
 * the app ships on. Both halves come from the layout — the constants are the
 * stylesheet's numbers and the floor is imported from the wordmark — so a future
 * change to either is a failing test rather than a silent overlap.
 */
import { PULSESOC_WORDMARK_MIN_WIDTH } from "../../components/home/LivingPulseSocWordmark";
import {
  HEADER_ACTION_GAP,
  HEADER_ACTION_SIZE_HOME,
  HEADER_ACTION_SIZE_STANDARD,
  headerChromeWidth,
  homeHeaderActionMetrics
} from "../headerActionMetrics";

/**
 * Every iPhone logical width from the 375pt class up, smallest first.
 *
 * SE 2nd/3rd gen and 6s/7/8 at 375, 12/13/14 mini at 375, 14/15/16 at 390–402,
 * Plus and Pro Max at 414–440.
 */
const IPHONE_WIDTHS = [375, 390, 393, 402, 414, 428, 430, 440];

/**
 * The two widths that physically cannot hold five controls and the wordmark.
 *
 * 320pt is SE 1st gen and iPod touch 7 — inside this app's iOS 15.1 floor, so
 * not hypothetical. They are listed separately rather than dropped, because the
 * point is that their behaviour is *defined* (floor size, clipped brand) rather
 * than that they are out of scope.
 */
const NARROW_WIDTHS = [320, 360];

/** Home's trailing cluster after this change: Marketplace, Search, Activity, Avatar. */
const HOME_ACTION_COUNT = 4;

describe("the wordmark keeps its width at four actions", () => {
  it.each(IPHONE_WIDTHS)("fits at %ipt", (width) => {
    const metrics = homeHeaderActionMetrics(width, HOME_ACTION_COUNT);
    const chrome = headerChromeWidth(metrics.size, metrics.gap, HOME_ACTION_COUNT);
    expect(chrome + PULSESOC_WORDMARK_MIN_WIDTH).toBeLessThanOrEqual(width);
    expect(metrics.brandFits).toBe(true);
  });

  it("would NOT fit at the fixed size the header used to hard-code", () => {
    // The assertion above is only meaningful if the naive implementation fails
    // it. At 375 and 393 a fixed 46pt overflows the wordmark by 33pt and 15pt.
    const naive = headerChromeWidth(HEADER_ACTION_SIZE_HOME, HEADER_ACTION_GAP, HOME_ACTION_COUNT);
    expect(naive + PULSESOC_WORDMARK_MIN_WIDTH).toBeGreaterThan(375);
    expect(naive + PULSESOC_WORDMARK_MIN_WIDTH).toBeGreaterThan(393);
  });
});

describe("the residual case is reported, not silently overlapped", () => {
  it.each(NARROW_WIDTHS)("says the brand does not fit at %ipt", (width) => {
    const metrics = homeHeaderActionMetrics(width, HOME_ACTION_COUNT);
    expect(metrics.brandFits).toBe(false);
    // And it does not buy the space by shrinking a tap target below the size the
    // rest of the app already ships.
    expect(metrics.size).toBe(HEADER_ACTION_SIZE_STANDARD);
  });

  it("records that 320pt already did not fit before Marketplace existed", () => {
    // Worth pinning rather than assuming. The three-action header — search,
    // activity, avatar, as Home shipped — needs 204pt of chrome at the floor
    // size, so 320pt was 4pt short of the wordmark already. The overlap there is
    // pre-existing, and the clip is the first thing that makes it non-destructive.
    expect(homeHeaderActionMetrics(320, 3).brandFits).toBe(false);
    // 360pt is the width this change actually costs: it fit at three actions and
    // does not at four.
    expect(homeHeaderActionMetrics(360, 3).brandFits).toBe(true);
    expect(homeHeaderActionMetrics(360, HOME_ACTION_COUNT).brandFits).toBe(false);
  });

  it("does not clip anything at 375pt and up", () => {
    // Which is every iPhone Apple still ships an OS update for.
    for (const width of IPHONE_WIDTHS) {
      expect(homeHeaderActionMetrics(width, HOME_ACTION_COUNT).brandFits).toBe(true);
      expect(homeHeaderActionMetrics(width, 3).brandFits).toBe(true);
    }
  });
});

describe("the clamp", () => {
  it("never returns a target smaller than the size every other header ships", () => {
    for (const width of [0, 200, ...NARROW_WIDTHS]) {
      expect(homeHeaderActionMetrics(width, HOME_ACTION_COUNT).size).toBeGreaterThanOrEqual(
        HEADER_ACTION_SIZE_STANDARD
      );
    }
  });

  it("never returns more than the roomy size, however wide the screen", () => {
    expect(homeHeaderActionMetrics(1366, HOME_ACTION_COUNT).size).toBe(HEADER_ACTION_SIZE_HOME);
  });

  it("gives Pro Max widths the full size rather than a small-tier compromise", () => {
    expect(homeHeaderActionMetrics(430, HOME_ACTION_COUNT).size).toBe(HEADER_ACTION_SIZE_HOME);
    expect(homeHeaderActionMetrics(440, HOME_ACTION_COUNT).size).toBe(HEADER_ACTION_SIZE_HOME);
  });

  it("gives a standard iPhone more than the floor, which a breakpoint would not", () => {
    expect(homeHeaderActionMetrics(393, HOME_ACTION_COUNT).size).toBeGreaterThan(
      HEADER_ACTION_SIZE_STANDARD
    );
  });

  it("grows monotonically with the width", () => {
    let previous = 0;
    for (const width of IPHONE_WIDTHS) {
      const size = homeHeaderActionMetrics(width, HOME_ACTION_COUNT).size;
      expect(size).toBeGreaterThanOrEqual(previous);
      previous = size;
    }
  });
});

describe("the returned metric is drawable", () => {
  it("keeps the radius an integer so a bordered circle has no lumpy edge", () => {
    for (const width of IPHONE_WIDTHS) {
      const metrics = homeHeaderActionMetrics(width, HOME_ACTION_COUNT);
      expect(Number.isInteger(metrics.radius)).toBe(true);
      expect(metrics.radius * 2).toBe(metrics.size);
    }
  });

  it("keeps every touch target at or above the 44pt minimum wherever it can", () => {
    // 375pt and up is every iPhone still receiving iOS updates. 320 and 360 fall
    // back to the standard 38pt header, which is what the rest of the app ships.
    for (const width of IPHONE_WIDTHS.filter((w) => w >= 402)) {
      expect(homeHeaderActionMetrics(width, HOME_ACTION_COUNT).size).toBeGreaterThanOrEqual(44);
    }
  });

  it("scales the glyph with the button rather than leaving a fixed icon", () => {
    const small = homeHeaderActionMetrics(375, HOME_ACTION_COUNT);
    const large = homeHeaderActionMetrics(430, HOME_ACTION_COUNT);
    expect(small.glyphSize).toBeLessThan(large.glyphSize);
    expect(large.glyphSize).toBeLessThan(large.size);
    expect(Number.isInteger(small.glyphSize)).toBe(true);
  });
});

describe("headerChromeWidth", () => {
  it("counts one leading button plus the trailing cluster and its gaps", () => {
    // 12 + 12 padding, 8 + 8 row gaps, one 40pt leading button, four 40pt
    // actions with three 6pt gaps.
    expect(headerChromeWidth(40, 6, 4)).toBe(24 + 16 + 40 + (160 + 18));
  });

  it("charges nothing for an empty trailing cluster", () => {
    expect(headerChromeWidth(40, 6, 0)).toBe(24 + 16 + 40);
  });
});
