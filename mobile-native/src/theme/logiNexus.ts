import { colors } from "./colors";

export const logiNexus = {
  colors: {
    ...colors,
    space: colors.background,
    elevated: colors.surfaceRaised,
    panel: colors.glass,
    activeSignal: colors.accent,
    neutralText: colors.text,
    mutedText: colors.muted
  },
  typography: {
    display: { fontSize: 34, lineHeight: 39, fontWeight: "900" as const },
    title: { fontSize: 24, lineHeight: 30, fontWeight: "900" as const },
    sectionTitle: { fontSize: 18, lineHeight: 24, fontWeight: "900" as const },
    body: { fontSize: 15, lineHeight: 22, fontWeight: "600" as const },
    metadata: { fontSize: 12, lineHeight: 17, fontWeight: "800" as const },
    label: { fontSize: 12, lineHeight: 16, fontWeight: "900" as const },
    button: { fontSize: 14, lineHeight: 18, fontWeight: "900" as const },
    metric: { fontSize: 26, lineHeight: 31, fontWeight: "900" as const }
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
    modal: 0.48
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
