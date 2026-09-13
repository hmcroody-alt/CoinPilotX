/**
 * Light palette for the two-sided Advertising manager (card #4 of the Business
 * "Sections" grid).
 *
 * Advertising is one screen holding two ad products — Marketplace ads (commerce
 * campaigns, listing boosts) and Post ads (promoting feed posts, Reels, live
 * replays) — switched by a header ModeToggle. It extends `storeLight` rather
 * than forking it: every neutral (page, card, hairline, black header, muted text,
 * tap targets, radii, spacing) is inherited so the surface reads as the same
 * family as Store and Marketplace. Only the ad-specific accents live here.
 *
 * One semantic rule governs every colour choice on the screen, and it is worth
 * stating because it is what keeps a dense money surface legible:
 *
 *   • gold / yellow  → money             (wallet, budget, spend, today's bar)
 *   • green          → analytics          (charts, delivery, measurement)
 *   • neutral gray   → content promotion  (the Post-ads product)
 *
 * Analytics was blue and content promotion was violet. Under the black/white/
 * green lock analytics took green and promotion fell back to gray, which has one
 * consequence worth knowing before editing this file: green now does double duty
 * as "analytics" AND as the inherited "delivering / healthy" status. They do not
 * collide in practice — status green only appears inside a labelled status pill,
 * chart green only inside the plot — but a new component that puts a bare green
 * dot beside a chart would be genuinely ambiguous. Label it.
 *
 * Gold is untouched by the lock. It is not an accent here, it is the money
 * channel, and it is the one hue on this screen that still means exactly one
 * thing. Red stays reserved for "error / rejected".
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
    /** Post-ads product wash — the faintest neutral, behind promotion cards. */
    postSurface: "#F6F7F7"
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
   * ANALYTICS — green. Every chart, delivery figure and measurement element.
   * The bar fill is a top-to-bottom gradient; the flat/axis line is the darker
   * end so a single bar still reads against the card.
   */
  chart: {
    barFrom: "#2EA47C",
    barTo: "#0A7050",
    axis: "#0A7050",
    grid: "#E7E9E9",
    /** Today's column is money, so it breaks green and goes gold (see below). */
    trackEmpty: "#EFF1F1"
  },
  /**
   * CONTENT PROMOTION — neutral gray. The Post-ads product's signature. Solid for
   * chrome (tab, promoted badge), the gradient for the promote CTA.
   */
  post: {
    base: "#4A5250",
    from: "#5C6663",
    to: "#39413F",
    /** Text/icons on the gray promotion fill. */
    onPromotion: "#FFFFFF",
    /** Faint gray used for the promoted-post ring and chips. */
    tint: "#EFF1F0"
  },
  /**
   * MONEY — gold. The wallet chip, budget pacing, spend, and the "today" bar in
   * the spend chart. Gold is never used for anything that is not money.
   */
  money: {
    /** Today's spend bar — a warm gradient so the live day stands out in green. */
    todayFrom: "#FFD97A",
    todayTo: "#FFA41C",
    /** Budget pacing fill, on-track. */
    budget: "#F0A93B",
    /** Budget pacing fill, pacing hot (spending too fast). */
    budgetHot: storeLight.status.warning
  },
  /**
   * The wallet chip sits on the black header, so its surface and border are
   * expressed as light-on-dark rather than the light-palette hairline.
   */
  wallet: {
    chipBg: "rgba(255,255,255,0.07)",
    chipBorder: "#3A3D40",
    /** The balance figure itself — gold, because it is money. */
    amount: "#FFD97A",
    label: storeLight.text.onDarkMuted
  },
  /**
   * The post-performance suggestion card ("This Reel is outperforming — promote
   * it?"). A soft green-to-white so it reads as a content nudge, not a warning.
   */
  suggestion: {
    from: "#EEF6F2",
    to: "#FFFFFF",
    border: "#CBE3D9"
  },
  /** Content-type badges on promoted posts, paired with a text label always. */
  content: {
    postBg: "#EFF1F1",
    postText: "#3D4043",
    reelBg: "#EFF7F3",
    reelText: "#39413F",
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
