/**
 * Theme foundations and the current dark-only release contract.
 *
 * These tests are the contract for Settings → Appearance → Background & Theme:
 * every mode resolves to a scheme, a palette, system chrome, and a galactic
 * background profile — and the legacy `"light"` value stored by older builds
 * keeps meaning what it meant.
 */

import { buildTheme, __testing } from "../ThemeContext";
import { normalizePreferences, THEME_MODES, ThemeMode } from "../../settings/schema";

const { DARK, BLACK, WHITE, LIGHT_FUTURISTIC, resolveScheme, paletteFor, galacticProfileFor } = __testing;

const ACCESSIBILITY = {
  reduceMotion: false,
  boldText: false,
  highContrast: false,
  captionsEnabled: true,
  hapticFeedback: true,
  screenReaderHints: true
};

function themeFor(mode: ThemeMode, systemScheme: "light" | "dark" = "dark") {
  return buildTheme(
    { theme: mode, fontScale: 1, reduceTransparency: false, compactDensity: false },
    ACCESSIBILITY,
    systemScheme
  );
}

describe("scheme resolution", () => {
  it("maps every explicit mode to its scheme, and system to the OS", () => {
    expect(resolveScheme("dark", "light")).toBe("dark");
    expect(resolveScheme("black", "light")).toBe("dark");
    expect(resolveScheme("light_futuristic", "dark")).toBe("light");
    expect(resolveScheme("white", "dark")).toBe("light");
    expect(resolveScheme("system", "light")).toBe("light");
    expect(resolveScheme("system", "dark")).toBe("dark");
  });
});

describe("palettes", () => {
  it("black is true black, distinct from dark", () => {
    const palette = paletteFor("black", "dark");
    expect(palette.background).toBe("#000000");
    expect(palette.background).not.toBe(DARK.background);
    // Accents carry over — they were tuned against near-black already.
    expect(palette.accent).toBe(DARK.accent);
  });

  it("white is plain white, distinct from light futuristic", () => {
    const palette = paletteFor("white", "light");
    expect(palette.background).toBe("#ffffff");
    expect(palette.background).not.toBe(LIGHT_FUTURISTIC.background);
    expect(palette.glass).toBe("#ffffff");
  });

  it("system follows the OS with the two canonical palettes", () => {
    expect(paletteFor("system", "dark").background).toBe(DARK.background);
    expect(paletteFor("system", "light").background).toBe(LIGHT_FUTURISTIC.background);
  });

  it("every palette defines every token — no theme can render an undefined color", () => {
    const keys = Object.keys(DARK).sort();
    [BLACK, WHITE, LIGHT_FUTURISTIC].forEach((palette) => {
      expect(Object.keys(palette).sort()).toEqual(keys);
      Object.values(palette).forEach((value) => expect(typeof value).toBe("string"));
    });
  });
});

describe("system chrome", () => {
  it("all stored modes render with released dark system chrome", () => {
    THEME_MODES.forEach((mode) => {
      const theme = themeFor(mode);
      expect(theme.statusBarStyle).toBe("light");
      expect(theme.keyboardAppearance).toBe("dark");
      expect(theme.mode).toBe("dark");
    });
  });
});

describe("galactic background profile", () => {
  it("keeps future profiles defined while every released mode renders dark", () => {
    expect(galacticProfileFor("white", "light")).toEqual({ enabled: false, intensity: 0, variant: "light" });
    expect(galacticProfileFor("black", "dark").intensity).toBeLessThan(galacticProfileFor("dark", "dark").intensity);
    THEME_MODES.forEach((mode) => {
      expect(themeFor(mode).galacticBackground).toEqual(galacticProfileFor("dark", "dark"));
    });
  });
});

describe("reduce transparency vs high contrast", () => {
  /**
   * These two must stay distinguishable on the theme.
   *
   * `reduceTransparency` is deliberately the OR of the appearance preference
   * and high contrast, because almost every consumer wants "stop layering" and
   * both modes imply it. But high contrast additionally *substitutes* the
   * palette, and a consumer that can only see the OR cannot tell the two
   * apart. `ChatWallpaper` was doing exactly that: it read the OR and replaced
   * the whole graphite canvas with `colors.background`, so a phone with only
   * Reduce Transparency on painted near-black while every other device painted
   * graphite. Keeping `highContrast` separately readable is what lets that
   * component drop the alpha layers without also dropping the palette.
   */
  const build = (reduceTransparency: boolean, highContrast: boolean) =>
    buildTheme(
      { theme: "dark", fontScale: 1, reduceTransparency, compactDensity: false },
      { ...ACCESSIBILITY, highContrast },
      "dark"
    );

  it("reports the appearance preference without claiming high contrast", () => {
    const theme = build(true, false);
    expect(theme.reduceTransparency).toBe(true);
    expect(theme.highContrast).toBe(false);
  });

  it("implies reduced transparency when high contrast is on", () => {
    const theme = build(false, true);
    expect(theme.reduceTransparency).toBe(true);
    expect(theme.highContrast).toBe(true);
  });

  it("leaves both off by default", () => {
    const theme = build(false, false);
    expect(theme.reduceTransparency).toBe(false);
    expect(theme.highContrast).toBe(false);
  });
});

describe("stored-value migration", () => {
  it('accepts every current mode and maps legacy "light" to light_futuristic', () => {
    THEME_MODES.forEach((mode) => {
      expect(normalizePreferences({ appearance: { theme: mode } }).appearance.theme).toBe(mode);
    });
    expect(normalizePreferences({ appearance: { theme: "light" } }).appearance.theme).toBe("light_futuristic");
  });

  it("rejects unknown modes back to the default rather than guessing", () => {
    expect(normalizePreferences({ appearance: { theme: "hologram" } }).appearance.theme).toBe("system");
  });
});
