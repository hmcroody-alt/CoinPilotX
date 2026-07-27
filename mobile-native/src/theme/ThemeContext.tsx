/**
 * Runtime theming for PulseSoc.
 *
 * The app historically imported a frozen dark palette (`theme/colors`) directly
 * into ~50 screens. Rewriting every one of those call sites in a single pass is
 * unnecessary churn, so this module does two things at once:
 *
 *  1. Exposes `useTheme()` — the forward-looking API. All new code (the entire
 *     settings platform) reads its palette from here and re-renders correctly
 *     when the user changes theme, contrast, or density.
 *
 *  2. Publishes the active palette back into the shared `colors` object via
 *     `applyPaletteToLegacyColors`, then bumps `themeEpoch`. `AppRoot` keys the
 *     navigation tree on that epoch, so legacy screens that captured colors in
 *     a module-scope `StyleSheet.create` are remounted with the new values.
 *
 * (2) is a deliberate migration bridge, not the end state — as screens migrate
 * to `useTheme()` they stop depending on the remount and the epoch key becomes
 * a no-op for them.
 */

import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Appearance, ColorSchemeName, Platform, Text, TextInput } from "react-native";
import { colors as legacyColors } from "./colors";
import type { AppearancePreferences, AccessibilityPreferences, ThemeMode } from "../settings/schema";

export type Palette = typeof legacyColors;

/** Resolved dark palette — the app's canonical look. */
const DARK: Palette = { ...legacyColors };

/**
 * Light palette. Derived to preserve PulseSoc's semantic roles (accent stays
 * the signal green, danger stays rose) while inverting the surface ramp and
 * darkening accents enough to pass 4.5:1 against light surfaces.
 */
const LIGHT: Palette = {
  background: "#f6f8fb",
  surface: "#ffffff",
  surfaceRaised: "#eef2f7",
  text: "#0b141c",
  muted: "#5b6b7c",
  accent: "#00966f",
  accentStrong: "#0071a6",
  warning: "#a06a00",
  danger: "#c02341",
  border: "#d3dde7",
  intelligence: "#6a3fd6",
  creator: "#0f8f83",
  economy: "#9a7413",
  safety: "#0a8a52",
  crypto: "#0a7ba3",
  disabled: "#a3b0bc",
  focus: "#0071a6",
  glass: "rgba(255, 255, 255, 0.86)",
  glassStrong: "rgba(246, 248, 251, 0.96)",
  signalDim: "rgba(0, 150, 111, 0.10)",
  signalSoft: "rgba(0, 113, 166, 0.10)",
  dangerSoft: "rgba(192, 35, 65, 0.10)",
  warningSoft: "rgba(160, 106, 0, 0.10)"
};

/** High-contrast overrides: maximize text/border separation, drop translucency. */
const HIGH_CONTRAST_DARK: Partial<Palette> = {
  background: "#000000",
  surface: "#0a0a0a",
  surfaceRaised: "#161616",
  text: "#ffffff",
  muted: "#d6dde4",
  border: "#7b8b99",
  accent: "#4dffc8",
  glass: "#0a0a0a",
  glassStrong: "#000000"
};

const HIGH_CONTRAST_LIGHT: Partial<Palette> = {
  background: "#ffffff",
  surface: "#ffffff",
  surfaceRaised: "#f0f0f0",
  text: "#000000",
  muted: "#2b3843",
  border: "#4a5a68",
  accent: "#00614a",
  glass: "#ffffff",
  glassStrong: "#ffffff"
};

/** Spacing/typography scale. `compactDensity` tightens rows for power users. */
export type Metrics = {
  rowMinHeight: number;
  rowPaddingVertical: number;
  rowPaddingHorizontal: number;
  sectionGap: number;
  radius: number;
  fontScale: number;
  /** `fontWeight` to use for body copy — bumped by the boldText preference. */
  bodyWeight: "400" | "600";
  titleWeight: "700" | "800" | "900";
};

export type Theme = {
  mode: ThemeMode;
  /** The scheme actually in effect after resolving `system`. */
  scheme: "light" | "dark";
  colors: Palette;
  metrics: Metrics;
  /** True when animations should be skipped or shortened. */
  reduceMotion: boolean;
  /** True when blur/translucency should be replaced with opaque fills. */
  reduceTransparency: boolean;
  hapticFeedback: boolean;
  /** Multiply any explicit fontSize by this. */
  scaleFont: (size: number) => number;
  /** Animation duration honouring reduce-motion (returns 0 when reduced). */
  duration: (ms: number) => number;
};

function resolveScheme(mode: ThemeMode, system: ColorSchemeName): "light" | "dark" {
  if (mode === "light") return "light";
  if (mode === "dark") return "dark";
  return system === "light" ? "light" : "dark";
}

export function buildTheme(
  appearance: AppearancePreferences,
  accessibility: AccessibilityPreferences,
  systemScheme: ColorSchemeName
): Theme {
  const scheme = resolveScheme(appearance.theme, systemScheme);
  const base = scheme === "light" ? LIGHT : DARK;
  const contrast = accessibility.highContrast ? (scheme === "light" ? HIGH_CONTRAST_LIGHT : HIGH_CONTRAST_DARK) : null;
  const palette: Palette = contrast ? { ...base, ...contrast } : { ...base };

  if (appearance.reduceTransparency || accessibility.highContrast) {
    palette.glass = palette.surface;
    palette.glassStrong = palette.background;
  }

  const compact = appearance.compactDensity;
  const fontScale = appearance.fontScale;

  return {
    mode: appearance.theme,
    scheme,
    colors: palette,
    metrics: {
      rowMinHeight: Math.round((compact ? 46 : 56) * Math.max(1, fontScale)),
      rowPaddingVertical: compact ? 8 : 12,
      rowPaddingHorizontal: 16,
      sectionGap: compact ? 18 : 26,
      radius: 14,
      fontScale,
      bodyWeight: accessibility.boldText ? "600" : "400",
      titleWeight: accessibility.boldText ? "900" : "800"
    },
    reduceMotion: accessibility.reduceMotion,
    reduceTransparency: appearance.reduceTransparency || accessibility.highContrast,
    hapticFeedback: accessibility.hapticFeedback,
    scaleFont: (size: number) => Math.round(size * fontScale),
    duration: (ms: number) => (accessibility.reduceMotion ? 0 : ms)
  };
}

/**
 * Copy the active palette into the shared `colors` object so legacy screens
 * that captured it at module scope observe the new values after remount.
 * Mutation is intentional and confined to this function.
 */
export function applyPaletteToLegacyColors(palette: Palette) {
  (Object.keys(palette) as (keyof Palette)[]).forEach((key) => {
    legacyColors[key] = palette[key];
  });
}

/**
 * Ensure OS-level font scaling is honoured and bounded across the whole app.
 *
 * Scope note: React Native resolves `fontSize` per component, so there is no
 * supported way to retroactively scale a screen whose StyleSheet hardcodes
 * numeric sizes. The in-app `fontScale` preference is therefore applied through
 * `useTheme().scaleFont` — every settings surface uses it, and other screens
 * pick it up as they migrate. What this function guarantees app-wide is that
 * OS accessibility text sizing is never silently disabled, and never scales far
 * enough to break layout.
 */
export function applyGlobalFontScale(scale: number) {
  const targets = [Text, TextInput] as unknown as { defaultProps?: Record<string, unknown> }[];
  const maxMultiplier = Math.min(2, Math.max(1.2, 1.6 / Math.max(scale, 0.85)));
  targets.forEach((target) => {
    if (!target.defaultProps) target.defaultProps = {};
    target.defaultProps.allowFontScaling = true;
    target.defaultProps.maxFontSizeMultiplier = maxMultiplier;
  });
  // Android renders sub-pixel font scales inconsistently; round to 2dp.
  return Platform.OS === "android" ? Number(scale.toFixed(2)) : scale;
}

const ThemeContext = createContext<Theme | null>(null);

export function ThemeProvider({
  appearance,
  accessibility,
  children
}: {
  appearance: AppearancePreferences;
  accessibility: AccessibilityPreferences;
  children: ReactNode;
}) {
  const [systemScheme, setSystemScheme] = useState<ColorSchemeName>(() => Appearance.getColorScheme());

  useEffect(() => {
    // Only subscribe while the user is actually on "system" — avoids waking
    // React on every OS appearance change for users pinned to light/dark.
    if (appearance.theme !== "system") return;
    setSystemScheme(Appearance.getColorScheme());
    const subscription = Appearance.addChangeListener(({ colorScheme }) => setSystemScheme(colorScheme));
    return () => subscription.remove();
  }, [appearance.theme]);

  const theme = useMemo(
    () => buildTheme(appearance, accessibility, systemScheme),
    [appearance, accessibility, systemScheme]
  );

  useEffect(() => {
    applyPaletteToLegacyColors(theme.colors);
  }, [theme.colors]);

  return <ThemeContext.Provider value={theme}>{children}</ThemeContext.Provider>;
}

/**
 * Read the active theme. Falls back to the default dark theme when rendered
 * outside a provider so isolated component tests and Storybook-style previews
 * do not need to wrap every tree.
 */
export function useTheme(): Theme {
  const context = useContext(ThemeContext);
  const fallback = useMemo(
    () =>
      buildTheme(
        { theme: "dark", fontScale: 1, reduceTransparency: false, compactDensity: false },
        {
          reduceMotion: false,
          boldText: false,
          highContrast: false,
          captionsEnabled: true,
          hapticFeedback: true,
          screenReaderHints: true
        },
        "dark"
      ),
    []
  );
  return context ?? fallback;
}

/**
 * Bump-able identity for the active visual configuration. `AppRoot` uses this
 * as a React `key` so legacy module-scope StyleSheets are rebuilt on change.
 */
export function useThemeEpoch(theme: Theme): string {
  return `${theme.scheme}:${theme.metrics.fontScale}:${theme.reduceTransparency ? 1 : 0}:${theme.metrics.bodyWeight}:${
    theme.metrics.rowMinHeight
  }`;
}

/** Convenience for memoizing StyleSheet factories against the active theme. */
export function useThemedStyles<T>(factory: (theme: Theme) => T): T {
  const theme = useTheme();
  return useMemo(() => factory(theme), [factory, theme]);
}

export const __testing = { LIGHT, DARK, HIGH_CONTRAST_DARK, HIGH_CONTRAST_LIGHT, resolveScheme };
