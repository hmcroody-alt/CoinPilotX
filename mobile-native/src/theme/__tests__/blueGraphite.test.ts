import { readFileSync } from "fs";
import { join } from "path";
import { BLUE_GRAPHITE_CARD, BLUE_GRAPHITE_NAV, blueGraphite } from "../blueGraphite";
import { colors } from "../colors";

/**
 * The arithmetic behind the blue-graphite material.
 *
 * `blueGraphite.ts` makes four claims a reader cannot check by looking at it:
 * that the dock is darker than the card at every stop, that neither ramp ever
 * gets *lighter* than its own core, that every piece of text on either surface
 * clears AA, and that the perimeter layer deepens rather than lightens. All
 * four are arithmetic on the hex values, so all four belong here — a comment
 * saying "one step darker" keeps saying it after someone edits the hex.
 *
 * The literals are pinned as well as derived. The whole point of a token file
 * is that a colour change is a decision someone has to make on purpose; pinning
 * means an edit to an approved value turns this file red and has to be argued
 * for, rather than sliding through because the ratios happen to still pass.
 */

type Rgb = { r: number; g: number; b: number };
type Rgba = Rgb & { a: number };

function parse(color: string): Rgba {
  const hex = color.match(/^#([0-9a-f]{6})$/i);
  if (hex) {
    const n = parseInt(hex[1], 16);
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255, a: 1 };
  }
  const rgba = color.match(/^rgba?\(\s*([\d.]+)\s*,\s*([\d.]+)\s*,\s*([\d.]+)\s*(?:,\s*([\d.]+)\s*)?\)$/);
  if (!rgba) throw new Error(`unparsed colour: ${color}`);
  return { r: Number(rgba[1]), g: Number(rgba[2]), b: Number(rgba[3]), a: rgba[4] === undefined ? 1 : Number(rgba[4]) };
}

/** Source-over, which is what the compositor does with the perimeter layer. */
function over(under: Rgb, above: Rgba): Rgb {
  return {
    r: above.a * above.r + (1 - above.a) * under.r,
    g: above.a * above.g + (1 - above.a) * under.g,
    b: above.a * above.b + (1 - above.a) * under.b
  };
}

function relativeLuminance({ r, g, b }: Rgb): number {
  const channel = (value: number) => {
    const c = value / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(a: string | Rgb, b: string | Rgb): number {
  const la = relativeLuminance(typeof a === "string" ? parse(a) : a);
  const lb = relativeLuminance(typeof b === "string" ? parse(b) : b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const luminance = (color: string) => relativeLuminance(parse(color));

/** Every opaque stop a pixel of either surface can actually be. */
const CARD_STOPS = [
  blueGraphite.surfaceBlueGraphiteCore,
  blueGraphite.surfaceBlueGraphiteEdge,
  blueGraphite.surfaceBlueGraphiteDeep
];
const NAV_STOPS = [
  blueGraphite.surfaceBlueGraphiteNavCore,
  blueGraphite.surfaceBlueGraphiteNavEdge,
  blueGraphite.surfaceBlueGraphiteNavDeep
];

describe("the approved values are pinned", () => {
  it("carries the seven tokens verbatim, with the one calibrated edge called out", () => {
    expect(blueGraphite).toEqual({
      surfaceBlueGraphiteCore: "#363D46",
      // Approved `#29466A`, lowered at constant hue and saturation. See the
      // block below, which is the reason, stated as a ratio.
      surfaceBlueGraphiteEdge: "#243D5D",
      surfaceBlueGraphiteDeep: "#263854",
      surfaceBlueGraphiteNavCore: "#303843",
      surfaceBlueGraphiteNavEdge: "#243B5A",
      surfaceBlueGraphiteNavDeep: "#1F314B",
      surfaceBlueGraphiteBorder: "rgba(118, 150, 196, 0.26)"
    });
  });

  /**
   * The deviation, as the number that forced it. If someone restores `#29466A`
   * this is the test that says what breaks and by how much, rather than a
   * generic "contrast failed".
   */
  it("would fail AA for 12pt muted text at the approved blue edge", () => {
    expect(contrast(colors.muted, "#29466A")).toBeLessThan(4.5);
    expect(contrast(colors.muted, blueGraphite.surfaceBlueGraphiteEdge)).toBeGreaterThanOrEqual(4.5);
    // Same hue family, not a different colour: still decisively blue-dominant.
    const { r, g, b } = parse(blueGraphite.surfaceBlueGraphiteEdge);
    expect(b).toBeGreaterThan(g);
    expect(g).toBeGreaterThan(r);
  });

  it("introduces no colour the token file does not declare", () => {
    const declared = new Set<string>(Object.values(blueGraphite));
    const fromSpecs = [BLUE_GRAPHITE_CARD, BLUE_GRAPHITE_NAV].flatMap((surface) => [
      ...surface.base.colors,
      surface.fallback
    ]);
    for (const color of fromSpecs) expect(declared.has(color)).toBe(true);

    // The perimeter layer is the deep token at alpha and nothing else.
    const deep = parse(blueGraphite.surfaceBlueGraphiteDeep);
    for (const surface of [BLUE_GRAPHITE_CARD, BLUE_GRAPHITE_NAV]) {
      for (const color of surface.edge.colors) {
        const { r, g, b } = parse(color);
        expect({ r, g, b }).toEqual({ r: deep.r, g: deep.g, b: deep.b });
      }
    }
  });
});

describe("the hierarchy holds as luminance", () => {
  it("keeps the dock darker than the card at every stop", () => {
    for (let i = 0; i < CARD_STOPS.length; i += 1) {
      expect(luminance(CARD_STOPS[i])).toBeGreaterThan(luminance(NAV_STOPS[i]));
    }
    // And at the single colour each falls back to.
    expect(luminance(BLUE_GRAPHITE_CARD.fallback)).toBeGreaterThan(luminance(BLUE_GRAPHITE_NAV.fallback));
  });

  /**
   * The invariant the calibration exists to create. The core is the value the
   * whole contrast argument is made against, so nothing else on the card may be
   * lighter than it — otherwise the argument is being made against the wrong
   * pixel.
   */
  it("never lets the card ramp rise above its own core", () => {
    const core = luminance(blueGraphite.surfaceBlueGraphiteCore);
    for (const stop of CARD_STOPS) expect(luminance(stop)).toBeLessThanOrEqual(core);
  });

  it("deepens at the perimeter rather than lightening", () => {
    for (const [surface, core] of [
      [BLUE_GRAPHITE_CARD, blueGraphite.surfaceBlueGraphiteCore],
      [BLUE_GRAPHITE_NAV, blueGraphite.surfaceBlueGraphiteNavCore]
    ] as const) {
      const base = parse(core);
      for (const color of surface.edge.colors) {
        const composited = over(base, parse(color));
        expect(relativeLuminance(composited)).toBeLessThanOrEqual(relativeLuminance(base));
      }
      // Strongest stop is a half-step, not a wash: still unmistakably graphite.
      const strongest = surface.edge.colors
        .map((color) => parse(color))
        .reduce((worst, candidate) => (candidate.a > worst.a ? candidate : worst));
      expect(strongest.a).toBeLessThanOrEqual(0.34);
      expect(contrast(over(base, strongest), core)).toBeLessThan(1.3);
    }
  });

  /** Opaque is the whole promise: this is a material, not an overlay. */
  it("keeps every base stop and both fallbacks fully opaque", () => {
    for (const surface of [BLUE_GRAPHITE_CARD, BLUE_GRAPHITE_NAV]) {
      for (const color of surface.base.colors) expect(parse(color).a).toBe(1);
      expect(parse(surface.fallback).a).toBe(1);
    }
  });

  /** Not black, and not near-black — which is the appearance being removed. */
  it("puts both surfaces well clear of the near-blacks they replace", () => {
    for (const stop of [...CARD_STOPS, ...NAV_STOPS]) {
      // The Pulse Network card's old base and the dock's old fill.
      expect(luminance(stop)).toBeGreaterThan(luminance("#06101C"));
      expect(luminance(stop)).toBeGreaterThan(luminance("#070E20"));
    }
  });
});

/**
 * Contrast, per surface, against the worst stop rather than the average — text
 * does not get to choose which part of a gradient it lands on. Sizes are the
 * ones actually rendered: the hero's metric labels are 12pt and the dock's are
 * 12pt, both small text, so 4.5:1 is the bar and 3:1 does not apply to them.
 */
describe("text clears AA on both surfaces", () => {
  const SMALL = [
    ["colors.text — hero tile labels, 9pt", colors.text],
    ["colors.muted — hero metric labels, 12pt", colors.muted],
    ["colors.accent — health pill, 13pt", colors.accent]
  ] as const;

  /** 18pt, or 14pt bold: the hero's value type and its tinted tile values. */
  const LARGE = [
    ["colors.danger — live count, 23pt", colors.danger],
    ["colors.intelligence — UNDX tile value, 14pt/900", colors.intelligence],
    ["colors.safety — Safety Shield tile value, 14pt/900", colors.safety],
    ["colors.accentStrong", colors.accentStrong],
    ["colors.warning", colors.warning]
  ] as const;

  it.each(SMALL)("card: %s clears 4.5:1 on every stop", (_label, value) => {
    for (const stop of CARD_STOPS) expect(contrast(value, stop)).toBeGreaterThanOrEqual(4.5);
  });

  it.each(LARGE)("card: %s clears 3:1 on every stop", (_label, value) => {
    for (const stop of CARD_STOPS) expect(contrast(value, stop)).toBeGreaterThanOrEqual(3);
  });

  it("dock: the inactive and active labels both clear 4.5:1 on every stop", () => {
    for (const stop of NAV_STOPS) {
      expect(contrast(colors.muted, stop)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(colors.accent, stop)).toBeGreaterThanOrEqual(4.5);
    }
  });

  /**
   * Active and inactive must be told apart by more than hue, or the dock is
   * unreadable to a red/green deficiency and in grayscale. The teal active
   * label is a full step brighter than the muted one against the same fill.
   */
  it("dock: separates active from inactive by luminance, not only by hue", () => {
    const active = relativeLuminance(parse(colors.accent));
    const inactive = relativeLuminance(parse(colors.muted));
    expect(Math.abs(active - inactive)).toBeGreaterThan(0.1);
  });
});

/**
 * The gradient axis, as the corner projections it produces. This is the
 * approved direction — graphite across the middle, navy arriving at the right
 * edges — expressed as the only thing that can actually be checked.
 */
describe("the axis puts the blue where the brief puts it", () => {
  const project = (surface: typeof BLUE_GRAPHITE_CARD, x: number, y: number) => {
    const { start, end } = surface.base;
    if (!start || !end) throw new Error("expected an explicit axis");
    const dx = end.x - start.x;
    const dy = end.y - start.y;
    const t = ((x - start.x) * dx + (y - start.y) * dy) / (dx * dx + dy * dy);
    return Math.min(1, Math.max(0, t));
  };

  it.each([
    ["card", BLUE_GRAPHITE_CARD],
    ["dock", BLUE_GRAPHITE_NAV]
  ])("%s: centre and left stay graphite, the lower-right reaches deep navy", (_name, surface) => {
    const flatUntil = surface.base.locations[1];
    expect(project(surface, 0, 0)).toBeLessThanOrEqual(flatUntil);
    expect(project(surface, 0, 1)).toBeLessThanOrEqual(flatUntil);
    // No centre spotlight: the middle of the surface is in the flat run.
    expect(project(surface, 0.5, 0.5)).toBeLessThanOrEqual(flatUntil);
    // The lower-right corner is the deep stop.
    expect(project(surface, 1, 1)).toBe(1);
    // The upper-right has only just turned — "slightly bluer", not blue.
    expect(project(surface, 1, 0)).toBeGreaterThan(flatUntil - 0.05);
    expect(project(surface, 1, 0)).toBeLessThan(flatUntil + 0.2);
  });

  it("orders the stops monotonically so there is no band", () => {
    for (const surface of [BLUE_GRAPHITE_CARD, BLUE_GRAPHITE_NAV]) {
      const { colors: stops, locations } = surface.base;
      expect(stops).toHaveLength(locations.length);
      for (let i = 1; i < locations.length; i += 1) {
        expect(locations[i]).toBeGreaterThan(locations[i - 1]);
      }
      expect(locations[0]).toBe(0);
      expect(locations[locations.length - 1]).toBe(1);
    }
  });
});

/**
 * The file is the single source. A hex belonging to this material appearing
 * anywhere else in `src/` means the system has a second copy, which is the
 * failure mode a token file exists to prevent.
 */
describe("nothing re-declares the material", () => {
  it("keeps every blue-graphite hex inside the token file", () => {
    const root = join(__dirname, "..", "..");
    const seen: string[] = [];
    const walk = (dir: string) => {
      for (const entry of require("fs").readdirSync(dir, { withFileTypes: true })) {
        const path = join(dir, entry.name);
        if (entry.isDirectory()) {
          if (entry.name !== "__tests__") walk(path);
          continue;
        }
        if (!/\.tsx?$/.test(entry.name)) continue;
        if (path.endsWith(join("theme", "blueGraphite.ts"))) continue;
        const source = readFileSync(path, "utf8");
        for (const token of Object.values(blueGraphite)) {
          if (token.startsWith("#") && source.includes(token)) seen.push(`${path}: ${token}`);
        }
      }
    };
    walk(root);
    expect(seen).toEqual([]);
  });
});
