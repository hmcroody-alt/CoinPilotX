/**
 * Light palette for the commerce inbox (card #6 of the Business "Sections" grid,
 * "Messages — Conversations with buyers").
 *
 * This is NOT the dark consumer Messenger tab. It is the seller's commerce inbox,
 * where every row is about a money object — an offer, an order, a pickup, a
 * listing question, a completed sale — surfaced through a context chip so the
 * seller can triage money-relevant threads at a glance. It extends `storeLight`
 * so it reads as the same family as Store, Orders, Marketplace and Advertising:
 * every neutral (page, card, hairline, black header, muted text, tap targets,
 * radii, spacing) is inherited. Only the inbox-specific accents live here.
 *
 * The colour language is continuous with Orders — one fact, one colour across the
 * whole Business surface:
 *
 *   • green  → orders / Store          (order chips, the unread edge and badge)
 *   • green  → done / positive         (completed chips, presence, typing, reply speed)
 *   • gold   → money in play           (offer chips)
 *   • gray   → local pickup / Marketplace, and the neutral listing question
 *
 * Offers were violet and orders were blue, matching the hues Orders and
 * Marketplace used for the same two facts. The business surfaces are locked to
 * black, white and green, so this file follows the mapping the rest of the sweep
 * settled on: the Store/transit blue joined the green family as `#0A7050`, and
 * the Marketplace/pickup violet fell back to the neutral `#4A5250`. Offers did
 * not follow pickup into gray, because an offer is a live sum of money and gold
 * is the money channel app-wide (`adsLight`: "gold → money") — three gray chip
 * variants beside each other would have been the worse outcome.
 *
 * The cost, stated because it is real: green now means both "order" and
 * "completed", and gray means both "pickup" and "question". Every chip renders
 * an icon AND its label (`MESSAGES_CHIP_VARIANTS` below is never drawn as a bare
 * swatch), so no state here is signalled by colour alone — the same mitigation
 * Orders and Advertising rely on.
 *
 * Red is reserved for issues elsewhere and never appears as a chip variant.
 */

import { storeLight } from "./storeLight";

/**
 * AVATAR GRADIENTS — a person's colour is derived deterministically from their
 * stable id (see `avatarGradientFor` in api/commerceInbox), so the same buyer is
 * always the same colour across sessions and screens. Five hues, all drawn from
 * the app's existing accent family so avatars never introduce a foreign colour.
 *
 * The violet and blue entries went with the black/white/green lock. They are
 * replaced rather than dropped, because `avatarGradientFor` indexes this list
 * modulo its length — shortening it would silently re-colour every existing
 * buyer. The two new hues are a deep forest green and the money gold, which
 * keeps five values that are still distinguishable from one another under a
 * palette with no cool end.
 *
 * `key` is descriptive only. Nothing persists or matches on it (the resolver
 * hashes an id to an index and the avatar reads `from`/`to`), so renaming these
 * alongside the values does not move anyone's colour.
 *
 * Initials are drawn in white over the gradient, so the lighter `to` end is what
 * has to carry them. Against white the new ends measure 3.20 (forest), 3.25
 * (gold) and 3.06 (gray) — over the 3:1 large-text floor, and better than three
 * of the five they replace or sit beside.
 */
export const MESSAGES_AVATAR_GRADIENTS = [
  { key: "forest", from: "#0F3B31", to: "#4E9E86" },
  { key: "gold", from: "#8A6100", to: "#C77F00" },
  { key: "green", from: "#067D62", to: "#3EC488" },
  { key: "warm", from: "#C7511F", to: "#F6A06B" },
  { key: "gray", from: "#4A5250", to: "#8A9691" }
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
  /** Money in play — gold. See the money note in the file header. */
  offer: {
    bg: "#FBF3E3",
    border: "#EBD9B0",
    text: "#8A6100",
    icon: "🤝"
  },
  /** Store / orders — the same green Orders gives a Store-sourced order. */
  order: {
    bg: "#ECF6F2",
    border: "#C9E2D8",
    text: "#0A7050",
    icon: "📦"
  },
  /** Local pickup / Marketplace — the neutral the sweep gave that family. */
  pickup: {
    bg: "#EFF1F1",
    border: "#DCDFDF",
    text: "#4A5250",
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
    /** Unread row wash — a barely-there tint behind the green edge. */
    unread: "#F7FBF9"
  },
  border: {
    hairline: storeLight.border.hairline,
    secondaryButton: storeLight.border.secondaryButton,
    warning: storeLight.border.warning,
    /** The 3px left edge on an unread row. Green = Store/orders family. */
    unreadEdge: "#0A7050"
  },
  text: {
    primary: storeLight.text.primary,
    muted: storeLight.text.muted,
    link: storeLight.text.link,
    linkActive: storeLight.text.linkActive,
    onDark: storeLight.text.onDark,
    onDarkMuted: storeLight.text.onDarkMuted,
    /** Unread timestamps + names read green and bold (6.09:1 on the card). */
    unread: "#0A7050"
  },
  status: {
    success: storeLight.status.success,
    warning: storeLight.status.warning,
    error: storeLight.status.error,
    neutral: storeLight.status.neutral
  },
  /**
   * UNREAD COUNT BADGE — green pill in the row's right column. The Unread FILTER
   * chip count, by contrast, goes hot-orange when nonzero (see `filterHot`) to
   * pull the eye to the triage control, not each row.
   */
  unreadBadge: {
    bg: "#0A7050",
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
   * REPLY-TIME STRIP — the "⚡ Avg reply {time}" band. Mint accent on the black
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
