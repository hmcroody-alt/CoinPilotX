/**
 * PulseSoc native Unicode emoji foundation — shared types.
 *
 * The single source of truth for emoji everywhere in the app. Emoji are native
 * Unicode strings, rendered by the OS text stack. Never images, never vendor
 * IDs, never a remote API. Backends store the Unicode value itself.
 */

/** One skin-tone (or other) variant of a base emoji. */
export interface EmojiVariant {
  emoji: string;
  name: string;
}

/** One emoji in the checked-in metadata artifact (src/emoji/data/emoji.json). */
export interface EmojiEntry {
  /** The native Unicode emoji string (may be multi-codepoint: ZWJ, VS16, tags). */
  emoji: string;
  /** CLDR short name, lowercase — also the accessibility label source. */
  name: string;
  /** Search keywords (CLDR tags). */
  keywords: string[];
  /** Canonical PulseSoc category (uppercase, e.g. "SMILEYS & EMOTION"). */
  category: EmojiCategory;
  /** CLDR subgroup, e.g. "face-smiling". */
  subgroup: string;
  /** True when the emoji supports Fitzpatrick skin-tone modifiers. */
  skin_tone_capable: boolean;
  /** Skin-tone variants, in tone order 1-2..6. Empty when not tone-capable. */
  variants: EmojiVariant[];
}

/** Canonical category order. RECENT is virtual — populated from local usage. */
export const EMOJI_CATEGORIES = [
  "RECENT",
  "SMILEYS & EMOTION",
  "PEOPLE & BODY",
  "ANIMALS & NATURE",
  "FOOD & DRINK",
  "ACTIVITIES",
  "TRAVEL & PLACES",
  "OBJECTS",
  "SYMBOLS",
  "FLAGS"
] as const;

export type EmojiCategory = (typeof EMOJI_CATEGORIES)[number];

/** Quick-reaction bar shown on message long-press (Stage 8 spec order). */
export const QUICK_REACTIONS = ["❤️", "😂", "😮", "😢", "😡", "👍"] as const;

/** Skin tone preference: 0 = default/yellow, 1..5 = light..dark. */
export type SkinTonePreference = 0 | 1 | 2 | 3 | 4 | 5;
