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
      surfaceGlass: "rgba(9, 20, 33, 0.78)",
      surfaceGlassStrong: "rgba(11, 27, 44, 0.94)",
      surfaceSignal: "rgba(50, 230, 179, 0.105)",
      borderSubtle: "rgba(121, 210, 255, 0.18)",
      borderActive: "rgba(50, 230, 179, 0.72)",
      borderIntelligence: "rgba(159, 124, 255, 0.62)",
      borderCreator: "rgba(66, 231, 212, 0.58)",
      borderSafety: "rgba(63, 240, 160, 0.6)",
      accentPrimary: colors.accent,
      accentSecondary: colors.accentStrong,
      accentUndx: colors.intelligence,
      accentRadio: "#42e7d4",
      accentLive: colors.danger,
      accentSafety: colors.safety,
      accentCreator: colors.creator
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
    panel: 20,
    capsule: 999,
    circular: 999
  },
  motion: {
    instant: 80,
    quick: 150,
    standard: 240,
    reveal: 360,
    ambient: 1400
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
