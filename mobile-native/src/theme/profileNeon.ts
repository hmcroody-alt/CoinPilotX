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

export const profileNeon = {
  /** The identity blue. Electric but not fluorescent — readable on #050910. */
  electric: "#3d8bff",
  /** Cyan tail for gradients and highlights (matches `colors.accentStrong`). */
  cyan: "#61d8ff",
  /** Deep blue for gradient tails and pressed states. */
  deep: "#0b2f6b",
  /**
   * Secondary accent. Its own value rather than `colors.intelligence`, because
   * that token means "AI surface" app-wide and is not free to move with the
   * profile palette — this one is.
   */
  violet: "#9d5cff",
  /** Tertiary accent, and the warm end of every profile ramp. */
  magenta: "#f45cd8",

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

  /**
   * Gradient ramps. `as const` so they satisfy LinearGradient's tuple type.
   *
   * Every ramp walks the same blue → violet → magenta arc, which is what makes
   * the surface read as one palette rather than as three accent colours used
   * near each other. Blue always leads: it carries the identity, and the warm
   * end is a tail, not an equal partner.
   */
  primaryAction: ["#4f9bff", "#8b5cff", "#f45cd8"] as const,
  identityRing: ["#61d8ff", "#4f9bff", "#9d5cff", "#f45cd8"] as const,
  horizon: ["rgba(61, 139, 255, 0.00)", "rgba(157, 92, 255, 0.24)", "rgba(244, 92, 216, 0.40)"] as const,
  /**
   * Hue rotation for the Profile OS tiles that carry no fixed brand colour.
   * Applied by grid position, so the grid reads as a spectrum instead of as
   * twelve copies of the accent. Tiles that DO own a colour (Presence, Progress,
   * Premium) opt out — see `MODULES` in ProfileHeader.
   */
  tileCycle: ["#3d8bff", "#9d5cff", "#f45cd8", "#61d8ff"] as const,

  radius: { panel: 20, card: 16, action: 14 },
  /** Apple's 44pt floor; every profile control is at or above it. */
  tapTarget: 44
} as const;

export type ProfileNeon = typeof profileNeon;

/**
 * The accent the profile surface should actually draw with.
 *
 * `/api/pulse/profile` never returns a null theme: when a user has no theme row
 * it substitutes a whole default object, `accent_color` included, and that
 * default is the app-wide teal. So `profile.theme?.accent_color || electric`
 * — the obvious way to write this — can never reach its fallback for any
 * profile this backend serves. Every neon token below was unreachable in
 * production while the tests, which build their own fixtures, stayed green.
 *
 * There is no flag distinguishing "the owner chose teal" from "nobody chose
 * anything", so the legacy default is matched by value and treated as unset.
 * The cost is that an owner who deliberately picks the old teal gets the neon
 * ramp instead; the alternative is that nobody ever sees the palette at all.
 */
const SERVER_DEFAULT_ACCENT = "#32e6b3";

export function resolveProfileAccent(accentColor?: string | null): string {
  const chosen = String(accentColor || "").trim().toLowerCase();
  if (!chosen || chosen === SERVER_DEFAULT_ACCENT) return profileNeon.electric;
  return chosen;
}

/** True when the surface is on its own palette and may use the full ramps. */
export function usesNeonRamp(accent: string): boolean {
  return accent === profileNeon.electric;
}
