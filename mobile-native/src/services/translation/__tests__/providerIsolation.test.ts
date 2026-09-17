/**
 * Stage 1's structural requirement: no screen may call a translation provider
 * directly. Every other test in this directory checks what the router *decides*;
 * this one checks that the router is the only thing in a position to decide,
 * because a screen that imports a provider itself bypasses the privacy rule,
 * the budget, the breaker and the cache in one line — and would do so while
 * every behavioural test above stayed green.
 *
 * The assertions are exact-set rather than subset. A new importer fails, which
 * is the obvious direction; but an importer that *disappears* fails too, which
 * matters more than it looks: it means the list in this file is a claim about
 * the current seam, and the commit that removes the last screen-level call to
 * the cloud has to come here and say so rather than leaving a stale entry that
 * reads as "still migrating".
 *
 * `__tests__` files are excluded. A test importing a provider is mocking it,
 * which is the opposite of calling it.
 */

import { execFileSync } from "child_process";
import path from "path";

const SRC_ROOT = path.resolve(__dirname, "../../..");

function importersOf(symbol: string) {
  let output = "";
  try {
    output = execFileSync(
      "grep",
      ["-rl", symbol, ".", "--include=*.ts", "--include=*.tsx"],
      { cwd: SRC_ROOT, encoding: "utf8" }
    );
  } catch (error) {
    // grep exits 1 for "no matches", which is a legitimate empty result rather
    // than a failure to look.
    const status = (error as { status?: number }).status;
    if (status !== 1) throw error;
  }
  return output
    .split("\n")
    .map(line => line.replace(/^\.\//, "").trim())
    .filter(Boolean)
    .filter(file => !file.includes("__tests__"))
    .sort();
}

describe("no screen reaches a provider directly", () => {
  it("keeps the native Apple module inside the translation service", () => {
    // Zero exceptions here, and there never needs to be one: the native module
    // is stateful per mounted host, so a second importer would also be a second
    // session lifecycle owner (Stage 2).
    expect(importersOf("pulse-apple-translation")).toEqual([
      "services/translation/index.ts",
      "services/translation/languages.ts",
      "services/translation/providers/apple.ts",
      "services/translation/types.ts"
    ]);
  });

  it("names every remaining caller of the billable client", () => {
    // `api/translation.ts` defines it; `providers/cloud.ts` is the one
    // sanctioned consumer. `components/ContentTranslation.tsx` is the
    // pre-migration seam and is expected to drop off this list when the UI is
    // moved onto `translateText`.
    expect(importersOf("translatePulseContent")).toEqual([
      "api/translation.ts",
      "components/ContentTranslation.tsx",
      "services/translation/index.ts",
      "services/translation/providers/cloud.ts"
    ]);
  });
});
