import { cachedAgeSuffix, withCachedAge } from "../ageLabel";

describe("cachedAgeSuffix", () => {
  it("renders each unit", () => {
    expect(cachedAgeSuffix(0)).toBe(" · just now");
    expect(cachedAgeSuffix(12 * 60_000)).toBe(" · 12m ago");
    expect(cachedAgeSuffix(3 * 3_600_000)).toBe(" · 3h ago");
    expect(cachedAgeSuffix(50 * 3_600_000)).toBe(" · 2d ago");
  });

  it("says nothing when the age is unknown", () => {
    // The §91-adjacent lie: an entry written before cache entries carried
    // timestamps has no age, and "just now" over content that may be weeks old
    // is the app asserting a freshness it never observed.
    expect(cachedAgeSuffix(null)).toBe("");
    expect(withCachedAge("Showing saved profile", null)).toBe("Showing saved profile");
  });

  it("appends to the caller's sentence without punctuating it", () => {
    expect(withCachedAge("Showing saved Status", 5 * 60_000)).toBe("Showing saved Status · 5m ago");
  });

  it("is the only place the surfaces format an age", () => {
    // Three screens had begun growing near-identical formatters, which is how
    // "12m ago" and "12 min ago" end up on adjacent screens. This pins the
    // consolidation rather than trusting it to survive the next edit.
    const fs = require("fs");
    const path = require("path");
    const screens = path.join(__dirname, "..", "..", "..", "screens");
    const offenders: string[] = [];
    for (const file of fs.readdirSync(screens)) {
      if (!file.endsWith(".tsx")) continue;
      const source = fs.readFileSync(path.join(screens, file), "utf8");
      if (source.includes("describeAge(")) offenders.push(file);
    }
    expect(offenders).toEqual([]);
  });
});
