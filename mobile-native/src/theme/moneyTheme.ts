/**
 * Money layers — gold and green on a deep vault base.
 *
 * Where this palette applies, and where it deliberately does not
 * --------------------------------------------------------------
 * The Payments hub itself is a light surface (`paymentsLight`), part of the
 * Store / Marketplace / Advertising family, and it stays that way. This palette
 * dresses the *layers underneath it* — the screens a seller opens to interrogate
 * a figure: where a balance comes from, why money is processing, what a payout
 * did, what one ledger row was. Those are the screens where the seller has
 * stopped skimming and started asking, and giving them their own dark, quiet
 * identity is what separates "reading my accounts" from "using the app".
 *
 * The hub's dark navy header already spans both worlds, which is what keeps the
 * transition from reading as a different app: you tap a figure under a dark
 * header and arrive on a screen that is that header, expanded.
 *
 * The two hues each mean exactly one thing
 * ----------------------------------------
 *   gold  → value: an amount that is the point of the screen, a reward, a
 *           lifetime total, the premium identity of the surface itself.
 *   green → health: available, enabled, connected, succeeded, released.
 *
 * A figure is never gold *and* green. Gold says "this is the number"; green says
 * "this state is good". A payout that failed is still a payout, so its amount
 * stays gold and only its chip turns red — the amount is not a judgement.
 *
 * Inherited, not forked
 * ---------------------
 * `gold` comes from `premiumTheme`, which takes it from `progressTheme`. There
 * is one Founders Gold in this app and this file does not become the second one.
 * A money screen and a membership screen that disagree by a few points of hue
 * look like a rendering fault on precisely the surfaces that must feel exact.
 *
 * Green is `colors.success`-adjacent rather than the Payments CTA green, because
 * this palette sits on a dark base where the light-surface CTA green loses
 * contrast against the vault background.
 */

import { colors } from "./colors";
import { premiumTheme } from "./premiumTheme";

/**
 * The vault gradient behind every money layer header.
 *
 * Two stops of near-black blue rather than a flat fill: a flat dark header on a
 * dark body gives the eye no horizon, and the seller loses track of where the
 * scrolling content begins.
 */
export const MONEY_HEADER_GRADIENT = ["#0A1622", "#0E2130"] as const;

export const moneyTheme = {
  /** Value, rewards, and the identity of the surface. Never a filled slab. */
  gold: premiumTheme.gold,
  goldBright: premiumTheme.goldBright,
  goldMuted: premiumTheme.goldMuted,
  goldBorder: premiumTheme.goldBorder,
  goldSoft: premiumTheme.goldSoft,

  /**
   * Health: available, connected, enabled, paid.
   *
   * `green` is the figure/emphasis tone and `greenSoft` the low-alpha fill it
   * sits on. Both are tuned for a dark base — the Payments CTA green (#2EE6A8)
   * is a fill colour on white and reads as a highlighter pen here.
   */
  green: "#3FD8A4",
  greenMuted: "#1F8F6A",
  greenBorder: "rgba(63, 216, 164, 0.42)",
  greenSoft: "rgba(63, 216, 164, 0.12)",

  bg: {
    /** The vault floor. Darker than `colors.surface` so cards can lift off it. */
    page: "#070E16",
    /** Card fill — one step up from the page, never a pure black. */
    card: "#101C28",
    /** A card that wants slightly more presence (the layer's headline figure). */
    cardRaised: "#14232F",
    /** Strip behind grouped rows and help text. */
    strip: "rgba(255, 255, 255, 0.04)",
    surface: colors.surface
  },

  border: {
    /** The default card edge — visible, not decorative. */
    hairline: "rgba(255, 255, 255, 0.09)",
    /** The edge of the one card that is the point of the screen. */
    gold: premiumTheme.goldBorder,
    green: "rgba(63, 216, 164, 0.42)"
  },

  text: {
    primary: "#F2F6FA",
    /** Body copy and explanations — the sentences that make a figure honest. */
    secondary: "rgba(242, 246, 250, 0.78)",
    /** Labels, captions, timestamps. */
    muted: "rgba(242, 246, 250, 0.56)",
    /** On a gold fill (chips only — gold is never a page background). */
    onGold: "#231703"
  },

  /**
   * Status tones, keyed to the same four words `payoutStatusChip` and
   * `rewardStatusChip` already decide in the API layer. Mapping them here rather
   * than in each screen is what keeps a payout chip and a reward chip the same
   * colour for the same meaning.
   */
  tone: {
    progress: "#5AB4F0",
    success: "#3FD8A4",
    error: "#FF7A6B",
    neutral: "rgba(242, 246, 250, 0.56)"
  },

  /**
   * The em dash colour for a figure that could not be read.
   *
   * Muted, never red, for the reason `paymentsLight` gives: a failed read is not
   * a financial event, and an alarming dash suggests something happened to the
   * money rather than to the request.
   */
  unavailable: "rgba(242, 246, 250, 0.45)",

  radius: {
    card: 16,
    control: 12,
    chip: 999
  },

  space: {
    gutter: 16,
    card: 16,
    section: 22
  },

  /** 44pt, the platform minimum, applied to every row and control. */
  tapTarget: 44,

  /**
   * Money type. `tabular-nums` for the same reason `paymentsLight` gives: a
   * column of amounts has to align on the decimal to be scannable, and a figure
   * that changes width as it updates reads as instability in the amount.
   */
  money: {
    hero: {
      fontSize: 34,
      fontWeight: "800" as const,
      fontVariant: ["tabular-nums"] as const,
      letterSpacing: -0.6
    },
    figure: {
      fontSize: 20,
      fontWeight: "700" as const,
      fontVariant: ["tabular-nums"] as const
    },
    row: {
      fontSize: 16,
      fontWeight: "600" as const,
      fontVariant: ["tabular-nums"] as const
    }
  }
} as const;

export type MoneyTheme = typeof moneyTheme;
export type MoneyTone = keyof typeof moneyTheme.tone;
