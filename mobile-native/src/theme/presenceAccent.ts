/**
 * The colour a presence is drawn in, decided by its page type.
 *
 * The Page OS doc says the type "controls presentation (tab set, labels) —
 * never a different backend". Colour is the third thing in that sentence: an
 * artist page and a restaurant page are the same row in the same table, served
 * by the same routes, and the only honest way they differ is in how they are
 * presented. So the table below is client-side on purpose. It is not a copy of
 * anything the server knows — unlike `BUSINESS_PAGE_TYPES`, which the hub used
 * to keep its own copy of and had already drifted out of agreement with, and
 * which is now sent as `business_os_capable` rather than re-derived here.
 *
 * ## Why this is a table and not a function
 *
 * The obvious shape is `isBusiness(type) ? teal : violet`, and that shape is
 * exactly the bug that was just removed from `PresenceHubScreen`: every
 * grouping function needs a fall-through, the fall-through is silent, and the
 * types nobody thought about are precisely the ones it swallows. `Record<
 * PageType, ...>` has no fall-through. Adding a seventeenth page type to
 * `PAGE_TYPES` fails the build here until somebody says what colour it is,
 * which is the one moment they are thinking about it.
 *
 * Several types share a hue. That is stated per row rather than computed, so
 * a type can be moved between families without moving any other type with it.
 *
 * ## Why this is not in `presenceTheme`
 *
 * `presenceTheme` is the *fixed* brand identity of the Presence entry point —
 * the profile tile and the hub header, which stand for every one of a member's
 * presences at once and must therefore stand for none of them in particular.
 * Those tokens are deliberately not overridable. These ones vary by design.
 * Keeping them in one object would make it easy to paint the hub tile in an
 * artist's violet, which would say the wrong thing about a member who also
 * runs a restaurant.
 *
 * ## Restraint
 *
 * The alpha ramp is generated, not hand-written, and follows the doctrine set
 * out in `profileNeon`: borders ~0.30–0.45, fills ~0.10–0.18, and only the
 * primary action and the avatar ring genuinely bright. Hand-written rgba
 * strings drift a hundredth at a time until one surface is a gaming HUD; a
 * function cannot. `presenceAccent.test.ts` holds the bands to that, and holds
 * every hue to a legible contrast ratio on the dark background it is drawn on.
 */

import { PAGE_TYPES, type PageType } from "../api/pages";
import { colors } from "./colors";

/** The four hues. Named for what they mean, not for what they look like. */
const HUES = {
  /** Brand teal — an operation. The default weight of the product. */
  enterprise: "#32e6b3",
  /** Violet — a person's creative output. Mirrors PulseSoc's creator accent. */
  creative: "#9f7cff",
  /** Cyan — something with a storefront and a price on it. */
  commerce: "#61d8ff",
  /** Magenta — something people gather at or belong to. */
  community: "#ff6ad5"
} as const;

export type PresenceHue = keyof typeof HUES;

function rgba(hex: string, alpha: number): string {
  const value = hex.replace("#", "");
  const r = parseInt(value.slice(0, 2), 16);
  const g = parseInt(value.slice(2, 4), 16);
  const b = parseInt(value.slice(4, 6), 16);
  return `rgba(${r}, ${g}, ${b}, ${alpha})`;
}

export type PresenceAccent = {
  hue: PresenceHue;
  /** The line and label colour. Bright enough to read as text. */
  base: string;
  /** Panel wash. Low by design — this sits under body copy. */
  fill: string;
  /** A shade up, for the one selected chip or the active tab. */
  fillStrong: string;
  /** The neon edge that does the work without a glow behind it. */
  border: string;
  /** Only for the avatar ring and the primary action. */
  glow: string;
  /**
   * What to caption a filled accent button in.
   *
   * This token exists because the test asked for it. The obvious primary
   * action is a gradient with white text on top, which is what
   * `profileNeon.primaryAction` does — and it works there because that blue is
   * dark. All four hues here are bright: white on teal is about 1.6:1, and
   * unreadable is not a matter of taste. Dark ink on the bright fill is 7:1 or
   * better for every one of them.
   *
   * Which also settles what a gradient is for here. A ramp from bright to deep
   * has no single ink that reads at both ends, so this system has no
   * bright-to-deep ramp at all: the filled action is a flat `base` with `ink`
   * on it, and `wash` below fades within the fill band, over surfaces that
   * carry no text.
   */
  ink: string;
  /**
   * The cover wash: the accent fading to nothing. Decorative, and never under
   * a caption.
   *
   * It stays inside the fill band rather than running from full-strength
   * accent, because the surface it covers is 130pt tall — a saturated block
   * that size stops being an accent and becomes the page.
   */
  wash: readonly [string, string];
};

function accent(hue: PresenceHue): PresenceAccent {
  const base = HUES[hue];
  return {
    hue,
    base,
    fill: rgba(base, 0.12),
    fillStrong: rgba(base, 0.18),
    border: rgba(base, 0.34),
    glow: rgba(base, 0.42),
    ink: colors.background,
    wash: [rgba(base, 0.18), rgba(base, 0)] as const
  };
}

/**
 * Every page type, and the hue it is drawn in. No default, no fall-through.
 *
 * The groupings answer "what is this presence for", which is the question a
 * visitor is asking when the colour reaches them before the words do:
 *
 * - creative — the presence *is* a person's work (ARTIST, CREATOR,
 *   PUBLIC_FIGURE, MEDIA).
 * - commerce — there is something to buy, and the page's tabs say so
 *   (BRAND, STORE, RESTAURANT).
 * - community — somewhere to turn up or something to belong to
 *   (NONPROFIT, SPORTS_TEAM, VENUE, EDUCATION).
 * - enterprise — an operation, which is also where OTHER sits, because brand
 *   teal is the product's own colour and the least presumptuous answer for a
 *   presence that declined to say what it is.
 */
const HUE_BY_TYPE: Record<PageType, PresenceHue> = {
  ARTIST: "creative",
  CREATOR: "creative",
  PUBLIC_FIGURE: "creative",
  MEDIA: "creative",

  BRAND: "commerce",
  STORE: "commerce",
  RESTAURANT: "commerce",

  NONPROFIT: "community",
  SPORTS_TEAM: "community",
  VENUE: "community",
  EDUCATION: "community",

  BUSINESS: "enterprise",
  PROFESSIONAL_SERVICE: "enterprise",
  LOCAL_BUSINESS: "enterprise",
  ORGANIZATION: "enterprise",
  OTHER: "enterprise"
};

const ACCENTS: Record<PresenceHue, PresenceAccent> = {
  enterprise: accent("enterprise"),
  creative: accent("creative"),
  commerce: accent("commerce"),
  community: accent("community")
};

/**
 * The accent for a page type.
 *
 * Takes a plain string rather than a `PageType`, because the value arriving
 * here came off the wire: a server one version ahead can name a type this
 * build has never heard of, and a screen cannot refuse to render because of
 * it. An unknown type is drawn in brand teal — the same answer OTHER gets,
 * which is the honest one, since all the app knows is that this is a presence.
 * That is a fall-through for *unknown input*, not for a known type somebody
 * forgot to place: `HUE_BY_TYPE` still has to name all sixteen to compile.
 */
export function presenceAccent(pageType: string | undefined | null): PresenceAccent {
  const hue = HUE_BY_TYPE[(pageType || "") as PageType];
  return ACCENTS[hue] ?? ACCENTS.enterprise;
}

/** Exposed for the tests that hold the table exhaustive and the ramp restrained. */
export const presenceAccentInternals = { HUES, HUE_BY_TYPE, ACCENTS, PAGE_TYPES } as const;
