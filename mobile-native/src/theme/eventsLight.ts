/**
 * Light palette for the seller Events manager and the Activity center.
 *
 * These two screens share the same navy-header, white-card trade dress as the
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

/** Event cover gradient (dark teal → navy) with a translucent tag pill on top. */
export const EVENT_COVER = {
  from: "#0F3B31",
  mid: "#15564A",
  to: "#0B2A3A",
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

/** Calendar date tile — navy month band for upcoming, muted grey for past. */
export const DATE_TILE = {
  upcomingBand: "#232F3E",
  pastBand: "#8FA5B8",
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
  status: {
    /** Published (green ping). */
    published: "#067D62",
    /** Promoted (violet). */
    promoted: "#6D4AC4",
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
