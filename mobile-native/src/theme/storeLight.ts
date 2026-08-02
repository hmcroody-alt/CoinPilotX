/**
 * Light palette for the seller Store dashboard.
 *
 * PulseSoc is a dark-theme app. This is the one surface that is deliberately
 * light, because it is a dense data-management screen where hairline borders and
 * white cards do more work than the dark chrome does elsewhere. `colors` (the
 * dark theme) is untouched and every other screen keeps reading it.
 *
 * Values live here rather than inline in components so that the trade-dress
 * decision below is one edit rather than a search-and-replace across a screen.
 */

/**
 * The primary call-to-action fill.
 *
 * The reference design specifies a yellow gradient (#FFD814 → #F7CA00) against a
 * navy header. That pairing is close enough to Amazon's trade dress to be worth a
 * deliberate decision rather than an accident of following a mock, so the default
 * shipped here is PulseSoc's own green.
 *
 * To ship the reference colour instead, swap this one constant:
 *
 *     export const STORE_CTA = STORE_CTA_REFERENCE;
 *
 * Nothing else changes — `ctaText` travels with the fill, so contrast stays
 * correct either way.
 */
export const STORE_CTA_PULSESOC = {
  from: "#2EE6A8",
  to: "#22C48D",
  text: "#04231A"
} as const;

/** The reference design's yellow. Kept so the swap above is a one-line change. */
export const STORE_CTA_REFERENCE = {
  from: "#FFD814",
  to: "#F7CA00",
  text: "#0F1111"
} as const;

export const STORE_CTA = STORE_CTA_PULSESOC;

export const storeLight = {
  bg: {
    /** Page behind the cards. */
    page: "#EAEDED",
    card: "#FFFFFF",
    /** Navy header gradient, top to bottom. */
    headerFrom: "#131A22",
    headerTo: "#232F3E",
    /** The status strip sitting directly under the header. */
    strip: "#232F3E",
    /** Attention banner fill. */
    warning: "#FCF5EE",
    /** Skeleton blocks and pressed-tile wash. */
    skeleton: "#E6E9EA"
  },
  border: {
    hairline: "#D5D9D9",
    secondaryButton: "#ADB1B8",
    warning: "#F0D8B6"
  },
  text: {
    primary: "#0F1111",
    muted: "#565959",
    link: "#007185",
    /** Pressed state for links. */
    linkActive: "#C7511F",
    /** Text on the navy header and status strip. */
    onDark: "#FFFFFF",
    onDarkMuted: "#C7CDD3"
  },
  status: {
    /** In stock, store open, positive trend. */
    success: "#067D62",
    /** Low stock, needs attention. */
    warning: "#C7511F",
    /** Out of stock, hidden, failed. */
    error: "#B12704",
    /** Draft, paused, unknown. */
    neutral: "#565959"
  },
  accent: {
    /** Search button and active tab underline. */
    orange: "#FF9900",
    /** Review stars. */
    star: "#FFA41C"
  },
  cta: STORE_CTA,
  radius: {
    card: 10,
    control: 8,
    thumb: 8,
    pill: 999
  },
  size: {
    thumb: 64,
    /** Minimum touch target. Every Pressable on this screen honours it. */
    tapTarget: 44
  },
  space: {
    gutter: 12,
    section: 14,
    card: 14
  }
} as const;

export type StoreLightTheme = typeof storeLight;
