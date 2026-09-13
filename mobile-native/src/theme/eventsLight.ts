/**
 * Light palette for the seller Events manager and the Activity center.
 *
 * These two screens share the same black-header, white-card trade dress as the
 * Store / Orders / Messages seller surfaces, so the base neutrals come straight
 * from `storeLight` — page, card, header gradient, hairline, text families.
 * Only the domain-specific additions the mission spells out live here, so a
 * colour decision is one edit rather than a search-and-replace:
 *
 *   - live red (dot / LIVE label / live banner wash)
 *   - event cover gradient + translucent tag pill
 *   - countdown unit tiles
 *   - calendar date tile bands (upcoming navy / past muted)
 *   - capacity fill gradient (+ amber "nearly full")
 *   - avatar stack rings/overlap
 *   - Activity type-icon circles, reusing the app-wide domain semantics
 *     (violet = marketplace, blue = orders, green = money-in/positive,
 *      red = live/urgent, pink = social, shield-green = system).
 *
 * Nothing here overrides an existing token; this is purely additive.
 *
 * THIS FILE STRADDLES THE BLACK/WHITE/GREEN LOCK, which is why it is not in
 * `businessPaletteLock`'s scan list and why the violet and blue above survive.
 *
 * `EventsManagerScreen` is a seller surface and is subject to the lock;
 * `ActivityScreen` is the app-wide notification centre and is not. The tokens
 * divide along exactly that line, and it is worth checking before editing one:
 *
 *   Events manager only — locked:  EVENT_COVER, COUNTDOWN, DATE_TILE, CAPACITY,
 *                                  AVATAR_STACK, `status.*`
 *   Activity only — not locked:    ACTIVITY_TYPE, `bg.unread`,
 *                                  `border.unreadEdge`, `text.unread`
 *   Shared:                        EVENTS_LIVE and the inherited neutrals
 *
 * So `ACTIVITY_TYPE.orders` is still the Store blue #2B6DA8 and
 * `.marketplace` still a violet. That is not an oversight — those circles are
 * the app's domain colour-coding, they appear beside social and safety rows
 * that were never in scope, and collapsing six categories onto green would cost
 * the Activity feed the distinction it is built on. Recolouring them is a
 * product decision about the notification centre, not a consequence of this
 * lock.
 */

import { storeLight } from "./storeLight";

/** Live / urgent-attention red — one value, used by both screens. */
export const EVENTS_LIVE = {
  /** The pulsing dot and the LIVE label. */
  dot: "#E0332E",
  label: "#E0332E",
  /** The live-now banner wash + its border. */
  bannerBg: "#FDF1F1",
  bannerBorder: "#F0C9C8"
} as const;

/**
 * Event cover gradient (dark teal, deepening) with a translucent tag pill on top.
 *
 * The ramp used to end on the navy #0B2A3A. Under the black/white/green lock it
 * ends on a deep green-black instead, at the same relative luminance (0.0199 vs
 * 0.0203) so the cover is exactly as dark as it was and the white cover text
 * keeps its contrast — only the hue changed.
 */
export const EVENT_COVER = {
  from: "#0F3B31",
  mid: "#15564A",
  to: "#0A2C24",
  /** Translucent tag pill (IN-PERSON · WORKSHOP etc.) floating on the cover. */
  tagBg: "rgba(255,255,255,0.16)",
  tagBorder: "rgba(255,255,255,0.28)",
  tagText: "#FFFFFF",
  onCover: "#FFFFFF",
  onCoverMuted: "rgba(255,255,255,0.78)"
} as const;

/** Countdown unit tiles (days / hours / minutes). */
export const COUNTDOWN = {
  tileBg: "#F4F6F6",
  tileBorder: "#E7E9E9",
  number: storeLight.text.primary,
  unit: storeLight.text.muted
} as const;

/**
 * Calendar date tile — black month band for upcoming, muted grey for past.
 *
 * The upcoming band was the reference navy #232F3E and is now the same
 * near-black as the header, so a date tile and the chrome above it are one dark
 * rather than two. The past band was #8FA5B8, a cool blue-grey; it is now a true
 * grey at the same luminance (0.360 vs 0.362), which keeps "past" reading as
 * exactly as recessive as it did.
 *
 * Worth noting that #8FA5B8 is 22% saturated and so sits *under* the
 * `businessPaletteLock` floor — the scan would not have caught it. It went
 * because it was read, next to a navy that the scan did catch.
 */
export const DATE_TILE = {
  upcomingBand: storeLight.bg.headerFrom,
  pastBand: "#9DA3A3",
  bandText: "#FFFFFF",
  bodyBg: "#FFFFFF",
  bodyBorder: storeLight.border.hairline,
  day: storeLight.text.primary,
  dayMuted: storeLight.text.muted
} as const;

/** Capacity bar fill gradient + the amber "nearly full" treatment (>90%). */
export const CAPACITY = {
  from: "#3EC488",
  to: "#067D62",
  track: "#E7E9E9",
  /** Amber once the bar crosses the nearly-full threshold. */
  nearlyFull: "#C77F00",
  full: "#B12704"
} as const;

/** Stacked attendee avatars: 2px white ring, −7px overlap. */
export const AVATAR_STACK = {
  ring: "#FFFFFF",
  ringWidth: 2,
  overlap: -7,
  moreBg: "#E7E9E9",
  moreText: storeLight.text.muted
} as const;

/**
 * Activity type-icon circles. Each domain reuses its app-wide semantic colour so
 * a violet circle means "marketplace" here exactly as it does on every other
 * seller surface. `bg` is the soft circle fill, `fg` the glyph / ring.
 */
export const ACTIVITY_TYPE = {
  social: { bg: "#FDF1F4", fg: "#D34B7D" }, // like / reaction — pink
  marketplace: { bg: "#F3EFFF", fg: "#6D4AC4" }, // offers / listings — violet
  orders: { bg: "#EAF2FB", fg: "#2B6DA8" }, // orders / shipping — blue
  payments: { bg: "#E9F6F0", fg: "#067D62" }, // money-in / payouts — green
  live: { bg: EVENTS_LIVE.bannerBg, fg: EVENTS_LIVE.dot }, // live / urgent — red
  system: { bg: "#E9F6EE", fg: "#1F8A5B" } // shield / system — green
} as const;

export type ActivityTypeColorKey = keyof typeof ACTIVITY_TYPE;

/**
 * The full events/activity light theme. Base neutrals are inherited from
 * `storeLight`; the groups above are the additions.
 */
export const eventsLight = {
  bg: {
    page: storeLight.bg.page,
    card: storeLight.bg.card,
    headerFrom: storeLight.bg.headerFrom,
    headerTo: storeLight.bg.headerTo,
    strip: storeLight.bg.strip,
    skeleton: storeLight.bg.skeleton,
    /** Unread activity row tint — same family as the Messages unread row. */
    unread: "#FBFDFF"
  },
  border: {
    hairline: storeLight.border.hairline,
    secondaryButton: storeLight.border.secondaryButton,
    /** 3px left edge on an unread activity row (blue = Store/orders family). */
    unreadEdge: "#2B6DA8"
  },
  text: {
    primary: storeLight.text.primary,
    muted: storeLight.text.muted,
    link: storeLight.text.link,
    linkActive: storeLight.text.linkActive,
    onDark: storeLight.text.onDark,
    onDarkMuted: storeLight.text.onDarkMuted,
    unread: "#2B6DA8"
  },
  /**
   * The upcoming-row status LED (`EventRow`'s `StatusLED`). Three lifecycle
   * states, and it is worth being precise about how they relate, because it is
   * what decides the colours: `promoted` is not a peer of `published`, it is
   * `published` *plus a live Advertising campaign* (`deriveEventStatus` returns
   * it only when `event.promotionCampaignId` is set). `draft` is the one state
   * that is not live.
   *
   * `promoted` was the Marketplace violet. Under the black/white/green lock the
   * replacement is the app's gold, not a second green and not the promotion
   * gray:
   *
   *   - A second green would put "published" and "promoted" a hair apart on the
   *     one axis a reader scans, and they are the two states most often on
   *     screen together.
   *   - `adsLight.post.base` gray is the *product* identity of Post ads, not a
   *     lifecycle state; borrowing it would make a promoted event read as more
   *     recessive than a plain published one, which is backwards.
   *   - Gold is the money/ads channel app-wide (`adsLight`: "gold → money";
   *     `insightsLight.source.ads`), and what distinguishes a promoted event
   *     from a published one is exactly that a campaign is spending on it. This
   *     is the case gold exists for.
   *
   * It is a *dark* gold because this token is a text colour as well as a dot
   * fill. The money golds are fills and fail as text on the white card:
   * #FFA41C is 1.99:1 and this file's own #C77F00 is 3.25:1. #8A6100 is the
   * same hue at 5.54:1, over the 4.5:1 body-text floor and close to the 6.13:1
   * the violet had.
   *
   * All three are drawn beside `status.line` ("Promoted · 4.2k reach",
   * "Published · 88 interested", or the first publish blocker), so the state is
   * never carried by colour alone.
   */
  status: {
    /** Published (green ping). */
    published: "#067D62",
    /** Promoted — dark gold, the money/ads channel. See above. */
    promoted: "#8A6100",
    /** Draft (grey). */
    draft: storeLight.text.muted
  },
  live: EVENTS_LIVE,
  cover: EVENT_COVER,
  countdown: COUNTDOWN,
  dateTile: DATE_TILE,
  capacity: CAPACITY,
  avatarStack: AVATAR_STACK,
  activityType: ACTIVITY_TYPE,
  cta: storeLight.cta,
  radius: storeLight.radius,
  size: storeLight.size,
  space: storeLight.space
} as const;

export type EventsLightTheme = typeof eventsLight;
