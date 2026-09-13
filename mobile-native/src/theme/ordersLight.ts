/**
 * Light palette for the two-sided Orders surface (card #5 of the Business
 * "Sections" grid).
 *
 * Orders is one order model seen from two ends: the seller's fulfillment queue
 * ("Orders buyers placed with you") and the buyer's "Your orders". Both render
 * the SAME underlying order object, so both must speak the same colour language —
 * a state that is green on the seller's card is green on the buyer's, because it
 * is the same fact. This palette extends `storeLight` rather than forking it:
 * every neutral (page, card, hairline, black header, muted text, tap targets,
 * radii, spacing) is inherited so Orders reads as the same family as Store,
 * Marketplace and Advertising. Only the order-specific accents live here.
 *
 * One semantic rule governs every colour choice, and it is what keeps a dual-
 * perspective commerce surface legible:
 *
 *   • green   → progress / arrival        (timeline fills, delivered, handed off)
 *   • amber   → deadline pressure         (ship-by countdown, overdue)
 *   • green   → in transit / Store        (shipped/tracking, Store source)
 *   • gray    → local pickup / Marketplace (pickup status, Marketplace source)
 *
 * The last two were separate hues (blue for transit, violet for pickup). The
 * business surfaces are locked to black, white and green, so transit joined the
 * green family and pickup fell back to neutral gray. Those two states are now
 * much less separable by colour than they were, and the thing that carries the
 * distinction is the status word beside them — every badge and timeline step
 * renders its label, so none of these states is signalled by colour alone.
 *
 * A control never uses a colour that contradicts its job. Red stays reserved for
 * "issue / cancelled / refunded" so the accents above never also have to mean
 * "bad".
 */

import { storeLight } from "./storeLight";

/**
 * The primary call-to-action fill (Buy again, Mark shipped, View payout). The
 * reference design specifies a yellow gradient against a navy header. Following
 * that mock verbatim lands close to a well-known marketplace's trade dress, so
 * the default shipped here is PulseSoc's own green — the same deliberate decision
 * `STORE_CTA` and `ADS_CTA` make, kept consistent across every Business surface.
 *
 * To ship the reference yellow instead, swap this one constant:
 *
 *     export const ORDERS_CTA = ORDERS_CTA_REFERENCE;
 *
 * `text` travels with the fill, so contrast stays correct either way. No other
 * file hardcodes a CTA colour.
 */
export const ORDERS_CTA_PULSESOC = {
  from: "#2EE6A8",
  to: "#22C48D",
  text: "#04231A"
} as const;

/** The reference design's yellow. Kept so the swap above is a one-line change. */
export const ORDERS_CTA_REFERENCE = {
  from: "#FFD814",
  to: "#F7CA00",
  text: "#0F1111"
} as const;

export const ORDERS_CTA = ORDERS_CTA_PULSESOC;

export const ordersLight = {
  bg: {
    /** Inherited neutrals — same page/card/header family as Store. */
    page: storeLight.bg.page,
    card: storeLight.bg.card,
    headerFrom: storeLight.bg.headerFrom,
    headerTo: storeLight.bg.headerTo,
    strip: storeLight.bg.strip,
    warning: storeLight.bg.warning,
    skeleton: storeLight.bg.skeleton
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
   * SOURCE BADGES — where the order came from. STORE is green (the Store product,
   * in-transit family); MARKETPLACE is neutral gray (the Marketplace product,
   * local-pickup family). These were blue and violet, which is why the two tints
   * are now so close: the badge's text label is what actually distinguishes them,
   * and every badge renders one, so the colour is a reinforcement and never the
   * sole signal.
   */
  source: {
    storeBg: "#ECF6F2",
    storeText: "#0A7050",
    storeBorder: "#C9E2D8",
    marketplaceBg: "#EEF6F2",
    marketplaceText: "#4A5250",
    marketplaceBorder: "#CBE3D9"
  },
  /**
   * TIMELINE — green progress. The filled portion of the order timeline (paid →
   * … → delivered / handed off) is green on a neutral track. Dots are 11px so the
   * line reads at a glance without dominating the card.
   */
  timeline: {
    fill: "#067D62",
    track: "#E7E9E9",
    dot: 11,
    /** Dot/label for a step not yet reached. */
    pending: storeLight.text.muted
  },
  /**
   * IN TRANSIT — green. Shipped orders, tracking links, the "on its way" strip.
   * Shares the Store green so "shipping / Store" is one visual idea.
   */
  transit: {
    base: "#0A7050",
    tint: "#ECF6F2"
  },
  /**
   * LOCAL PICKUP — neutral gray. The buyer's pickup status, the pickup handoff
   * step, and Marketplace-sourced local orders. Shares the Marketplace gray.
   *
   * Gray rather than a second green on purpose. Transit and pickup are mutually
   * exclusive outcomes for the same order, so giving both a green would make the
   * two most confusable states on the card the two most similar in colour. Gray
   * is not "worse than shipped" here, it is simply "not the shipping path".
   */
  pickup: {
    status: "#4A5250",
    tint: "#EEF6F2"
  },
  /**
   * DEADLINE PRESSURE — amber. The ship-by countdown line and its overdue
   * escalation. Amber for approaching, the inherited burnt-orange warning for
   * overdue, so "late" is visibly hotter than "soon".
   */
  deadline: {
    text: "#B7791F",
    soft: "#FBEFDD",
    overdue: storeLight.status.warning
  },
  /**
   * QUANTITY BADGE — near-black pill on a thumbnail ("×2"). Matches the header
   * so it reads as chrome rather than a status.
   */
  quantity: {
    badge: "#141518",
    text: "#FFFFFF"
  },
  /**
   * ESCROW / SAFETY PANEL — the "your payment is held until you confirm handoff"
   * reassurance on pickup orders. Rendered ONLY when the backend actually holds
   * the funds (canonical escrow present); otherwise the whole panel is withheld
   * rather than claiming a hold that does not exist. A soft green so it reads as
   * a trust note, not a warning — and matching `paymentsLight.bg.escrowCard`,
   * since a held payment must not look like one thing on Orders and another on
   * Payments.
   */
  safety: {
    panelBg: "#F4FAF7",
    panelBorder: "#D6E9E0",
    panelText: "#4A5250"
  },
  /**
   * URGENCY STRIP — the attention band under the black header. Seller sees a
   * warm peach (orders needing action / overdue); buyer sees a cool mint (an
   * order is moving / arriving). Both sit on the near-black strip.
   */
  urgency: {
    seller: "#FFD9B8",
    buyer: "#BFE9D6",
    bg: storeLight.bg.strip
  },
  cta: ORDERS_CTA,
  radius: {
    card: storeLight.radius.card,
    control: storeLight.radius.control,
    thumb: storeLight.radius.thumb,
    pill: storeLight.radius.pill
  },
  size: {
    thumb: storeLight.size.thumb,
    tapTarget: storeLight.size.tapTarget,
    /** Timeline dot diameter, mirrored from `timeline.dot` for style use. */
    dot: 11
  },
  space: {
    gutter: storeLight.space.gutter,
    section: storeLight.space.section,
    card: storeLight.space.card
  }
} as const;

export type OrdersLightTheme = typeof ordersLight;
