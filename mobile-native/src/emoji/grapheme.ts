/**
 * Grapheme-safe emoji utilities (Stage 14).
 *
 * Never use naive string.length to count emoji: "👨‍👩‍👧‍👦".length is 11, "🇭🇹".length
 * is 4, "👍🏿".length is 4. Hermes does not ship Intl.Segmenter, so this module
 * implements a small emoji-aware clusterer that understands exactly the
 * constructs RGI emoji use: ZWJ joins, variation selectors, Fitzpatrick
 * modifiers, keycap sequences, regional-indicator pairs and tag sequences.
 * It is not a general Unicode grapheme algorithm — it is scoped to emoji
 * validation and counting, which is all the app needs it for.
 */

const ZWJ = 0x200d;
const VS15 = 0xfe0e;
const VS16 = 0xfe0f;
const KEYCAP = 0x20e3;

const isRegionalIndicator = (cp: number) => cp >= 0x1f1e6 && cp <= 0x1f1ff;
const isSkinToneModifier = (cp: number) => cp >= 0x1f3fb && cp <= 0x1f3ff;
const isTag = (cp: number) => cp >= 0xe0020 && cp <= 0xe007f;
const isVariationSelector = (cp: number) => cp === VS15 || cp === VS16;

/** True when the codepoint can plausibly begin an emoji cluster. */
export function isEmojiCodePoint(cp: number): boolean {
  return (
    (cp >= 0x1f000 && cp <= 0x1faff) || // main emoji planes
    (cp >= 0x2600 && cp <= 0x27bf) || // misc symbols + dingbats
    (cp >= 0x2190 && cp <= 0x21ff) || // arrows (↔️ …)
    (cp >= 0x2300 && cp <= 0x23ff) || // technical (⌚ ⏰ …)
    (cp >= 0x25a0 && cp <= 0x25ff) || // geometric (▶️ …)
    (cp >= 0x2900 && cp <= 0x297f) ||
    (cp >= 0x2b00 && cp <= 0x2bff) || // ⬆️ ⭐ …
    (cp >= 0x3030 && cp <= 0x303d) ||
    (cp >= 0x3297 && cp <= 0x3299) ||
    cp === 0x00a9 || cp === 0x00ae || // © ®
    cp === 0x203c || cp === 0x2049 || // ‼️ ⁉️
    cp === 0x2122 || cp === 0x2139 || // ™️ ℹ️
    (cp >= 0x2194 && cp <= 0x2199) ||
    (cp >= 0x0030 && cp <= 0x0039) || cp === 0x0023 || cp === 0x002a // keycap bases
  );
}

/**
 * Split a string into emoji-aware clusters. Non-emoji characters come back as
 * single-codepoint clusters, which is fine for the validation/counting uses
 * this module serves.
 */
export function splitEmojiClusters(input: string): string[] {
  const cps = Array.from(input); // codepoint-safe iteration (no surrogate splits)
  const clusters: string[] = [];
  let i = 0;
  while (i < cps.length) {
    let j = i + 1;
    const startCp = cps[i].codePointAt(0)!;
    // Regional-indicator pair (flags): consume exactly two.
    if (isRegionalIndicator(startCp) && j < cps.length && isRegionalIndicator(cps[j].codePointAt(0)!)) {
      j += 1;
    } else {
      // Consume trailing modifiers / VS / keycap / tags, and ZWJ-joined continuations.
      while (j < cps.length) {
        const cp = cps[j].codePointAt(0)!;
        if (isVariationSelector(cp) || isSkinToneModifier(cp) || cp === KEYCAP || isTag(cp)) {
          j += 1;
        } else if (cp === ZWJ && j + 1 < cps.length) {
          j += 2; // the ZWJ and the joined base
        } else {
          break;
        }
      }
    }
    clusters.push(cps.slice(i, j).join(""));
    i = j;
  }
  return clusters;
}

/** Grapheme-aware count — the correct replacement for string.length on emoji. */
export function countEmojiClusters(input: string): number {
  return splitEmojiClusters(input).length;
}

/**
 * True when `input` is exactly ONE emoji cluster (reaction validation,
 * Stage 16). Rejects empty strings, plain text, multi-emoji strings and
 * emoji+text mixes; accepts ZWJ families, flags and skin-tone variants.
 */
export function isSingleEmoji(input: string): boolean {
  if (!input || input.length > 32) return false;
  const clusters = splitEmojiClusters(input);
  if (clusters.length !== 1) return false;
  const first = clusters[0].codePointAt(0)!;
  return isEmojiCodePoint(first);
}

/** Strip skin-tone modifiers, giving the canonical base emoji. */
export function stripSkinTone(emoji: string): string {
  return Array.from(emoji)
    .filter((ch) => !isSkinToneModifier(ch.codePointAt(0)!))
    .join("");
}
