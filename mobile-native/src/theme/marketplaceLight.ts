/**
 * Palette additions for the two-sided Marketplace screen.
 *
 * `storeLight` is the base and is deliberately *not* edited here. The Store
 * dashboard is a shipped surface; widening its token object would put every
 * Store screen in the blast radius of a Marketplace tweak. So this module
 * imports the base, re-exports it, and adds only what Marketplace needs on top.
 *
 * The trade-dress decision made in `storeLight` carries over unchanged: the
 * primary CTA fill is one swappable constant (`STORE_CTA`), defaulting to
 * PulseSoc green rather than the reference yellow. Marketplace has a second
 * CTA — the buyer-side "Add to cart" — and it reads from that same constant, so
 * a single edit still swaps every yellow surface in both missions. See
 * `MARKETPLACE_CART_CTA` below for why that one is not hardcoded either.
 */

import { STORE_CTA, storeLight } from "./storeLight";

export { storeLight, STORE_CTA };

/**
 * The buyer-side cart button fill.
 *
 * The reference design paints this the same yellow as the Store CTA, which is
 * exactly the pairing flagged as trade dress in `storeLight`. Rather than
 * restate a colour here, it aliases the shared constant — so the swap documented
 * over there is genuinely one edit for the whole app, not one edit per screen
 * that someone later has to discover.
 *
 * The offer button is a different affordance (negotiate, not buy) and so takes
 * the success green rather than the CTA fill. That is a semantic difference, not
 * a trade-dress one, so it is a real token below.
 */
export const MARKETPLACE_CART_CTA = STORE_CTA;

export const marketplaceLight = {
  offer: {
    /** Card fill and hairline for an offer that has not been answered yet. */
    freshBorder: "#BFE0D3",
    /**
     * The 3px left edge on a fresh offer. Deliberately the same green as
     * `storeLight.status.success` — an unanswered offer is a positive signal,
     * and reusing the status colour keeps the screen's vocabulary small.
     */
    freshEdge: "#067D62",
    /** Width of that edge, in px. Named so the card and its skeleton agree. */
    freshEdgeWidth: 3,
    /** Fill for the offer amount, which is the largest number on the card. */
    amount: "#067D62",
    /** Decline is destructive but not dangerous — subtle, not alarming. */
    decline: "#B12704"
  },
  savedSearch: {
    /** Vertical gradient, top to bottom. */
    from: "#EEF7F4",
    to: "#FFFFFF"
  },
  boost: {
    from: "#FFF8E8",
    to: "#FFFFFF",
    border: "#F0DFAE"
  },
  badge: {
    /**
     * FEATURED. Navy plate, brand-green text.
     *
     * This was the reference design's yellow (#FFD814) — the one place that
     * yellow appeared as *text* rather than as a CTA fill, which is why it sat
     * outside the `STORE_CTA` swap and stayed literal. It is now the same
     * PulseSoc green the Store accent and the primary CTA use, so no yellow is
     * left anywhere in the Marketplace or Store chrome. On the navy plate the
     * green reads at least as strongly as the yellow did.
     */
    featuredBg: "#131A22",
    featuredText: storeLight.accent.brand,
    /** NEW. Solid success green, white text. */
    newBg: "#067D62",
    newText: "#FFFFFF",
    /** Scrim laid over a sold item's image, with the word SOLD on top. */
    soldOverlay: "rgba(19, 26, 34, 0.85)",
    soldText: "#FFFFFF"
  },
  /**
   * The buyer-side "Make offer" fill. Green because it is a negotiation, not a
   * purchase — the colour is doing semantic work, so unlike the cart button it
   * does not alias the CTA constant.
   */
  offerCta: {
    from: "#067D62",
    to: "#04664F",
    text: "#FFFFFF"
  },
  grid: {
    /** Gap between the two columns, and the card corner radius. */
    gutter: 10,
    radius: 10,
    /** Image aspect ratio inside a grid card. */
    imageAspect: 1
  },
  chip: {
    /** Category rail, inactive. */
    bg: "#FFFFFF",
    text: "#0F1111",
    /** Category rail, active — filled navy per the reference. */
    activeBg: "#131A22",
    activeText: "#FFFFFF"
  }
} as const;

export type MarketplaceLightTheme = typeof marketplaceLight;
