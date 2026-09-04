/**
 * Native Unicode emoji foundation — Stage 20 test pack.
 *
 * Covers the mission's non-negotiables: grapheme safety (never naive
 * string.length), dataset integrity (local, categorized, keyworded),
 * search ranking, recents (bounded/deduped/most-recent-first), skin-tone
 * persistence, and reaction display mapping.
 */
import {
  countEmojiClusters,
  isSingleEmoji,
  splitEmojiClusters,
  stripSkinTone
} from "../grapheme";
import {
  allEmoji,
  emojiA11yLabel,
  emojiByCategory,
  findEmoji,
  searchEmoji
} from "../emojiData";
import {
  __resetEmojiStoreForTests,
  getRecentEmoji,
  getSkinTonePreference,
  recordRecentEmoji,
  setSkinTonePreference
} from "../recents";
import { EMOJI_CATEGORIES, QUICK_REACTIONS } from "../types";
import { optimisticReaction, reactionIcon } from "../../pulseCommand/domain";

const FAMILY = "👨‍👩‍👧‍👦";
const DARK_THUMB = "👍🏿";
const HAITI = "🇭🇹";

describe("grapheme safety (Stage 14)", () => {
  it("counts multi-codepoint emoji as single clusters, not string.length", () => {
    expect(FAMILY.length).toBeGreaterThan(1); // the naive trap
    expect(countEmojiClusters(FAMILY)).toBe(1);
    expect(countEmojiClusters(DARK_THUMB)).toBe(1);
    expect(countEmojiClusters(HAITI)).toBe(1);
    expect(countEmojiClusters("❤️")).toBe(1);
    expect(countEmojiClusters("😂❤️👍🏿")).toBe(3);
  });

  it("splits mixed sequences without corrupting clusters", () => {
    expect(splitEmojiClusters(`😂${FAMILY}${HAITI}`)).toEqual(["😂", FAMILY, HAITI]);
  });

  it("isSingleEmoji accepts every required emoji and rejects text", () => {
    for (const emoji of ["😂", "❤️", FAMILY, DARK_THUMB, HAITI]) {
      expect(isSingleEmoji(emoji)).toBe(true);
    }
    expect(isSingleEmoji("hello")).toBe(false);
    expect(isSingleEmoji("😂😂")).toBe(false);
    expect(isSingleEmoji("")).toBe(false);
  });

  it("stripSkinTone reduces toned emoji to the base", () => {
    expect(stripSkinTone(DARK_THUMB)).toBe("👍");
    expect(stripSkinTone("👍")).toBe("👍");
  });
});

describe("local dataset integrity (Stages 1-2)", () => {
  it("ships a local checked-in dataset with no remote dependency", () => {
    expect(allEmoji().length).toBeGreaterThan(1500);
  });

  it("covers every canonical category (RECENT is synthetic)", () => {
    for (const category of EMOJI_CATEGORIES) {
      if (category === "RECENT") continue;
      expect(emojiByCategory(category).length).toBeGreaterThan(10);
    }
  });

  it("finds required emoji with skin-tone variants and metadata", () => {
    const joy = findEmoji("😂");
    expect(joy?.name).toBe("face with tears of joy");
    expect(joy?.keywords.length).toBeGreaterThan(0);
    const thumb = findEmoji("👍");
    expect(thumb?.skin_tone_capable).toBe(true);
    expect(thumb?.variants?.some((v) => v.emoji === DARK_THUMB)).toBe(true);
    expect(findEmoji(HAITI)?.name).toContain("Haiti");
    expect(findEmoji(FAMILY)).toBeTruthy();
  });

  it("resolves toned and VS16-stripped lookups to their entries", () => {
    expect(findEmoji(DARK_THUMB)).toBeTruthy();
    expect(findEmoji("❤️")).toBeTruthy();
  });

  it("provides a11y labels (Stage 19)", () => {
    expect(emojiA11yLabel("😂")).toBe("face with tears of joy");
    expect(emojiA11yLabel(DARK_THUMB).toLowerCase()).toContain("dark skin tone");
  });
});

describe("search (Stage 3)", () => {
  it("ranks exact name match first", () => {
    expect(searchEmoji("fire")[0]?.emoji).toBe("🔥");
  });

  it("matches by keyword", () => {
    const results = searchEmoji("funny");
    expect(results.some((e) => e.emoji === "😂")).toBe(true);
  });

  it("returns nothing for garbage and respects the limit", () => {
    expect(searchEmoji("zzzzqqqq")).toEqual([]);
    expect(searchEmoji("a", 10).length).toBeLessThanOrEqual(10);
  });
});

describe("recents + skin tone persistence (Stages 5-6)", () => {
  beforeEach(() => __resetEmojiStoreForTests());

  it("dedupes, orders most-recent-first, and bounds the list", async () => {
    await recordRecentEmoji("😂");
    await recordRecentEmoji("❤️");
    await recordRecentEmoji("😂");
    const recents = await getRecentEmoji();
    expect(recents.slice(0, 2)).toEqual(["😂", "❤️"]);
    for (let i = 0; i < 60; i += 1) {
      // eslint-disable-next-line no-await-in-loop
      await recordRecentEmoji(String.fromCodePoint(0x1f600 + i));
    }
    expect((await getRecentEmoji()).length).toBeLessThanOrEqual(40);
  });

  it("persists the skin tone preference", async () => {
    expect(await getSkinTonePreference()).toBe(0);
    await setSkinTonePreference(5);
    __resetEmojiStoreForTests();
    expect(await getSkinTonePreference()).toBe(5);
  });
});

describe("reactions (Stages 8, 10)", () => {
  it("quick bar is the mission's canonical six", () => {
    expect(QUICK_REACTIONS).toEqual(["❤️", "😂", "😮", "😢", "😡", "👍"]);
  });

  it("reactionIcon shows raw Unicode as-is and maps legacy names display-only", () => {
    expect(reactionIcon("😂")).toBe("😂");
    expect(reactionIcon(DARK_THUMB)).toBe(DARK_THUMB);
    expect(reactionIcon(FAMILY)).toBe(FAMILY);
    expect(reactionIcon("pulse")).toBe("❤️");
    expect(reactionIcon("fire")).toBe("🔥");
  });

  it("optimisticReaction keeps counts accurate when switching reactions", () => {
    const next = optimisticReaction({ "😂": 2 }, "❤️", "😂");
    expect(next["❤️"]).toBe(1);
    expect(next["😂"]).toBe(1);
    const cleared = optimisticReaction({ "😂": 1 }, "❤️", "😂");
    expect(cleared["😂"]).toBeUndefined();
  });
});
