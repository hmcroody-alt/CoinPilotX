/**
 * Presence tokens — PulseSoc teal, fixed.
 *
 * Presence is the entry point to a member's professional identities (artist,
 * business, organization — the canonical Page OS underneath). Like Progress
 * (violet) and Premium (gold), the tile must survive the profile owner's
 * `theme.accent_color` override: a profile themed teal would otherwise make
 * Presence one ambient panel among seventeen. These tokens are fixed brand
 * teal — the PulseSoc identity colour — with a soft static glow, so the tile
 * reads as "this is where your public identities live" without becoming the
 * brightest thing on a dark profile or colliding with Progress's violet or
 * Premium's gold.
 */

import { colors } from "./colors";

export const presenceTheme = {
  /** The tile and header identity. Brand teal, deliberately not overridable. */
  teal: "#32e6b3",
  tealSoft: "rgba(50, 230, 179, 0.14)",
  tealBorder: "rgba(50, 230, 179, 0.42)",
  /** Deeper teal for gradient tails and pressed states. */
  tealDeep: "#0F8F6C",

  /** Static halo behind the tile icon — premium-quality, never animated. */
  glow: "rgba(50, 230, 179, 0.35)",

  /** Secondary identity accent for artist surfaces (mirrors PulseSoc violet). */
  violet: "#9f7cff",
  violetSoft: "rgba(159, 124, 255, 0.14)",

  tileGradient: ["#32e6b3", "#0F8F6C"] as const,

  surface: colors.surface,
  surfaceRaised: colors.surfaceRaised,
  text: colors.text,
  muted: colors.muted,
  border: colors.border,

  radius: { card: 16, chip: 999, tile: 14 },
  tapTarget: 44
} as const;

export type PresenceTheme = typeof presenceTheme;
