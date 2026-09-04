/**
 * PulseSoc native Unicode emoji foundation — public API.
 *
 * Import emoji functionality from here only. Regression guard
 * (tests/emoji) fails if a second picker or hardcoded emoji dataset
 * appears elsewhere in the app.
 */
export { EmojiPicker } from "./EmojiPicker";
export type { EmojiPickerProps } from "./EmojiPicker";
export {
  allEmoji,
  emojiA11yLabel,
  emojiByCategory,
  emojiDataVersion,
  findEmoji,
  searchEmoji
} from "./emojiData";
export {
  getRecentEmoji,
  getSkinTonePreference,
  recordRecentEmoji,
  setSkinTonePreference
} from "./recents";
export {
  countEmojiClusters,
  isEmojiCodePoint,
  isSingleEmoji,
  splitEmojiClusters,
  stripSkinTone
} from "./grapheme";
export { EMOJI_CATEGORIES, QUICK_REACTIONS } from "./types";
export type { EmojiCategory, EmojiEntry, EmojiVariant, SkinTonePreference } from "./types";
