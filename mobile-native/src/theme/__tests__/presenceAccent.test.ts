/**
 * A colour system is mostly untestable, and the parts that are not are the
 * parts that go wrong.
 *
 * Nothing here asserts a hex string. That would be a change-detector: it fails
 * when somebody adjusts a hue and says nothing about whether the adjustment was
 * any good. What is pinned instead is the three things that have a right answer
 * — the table covers every page type, the alpha ramp stays inside the restraint
 * bands the doctrine sets out, and every hue is legible on the surfaces it is
 * actually drawn on. All three fail for a reason a reader can act on.
 */
import { PAGE_TYPES } from "../../api/pages";
import { colors } from "../colors";
import { presenceAccent, presenceAccentInternals } from "../presenceAccent";

const { HUES, HUE_BY_TYPE } = presenceAccentInternals;

function alphaOf(token: string): number {
  const match = /rgba\(\s*\d+,\s*\d+,\s*\d+,\s*([\d.]+)\s*\)/.exec(token);
  if (!match) throw new Error(`not an rgba token: ${token}`);
  return Number(match[1]);
}

/** WCAG relative luminance. */
function luminance(hex: string): number {
  const value = hex.replace("#", "");
  const channel = (pair: string) => {
    const raw = parseInt(pair, 16) / 255;
    return raw <= 0.03928 ? raw / 12.92 : ((raw + 0.055) / 1.055) ** 2.4;
  };
  return (
    0.2126 * channel(value.slice(0, 2)) +
    0.7152 * channel(value.slice(2, 4)) +
    0.0722 * channel(value.slice(4, 6))
  );
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("every page type is told what colour it is", () => {
  it("names all sixteen, with nothing left over", () => {
    // TypeScript already requires this of `Record<PageType, PresenceHue>`, so
    // the test looks redundant — until somebody reaches for `as` to silence a
    // build, which is what people do at the end of a long day. This is the
    // check that survives that.
    expect(Object.keys(HUE_BY_TYPE).sort()).toEqual([...PAGE_TYPES].sort());
  });

  it("gives each of them a hue that exists", () => {
    for (const pageType of PAGE_TYPES) {
      expect(Object.keys(HUES)).toContain(HUE_BY_TYPE[pageType]);
      expect(presenceAccent(pageType).hue).toBe(HUE_BY_TYPE[pageType]);
    }
  });

  it("uses every hue it defines", () => {
    // An unused hue is a decision nobody made — either a family was collapsed
    // and the colour left behind, or a colour was added for a type that never
    // arrived. Both are worth a build failure rather than a dead constant.
    const used = new Set(PAGE_TYPES.map((pageType) => HUE_BY_TYPE[pageType]));
    expect([...used].sort()).toEqual(Object.keys(HUES).sort());
  });

  it("actually tells the families apart", () => {
    // The assertions above hold just as well if all sixteen types map to one
    // hue. These four are named outright, one per family, and are the four a
    // visitor is most likely to hold side by side.
    const seen = ["ARTIST", "RESTAURANT", "VENUE", "BUSINESS"].map(
      (pageType) => presenceAccent(pageType).base
    );
    expect(new Set(seen).size).toBe(4);
  });
});

describe("an unknown type is drawn, not refused", () => {
  it("falls back to brand teal for a type this build has never heard of", () => {
    // A server one version ahead can name a seventeenth type. The screen
    // cannot decline to render because of it, and teal is the honest answer:
    // all the app knows is that this is a presence.
    expect(presenceAccent("PODCAST").base).toBe(HUES.enterprise);
    expect(presenceAccent("").base).toBe(HUES.enterprise);
    expect(presenceAccent(undefined).base).toBe(HUES.enterprise);
    expect(presenceAccent(null).base).toBe(HUES.enterprise);
  });

  it("does not quietly hand back a broken accent", () => {
    // The failure this guards is `undefined.base` at render time, deep inside
    // a style array, on a page that had loaded fine a version ago.
    const fallback = presenceAccent("NOT_A_TYPE");
    for (const key of ["base", "fill", "fillStrong", "border", "glow", "ink"] as const) {
      expect(typeof fallback[key]).toBe("string");
      expect(fallback[key].length).toBeGreaterThan(0);
    }
    expect(fallback.wash).toHaveLength(2);
  });
});

describe("the restraint doctrine is enforced, not just written down", () => {
  // From `profileNeon`: "borders sit at ~0.30–0.45, fills at ~0.10–0.18, and
  // only the primary action and the avatar ring are allowed to be genuinely
  // bright." Hand-written rgba drifts a hundredth at a time; this is what
  // stops the drift being invisible until a screenshot looks wrong.
  const every = Object.keys(HUES).map((hue) => presenceAccent(
    PAGE_TYPES.find((pageType) => HUE_BY_TYPE[pageType] === hue) as string
  ));

  it("keeps panel fills under body copy", () => {
    for (const tone of every) {
      expect(alphaOf(tone.fill)).toBeGreaterThanOrEqual(0.1);
      expect(alphaOf(tone.fill)).toBeLessThanOrEqual(0.18);
      expect(alphaOf(tone.fillStrong)).toBeLessThanOrEqual(0.18);
      // The strong fill has to be visibly a step up, or the selected tab is
      // selected only in the stylesheet.
      expect(alphaOf(tone.fillStrong)).toBeGreaterThan(alphaOf(tone.fill));
    }
  });

  it("keeps borders an edge rather than a glow", () => {
    for (const tone of every) {
      expect(alphaOf(tone.border)).toBeGreaterThanOrEqual(0.3);
      expect(alphaOf(tone.border)).toBeLessThanOrEqual(0.45);
    }
  });

  it("lets the halo be the brightest thing and nothing else", () => {
    for (const tone of every) {
      expect(alphaOf(tone.glow)).toBeGreaterThan(alphaOf(tone.border));
      expect(alphaOf(tone.glow)).toBeLessThanOrEqual(0.5);
    }
  });
});

describe("every hue is legible where it is drawn", () => {
  const surfaces = {
    background: colors.background,
    surface: colors.surface,
    // The lightest surface a presence label sits on, and therefore the worst
    // case for a bright accent.
    surfaceRaised: colors.surfaceRaised
  };

  for (const [hue, hex] of Object.entries(HUES)) {
    for (const [name, surface] of Object.entries(surfaces)) {
      it(`reads ${hue} on ${name}`, () => {
        // WCAG AA for normal text. These are label and action colours, not
        // decoration — a handle line and a Follow button are read, so the
        // decorative 3:1 allowance does not apply.
        expect(contrast(hex, surface)).toBeGreaterThanOrEqual(4.5);
      });
    }
  }

  it("fades the cover wash to nothing, inside the fill band", () => {
    // A wash whose two stops have the same weight is a flat fill with extra
    // work, and one that starts at full strength is a 130pt saturated banner
    // rather than an accent. Both ends are pinned.
    for (const pageType of PAGE_TYPES) {
      const [from, to] = presenceAccent(pageType).wash;
      expect(alphaOf(from)).toBeLessThanOrEqual(0.18);
      expect(alphaOf(from)).toBeGreaterThan(alphaOf(to));
      expect(alphaOf(to)).toBe(0);
    }
  });

  it("captions a filled action in something that reads on it", () => {
    // This is the assertion that found `ink`. The first version of this file
    // asked whether `colors.text` — white — read on the primary action, on the
    // assumption it would be a gradient fill with a white caption, which is
    // what `profileNeon` does. It does not read: white on teal is about 1.6:1.
    // The token exists because of this test rather than the other way round.
    for (const pageType of PAGE_TYPES) {
      const tone = presenceAccent(pageType);
      expect(contrast(tone.ink, tone.base)).toBeGreaterThanOrEqual(4.5);
    }
  });

  it("does not caption anything in white on a bright accent", () => {
    // Naming the wrong answer as well as the right one. Without this, `ink`
    // could be quietly set back to `colors.text` for a hue dark enough to
    // scrape past the assertion above, and the system would have two rules.
    for (const pageType of PAGE_TYPES) {
      expect(presenceAccent(pageType).ink).not.toBe(colors.text);
    }
  });
});
