/**
 * Progress OS tokens — violet and Founders Gold.
 *
 * Every other Profile OS tile inherits `colors.accent`, the teal that is also
 * the profile owner's overridable `theme.accent_color`. Progress must not, and
 * the reason is not decoration.
 *
 * The Founding Member Challenge is the one surface on the profile that is
 * *private to the owner and carries money*. Teal is the app's ambient colour;
 * it says "this is another panel". A member glancing at a teal tile has no
 * signal that what is behind it is different in kind from Media or Collections.
 * Violet plus gold is reserved here and nowhere else, so the tile is legible as
 * "this is yours alone, and something is at stake" before a single word is read.
 *
 * It also survives the accent override. A profile whose owner picked a violet
 * `theme.accent_color` would make a teal-inheriting Progress tile indistinct;
 * these tokens are fixed, so the distinction holds for every profile theme.
 *
 * Gold is used sparingly and only ever for *earned* state — a reached milestone,
 * a paid cycle, the Founding Member badge. Unreached state is violet or muted.
 * Gold on an unreached milestone would read as an achievement the member has not
 * made, which is the one lie this surface cannot afford to tell.
 */

import { colors } from "./colors";

export const progressTheme = {
  /**
   * The tile and header identity. Violet, never `colors.accent`.
   *
   * Was `#8B5CF6`; +12% lightness, hue and saturation held. This is the one
   * value in the graphite migration that was already failing before it —
   * 3.96:1 on the old raised surface — so the lift is not a regression repair.
   * It is here because `ProgressCenterScreen` spends this token as link text,
   * tab labels and the hero eyebrow, and on graphite it fell to 2.59:1.
   *
   * `tileGradient` and `violetDeep` keep the old violet. They are fills, and
   * lifting a gradient's first stop to satisfy a text rule would have washed
   * out the tile to fix something that was never drawn there.
   *
   * The lifted value lands within a hair of `colors.intelligence` (`#b297ff`).
   * They stay separate literals: the two violets mean different things — Progress
   * OS identity and the UNDX/AI layer — and collapsing them is a brand decision,
   * not a contrast one.
   */
  violet: "#B496F9",
  violetSoft: "rgba(180, 150, 249, 0.14)",
  violetBorder: "rgba(180, 150, 249, 0.42)",
  /** Deeper violet for gradient tails and pressed states. */
  violetDeep: "#5B32C4",

  /** Founders Gold. Earned state only. */
  gold: "#E8B84B",
  goldSoft: "rgba(232, 184, 75, 0.16)",
  goldBorder: "rgba(232, 184, 75, 0.46)",

  /** The tile gradient: violet into gold, the two halves of the program. */
  tileGradient: ["#8B5CF6", "#5B32C4"] as const,
  badgeGradient: ["#E8B84B", "#B4801F"] as const,

  /**
   * Qualification checklist states.
   *
   * `pending` is deliberately muted rather than red. An invited friend who has
   * not posted yet is not a failure and must not look like one — the member did
   * nothing wrong, and the referred person is mid-journey.
   */
  state: {
    met: "#E8B84B",
    pending: colors.muted,
    /**
     * Under review. Blue: it needs nothing from the member.
     *
     * Was `#5B8DEF`; +7.6% lightness, same hue, in step with
     * `premiumTheme.state.hold`.
     */
    review: "#7EA6F2",
    /** Ended in a way that will not resume. */
    closed: colors.danger
  },

  surface: colors.surface,
  surfaceRaised: colors.surfaceRaised,
  text: colors.text,
  muted: colors.muted,
  border: colors.border,

  radius: { card: 16, chip: 999, tile: 14 },
  /** Minimum tap target; the milestone rail is dense and easy to get wrong. */
  tapTarget: 44
} as const;

export type ProgressTheme = typeof progressTheme;
