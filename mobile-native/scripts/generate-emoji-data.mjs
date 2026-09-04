#!/usr/bin/env node
/**
 * Regenerates src/emoji/data/emoji.json — the checked-in native Unicode emoji
 * metadata artifact (PulseSoc emoji foundation, Stage 1).
 *
 *   node scripts/generate-emoji-data.mjs
 *
 * Source: emojibase-data (RGI emoji, CLDR names + keyword tags), fetched at
 * DEV time only. The app has ZERO runtime network dependency for emoji: it
 * renders native Unicode glyphs and reads this checked-in JSON. Never store
 * emoji as images or vendor IDs — the Unicode string itself is the value.
 *
 * Output schema per entry:
 *   { emoji, name, keywords[], category, subgroup, skin_tone_capable, variants[] }
 * Categories are PulseSoc-canonical (RECENT is virtual/client-side):
 *   SMILEYS & EMOTION, PEOPLE & BODY, ANIMALS & NATURE, FOOD & DRINK,
 *   ACTIVITIES, TRAVEL & PLACES, OBJECTS, SYMBOLS, FLAGS
 */
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

const OUT = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "src", "emoji", "data", "emoji.json");
const DATA_URL = "https://cdn.jsdelivr.net/npm/emojibase-data@latest/en/data.json";
const MSG_URL = "https://cdn.jsdelivr.net/npm/emojibase-data@latest/en/messages.json";

// emojibase group index -> canonical PulseSoc category. Index 2 (components:
// bare skin-tone swatches, hair) is intentionally excluded from the picker.
const CATEGORY = [
  "SMILEYS & EMOTION", "PEOPLE & BODY", null, "ANIMALS & NATURE", "FOOD & DRINK",
  "TRAVEL & PLACES", "ACTIVITIES", "OBJECTS", "SYMBOLS", "FLAGS"
];

const [data, messages] = await Promise.all(
  [DATA_URL, MSG_URL].map(async (u) => {
    const r = await fetch(u);
    if (!r.ok) throw new Error(`${u}: HTTP ${r.status}`);
    return r.json();
  })
);

const subName = (order) => {
  const s = messages.subgroups.find((x) => x.order === order) ?? messages.subgroups[order];
  return s ? s.message : "";
};

const emojis = data
  .filter((e) => e.group !== undefined && e.group !== 2 && e.emoji)
  .sort((a, b) => a.order - b.order)
  .map((e) => ({
    emoji: e.emoji,
    name: e.label,
    keywords: e.tags ?? [],
    category: CATEGORY[e.group],
    subgroup: subName(e.subgroup),
    skin_tone_capable: Boolean(e.skins && e.skins.length),
    variants: (e.skins ?? []).map((s) => ({ emoji: s.emoji, name: s.label }))
  }));

const artifact = {
  version: `emojibase-data en (RGI), generated ${new Date().toISOString().slice(0, 10)}`,
  count: emojis.length,
  emojis
};

fs.writeFileSync(OUT, JSON.stringify(artifact));
console.log(`wrote ${OUT}: ${emojis.length} emoji, ${fs.statSync(OUT).size} bytes`);
