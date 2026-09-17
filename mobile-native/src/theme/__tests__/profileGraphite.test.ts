/**
 * The Profile graphite surface: the ramp's values, the contrast it has to clear,
 * and what happens on the four themes graphite is not for.
 *
 * This is the one place the colours are pinned as literals. Every other Profile
 * test names a token, so retuning the ramp turns exactly one file red — the file
 * whose job is to state what the ramp is — instead of scattering hex assertions
 * across the header, the screen and the canvas.
 */

import { graphite } from "../graphite";
import { chatGraphite } from "../chatGraphite";
import { GRAPHITE_THEME_CANVAS, profileSurface, PROFILE_PRESS_DURATION_MS, resolveProfileSurface } from "../profileGraphite";
import { __testing } from "../ThemeContext";
import type { Palette } from "../ThemeContext";

const { DARK, BLACK, WHITE, LIGHT_FUTURISTIC, HIGH_CONTRAST_DARK } = __testing;

/** sRGB relative luminance, WCAG 2.1 §relative-luminance. */
function luminance(hex: string): number {
  const m = hex.match(/^#([0-9a-f]{6})/i);
  if (!m) throw new Error(`not an opaque hex colour: ${hex}`);
  const n = parseInt(m[1], 16);
  const channel = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : Math.pow((s + 0.055) / 1.055, 2.4);
  };
  return 0.2126 * channel(n >> 16) + 0.7152 * channel((n >> 8) & 255) + 0.0722 * channel(n & 255);
}

function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

/** HSL lightness, as a percentage. The metric the brief's "4–6% lighter" uses. */
function lightness(hex: string): number {
  const n = parseInt(hex.slice(1, 7), 16);
  const [r, g, b] = [(n >> 16) / 255, ((n >> 8) & 255) / 255, (n & 255) / 255];
  return ((Math.max(r, g, b) + Math.min(r, g, b)) / 2) * 100;
}

describe("the shared graphite ramp", () => {
  // These six are the approved values. Pinned literally and only here.
  it("is the approved ramp", () => {
    expect(graphite).toEqual({
      canvasTop: "#3A4049",
      canvasBottom: "#343A42",
      chrome: "#292E36",
      sunken: "#20262E",
      raised: "#454C56",
      raisedStrong: "#474E58",
      steelBorder: "rgba(74, 85, 98, 0.65)",
      quietDivider: "rgba(230, 236, 245, 0.16)",
      primaryText: "#F7F8FA",
      secondaryText: "#C8D0DB"
    });
  });

  // The hierarchy the brief asks for, stated as the only form in which "darker"
  // and "one step lighter" are checkable. Chrome must sit *under* the canvas: a
  // header that out-brightens its own content reads as the subject.
  it("orders chrome below the canvas and elevations above it", () => {
    const order = [
      graphite.sunken,
      graphite.chrome,
      graphite.canvasBottom,
      graphite.canvasTop,
      graphite.raised,
      graphite.raisedStrong
    ].map(luminance);
    expect(order).toEqual([...order].sort((a, b) => a - b));
  });

  it("puts the card step inside the brief's 4-6% band above the canvas", () => {
    const delta = lightness(graphite.raised) - lightness(graphite.canvasTop);
    expect(delta).toBeGreaterThanOrEqual(4);
    expect(delta).toBeLessThanOrEqual(6);
  });

  // The reason tiles sit on the canvas and never inside a card. If this ever
  // rises far enough to be a real step, the constraint documented in
  // `graphite.ts` and honoured by `moduleIcon` can be revisited; until then a
  // tile drawn on the statistics panel would be invisible.
  it("keeps the tile step and the card step at the same conceptual level", () => {
    expect(contrast(graphite.raisedStrong, graphite.raised)).toBeLessThan(1.1);
    expect(contrast(graphite.raisedStrong, graphite.canvasTop)).toBeGreaterThan(1.2);
  });

  // Small text on a card: the 4.5:1 bar, not 3:1. Both weights are read at 10-14pt
  // somewhere on Profile (stat labels, handle, tile labels, utility row).
  it("clears 4.5:1 for both text weights on every surface step", () => {
    for (const surface of [graphite.canvasTop, graphite.canvasBottom, graphite.chrome, graphite.raised, graphite.raisedStrong]) {
      expect(contrast(graphite.primaryText, surface)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(graphite.secondaryText, surface)).toBeGreaterThanOrEqual(4.5);
    }
  });

  // The promotion's whole point: Profile's canvas IS the conversation canvas,
  // by reference, not a second approximation of it.
  it("is the same object the conversation surface reads", () => {
    expect(resolveProfileSurface(DARK).canvasTop).toBe(chatGraphite.canvasTop);
    expect(resolveProfileSurface(DARK).canvasBottom).toBe(chatGraphite.canvasBottom);
    expect(resolveProfileSurface(DARK).chrome).toBe(chatGraphite.headerSurface);
    expect(resolveProfileSurface(DARK).primaryText).toBe(chatGraphite.primaryText);
    expect(resolveProfileSurface(DARK).secondaryText).toBe(chatGraphite.secondaryText);
  });
});

describe("resolveProfileSurface on the graphite theme", () => {
  it("recognises the dark palette by its canvas", () => {
    expect(DARK.background).toBe(GRAPHITE_THEME_CANVAS);
    expect(resolveProfileSurface(DARK).isGraphite).toBe(true);
  });

  it("maps every Profile element onto a ramp step", () => {
    expect(resolveProfileSurface(DARK)).toEqual({
      canvasTop: graphite.canvasTop,
      canvasBottom: graphite.canvasBottom,
      chrome: graphite.chrome,
      raised: graphite.raised,
      raisedStrong: graphite.raisedStrong,
      border: graphite.steelBorder,
      divider: graphite.quietDivider,
      primaryText: graphite.primaryText,
      secondaryText: graphite.secondaryText,
      coverFieldMid: "#3A4049f2",
      coverScrim: "#343A4273",
      isGraphite: true
    });
  });

  // The rejected look was a translucent layer over the UI. Card and tile fills
  // have to be opaque, which for an 8-digit hex means no alpha suffix at all.
  it("gives every surface step an opaque fill", () => {
    const surface = resolveProfileSurface(DARK);
    for (const step of [surface.canvasTop, surface.canvasBottom, surface.chrome, surface.raised, surface.raisedStrong]) {
      expect(step).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  // The cover treatment: the only thing drawn over an uploaded photo is a fade
  // confined to the bottom of the hero, and it must be well short of opaque.
  it("keeps the cover scrim weak enough to be a fade and not fog", () => {
    const alpha = parseInt(resolveProfileSurface(DARK).coverScrim.slice(-2), 16) / 255;
    expect(alpha).toBeGreaterThan(0.3);
    expect(alpha).toBeLessThan(0.5);
  });

  // A generated field IS the cover, so it is allowed to be near-opaque; that is
  // the distinction the header's timing tests exercise.
  it("keeps the generated field near-opaque so it reads as the cover", () => {
    const alpha = parseInt(resolveProfileSurface(DARK).coverFieldMid.slice(-2), 16) / 255;
    expect(alpha).toBeGreaterThan(0.9);
    expect(alpha).toBeLessThan(1);
  });
});

describe("the four themes graphite is not for", () => {
  // The single most important assertion in this file. Graphite is a mid-gray; if
  // it leaked into Black the AMOLED promise would be broken, and Black is the
  // theme a user picks *because* it is black.
  it("leaves Black genuinely black", () => {
    const surface = resolveProfileSurface(BLACK);
    expect(surface.isGraphite).toBe(false);
    expect(surface.canvasTop).toBe("#000000");
    expect(surface.canvasBottom).toBe("#000000");
    for (const step of Object.values(surface)) {
      if (typeof step === "string") expect(step).not.toMatch(/3A4049|343A42|454C56|474E58/i);
    }
  });

  it("leaves the light themes as light, readable pages", () => {
    for (const palette of [WHITE, LIGHT_FUTURISTIC]) {
      const surface = resolveProfileSurface(palette);
      expect(surface.isGraphite).toBe(false);
      expect(luminance(surface.canvasTop)).toBeGreaterThan(0.8);
      // Dark text on a light page, still at the small-text bar.
      expect(contrast(surface.primaryText, surface.canvasTop)).toBeGreaterThanOrEqual(4.5);
      expect(contrast(surface.secondaryText, surface.raised)).toBeGreaterThanOrEqual(4.5);
    }
  });

  // White's `surface` IS its `background`, so an elevation built from fill alone
  // vanishes on exactly the theme whose promise is a plain page. The border is
  // what states the edge there, so it must not be missing.
  it("carries a visible border on the theme where the fill cannot separate", () => {
    const surface = resolveProfileSurface(WHITE);
    expect(surface.canvasTop).toBe(surface.raised);
    expect(surface.border).toBe(WHITE.border);
    expect(contrast(surface.border, surface.canvasTop)).toBeGreaterThan(1.15);
  });

  it("hands every non-graphite theme its own roles and invents nothing", () => {
    for (const palette of [BLACK, WHITE, LIGHT_FUTURISTIC]) {
      const surface = resolveProfileSurface(palette);
      expect(surface.canvasTop).toBe(palette.background);
      expect(surface.chrome).toBe(palette.surfaceRaised);
      expect(surface.raised).toBe(palette.surface);
      expect(surface.border).toBe(palette.border);
      expect(surface.primaryText).toBe(palette.text);
      expect(surface.secondaryText).toBe(palette.muted);
    }
  });

  // Increased Contrast overrides `background` to #000000, which moves Profile off
  // graphite and onto a black ramp. That is the correct direction — the black ramp
  // has strictly more separation — but it must be a deliberate, asserted outcome
  // rather than something noticed on a device.
  it("moves off graphite under Increased Contrast", () => {
    const highContrast = { ...DARK, ...HIGH_CONTRAST_DARK } as Palette;
    const surface = resolveProfileSurface(highContrast);
    expect(surface.isGraphite).toBe(false);
    expect(surface.canvasTop).toBe("#000000");
    expect(contrast(surface.primaryText, surface.canvasTop)).toBeGreaterThan(
      contrast(graphite.primaryText, graphite.canvasTop)
    );
  });
});

describe("profileSurface's cache", () => {
  it("returns one object per theme so no component allocates during render", () => {
    expect(profileSurface(DARK)).toBe(profileSurface(DARK));
    expect(profileSurface(WHITE)).toBe(profileSurface(WHITE));
  });

  it("agrees with the uncached resolver on every theme", () => {
    for (const palette of [DARK, BLACK, WHITE, LIGHT_FUTURISTIC]) {
      expect(profileSurface(palette)).toEqual(resolveProfileSurface(palette));
    }
  });

  // The trap the key exists to avoid. Black and high-contrast dark share
  // `background: #000000` but differ in `surface` (#070a0d vs #0a0a0a), so a
  // cache keyed on the canvas alone would serve Black's surfaces to a user who
  // turned Increased Contrast on. Guarding the premise too: if these palettes
  // ever stop colliding, this test stops testing anything.
  it("separates two themes that share a canvas but not their surfaces", () => {
    const highContrast = { ...DARK, ...HIGH_CONTRAST_DARK } as Palette;
    expect(highContrast.background).toBe(BLACK.background);
    expect(highContrast.surface).not.toBe(BLACK.surface);
    expect(profileSurface(BLACK).raised).toBe(BLACK.surface);
    expect(profileSurface(highContrast).raised).toBe(highContrast.surface);
    expect(profileSurface(BLACK).raised).toBe(BLACK.surface);
  });

  it("re-resolves when the live palette is mutated in place", () => {
    // `applyPaletteToLegacyColors` mutates the shared object rather than
    // replacing it, so the cache can never rely on identity.
    const live = { ...DARK } as Palette;
    expect(profileSurface(live).isGraphite).toBe(true);
    live.background = "#000000";
    expect(profileSurface(live).isGraphite).toBe(false);
  });
});

describe("press feedback", () => {
  it("sits in the brief's 120-180ms band", () => {
    expect(PROFILE_PRESS_DURATION_MS).toBeGreaterThanOrEqual(120);
    expect(PROFILE_PRESS_DURATION_MS).toBeLessThanOrEqual(180);
  });
});
