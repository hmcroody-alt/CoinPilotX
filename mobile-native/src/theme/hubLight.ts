/**
 * Business Hub tokens.
 *
 * This is deliberately NOT a new palette. The hub is the front door to ten
 * sections that each already chose their light-theme colours, and a hub with its
 * own greys would make every card look like it belonged to a different app than
 * the screen it opens. So the base is `storeLight` — the palette the Store,
 * Marketplace, Advertising, Orders and Insights rebuilds all share — re-exported
 * here under the hub's name, plus only the tokens the hub genuinely introduces.
 *
 * What the hub introduces is exactly three things: the tone colours the state
 * lines speak in, the urgent-card treatment, and the grid metrics. Everything
 * else — page background, card white, navy header, radii, tap targets — is
 * inherited, so a token change in the Store rebuild reaches the hub for free.
 */

import { storeLight } from "./storeLight";
import type { HubTone } from "../api/businessHub";

export const hubLight = {
  ...storeLight,

  /**
   * One colour per state-line tone.
   *
   * These are the section palettes' own status colours, not new ones: `green`
   * and `warn` are `storeLight.status.success` / `.warning`, `critical` is the
   * error red, and only `review` (verification in progress) and `violet`
   * (offers) are introduced — because no existing surface had a "we are looking
   * at it" state or an offer state to borrow from.
   */
  tone: {
    green: storeLight.status.success,
    warn: storeLight.status.warning,
    critical: storeLight.status.error,
    /** Verification in review. Blue rather than amber: it needs nothing from the seller. */
    review: "#3E6DB5",
    /** Marketplace offers. Matches the Marketplace card tint. */
    violet: "#7C4DDB",
    muted: storeLight.text.muted
  } satisfies Record<HubTone, string>,

  /**
   * The urgent card treatment, applied only to the platform-defined urgent
   * condition for a card. Amber, not red: red is for something already broken,
   * and an urgent card is something still savable.
   *
   * No card can currently take this treatment — see `HUB_ORDER_DEADLINES`. The
   * tokens exist so that turning that flag on is a data change rather than a
   * design exercise.
   */
  urgent: {
    border: "#F0D8B6",
    /** Top-to-bottom, per the reference: a barely-there warm wash. */
    gradient: ["#FFFDF9", "#FFFFFF"] as const
  },

  grid: {
    /** Two columns. Ten cards need no virtualization; a FlatList here would cost more than it saves. */
    columns: 2,
    gap: 10,
    /** Below this multiplier the grid reflows to one column — see `hubGridColumns`. */
    singleColumnFontScale: 1.3
  },

  card: {
    minHeight: 104,
    /** The tinted icon chip behind each card's glyph. */
    iconChip: 34,
    /** Alpha applied to a card's tint for its chip fill. */
    iconChipAlpha: "1A"
  }
} as const;

/**
 * Columns at the current font scale.
 *
 * At the largest accessibility text sizes a 2-up card cannot hold a title, a
 * subtitle and a wrapped state line without either truncating or growing past
 * the fold, so the grid becomes a single column. The state line must never
 * truncate — it is the only live information on the card — so the layout gives
 * way instead.
 */
export function hubGridColumns(fontScale: number): number {
  return fontScale >= hubLight.grid.singleColumnFontScale ? 1 : hubLight.grid.columns;
}

export type HubLightTheme = typeof hubLight;
