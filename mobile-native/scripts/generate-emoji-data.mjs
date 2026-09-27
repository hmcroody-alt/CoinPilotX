#!/usr/bin/env node
/**
 * Regenerates the checked-in native Unicode emoji metadata artifact (PulseSoc
 * emoji foundation, Stage 1) for BOTH platforms.
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
import { createHash } from "node:crypto";
import { fileURLToPath } from "node:url";

const REPO = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "..");

// Two emission targets, one generator, one dataset. The web picker and the
// native picker read the SAME bytes, and tests/web_surface asserts they are
// byte-identical -- but they cannot read the same FILE, because the two deploy
// pipelines exclude each other's tree: `.railwayignore` drops `mobile-native/`
// from the backend upload ("not runtime inputs"), and `.easignore` drops
// `static/` from the native build. Pointing either platform at the other's
// path produces an asset that is present in the repo and absent in the
// artifact that actually ships. So the duplication is deliberate, written by
// one command, and held byte-identical by a test rather than by discipline.
const OUTPUTS = [
  path.join(REPO, "mobile-native", "src", "emoji", "data", "emoji.json"),
  path.join(REPO, "static", "emoji", "emoji.json")
];
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

// The version stamp is derived from the dataset, not from the clock. A
// `new Date()` stamp meant every run rewrote both artifacts even when not one
// emoji had changed: the protected native file churned on each regeneration,
// and the byte-identity invariant between the two copies silently depended on
// them being generated in the same run -- regenerate one alone and the diff
// looks meaningful when only the date moved. A content digest makes the output
// reproducible, so `git status` stays clean unless upstream really changed,
// and it answers the question the stamp is actually asked: is this the same
// dataset the other platform has?
const digest = createHash("sha256").update(JSON.stringify(emojis)).digest("hex").slice(0, 12);
const artifact = {
  version: `emojibase-data en (RGI), ${emojis.length} emoji, sha256:${digest}`,
  count: emojis.length,
  emojis
};

const serialized = JSON.stringify(artifact);
for (const out of OUTPUTS) {
  fs.mkdirSync(path.dirname(out), { recursive: true });
  fs.writeFileSync(out, serialized);
  console.log(`wrote ${out}: ${emojis.length} emoji, ${fs.statSync(out).size} bytes`);
}
