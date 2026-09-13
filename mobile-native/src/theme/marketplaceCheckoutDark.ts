/**
 * Dark surfaces for the Marketplace checkout, and nothing else.
 *
 * Scope is one flow — Order details, Review, Payment handoff, Confirmation. The
 * rest of Marketplace stays on `storeLight`, so this file is additive in the
 * same way `marketplaceLight` is: it imports the base rather than editing it,
 * and a change here cannot reach the Store dashboard or the product grid.
 *
 * **No brand colour is defined here.** The CTA fill, the success/error statuses
 * and the type-badge accents are all re-exported from tokens that already
 * existed. `STORE_CTA` is `#2EE6A8 → #22C48D`, which is already the mint green
 * the checkout mockup paints its primary button — the mockup was drawn from the
 * live palette, not against it. What is genuinely new below is only *surface*:
 * the near-black page, the layered card fills, and the hairlines and text tones
 * that a dark background needs in order to stay legible.
 *
 * That split is the point. Swapping PulseSoc's green stays a one-line edit in
 * `storeLight`, and it still repaints this screen, because nothing here
 * restates it.
 */

import { STORE_CTA, storeLight } from "./storeLight";

export { STORE_CTA };

export const checkoutDark = {
  bg: {
    /** The page itself. Near-black rather than true black so the cards that sit
     * on it can be lighter without the contrast step looking like a seam. */
    page: "#070708",
    /** Standard card fill: sections, the product summary, the stepper strip. */
    card: "#0F1012",
    /** A card *inside* a card — the input wells and the radio rows. Lighter than
     * `card`, which is what makes a form group read as recessed rather than as
     * another slab at the same depth. */
    well: "#141517",
    /** Selected radio / chosen delivery lane. Brand green at low alpha, so the
     * selection reads as tinted rather than as a different component. */
    selected: "rgba(46, 230, 168, 0.09)",
    /** Informational callout (digital-delivery notice, pickup instructions). */
    info: "rgba(46, 230, 168, 0.07)",
    /** Skeletons and the image placeholder behind a summary thumbnail. */
    skeleton: "#1B1C1F"
  },
  border: {
    /** Default 1px edge on cards and inputs. */
    hairline: "#1F2124",
    /** A focused input or a hovered row — visible, still not brand-coloured. */
    strong: "#2D3033",
    /** The edge of a selected option. Brand green, borrowed not redefined. */
    selected: STORE_CTA.from
  },
  text: {
    primary: "#F5F6F6",
    /** Labels, helper copy, the inactive half of the stepper. */
    muted: "#95999B",
    /** Floating field labels — dimmer than `muted`, since the value below is
     * the thing being read. */
    faint: "#74787A",
    /** Prices, and the tick inside a selected radio. */
    accent: STORE_CTA.from,
    /** Text sitting on a brand-green fill. */
    onAccent: STORE_CTA.text
  },
  status: {
    /** Re-exported so the dark checkout cannot invent a second error red. */
    error: "#FF6B6B",
    success: STORE_CTA.from,
    /** The light-theme statuses, kept reachable for anything that needs to
     * match a shared surface exactly. */
    lightError: storeLight.status.error,
    lightSuccess: storeLight.status.success
  },
  /**
   * Type badges on the product summary — the small pill reading "Physical item",
   * "Digital product", "Service", "Event", "Rental".
   *
   * Each is a tinted plate with matching text, one per canonical listing type,
   * so a buyer can tell at a glance what kind of thing they are buying and
   * therefore why the form below asks what it asks.
   */
  badge: {
    physical: { bg: "rgba(46, 230, 168, 0.16)", text: "#7FE3C0" },
    digital: { bg: "rgba(46, 230, 168, 0.14)", text: STORE_CTA.from },
    service: { bg: "rgba(46, 230, 168, 0.16)", text: "#7FE3C0" },
    event: { bg: "rgba(46, 230, 168, 0.10)", text: "#B9EFD9" },
    booking: { bg: "rgba(34, 196, 141, 0.18)", text: "#5FC9A3" },
    neutral: { bg: "rgba(149, 153, 155, 0.16)", text: "#AFB3B5" }
  },
  stepper: {
    /** The completed and current dot. */
    done: STORE_CTA.from,
    /** A step not yet reached: hollow, outlined, muted label. */
    pending: "#2D3033",
    /** Connector line between two dots, before it is travelled. */
    track: "#1F2124"
  },
  radius: { card: 16, well: 12, pill: 999 },
  space: { gutter: 16, section: 14, field: 10 }
} as const;

export type CheckoutDarkTheme = typeof checkoutDark;
