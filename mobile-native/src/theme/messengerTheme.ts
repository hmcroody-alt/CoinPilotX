/**
 * NEON DUSK — the Messenger surface palette.
 *
 * Midnight blue, soft moonstone, restrained frosted glass, cyan/teal accents and
 * a controlled violet. Lighter than the near-black Messenger it replaces, darker
 * than the white concepts, and meant to be comfortable to read for hours.
 *
 * ## Why these live here and not in `ThemeContext`
 *
 * `buildTheme` pins `const activeTheme: ThemeMode = "dark"` — Black, White and
 * both Light Futuristic palettes are already written, already complete, and
 * already unreachable through `ThemeProvider`. A `"neonDusk"` ThemeMode would
 * join that list: it would type-check, it would have tests, and it would render
 * nothing at all on a device. So Neon Dusk ships the way `presenceTheme` does —
 * as a fixed, surface-scoped token set that the screen consumes directly.
 *
 * That also answers the other half of the constraint. Nothing here mutates the
 * shared `colors` object or the app-wide `PulseBackground`, so no other screen
 * changes appearance, and the existing appearance modes keep working exactly as
 * they do today. If the theme system ever unpins `activeTheme`, this module is
 * the palette to lift into it, not a pile of literals to go hunting for.
 *
 * ## The two rules that are not negotiable
 *
 * `online` is green and `offline` is gray, everywhere, always. They are the only
 * two tokens in this file that carry meaning rather than style: every other
 * colour here can be re-tuned by a designer, and these two cannot, because a
 * teal "OFFLINE" pill tells the user something false. `online` is deliberately
 * `colors.safety` — the canonical PulseSoc green — rather than a Messenger-local
 * approximation, so the presence green cannot drift away from the rest of the
 * app one surface at a time.
 *
 * Colour is never the only carrier: each pill renders its own word, so presence
 * survives a screenshot in grayscale and a red/green colour deficiency. That
 * matters more than usual here — green and gray are close in *luminance* (1.16:1
 * between them), so they are told apart by hue and by the label, not by weight.
 *
 * ## Measured contrast
 *
 * Ratios against a conversation card, which is the darkest thing any of this is
 * drawn on (the card composites to rgb(43,63,97) over the gradient's midpoint):
 *
 *   primaryText   9.56   secondaryText 6.36   tertiaryText 5.28
 *
 * And each pill's text against its own soft fill, which is the real background
 * for the badges and the tighter of the two tests:
 *
 *   online 5.46   teal 5.43   verified 5.20   blue 5.03   offline 4.78   violet 4.71
 *
 * All above 4.5:1. `onAccentText` on a filled teal button measures 10.65:1.
 */

import { colors } from "./colors";

/**
 * Base field, as a gradient rather than a fill.
 *
 * Deep slate blue at the top, medium midnight blue through the body, a cooler
 * blue lift at the bottom. It is drawn as a translucent veil over the shared
 * `PulseBackground` rather than as an opaque page, which is what keeps the depth
 * the brief asks to preserve: the mesh and nodes still read faintly through it
 * instead of the screen going flat.
 */
export const messengerBackgroundGradient = ["#14203A", "#1A2A47", "#21344F"] as const;

/** Held just under 1 so the shared field survives underneath. */
export const messengerBackgroundOpacity = 0.93;

export const messengerTheme = {
  /** The page itself. Matches the gradient's midpoint for any flat fallback. */
  background: "#1A2A47",

  /**
   * Conversation cards. A lighter blue-gray glass panel that separates from the
   * page by luminance, not by a heavy border or a glow.
   */
  surface: "rgba(52, 74, 111, 0.66)",

  /** Story rail and quick actions — one step up from a conversation card. */
  surfaceElevated: "rgba(66, 90, 130, 0.72)",

  /**
   * Filter bar and bottom-nav glass. Deliberately *darker* than the page: a
   * chrome container that out-brightens its own content reads as the subject.
   */
  surfaceRecessed: "rgba(13, 22, 40, 0.74)",

  /** Pressed state for a card. A lift, not a colour change. */
  surfacePressed: "rgba(84, 112, 156, 0.58)",

  /** Thin and soft. Separation comes from the fill; this only finishes the edge. */
  border: "rgba(148, 182, 228, 0.22)",
  borderStrong: "rgba(150, 196, 244, 0.40)",

  /** Names, titles, anything the eye lands on first. */
  primaryText: "#EFF4FD",
  /** Message previews and subtitles — softer, still comfortably readable. */
  secondaryText: "#BCCAE0",
  /** Timestamps and section labels. Muted, and audited to stay above 4.5:1. */
  tertiaryText: "#A7B9D2",

  /**
   * HARD RULE — presence online. Canonical PulseSoc green, never overridden.
   *
   * The soft fills below all sit at 0.10–0.12 rather than the 0.14–0.15 that
   * reads better in isolation. A bright accent used as its own 15% fill lifts
   * the pill's background almost as much as it lifts the text, so the gain
   * cancels: violet measured 3.18:1 at 0.15 and 4.71:1 at 0.10 against the same
   * card. Every pill below clears 4.5:1, which is the threshold these need —
   * 9px uppercase is small text, not large.
   */
  online: colors.safety,
  onlineSoft: "rgba(63, 240, 160, 0.12)",
  onlineBorder: "rgba(63, 240, 160, 0.42)",

  /** HARD RULE — presence offline. Neutral cool gray, no green and no blue cast. */
  offline: "#C0C6CE",
  offlineSoft: "rgba(192, 198, 206, 0.12)",
  offlineBorder: "rgba(192, 198, 206, 0.30)",

  /** Primary accent: new chat, pinned, direct, active tab. */
  tealAccent: "#4FEBD9",
  tealSoft: "rgba(79, 235, 217, 0.12)",
  tealBorder: "rgba(79, 235, 217, 0.40)",

  /** Reserved for AI and the assistant. Controlled — never a page-wide wash. */
  violetAccent: "#C8B4FF",
  violetSoft: "rgba(200, 180, 255, 0.10)",
  violetBorder: "rgba(200, 180, 255, 0.38)",

  /** Rooms and groups: blue, so they do not compete with the teal accents. */
  blueAccent: "#9CCBFF",
  blueSoft: "rgba(156, 203, 255, 0.10)",
  blueBorder: "rgba(156, 203, 255, 0.36)",

  /** Verification keeps the app's existing verified blue. */
  verifiedAccent: "#7FDCFF",
  verifiedSoft: "rgba(127, 220, 255, 0.12)",
  verifiedBorder: "rgba(127, 220, 255, 0.38)",

  /** Ink for anything sitting on a filled teal surface. */
  onAccentText: "#06201C"
} as const;

export type MessengerTheme = typeof messengerTheme;

export type MessengerBadgeTone = {
  background: string;
  border: string;
  text: string;
};

/**
 * Every badge the row can render, mapped to its own tone.
 *
 * This exists because the screen used to paint one green pill for all of them.
 * "OFFLINE" arrived green, and so did AI, ROOM, DIRECT and VERIFIED, which is
 * the failure the brief calls out by name: when every badge is green, green
 * stops meaning online.
 *
 * Keyed on the lowercased badge string that `conversationSignalBadges` emits, so
 * a new badge type gets the neutral tone rather than silently inheriting green.
 */
const BADGE_TONES: Record<string, MessengerBadgeTone> = {
  // Presence. The two that carry meaning.
  online: {
    background: messengerTheme.onlineSoft,
    border: messengerTheme.onlineBorder,
    text: messengerTheme.online
  },
  offline: {
    background: messengerTheme.offlineSoft,
    border: messengerTheme.offlineBorder,
    text: messengerTheme.offline
  },
  // The assistant is not a person and has no human presence; violet marks it as
  // the AI surface rather than borrowing either presence colour.
  assistant: {
    background: messengerTheme.violetSoft,
    border: messengerTheme.violetBorder,
    text: messengerTheme.violetAccent
  },

  // Conversation type.
  direct: {
    background: messengerTheme.tealSoft,
    border: messengerTheme.tealBorder,
    text: messengerTheme.tealAccent
  },
  group: {
    background: messengerTheme.blueSoft,
    border: messengerTheme.blueBorder,
    text: messengerTheme.blueAccent
  },
  room: {
    background: messengerTheme.blueSoft,
    border: messengerTheme.blueBorder,
    text: messengerTheme.blueAccent
  },
  ai: {
    background: messengerTheme.violetSoft,
    border: messengerTheme.violetBorder,
    text: messengerTheme.violetAccent
  },
  intelligence: {
    background: messengerTheme.violetSoft,
    border: messengerTheme.violetBorder,
    text: messengerTheme.violetAccent
  },
  undx: {
    background: messengerTheme.violetSoft,
    border: messengerTheme.violetBorder,
    text: messengerTheme.violetAccent
  },

  // Flags.
  pinned: {
    background: messengerTheme.tealSoft,
    border: messengerTheme.tealBorder,
    text: messengerTheme.tealAccent
  },
  verified: {
    background: messengerTheme.verifiedSoft,
    border: messengerTheme.verifiedBorder,
    text: messengerTheme.verifiedAccent
  },
  // Muted is an absence of activity, so it shares the inactive gray.
  muted: {
    background: messengerTheme.offlineSoft,
    border: messengerTheme.offlineBorder,
    text: messengerTheme.offline
  }
};

/**
 * Neutral fallback. An unrecognised badge must not land on a colour that claims
 * something — gray claims the least.
 */
const NEUTRAL_TONE: MessengerBadgeTone = {
  background: messengerTheme.offlineSoft,
  border: messengerTheme.offlineBorder,
  text: messengerTheme.offline
};

export function messengerBadgeTone(badge: string): MessengerBadgeTone {
  return BADGE_TONES[String(badge || "").trim().toLowerCase()] || NEUTRAL_TONE;
}

/**
 * The colour of the avatar's presence dot.
 *
 * Separate from the badge tone because the dot answers one question — is this
 * person available — and only ever has three answers: online, an assistant that
 * is reachable, or nothing at all. It used to be `toneColor(tone)`, which meant
 * a founder or intelligence contact showed a *violet* dot while online and every
 * other online contact showed the same teal as the PINNED and DIRECT pills.
 */
export function messengerPresenceDotColor(presence?: string): string {
  const value = String(presence || "").trim().toLowerCase();
  if (value === "assistant") return messengerTheme.violetAccent;
  if (value === "online") return messengerTheme.online;
  return messengerTheme.offline;
}
