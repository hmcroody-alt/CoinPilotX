/**
 * Profile neon tokens — the futuristic blue treatment for the public profile.
 *
 * Scoped to the profile surface on purpose. `theme/colors.ts` stays the global
 * palette (accent teal `#32e6b3`) and is deliberately NOT changed here: the
 * brief was a futuristic blue *profile*, not a new app-wide colour system, and
 * mutating `colors` would repaint the feed, messenger and every other screen
 * that reads the same object.
 *
 * These values are only a default. A profile owner's `theme.accent_color` still
 * wins wherever it is set — see `ProfileHeader`'s `accent` — so customised
 * profiles keep their identity and only the un-themed default moves from teal
 * to PulseSoc blue.
 *
 * Restraint is a token decision, not a styling afterthought. The alpha ramps
 * below are the whole reason the screen reads as premium rather than as a
 * gaming HUD: borders sit at ~0.30–0.45, fills at ~0.10–0.18, and only the
 * primary action and the avatar ring are allowed to be genuinely bright.
 */

import { colors } from "./colors";

export const profileNeon = {
  /** The identity blue. Electric but not fluorescent — readable on #050910. */
  electric: "#3d8bff",
  /** Cyan tail for gradients and highlights (matches `colors.accentStrong`). */
  cyan: "#61d8ff",
  /** Deep blue for gradient tails and pressed states. */
  deep: "#0b2f6b",
  /** Sparingly used secondary accent, for the horizon only. */
  violet: colors.intelligence,

  /** Fills. Low alpha by design — see the note above. */
  fillSoft: "rgba(61, 139, 255, 0.10)",
  fillMedium: "rgba(61, 139, 255, 0.16)",
  /** Borders. The neon edge that does the work without a glow. */
  border: "rgba(61, 139, 255, 0.34)",
  borderStrong: "rgba(61, 139, 255, 0.52)",
  /** Halo behind the avatar ring and the primary action. */
  glow: "rgba(61, 139, 255, 0.45)",
  glowCyan: "rgba(97, 216, 255, 0.38)",

  /** Dark translucent panel — the glass every profile card sits on. */
  panel: "rgba(9, 20, 38, 0.72)",
  panelRaised: "rgba(14, 30, 54, 0.88)",
  /** Hairline that separates segments inside a glass panel. */
  hairline: "rgba(120, 170, 255, 0.16)",

  /** Gradient ramps. `as const` so they satisfy LinearGradient's tuple type. */
  primaryAction: ["#4f9bff", "#2563eb"] as const,
  horizon: ["rgba(61, 139, 255, 0.00)", "rgba(61, 139, 255, 0.22)", "rgba(97, 216, 255, 0.40)"] as const,

  radius: { panel: 20, card: 16, action: 14 },
  /** Apple's 44pt floor; every profile control is at or above it. */
  tapTarget: 44
} as const;

export type ProfileNeon = typeof profileNeon;
