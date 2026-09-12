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
  /**
   * Selection mode — §3, §16–§20.
   *
   * Two states, and the reason they are tokens rather than inline values is that
   * both have to mean the same thing in three places at once: the row, the
   * checkbox inside it, and the bulk action bar that counts them. A seller
   * scanning for "which of these did I pick" is reading a wash and a tick that
   * must agree.
   *
   * `disabled` is the more important of the two. It is what a row blocked from a
   * bulk action wears, and it is deliberately a *legible* grey rather than a
   * faded one: the seller still has to read the title and the reason ("2 things
   * left") off a row they cannot act on. Dimming it to the point of illegibility
   * would hide the very thing that tells them how to unblock it.
   */
  select: {
    /** Fill behind a selected row, and the checkbox's box when ticked. */
    selected: "#E8F6F1",
    /**
     * Border and tick of a selected row.
     *
     * NOT `STORE_CTA_PULSESOC.to`, which was the obvious choice and measures
     * 2.25:1 against `bg.card` — below the 3:1 WCAG 1.4.11 asks of a non-text
     * control, so the one mark distinguishing a selected row would have been
     * invisible to a low-vision seller. This is the same hue three steps deeper,
     * at 3.75:1. Selection is still never signalled by colour alone; the tick
     * is a shape and the row announces `accessibilityState.selected`.
     */
    selectedBorder: "#189669",
    /** Wash behind a row the current bulk action cannot touch. */
    disabled: "#F4F5F5",
    /** Title and secondary text on a disabled row. 4.60:1 on `disabled`. */
    disabledText: "#6B7070",
    /**
     * The *reason* a row is blocked, drawn on the wash above.
     *
     * A separate token because `status.warning` measures 4.54:1 on the white
     * card — passing, but with nothing to spare — and drops to 4.16:1 once the
     * disabled wash is under it. This is the same warm hue darkened to 5.52:1.
     * The reason is the single most important string on a row the seller cannot
     * act on, since it is the only thing that says how to unblock it, so it is
     * the last text that should be allowed to fade.
     */
    disabledReason: "#A8431A"
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
    /**
     * The Store's interactive accent: search button fill, the unread badge on the
     * bell, and the status-strip action ("Manage" / "Reopen"). All three sit on
     * the navy header or strip.
     *
     * This was the reference design's orange (#FF9900). It is now PulseSoc's own
     * green — the same value `STORE_CTA_PULSESOC` already ships as the primary
     * button fill — so the header carries one brand colour instead of a green
     * button beside an orange one.
     */
    brand: STORE_CTA_PULSESOC.from,
    /**
     * The same green, one step deeper, for accents drawn on a white card rather
     * than on navy. `brand` is a mint that all but disappears as a 2px rule on
     * `bg.card`; this is the darker end of the same CTA pair, so the active tab
     * underline stays as visible as the orange it replaces without introducing a
     * third green.
     */
    brandOnLight: STORE_CTA_PULSESOC.to,
    /** Review stars. Gold is the convention for a rating and is not a brand accent. */
    star: "#FFA41C",
    /**
     * The reference orange. No Store surface uses it any more — it stays defined
     * because Insights reads it through the `storeLight` spread for its card
     * links, and Insights is outside the scope of the Store/Marketplace colour
     * change. Deleting it here would silently repaint that screen.
     */
    orange: "#FF9900"
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
