/**
 * The four-theme system, pinned.
 *
 * These tests are the contract for Settings → Appearance → Background & Theme:
 * every mode resolves to a scheme, a palette, system chrome, and a galactic
 * background profile — and the legacy `"light"` value stored by older builds
 * keeps meaning what it meant.
 */

import { buildTheme, __testing } from "../ThemeContext";
import { normalizePreferences, THEME_MODES, ThemeMode } from "../../settings/schema";

const { DARK, BLACK, WHITE, LIGHT_FUTURISTIC, resolveScheme, galacticProfileFor } = __testing;

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
    const theme = themeFor("black");
    expect(theme.colors.background).toBe("#000000");
    expect(theme.colors.background).not.toBe(DARK.background);
    // Accents carry over — they were tuned against near-black already.
    expect(theme.colors.accent).toBe(DARK.accent);
  });

  it("white is plain white, distinct from light futuristic", () => {
    const theme = themeFor("white");
    expect(theme.colors.background).toBe("#ffffff");
    expect(theme.colors.background).not.toBe(LIGHT_FUTURISTIC.background);
    expect(theme.colors.glass).toBe("#ffffff");
  });

  it("system follows the OS with the two canonical palettes", () => {
    expect(themeFor("system", "dark").colors.background).toBe(DARK.background);
    expect(themeFor("system", "light").colors.background).toBe(LIGHT_FUTURISTIC.background);
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
  it("dark and black get light status-bar content and a dark keyboard", () => {
    (["dark", "black"] as const).forEach((mode) => {
      const theme = themeFor(mode);
      expect(theme.statusBarStyle).toBe("light");
      expect(theme.keyboardAppearance).toBe("dark");
    });
  });

  it("light futuristic and white get dark status-bar content and a light keyboard", () => {
    (["light_futuristic", "white"] as const).forEach((mode) => {
      const theme = themeFor(mode);
      expect(theme.statusBarStyle).toBe("dark");
      expect(theme.keyboardAppearance).toBe("light");
    });
  });
});

describe("galactic background profile", () => {
  it("white renders no atmosphere at all", () => {
    expect(themeFor("white").galacticBackground).toEqual({ enabled: false, intensity: 0, variant: "light" });
  });

  it("black dims the atmosphere below dark's", () => {
    const black = themeFor("black").galacticBackground;
    const dark = themeFor("dark").galacticBackground;
    expect(black.enabled).toBe(true);
    expect(black.intensity).toBeLessThan(dark.intensity);
  });

  it("system inherits the profile of whichever scheme is active", () => {
    expect(themeFor("system", "dark").galacticBackground).toEqual(galacticProfileFor("dark", "dark"));
    expect(themeFor("system", "light").galacticBackground).toEqual(galacticProfileFor("light_futuristic", "light"));
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
