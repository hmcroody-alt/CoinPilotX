/**
 * Regression guard (Stage 22): one emoji engine, forever.
 *
 * Fails if a second emoji picker component or a new hardcoded emoji dataset
 * appears outside src/emoji. Legacy fixed reaction sets that predate the
 * foundation are baselined below — do NOT add to the baseline; import from
 * src/emoji instead.
 */
import * as fs from "fs";
import * as path from "path";

const SRC = path.resolve(__dirname, "../..");

/** Pre-existing fixed reaction sets (small, name-keyed, display-only). */
const LEGACY_EMOJI_ARRAY_BASELINE = new Set([
  "components/PostCard.tsx",
  "components/ConversationControlCenter.tsx",
  "components/ReelPlayerCard.tsx",
  "screens/ReelsScreen.tsx",
  "screens/ChatScreen.tsx",
  "pulseCommand/domain.ts",
  "live/LiveReactionLayer.tsx",
  "screens/LiveHostSessionScreen.tsx",
  "screens/LiveViewerScreen.tsx",
  "api/status.ts"
]);

const EMOJI_LITERAL = /[\u{1F300}-\u{1FAFF}]/gu;

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name === "__tests__" || entry.name === "node_modules") continue;
      walk(full, out);
    } else if (/\.(ts|tsx)$/.test(entry.name)) {
      out.push(full);
    }
  }
  return out;
}

const files = walk(SRC).filter((f) => !path.relative(SRC, f).startsWith("emoji" + path.sep));

describe("emoji engine regression guard", () => {
  it("no second EmojiPicker implementation exists outside src/emoji", () => {
    const offenders = files.filter((file) => {
      const source = fs.readFileSync(file, "utf8");
      // Definitions, not imports of the canonical one.
      return /(?:function|const|class)\s+EmojiPicker\b/.test(source);
    });
    expect(offenders.map((f) => path.relative(SRC, f))).toEqual([]);
  });

  it("no new hardcoded emoji dataset appears outside src/emoji", () => {
    const offenders: string[] = [];
    for (const file of files) {
      const rel = path.relative(SRC, file);
      if (LEGACY_EMOJI_ARRAY_BASELINE.has(rel)) continue;
      const source = fs.readFileSync(file, "utf8");
      const count = (source.match(EMOJI_LITERAL) || []).length;
      // A screen may use a few emoji as UI glyphs; 20+ distinct literals in
      // one file means someone is rebuilding an emoji dataset.
      if (count >= 20) offenders.push(`${rel} (${count} emoji literals)`);
    }
    expect(offenders).toEqual([]);
  });

  it("everything importing emoji functionality goes through src/emoji/index", () => {
    const offenders = files.filter((file) => {
      const source = fs.readFileSync(file, "utf8");
      return /from\s+["'][^"']*emoji\/(EmojiPicker|emojiData|recents|types)["']/.test(source);
    });
    // grapheme.ts is a pure utility and may be deep-imported.
    expect(offenders.map((f) => path.relative(SRC, f))).toEqual([]);
  });
});
