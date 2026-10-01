/**
 * Light palette for the commerce inbox (card #6 of the Business "Sections" grid,
 * "Messages — Conversations with buyers").
 *
 * This is NOT the dark consumer Messenger tab. It is the seller's commerce inbox,
 * where every row is about a money object — an offer, an order, a pickup, a
 * listing question, a completed sale — surfaced through a context chip so the
 * seller can triage money-relevant threads at a glance. It extends `storeLight`
 * so it reads as the same family as Store, Orders, Marketplace and Advertising:
 * every neutral (page, card, hairline, header navy, muted text, tap targets,
 * radii, spacing) is inherited. Only the inbox-specific accents live here.
 *
 * The colour language is continuous with Orders — one fact, one colour across the
 * whole Business surface:
 *
 *   • violet → offers / Marketplace   (offer chips, Marketplace-sourced threads)
 *   • blue   → orders / Store          (order chips, in-transit, the unread edge)
 *   • green  → done / positive         (completed chips, presence, typing, reply speed)
 *   • gray   → neutral question / inert (listing question chips)
 *
 * Red is reserved for issues elsewhere and never appears as a chip variant.
 */

import { storeLight } from "./storeLight";

/**
 * AVATAR GRADIENTS — a person's colour is derived deterministically from their
 * stable id (see `avatarGradientFor` in api/commerceInbox), so the same buyer is
 * always the same colour across sessions and screens. Five hues, all drawn from
 * the app's existing accent family so avatars never introduce a foreign colour.
 */
export const MESSAGES_AVATAR_GRADIENTS = [
  { key: "violet", from: "#6B4FA3", to: "#8465C0" },
  { key: "blue", from: "#2B6DA8", to: "#3FA3D1" },
  { key: "green", from: "#067D62", to: "#3EC488" },
  { key: "warm", from: "#C7511F", to: "#F6A06B" },
  { key: "gray", from: "#5A6B7C", to: "#8FA5B8" }
] as const;

export type MessagesAvatarGradient = (typeof MESSAGES_AVATAR_GRADIENTS)[number];

/**
 * CONTEXT-CHIP VARIANTS — the five commerce objects a thread can be about. Each
 * variant is a fill + border (+ text where the default primary would not read on
 * the fill). The chip is always icon + text, so the colour reinforces meaning and
 * is never the sole signal. `kind` is the data-contract key the resolver emits;
 * the thread-view pinned card (follow-up mission) reuses these same variants.
 */
export const MESSAGES_CHIP_VARIANTS = {
  offer: {
    bg: "#F2EEFB",
    border: "#D9CDF0",
    text: "#5B4B80",
    icon: "🤝"
  },
  order: {
    bg: "#EEF3F8",
    border: "#CFDEEA",
    text: "#2B6DA8",
    icon: "📦"
  },
  pickup: {
    bg: "#F6F3FB",
    border: "#DDD2F0",
    text: "#5B4B80",
    icon: "📍"
  },
  question: {
    bg: "#F4F6F6",
    border: "#E0E3E3",
    text: "#565959",
    icon: "💬"
  },
  completed: {
    bg: "#EEF7F1",
    border: "#BFE0D3",
    text: "#067D62",
    icon: "✅"
  }
} as const;

export type MessagesChipKind = keyof typeof MESSAGES_CHIP_VARIANTS;

export const messagesLight = {
  bg: {
    /** Inherited neutrals — same page/card/header family as Store & Orders. */
    page: storeLight.bg.page,
    card: storeLight.bg.card,
    headerFrom: storeLight.bg.headerFrom,
    headerTo: storeLight.bg.headerTo,
    strip: storeLight.bg.strip,
    warning: storeLight.bg.warning,
    skeleton: storeLight.bg.skeleton,
    /** Unread row wash — a barely-there cool tint behind the blue edge. */
    unread: "#FBFDFF"
  },
  border: {
    hairline: storeLight.border.hairline,
    secondaryButton: storeLight.border.secondaryButton,
    warning: storeLight.border.warning,
    /** The 3px left edge on an unread row. Blue = Store/orders family. */
    unreadEdge: "#2B6DA8"
  },
  text: {
    primary: storeLight.text.primary,
    muted: storeLight.text.muted,
    link: storeLight.text.link,
    linkActive: storeLight.text.linkActive,
    onDark: storeLight.text.onDark,
    onDarkMuted: storeLight.text.onDarkMuted,
    /** Unread timestamps + names read blue and bold. */
    unread: "#2B6DA8"
  },
  status: {
    success: storeLight.status.success,
    warning: storeLight.status.warning,
    error: storeLight.status.error,
    neutral: storeLight.status.neutral
  },
  /**
   * UNREAD COUNT BADGE — blue pill in the row's right column. The Unread FILTER
   * chip count, by contrast, goes hot-orange when nonzero (see `filterHot`) to
   * pull the eye to the triage control, not each row.
   */
  unreadBadge: {
    bg: "#2B6DA8",
    text: "#FFFFFF"
  },
  /** The Unread filter chip's count colour when > 0. */
  filterHot: "#C7511F",
  /**
   * PRESENCE — a green dot with a white ring, drawn only when the existing
   * product policy actually exposes mutual presence (flag-gated). Green = the
   * "positive / live" idea shared with typing and reply speed.
   */
  presence: {
    dot: "#3EC488",
    ring: "#FFFFFF"
  },
  /** Typing indicator dots — same green as presence; "someone is here, now". */
  typing: {
    dot: "#3EC488"
  },
  /**
   * REPLY-TIME STRIP — the "⚡ Avg reply {time}" band. Mint accent on the navy
   * strip, matching the header family. The incentive framing ("keeps your fast-
   * responder badge") is only shown when a real badge rule sources it; otherwise
   * the stat stands alone (see commerceInbox `replyBadgeIncentiveEnabled`).
   */
  replyStrip: {
    accent: "#3EC488",
    bg: storeLight.bg.strip,
    text: storeLight.text.onDark,
    muted: storeLight.text.onDarkMuted
  },
  /**
   * TIME-CRITICAL BANNER — an expiring-offer alert. Reads from the same offer
   * state the Marketplace mission owns (one expiry source of truth); the palette
   * is the inherited warm attention wash so it matches the Orders banner.
   */
  banner: {
    bg: storeLight.bg.warning,
    border: storeLight.border.warning,
    text: storeLight.text.primary,
    accent: "#C7511F"
  },
  chip: MESSAGES_CHIP_VARIANTS,
  avatarGradients: MESSAGES_AVATAR_GRADIENTS,
  radius: {
    card: storeLight.radius.card,
    control: storeLight.radius.control,
    thumb: storeLight.radius.thumb,
    pill: storeLight.radius.pill
  },
  size: {
    avatar: 48,
    presenceDot: 13,
    tapTarget: storeLight.size.tapTarget
  },
  space: {
    gutter: storeLight.space.gutter,
    section: storeLight.space.section,
    card: storeLight.space.card
  }
} as const;

export type MessagesLightTheme = typeof messagesLight;
