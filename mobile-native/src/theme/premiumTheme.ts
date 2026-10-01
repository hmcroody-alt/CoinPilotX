/**
 * Premium tokens — Founders Gold on a midnight base.
 *
 * Profile OS has exactly three accent identities, and each one means something
 * different:
 *
 *   teal   (`colors.accent`)      ambient panels; the profile owner can retheme it
 *   violet (`progressTheme`)      private, and something is at stake
 *   gold   (this file)            the paid membership
 *
 * Premium takes the third because it is the only tile that leads to a purchase.
 * A member about to be asked for money should be able to tell that surface apart
 * from Media or Collections before reading a word, and — like Progress — it must
 * survive the owner's `theme.accent_color` override, so these values are fixed
 * rather than inherited.
 *
 * There is ONE Founders Gold in this app
 * ---------------------------------------
 * `gold` below is imported from `progressTheme`, not re-declared. The Founding
 * Member badge and the Premium tile are the same brand promise seen from two
 * angles, and two gold hexes that drift a few points apart would look like a
 * rendering bug on the one screen that has to feel expensive. If the brand gold
 * ever changes it changes in one place.
 *
 * Restraint is deliberate
 * -----------------------
 * Gold is a border, an icon and a label — never a filled background. A tile that
 * is a solid gold slab reads as a promotion, and a paid-membership entry point
 * that looks like an ad is the fastest way to make Premium feel cheap. The glow
 * is a shadow at low opacity, and `reduced motion` never animates it.
 */

import { colors } from "./colors";
import { progressTheme } from "./progressTheme";

export const premiumTheme = {
  /**
   * Founders Gold. The single brand gold, shared with the Founding Member
   * badge — see the note above before changing this line.
   */
  gold: progressTheme.gold,
  /** Highlight for the one element that should catch the eye first. */
  goldBright: "#F5D083",
  /** Body copy and secondary marks that should read gold without shouting. */
  goldMuted: "#B4801F",
  goldBorder: "rgba(232, 184, 75, 0.46)",
  /** Low-alpha fill behind icons and chips. Never a full-strength background. */
  goldSoft: "rgba(232, 184, 75, 0.14)",

  /** Deep midnight base the gold sits on, matching the Profile OS shell. */
  surface: colors.surface,
  surfaceRaised: colors.surfaceRaised,
  /** Shadow colour for the tile's glow. Applied at low opacity, never animated. */
  glow: "rgba(232, 184, 75, 0.55)",

  /** The plan-card gradient: gold into its deeper tail. */
  planGradient: ["#E8B84B", "#B4801F"] as const,

  /**
   * Membership state colours.
   *
   * `grace` is amber rather than red on purpose. A card that failed to renew is
   * a billing hiccup the member can fix, and their access is still live — colouring
   * it as an error would tell them they had lost something they still have.
   */
  state: {
    active: progressTheme.gold,
    founder: progressTheme.gold,
    grace: colors.warning,
    /** No membership yet. Muted: not having bought something is not a fault. */
    none: colors.muted,
    expired: colors.muted,
    /** Access paused by an account hold. Blue: it is not a billing problem. */
    hold: "#5B8DEF"
  },

  text: colors.text,
  muted: colors.muted,
  border: colors.border,

  radius: { card: 16, chip: 999, tile: 14 },
  /** Matches Progress; the plan cards are the primary tap target on the screen. */
  tapTarget: 44
} as const;

export type PremiumTheme = typeof premiumTheme;
