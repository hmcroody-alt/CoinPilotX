import { colors } from "./colors";

export const logiNexus = {
  colors: {
    ...colors,
    space: colors.background,
    elevated: colors.surfaceRaised,
    panel: colors.glass,
    activeSignal: colors.accent,
    neutralText: colors.text,
    mutedText: colors.muted,
    home: {
      backgroundDeepSpace: "#030712",
      backgroundNetworkVoid: "#07101d",
      surfaceGlass: "rgba(11, 22, 51, 0.03)",
      surfaceGlassStrong: "rgba(18, 26, 61, 0.03)",
      surfaceSignal: "rgba(50, 230, 179, 0.105)",
      /**
       * The feed's hairline: post separators, count rows, panel edges. Used only
       * by the three Home Feed modules (`HomeScreen`, `PostCard`,
       * `HomePulseComposer`), so it is a feed token in practice as well as name.
       *
       * It was `rgba(121, 210, 255, 0.18)` — a pale cyan, chosen when the feed
       * sat on its own flat near-black fill, where any cool hairline reads the
       * same. The feed now sits on `PulseBackground`'s indigo-to-violet field,
       * and cyan is the one cool hue that is *not* in that field: several
       * hundred separators in a foreign hue is what makes a surface look
       * assembled from two designs. This is the same lightness and alpha in the
       * background's own family — muted indigo rather than violet, because a
       * violet separator on a violet field stops being a division and starts
       * being decoration.
       */
      borderSubtle: "rgba(100, 160, 255, 0.28)",
      borderActive: "rgba(45, 226, 194, 0.55)",
      borderIntelligence: "rgba(139, 92, 246, 0.5)",
      borderCreator: "rgba(45, 226, 194, 0.48)",
      borderSafety: "rgba(63, 240, 160, 0.5)",
      accentPrimary: colors.accent,
      accentSecondary: colors.accentStrong,
      accentUndx: colors.intelligence,
      accentRadio: "#42e7d4",
      accentLive: colors.danger,
      accentSafety: colors.safety,
      accentCreator: colors.creator
    },
    /**
     * "Live" business-profile palette. Its own nested namespace, the same shape
     * as `home`, so this surface can be restyled without disturbing app-wide
     * tokens.
     *
     * Structured so a light theme can be added later: the screen references
     * every value by name and inlines none of them, so a `businessLiveLight`
     * sibling swapped in at the theme boundary is the only change light mode
     * would need. Surfaces are expressed as rgba over the known background
     * rather than as flat hexes, because that is what lets them survive a
     * background change.
     *
     * THIS IS A BUSINESS SURFACE and is subject to the black/white/green lock —
     * `BusinessProfileScreen` and `BusinessBuyerPreviewScreen` are the two
     * screens in the commerce family that run dark instead of on `storeLight`'s
     * white page. It is the only locked palette that does not live in its own
     * `*Light.ts` file, which is exactly why the original sweep missed it;
     * `businessPaletteLock` now scans this file for this reason.
     *
     * The lock moved four things here, all of them at matched luminance so only
     * the hue changed:
     *
     *   background  #03070C → #050A08   0.0020 → 0.0027, still black
     *   textDim     #5A7186 → #67736E   0.157  → 0.163
     *   textMuted   #8FA5B8 → #9DA3A3   0.3621 → 0.3601
     *   secondary   #3FD4FF → #7FE9D0   0.554  → 0.673
     *
     * `secondary` is the only one that is a real change rather than a
     * neutralisation. It was a cyan and is now a pale mint — a lighter step of
     * `accent` rather than a hue away from it. That is a genuine loss of
     * separation, and it is acceptable here only because of how `secondary` is
     * actually used: it is the far stop of gradients that start at `accent`
     * (the verification ring, the Save button, the rotating sweep), where a
     * two-step mint ramp is if anything more coherent than mint→cyan, and it
     * tints two glyphs that carry their own label (`accessibilityLabel=
     * "Verified"`, and the row icons beside their text). Nothing distinguishes
     * a state by `accent`-vs-`secondary` alone.
     *
     * `textPrimary` #EEF6FB is left as it is. Blue leads it, but at 5%
     * saturation it is a near-white and sits in the same class as the cool
     * neutrals the lock explicitly keeps (#C7CDD3, #ADB1B8).
     */
    businessLive: {
      background: "#050A08",
      /**
       * The reference design blurs its panels. No blur library is installed
       * (see the report's dependency note), so these sit slightly more opaque
       * than a true blur would need, which keeps text contrast honest when a
       * panel overlaps a busy cover image.
       */
      panel: "rgba(16, 28, 25, 0.72)",
      panelStrong: "rgba(16, 28, 25, 0.92)",
      panelRaised: "rgba(22, 36, 33, 0.86)",
      accent: "#2EE6A8",
      accentSoft: "rgba(46, 230, 168, 0.16)",
      accentGlow: "rgba(46, 230, 168, 0.38)",
      secondary: "#7FE9D0",
      secondarySoft: "rgba(127, 233, 208, 0.16)",
      warning: "#F5B544",
      warningSoft: "rgba(245, 181, 68, 0.14)",
      warningGlow: "rgba(245, 181, 68, 0.32)",
      textPrimary: "#EEF6FB",
      textMuted: "#9DA3A3",
      textDim: "#67736E",
      hairline: "rgba(64, 224, 178, 0.14)",
      hairlineStrong: "rgba(64, 224, 178, 0.28)",
      /** Sheen for the rotating card border and the verification scan stripe. */
      sheen: "rgba(238, 246, 251, 0.10)",
      gridLine: "rgba(127, 233, 208, 0.18)",
      overlayScrim: "rgba(5, 10, 8, 0.82)",
      danger: colors.danger
    }
  },
  typography: {
    display: { fontSize: 34, lineHeight: 39, fontWeight: "900" as const },
    title: { fontSize: 24, lineHeight: 30, fontWeight: "900" as const },
    sectionTitle: { fontSize: 18, lineHeight: 24, fontWeight: "900" as const },
    body: { fontSize: 15, lineHeight: 22, fontWeight: "600" as const },
    metadata: { fontSize: 12, lineHeight: 17, fontWeight: "800" as const },
    label: { fontSize: 12, lineHeight: 16, fontWeight: "900" as const },
    button: { fontSize: 14, lineHeight: 18, fontWeight: "900" as const },
    metric: { fontSize: 26, lineHeight: 31, fontWeight: "900" as const },
    home: {
      brand: { fontSize: 27, lineHeight: 32, fontWeight: "900" as const },
      heroLabel: { fontSize: 12, lineHeight: 16, fontWeight: "900" as const },
      heroMetric: { fontSize: 34, lineHeight: 39, fontWeight: "900" as const },
      heroSupporting: { fontSize: 14, lineHeight: 20, fontWeight: "700" as const },
      sectionLabel: { fontSize: 13, lineHeight: 17, fontWeight: "900" as const },
      cardAuthor: { fontSize: 16, lineHeight: 20, fontWeight: "900" as const },
      cardBody: { fontSize: 16, lineHeight: 23, fontWeight: "600" as const },
      cardMetadata: { fontSize: 12, lineHeight: 17, fontWeight: "800" as const },
      cardMetric: { fontSize: 13, lineHeight: 17, fontWeight: "900" as const },
      buttonPrimary: { fontSize: 15, lineHeight: 19, fontWeight: "900" as const },
      buttonSecondary: { fontSize: 13, lineHeight: 17, fontWeight: "900" as const },
      badge: { fontSize: 11, lineHeight: 15, fontWeight: "900" as const },
      tab: { fontSize: 14, lineHeight: 18, fontWeight: "900" as const },
      emptyTitle: { fontSize: 18, lineHeight: 23, fontWeight: "900" as const },
      emptyBody: { fontSize: 14, lineHeight: 21, fontWeight: "700" as const }
    }
  },
  spacing: {
    xs: 4,
    sm: 8,
    md: 12,
    lg: 16,
    xl: 20,
    xxl: 24,
    xxxl: 32,
    huge: 40,
    giant: 48
  },
  radius: {
    small: 8,
    medium: 12,
    large: 16,
    /** 18px cards — the "live" surfaces sit between `large` and `panel`. */
    card: 18,
    panel: 20,
    capsule: 999,
    circular: 999
  },
  motion: {
    instant: 80,
    quick: 150,
    standard: 240,
    reveal: 360,
    ambient: 1400,
    /**
     * Entrance choreography. `entrance` is the per-element fade/slide duration
     * and `stagger` the gap between neighbours, so a section N places down
     * starts at N * stagger. Both are read by the stagger helper rather than
     * being restated at call sites.
     */
    entrance: 600,
    stagger: 90,
    /**
     * Continuous ambience. These are long on purpose: anything faster reads as
     * activity rather than atmosphere, and all of them are suppressed outright
     * under reduce-motion.
     */
    tickerCycle: 26000,
    borderShimmer: 5200,
    scanSweep: 2600,
    ringDraw: 1100
  },
  depth: {
    none: 0,
    subtle: 0.18,
    panel: 0.28,
    floating: 0.38,
    modal: 0.48,
    commandStrip: 0.22,
    hero: 0.34,
    orbit: 0.24,
    composer: 0.36,
    feedRail: 0.2,
    feedCard: 0.3,
    floatingNav: 0.42,
    selectedTab: 0.26,
    livePulse: 0.5
  }
};

export type LogiNexusTone = "default" | "intelligence" | "creator" | "economy" | "safety" | "crypto" | "danger" | "warning";

export function toneColor(tone: LogiNexusTone = "default") {
  if (tone === "intelligence") return colors.intelligence;
  if (tone === "creator") return colors.creator;
  if (tone === "economy") return colors.economy;
  if (tone === "safety") return colors.safety;
  if (tone === "crypto") return colors.crypto;
  if (tone === "danger") return colors.danger;
  if (tone === "warning") return colors.warning;
  return colors.accent;
}
