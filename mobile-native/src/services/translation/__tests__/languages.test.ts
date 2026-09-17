import { isSameLanguage, modelScope, normalizeLanguage } from "../languages";

/**
 * These cases are shared with `LanguageNormalizer` in
 * `modules/pulse-apple-translation/ios/AppleTranslationModels.swift`. Both
 * implementations must produce the same tag for the same input, because the
 * router builds cache keys from this one and Apple builds sessions from that
 * one. `AppleTranslationModelsTests.swift` pins the same table.
 */
const SHARED_CASES: Array<[string | null | undefined, string | "automatic" | "invalid"]> = [
  [null, "automatic"],
  [undefined, "automatic"],
  ["", "automatic"],
  ["auto", "automatic"],
  ["AUTO", "automatic"],
  ["und", "automatic"],
  ["zxx", "automatic"],
  ["mul", "automatic"],
  ["fr", "fr"],
  ["FR", "fr"],
  ["  fr  ", "fr"],
  ["en_US", "en-US"],
  ["en-us", "en-US"],
  ["pt-br", "pt-BR"],
  ["zh-CN", "zh-Hans-CN"],
  ["zh-TW", "zh-Hant-TW"],
  ["zh-HK", "zh-Hant-HK"],
  ["zh-Hans", "zh-Hans"],
  ["zh_hant_tw", "zh-Hant-TW"],
  ["iw", "he"],
  ["in", "id"],
  ["tl", "fil"],
  ["no", "nb"],
  ["ht", "ht"],
  ["HT", "ht"],
  ["sr-Latn-RS", "sr-Latn-RS"],
  ["es-419", "es-419"],
  ["1", "invalid"],
  ["e", "invalid"],
  ["f9", "invalid"],
  ["toolongsubtag", "invalid"]
];

describe("normalizeLanguage matches the Swift normalizer", () => {
  it.each(SHARED_CASES)("normalizes %p", (input, expected) => {
    const outcome = normalizeLanguage(input);
    if (expected === "automatic") {
      expect(outcome).toEqual({ kind: "automatic" });
    } else if (expected === "invalid") {
      expect(outcome).toEqual({ kind: "invalid" });
    } else {
      expect(outcome).toEqual({ kind: "language", tag: expected });
    }
  });

  it("keeps the region on Chinese so the more specific model can be chosen", () => {
    // Apple accepts zh-Hant-TW, and dropping the region would ask for a less
    // specific pair than the caller stated.
    expect(normalizeLanguage("zh-TW")).toEqual({ kind: "language", tag: "zh-Hant-TW" });
  });
});

describe("modelScope", () => {
  it("drops the region so pt-BR and pt-PT share one model", () => {
    expect(modelScope("pt-BR")).toBe("pt");
    expect(modelScope("pt-PT")).toBe("pt");
  });

  it("keeps the script, which does select a different model", () => {
    expect(modelScope("zh-Hans-CN")).toBe("zh-Hans");
    expect(modelScope("zh-Hant-TW")).toBe("zh-Hant");
  });

  it("is identity for a bare primary subtag", () => {
    expect(modelScope("ht")).toBe("ht");
  });
});

describe("isSameLanguage", () => {
  it("treats two regions of one language as the same, because Apple does", () => {
    expect(isSameLanguage("pt-BR", "pt-PT")).toBe(true);
    expect(isSameLanguage("en-US", "en-GB")).toBe(true);
  });

  it("treats two Chinese scripts as different, because Apple does", () => {
    expect(isSameLanguage("zh-Hans", "zh-Hant")).toBe(false);
  });

  it("does not confuse Haitian Creole with French", () => {
    expect(isSameLanguage("ht", "fr")).toBe(false);
  });
});
