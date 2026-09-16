import { readFileSync } from "fs";
import { join } from "path";
import { colors } from "../colors";
import {
  messengerBadgeTone,
  messengerBackgroundGradient,
  messengerPresenceDotColor,
  messengerTheme
} from "../messengerTheme";

/**
 * Neon Dusk's two hard rules, and the contrast budget that lets them be read.
 *
 * The defect this palette replaces was not a missing colour — it was one pill
 * style shared by every badge, so "OFFLINE" rendered in the same green as
 * "ONLINE". Nothing failed: the screen rendered, the badge text was correct, and
 * the only thing wrong was that the colour said the opposite of the word.
 *
 * So these tests assert on *properties* — is this actually a green, is this
 * actually a neutral gray, is this readable — rather than on hex literals. A
 * test that pins `offline === "#C0C6CE"` passes just as happily when someone
 * retunes it to a mint green, which is the failure it was supposed to catch.
 */

// ---------------------------------------------------------------------------
// Colour maths. WCAG 2.1 relative luminance and contrast ratio.
// ---------------------------------------------------------------------------

type Rgb = [number, number, number];

function parse(color: string): { rgb: Rgb; alpha: number } {
  const hex = color.trim().match(/^#([0-9a-f]{6})$/i);
  if (hex) {
    const value = hex[1];
    return {
      rgb: [parseInt(value.slice(0, 2), 16), parseInt(value.slice(2, 4), 16), parseInt(value.slice(4, 6), 16)],
      alpha: 1
    };
  }
  const rgba = color.trim().match(/^rgba?\(([^)]+)\)$/i);
  if (!rgba) throw new Error(`Unparseable colour: ${color}`);
  const parts = rgba[1].split(",").map((part) => Number(part.trim()));
  return { rgb: [parts[0], parts[1], parts[2]], alpha: parts.length > 3 ? parts[3] : 1 };
}

/** Source-over composite: what the eye actually sees for a translucent fill. */
function composite(top: string, bottom: Rgb): Rgb {
  const { rgb, alpha } = parse(top);
  return [0, 1, 2].map((i) => rgb[i] * alpha + bottom[i] * (1 - alpha)) as Rgb;
}

function luminance(rgb: Rgb): number {
  const [r, g, b] = rgb.map((channel) => {
    const c = channel / 255;
    return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  });
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(a: Rgb, b: Rgb): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** The darkest surface any of this text is drawn on: a conversation card. */
const PAGE: Rgb = parse(messengerBackgroundGradient[1]).rgb;
const CARD: Rgb = composite(messengerTheme.surface, PAGE);

// ---------------------------------------------------------------------------

describe("the two presence colours mean what they say", () => {
  /**
   * Green is asserted as a property — the green channel leads, and it leads by a
   * wide margin — rather than as a hex. `colors.safety` is additionally pinned
   * because presence green drifting on one surface at a time is how an app ends
   * up with four different greens that all mean "online".
   */
  it("makes ONLINE the canonical PulseSoc green", () => {
    const [r, g, b] = parse(messengerTheme.online).rgb;
    expect(g).toBeGreaterThan(r + 60);
    expect(g).toBeGreaterThan(b + 60);
    expect(messengerTheme.online).toBe(colors.safety);
  });

  /**
   * Gray is asserted as near-neutrality: all three channels within a narrow
   * spread. A teal, a mint or a steel blue all fail this, and those are exactly
   * the three things "offline" keeps turning into.
   */
  it("makes OFFLINE a neutral cool gray with no green and no blue cast", () => {
    const [r, g, b] = parse(messengerTheme.offline).rgb;
    expect(Math.max(r, g, b) - Math.min(r, g, b)).toBeLessThanOrEqual(24);
    // Cool, so blue may lead — but only barely, and green must never lead.
    expect(b).toBeGreaterThanOrEqual(r);
    expect(g).toBeLessThan(b);
  });

  it("keeps offline less dominant than online", () => {
    expect(contrast(parse(messengerTheme.offline).rgb, CARD)).toBeLessThan(
      contrast(parse(messengerTheme.online).rgb, CARD)
    );
  });
});

describe("every badge is toned by what it says", () => {
  const CASES: Array<[string, string]> = [
    ["online", messengerTheme.online],
    ["offline", messengerTheme.offline],
    ["muted", messengerTheme.offline],
    ["assistant", messengerTheme.violetAccent],
    ["ai", messengerTheme.violetAccent],
    ["intelligence", messengerTheme.violetAccent],
    ["undx", messengerTheme.violetAccent],
    ["direct", messengerTheme.tealAccent],
    ["pinned", messengerTheme.tealAccent],
    ["group", messengerTheme.blueAccent],
    ["room", messengerTheme.blueAccent],
    ["verified", messengerTheme.verifiedAccent]
  ];

  it.each(CASES)("tones %s correctly", (badge, expected) => {
    expect(messengerBadgeTone(badge).text).toBe(expected);
  });

  it("matches the badge strings case-insensitively, the way a server sends them", () => {
    expect(messengerBadgeTone("OFFLINE").text).toBe(messengerTheme.offline);
    expect(messengerBadgeTone(" Online ").text).toBe(messengerTheme.online);
  });

  /**
   * The acceptance criterion, stated as a rule rather than as four names.
   *
   * The brief names PulseSoc Music as the online row and Maria Cherie, Fabiola
   * Chery, ALTEON JACOB and UNDX as the offline ones, but the rule underneath is
   * that *nothing except online* may be green. Checking the rule catches the
   * fifth contact the brief did not have room to list.
   */
  it("gives the online green to nothing but online", () => {
    const green = messengerTheme.online;
    const everythingElse = [
      "offline",
      "muted",
      "assistant",
      "ai",
      "intelligence",
      "undx",
      "direct",
      "pinned",
      "group",
      "room",
      "verified",
      "something-nobody-has-shipped-yet"
    ];
    for (const badge of everythingElse) {
      const tone = messengerBadgeTone(badge);
      expect(tone.text).not.toBe(green);
      expect(tone.background).not.toBe(messengerTheme.onlineSoft);
      expect(tone.border).not.toBe(messengerTheme.onlineBorder);
    }
  });

  /** "No offline badge should appear teal/green" — teal is the other half. */
  it("never gives OFFLINE the teal accent either", () => {
    for (const badge of ["offline", "OFFLINE", "muted"]) {
      expect(messengerBadgeTone(badge).text).not.toBe(messengerTheme.tealAccent);
      expect(messengerBadgeTone(badge).text).not.toBe(messengerTheme.online);
    }
  });

  /** An unknown badge must claim nothing, so it lands on the neutral gray. */
  it("falls back to neutral for a badge it has never seen", () => {
    for (const unknown of ["", "   ", "sponsored", undefined as unknown as string]) {
      expect(messengerBadgeTone(unknown).text).toBe(messengerTheme.offline);
    }
  });
});

describe("the avatar presence dot", () => {
  it("is green when online and violet for the assistant", () => {
    expect(messengerPresenceDotColor("online")).toBe(messengerTheme.online);
    expect(messengerPresenceDotColor("ONLINE")).toBe(messengerTheme.online);
    expect(messengerPresenceDotColor("assistant")).toBe(messengerTheme.violetAccent);
  });

  it("is gray for everything else, including an absent presence", () => {
    for (const presence of ["offline", "away", "", undefined]) {
      expect(messengerPresenceDotColor(presence)).toBe(messengerTheme.offline);
    }
  });

  /**
   * The specific regression. The dot was `toneColor(tone)`, so an online founder
   * or intelligence contact got the *identity* violet and everyone else got the
   * same teal as the PINNED and DIRECT pills — presence was the one thing on the
   * row that could not be read from its own colour.
   */
  it("never returns the teal accent, which is what it used to return for online", () => {
    for (const presence of ["online", "offline", "assistant", "away", undefined]) {
      expect(messengerPresenceDotColor(presence)).not.toBe(messengerTheme.tealAccent);
    }
  });
});

describe("contrast", () => {
  /**
   * Badge text against its own fill, not against the card. The fill is the real
   * background, and it is the tighter test: a bright accent used as its own 15%
   * fill lifts the pill's background nearly as much as the text, so the apparent
   * gain cancels. Violet measured 3.18:1 that way and 4.71:1 at 10%.
   *
   * 4.5:1 is the threshold rather than 3:1 because these pills are 9px — small
   * text by every definition WCAG offers.
   */
  const PILLS = ["online", "offline", "direct", "group", "room", "ai", "verified", "pinned"];

  it.each(PILLS)("keeps the %s pill above 4.5:1 against its own fill", (badge) => {
    const tone = messengerBadgeTone(badge);
    const ratio = contrast(parse(tone.text).rgb, composite(tone.background, CARD));
    expect(ratio).toBeGreaterThanOrEqual(4.5);
  });

  it.each([
    ["primaryText", messengerTheme.primaryText],
    ["secondaryText", messengerTheme.secondaryText],
    ["tertiaryText", messengerTheme.tertiaryText]
  ])("keeps %s above 4.5:1 on a conversation card", (_name, color) => {
    expect(contrast(parse(color).rgb, CARD)).toBeGreaterThanOrEqual(4.5);
  });

  it("keeps the accent button's ink readable on a filled teal surface", () => {
    expect(contrast(parse(messengerTheme.onAccentText).rgb, parse(messengerTheme.tealAccent).rgb)).toBeGreaterThanOrEqual(4.5);
  });

  /**
   * §2: a card has to separate from the page. Not a contrast threshold — a card
   * must not be *readable against* the page, only distinguishable from it — so
   * this asserts a luminance step in the right direction, with a floor that a
   * card tinted back toward the background would fail.
   */
  it("separates a conversation card from the page by luminance", () => {
    expect(luminance(CARD)).toBeGreaterThan(luminance(PAGE) * 1.5);
  });

  /**
   * §3 and §10: the filter bar and the bottom nav are chrome. Chrome brighter
   * than the conversations it frames reads as the subject of the screen.
   */
  it("keeps the recessed chrome darker than the page it sits on", () => {
    expect(luminance(composite(messengerTheme.surfaceRecessed, PAGE))).toBeLessThan(luminance(PAGE));
  });

  /** §1: lighter than the near-black it replaces, and still clearly a dark theme. */
  it("lifts the page well above the old near-black without going light", () => {
    const old = parse(colors.background).rgb;
    expect(luminance(PAGE)).toBeGreaterThan(luminance(old) * 3);
    expect(luminance(PAGE)).toBeLessThan(0.1);
  });
});

/**
 * The other half of the job.
 *
 * Everything above tests a mapping function. A correct mapping function that the
 * screen does not call is precisely the bug this mission exists to fix — the old
 * code had correct badge *text* and painted it all one colour. So these read the
 * screen as source and check it actually routes through the tokens.
 */
describe("the screen actually uses the tokens", () => {
  const SRC = join(__dirname, "..", "..");
  const screen = readFileSync(join(SRC, "screens", "MessengerScreen.tsx"), "utf8");
  const flat = screen.replace(/\s+/g, " ");
  /**
   * Comments stripped, for the "this literal is gone" assertions below.
   *
   * Without this they fail on the comment that *explains* what was removed and
   * why, which would make the test forbid its own documentation. Checking the
   * code is the point; checking the prose about the code is not.
   */
  const code = screen.replace(/\/\*[\s\S]*?\*\//g, "").replace(/^\s*\/\/.*$/gm, "");

  it("tones each badge at the call site instead of sharing one pill colour", () => {
    expect(screen).toContain("messengerBadgeTone(badge)");
    expect(flat).toContain("backgroundColor: tone.background, borderColor: tone.border, color: tone.text");
  });

  it("passes a presence colour to both avatars", () => {
    expect(screen.match(/signalColor=\{messengerPresenceDotColor\(item\.presence\)\}/g)).toHaveLength(2);
  });

  /** The literals this mission removed. Each one was a green that meant nothing. */
  it("no longer hardcodes the old greens", () => {
    // The shared pill's green fill, border and text.
    expect(code).not.toContain("rgba(63,240,160,0.11)");
    expect(code).not.toContain("#94f6b1");
    // "Create Group", the only non-presence green on the screen.
    expect(code).not.toContain("#73f27d");
  });

  /**
   * The general form of the rule above, so the next stray green is caught
   * without anyone remembering to add its hex here. Any six-digit literal whose
   * green channel dominates by a clear margin is a green, and the screen should
   * now be sourcing all of those from `messengerTheme`.
   */
  it("has no green hex literals left anywhere in the screen", () => {
    const greens = (code.match(/#[0-9a-fA-F]{6}/g) || []).filter((hex) => {
      const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16));
      return g > r + 40 && g > b + 40;
    });
    expect(greens).toEqual([]);
  });

  /**
   * §14. A shadow on a row is an offscreen render per row, and this list is
   * virtualized — rows are built and destroyed continuously while scrolling.
   */
  it("keeps shadows off the conversation rows", () => {
    const row = flat.match(/ row: \{[^}]*\}/);
    expect(row).not.toBeNull();
    expect(row![0]).not.toContain("shadow");

    const pinned = flat.match(/pinnedRow: \{[^}]*\}/);
    expect(pinned).not.toBeNull();
    expect(pinned![0]).not.toContain("shadow");
  });

  /**
   * §1 again, from the other side. `PulseBackground` is the app's single shared
   * backdrop and `navigation/__tests__/backgroundSurfaces.test.ts` pins it to one
   * mount at the root — so Messenger lightens its own page and must not mount a
   * second copy of the shared one.
   */
  it("lightens its own page without touching the shared backdrop", () => {
    expect(screen).toContain("messengerBackgroundGradient");
    expect(screen).not.toContain("<PulseBackground");
    expect(screen).not.toMatch(/import .*PulseBackground.* from/);
  });
});
