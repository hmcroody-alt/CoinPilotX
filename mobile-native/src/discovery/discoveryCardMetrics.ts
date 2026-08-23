/**
 * How big a suggestion card is, on this device, for this module kind.
 *
 * ## Why this is a module and not a constant
 *
 * The row used to be built from `const CARD_WIDTH = 148`, which is ~38% of a
 * 390pt phone and ~34% of a 440pt one: a thumbnail floating in a bordered box,
 * with 32pt of gutter (row margin + carousel padding) before the first pixel of
 * media. The product requirement is the opposite — the media should dominate the
 * card and the card should dominate the feed — and "dominate" is a *ratio*, not
 * a pixel count. A fixed width cannot be 78% of two different screens, so the
 * number has to be computed.
 *
 * It is computed *here*, as a pure function, rather than inline in the view, for
 * two reasons. The peek invariant below is arithmetic and is asserted as
 * arithmetic instead of inferred from a rendered tree. And `getItemLayout` on
 * the carousel needs the exact same stride the cards are laid out with — two
 * independent expressions for one number is how a carousel ends up snapping to
 * a position no card starts at.
 *
 * ## The peek invariant
 *
 * A horizontal carousel that shows exactly one card reads as a static banner:
 * nothing tells the user there is more to the right. So the card width is capped
 * such that the *next* card is always at least `MIN_PEEK` wide on screen, on
 * every device, for every kind. That cap is a clamp rather than an assertion
 * because the alternative — a card that is 85% of a narrow phone and hides its
 * neighbour — is a silent regression on exactly the devices nobody tests on.
 */
import type { DiscoveryModuleKind } from "./discoveryRows";

/** Distance from the screen edge to the first card. Small: the row is full-bleed. */
export const DISCOVERY_EDGE_INSET = 12;

/** Between cards. Tight on purpose — wide gaps re-introduce the empty side space. */
export const DISCOVERY_CARD_GAP = 10;

/** How much of the next card must remain visible. Below this it reads as a crop bug. */
export const DISCOVERY_MIN_PEEK = 28;

const MIN_CARD_WIDTH = 148;
const MAX_CARD_WIDTH = 460;

/** Fallback for the (unreachable) case of a zero/NaN measurement before first layout. */
const FALLBACK_SCREEN_WIDTH = 390;

/**
 * Per-kind shape.
 *
 * `widthRatio` is the fraction of the screen the card occupies; `aspect` is
 * width ÷ height of the *media*, so a value below 1 is a portrait frame.
 *
 * Reels and statuses are shot vertically, so their previews are tall — that is
 * what makes the card feel like content rather than a thumbnail. Groups are
 * represented by landscape cover art and would be letterboxed into a portrait
 * frame, which §2 explicitly forbids, so they get a landscape card. People are
 * portraits — never shaped like a video, because a profile in a video-shaped
 * frame reads as a clip that failed to load.
 *
 * People sit at the top of the band rather than the bottom. At 0.7 the card was
 * the narrowest of any kind while the peek was the widest: on a 390pt phone that
 * is 273pt of card against 95pt of the next card, which does not read as a
 * scroll cue so much as a row that failed to fill its own width. Matching groups
 * at 0.82 spends that space on the portrait and leaves a peek of 48pt.
 */
const SHAPE: Record<DiscoveryModuleKind, { widthRatio: number; aspect: number }> = {
  reels: { widthRatio: 0.78, aspect: 0.7 },
  statuses: { widthRatio: 0.78, aspect: 0.7 },
  groups: { widthRatio: 0.82, aspect: 1.45 },
  people: { widthRatio: 0.82, aspect: 0.8 },
  creators: { widthRatio: 0.7, aspect: 0.8 },
  topics: { widthRatio: 0.7, aspect: 0.8 },
  sponsored: { widthRatio: 0.7, aspect: 0.8 }
};

export type DiscoveryCardMetrics = {
  /** Card (and therefore media) width in points. */
  cardWidth: number;
  /** Media height in points. The card is taller by whatever text sits under it. */
  mediaHeight: number;
  gap: number;
  edgeInset: number;
  /** `cardWidth + gap` — the carousel's snap interval and `getItemLayout` length. */
  stride: number;
  /** Points of the second card visible when the first is snapped into place. */
  peek: number;
  /** cardWidth ÷ screenWidth, carried so the ratio can be asserted directly. */
  widthRatio: number;
};

function clamp(value: number, min: number, max: number) {
  return Math.min(Math.max(value, min), max);
}

export function discoveryCardMetrics(screenWidth: number, kind: DiscoveryModuleKind): DiscoveryCardMetrics {
  const width = Number.isFinite(screenWidth) && screenWidth > 0 ? screenWidth : FALLBACK_SCREEN_WIDTH;
  const shape = SHAPE[kind] || SHAPE.people;

  // The widest a card may be and still leave MIN_PEEK of its neighbour showing.
  // On a phone this never binds; it binds on narrow devices, which is the point.
  const peekCap = width - DISCOVERY_EDGE_INSET - DISCOVERY_CARD_GAP - DISCOVERY_MIN_PEEK;
  const cardWidth = Math.round(
    clamp(width * shape.widthRatio, Math.min(MIN_CARD_WIDTH, peekCap), Math.min(MAX_CARD_WIDTH, peekCap))
  );

  return {
    cardWidth,
    mediaHeight: Math.round(cardWidth / shape.aspect),
    gap: DISCOVERY_CARD_GAP,
    edgeInset: DISCOVERY_EDGE_INSET,
    stride: cardWidth + DISCOVERY_CARD_GAP,
    peek: width - DISCOVERY_EDGE_INSET - cardWidth - DISCOVERY_CARD_GAP,
    widthRatio: cardWidth / width
  };
}
