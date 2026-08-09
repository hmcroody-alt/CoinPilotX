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
 *     `applyPaletteToLegacyColors`, so legacy code that reads `colors.x` at
 *     render time observes the new values on its next render. Screens that
 *     captured colors in a module-scope `StyleSheet.create` do NOT follow a
 *     change (module scope runs once); they must migrate to
 *     `useThemedStyles`, which is the ongoing per-screen migration.
 *
 * (2) is a deliberate migration bridge, not the end state.
 */

import { createContext, ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { Appearance, ColorSchemeName, Platform, Text, TextInput } from "react-native";
import { colors as legacyColors } from "./colors";
import { bumpThemedStylesEpoch } from "./themedStyles";
import type { AppearancePreferences, AccessibilityPreferences, ThemeMode } from "../settings/schema";

export type Palette = typeof legacyColors;

/** Resolved dark palette — the app's canonical look and the default theme. */
const DARK: Palette = { ...legacyColors };

/**
 * Black: the AMOLED variant of dark. True-black background, near-black
 * surfaces so cards still read as cards, and the dark accents unchanged —
 * they were tuned against near-black already. Glass goes fully opaque because
 * translucency over pure black just looks like banding.
 */
const BLACK: Palette = {
  ...legacyColors,
  background: "#000000",
  surface: "#070a0d",
  surfaceRaised: "#10161c",
  border: "#1b2b36",
  glass: "rgba(5, 8, 10, 0.92)",
  glassStrong: "rgba(3, 5, 7, 0.97)"
};

/**
 * Light Futuristic — the original light theme (glassy, tinted surfaces).
 * Derived to preserve PulseSoc's semantic roles (accent stays the signal
 * green, danger stays rose) while inverting the surface ramp and darkening
 * accents enough to pass 4.5:1 against light surfaces.
 */
const LIGHT_FUTURISTIC: Palette = {
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

/**
 * White: plain neutral light. Same accessible light accents as Light
 * Futuristic, but the tinted blue-gray surface ramp flattens to plain white
 * and neutral grays, and glass is simply opaque white — no atmosphere.
 */
const WHITE: Palette = {
  ...LIGHT_FUTURISTIC,
  background: "#ffffff",
  surface: "#ffffff",
  surfaceRaised: "#f4f5f6",
  text: "#111417",
  muted: "#5d6670",
  border: "#dde1e5",
  glass: "#ffffff",
  glassStrong: "#fafafa"
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

/**
 * How the galactic background layers should render under this theme. The
 * profile lives on the theme (not on each screen) so all atmosphere surfaces
 * agree: `intensity` scales opacity, and `enabled: false` means render nothing
 * at all — White is deliberately atmosphere-free. Content always sits above;
 * this only describes the layer underneath, and components must still honour
 * Reduce Motion for any animation of it.
 */
export type GalacticBackgroundProfile = {
  enabled: boolean;
  /** 0..1 opacity multiplier for star/nebula layers. */
  intensity: number;
  variant: "dark" | "light";
};

export type Theme = {
  mode: ThemeMode;
  /** The scheme actually in effect after resolving `system`. */
  scheme: "light" | "dark";
  colors: Palette;
  metrics: Metrics;
  /**
   * System chrome derived from the scheme: dark/black themes get light
   * status-bar content and a dark keyboard; light themes the reverse.
   * Values match `expo-status-bar`'s `style` and RN's `keyboardAppearance`.
   */
  statusBarStyle: "light" | "dark";
  keyboardAppearance: "light" | "dark";
  galacticBackground: GalacticBackgroundProfile;
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
  if (mode === "light_futuristic" || mode === "white") return "light";
  if (mode === "dark" || mode === "black") return "dark";
  return system === "light" ? "light" : "dark";
}

/** The concrete palette for a mode, after `system` has been resolved. */
function paletteFor(mode: ThemeMode, scheme: "light" | "dark"): Palette {
  if (mode === "black") return BLACK;
  if (mode === "white") return WHITE;
  if (mode === "light_futuristic") return LIGHT_FUTURISTIC;
  if (mode === "dark") return DARK;
  // `system`: follow the OS scheme with the two canonical palettes.
  return scheme === "light" ? LIGHT_FUTURISTIC : DARK;
}

/**
 * Atmosphere per theme. Dark keeps the full galactic treatment; Black dims it
 * (stars over true black bloom on OLED); Light Futuristic gets a faint light
 * variant; White gets none — that theme's promise is a plain page.
 */
function galacticProfileFor(mode: ThemeMode, scheme: "light" | "dark"): GalacticBackgroundProfile {
  if (mode === "white") return { enabled: false, intensity: 0, variant: "light" };
  if (mode === "black") return { enabled: true, intensity: 0.55, variant: "dark" };
  if (mode === "light_futuristic") return { enabled: true, intensity: 0.35, variant: "light" };
  if (mode === "dark") return { enabled: true, intensity: 1, variant: "dark" };
  return scheme === "light"
    ? { enabled: true, intensity: 0.35, variant: "light" }
    : { enabled: true, intensity: 1, variant: "dark" };
}

export function buildTheme(
  appearance: AppearancePreferences,
  accessibility: AccessibilityPreferences,
  systemScheme: ColorSchemeName
): Theme {
  // Dark is the only released appearance for now.
  // Keep the other theme implementations intact for future activation.
  const activeTheme: ThemeMode = "dark";
  const scheme = resolveScheme(activeTheme, systemScheme);
  const base = paletteFor(activeTheme, scheme);
  const contrast = accessibility.highContrast ? (scheme === "light" ? HIGH_CONTRAST_LIGHT : HIGH_CONTRAST_DARK) : null;
  const palette: Palette = contrast ? { ...base, ...contrast } : { ...base };

  if (appearance.reduceTransparency || accessibility.highContrast) {
    palette.glass = palette.surface;
    palette.glassStrong = palette.background;
  }

  const compact = appearance.compactDensity;
  const fontScale = appearance.fontScale;

  return {
    mode: activeTheme,
    scheme,
    colors: palette,
    statusBarStyle: scheme === "dark" ? "light" : "dark",
    keyboardAppearance: scheme === "dark" ? "dark" : "light",
    galacticBackground: galacticProfileFor(activeTheme, scheme),
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
  // Invalidate every `createThemedStyles` sheet so legacy module-scope styles
  // rebuild from the new palette on their next property access.
  bumpThemedStylesEpoch();
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

  // Publish synchronously (render phase) so the very first frame of legacy
  // screens builds from the active palette instead of flashing default dark.
  // The mutation is idempotent and confined to the shared bridge object.
  useMemo(() => applyPaletteToLegacyColors(theme.colors), [theme.colors]);

  useEffect(() => {
    applyPaletteToLegacyColors(theme.colors);
    // Global keyboard chrome. RN resolves `keyboardAppearance` per TextInput
    // and most inputs in the app never set it, so the default is the one lever
    // that reaches all of them: dark keyboards on Dark/Black, light keyboards
    // on Light Futuristic/White. An input that sets its own value still wins.
    const input = TextInput as unknown as { defaultProps?: Record<string, unknown> };
    if (!input.defaultProps) input.defaultProps = {};
    input.defaultProps.keyboardAppearance = theme.keyboardAppearance;
  }, [theme.colors, theme.keyboardAppearance]);

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
 * Bump-able identity for the active visual configuration. Screens that want a
 * local remount on theme change can use this as a React `key` — but never on
 * NavigationContainer, which would reset navigation state.
 */
export function useThemeEpoch(theme: Theme): string {
  // `mode` (not just scheme) must be part of the identity: dark → black keeps
  // scheme "dark" but changes every surface color, and legacy screens only
  // observe that through the remount this key forces.
  return `${theme.mode}:${theme.scheme}:${theme.metrics.fontScale}:${theme.reduceTransparency ? 1 : 0}:${
    theme.metrics.bodyWeight
  }:${theme.metrics.rowMinHeight}`;
}

/** Convenience for memoizing StyleSheet factories against the active theme. */
export function useThemedStyles<T>(factory: (theme: Theme) => T): T {
  const theme = useTheme();
  return useMemo(() => factory(theme), [factory, theme]);
}

export const __testing = {
  LIGHT_FUTURISTIC,
  DARK,
  BLACK,
  WHITE,
  HIGH_CONTRAST_DARK,
  HIGH_CONTRAST_LIGHT,
  resolveScheme,
  paletteFor,
  galacticProfileFor
};
