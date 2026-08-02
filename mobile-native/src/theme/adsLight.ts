/**
 * Light palette for the two-sided Advertising manager (card #4 of the Business
 * "Sections" grid).
 *
 * Advertising is one screen holding two ad products — Marketplace ads (commerce
 * campaigns, listing boosts) and Post ads (promoting feed posts, Reels, live
 * replays) — switched by a header ModeToggle. It extends `storeLight` rather
 * than forking it: every neutral (page, card, hairline, header navy, muted text,
 * tap targets, radii, spacing) is inherited so the surface reads as the same
 * family as Store and Marketplace. Only the ad-specific accents live here.
 *
 * One semantic rule governs every colour choice on the screen, and it is worth
 * stating because it is what keeps a dense money surface legible:
 *
 *   • gold / yellow  → money            (wallet, budget, spend, today's bar)
 *   • violet         → content promotion (the Post-ads product)
 *   • blue           → analytics         (charts, delivery, measurement)
 *
 * A control never uses a colour that contradicts its job. Green stays reserved
 * for "delivering / healthy" status (inherited from `storeLight.status.success`)
 * and red for "error / rejected", so the three accents above never have to also
 * mean "good" or "bad".
 */

import { storeLight } from "./storeLight";

/**
 * The primary call-to-action fill for money actions (Add funds, Create
 * campaign). The reference design specifies a gold gradient. Following that
 * mock verbatim lands close to a well-known marketplace's trade dress, so the
 * default shipped here is PulseSoc's own green — the same deliberate decision
 * `STORE_CTA` makes on the Store surface, kept consistent across both.
 *
 * To ship the reference gold instead, swap this one constant:
 *
 *     export const ADS_CTA = ADS_CTA_REFERENCE;
 *
 * `text` travels with the fill, so contrast stays correct either way.
 */
export const ADS_CTA_PULSESOC = {
  from: "#2EE6A8",
  to: "#22C48D",
  text: "#04231A"
} as const;

/** The reference design's gold. Kept so the swap above is a one-line change. */
export const ADS_CTA_REFERENCE = {
  from: "#FFD814",
  to: "#F7CA00",
  text: "#0F1111"
} as const;

export const ADS_CTA = ADS_CTA_PULSESOC;

export const adsLight = {
  bg: {
    /** Inherited neutrals — same page/card/header family as Store. */
    page: storeLight.bg.page,
    card: storeLight.bg.card,
    headerFrom: storeLight.bg.headerFrom,
    headerTo: storeLight.bg.headerTo,
    strip: storeLight.bg.strip,
    warning: storeLight.bg.warning,
    skeleton: storeLight.bg.skeleton,
    /** Post-ads product wash — the faintest violet, behind promotion cards. */
    postSurface: "#F7F4FC"
  },
  border: {
    hairline: storeLight.border.hairline,
    secondaryButton: storeLight.border.secondaryButton,
    warning: storeLight.border.warning
  },
  text: {
    primary: storeLight.text.primary,
    muted: storeLight.text.muted,
    link: storeLight.text.link,
    linkActive: storeLight.text.linkActive,
    onDark: storeLight.text.onDark,
    onDarkMuted: storeLight.text.onDarkMuted
  },
  status: {
    success: storeLight.status.success,
    warning: storeLight.status.warning,
    error: storeLight.status.error,
    neutral: storeLight.status.neutral
  },
  /**
   * ANALYTICS — blue. Every chart, delivery figure and measurement element.
   * The bar fill is a top-to-bottom gradient; the flat/axis line is the darker
   * end so a single bar still reads against the card.
   */
  chart: {
    barFrom: "#3FA3D1",
    barTo: "#2B6DA8",
    axis: "#2B6DA8",
    grid: "#E3E8EC",
    /** Today's column is money, so it breaks blue and goes gold (see below). */
    trackEmpty: "#EDF1F4"
  },
  /**
   * CONTENT PROMOTION — violet. The Post-ads product's signature. Solid for
   * chrome (tab, promoted badge), the gradient for the promote CTA.
   */
  post: {
    base: "#6B4FA3",
    from: "#7C5DB8",
    to: "#5C3F94",
    /** Text/icons on a violet fill. */
    onViolet: "#FFFFFF",
    /** Faint violet used for the promoted-post ring and chips. */
    tint: "#EFE9F8"
  },
  /**
   * MONEY — gold. The wallet chip, budget pacing, spend, and the "today" bar in
   * the spend chart. Gold is never used for anything that is not money.
   */
  money: {
    /** Today's spend bar — a warm gradient so the live day stands out in blue. */
    todayFrom: "#FFD97A",
    todayTo: "#FFA41C",
    /** Budget pacing fill, on-track. */
    budget: "#F0A93B",
    /** Budget pacing fill, pacing hot (spending too fast). */
    budgetHot: storeLight.status.warning
  },
  /**
   * The wallet chip sits on the navy header, so its surface and border are
   * expressed as light-on-dark rather than the light-palette hairline.
   */
  wallet: {
    chipBg: "rgba(255,255,255,0.07)",
    chipBorder: "#37475A",
    /** The balance figure itself — gold, because it is money. */
    amount: "#FFD97A",
    label: storeLight.text.onDarkMuted
  },
  /**
   * The post-performance suggestion card ("This Reel is outperforming — promote
   * it?"). A soft violet-to-white so it reads as a content nudge, not a warning.
   */
  suggestion: {
    from: "#F2EEFB",
    to: "#FFFFFF",
    border: "#D9CDF0"
  },
  /** Content-type badges on promoted posts, paired with a text label always. */
  content: {
    postBg: "#EDEFF2",
    postText: "#3A4A5C",
    reelBg: "#F0E9FA",
    reelText: "#5C3F94",
    liveBg: "#FBE9EC",
    liveText: "#B12704"
  },
  cta: ADS_CTA,
  radius: {
    card: storeLight.radius.card,
    control: storeLight.radius.control,
    thumb: storeLight.radius.thumb,
    pill: storeLight.radius.pill,
    /** Chart bar corners — squarer than a card, softer than a hard edge. */
    bar: 3
  },
  size: {
    thumb: storeLight.size.thumb,
    tapTarget: storeLight.size.tapTarget
  },
  space: {
    gutter: storeLight.space.gutter,
    section: storeLight.space.section,
    card: storeLight.space.card
  }
} as const;

export type AdsLightTheme = typeof adsLight;
