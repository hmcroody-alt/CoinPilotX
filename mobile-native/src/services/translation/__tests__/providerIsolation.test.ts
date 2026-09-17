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

function importersOf(pattern: string) {
  let output = "";
  try {
    output = execFileSync(
      "grep",
      ["-rlE", pattern, ".", "--include=*.ts", "--include=*.tsx"],
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
      "services/translation/AppleTranslationHost.tsx",
      "services/translation/index.ts",
      "services/translation/languages.ts",
      "services/translation/providers/apple.ts",
      "services/translation/types.ts"
    ]);
  });

  it("mounts the Apple host in exactly one place", () => {
    // The native coordinator multiplexes every language pair behind one host,
    // so a second mount is not a redundancy — it is a second session owner for
    // the same pairs, which is Stage 3's "one session per feed cell" mistake
    // arriving through the front door. The component takes no props precisely
    // so that there is nothing a screen could usefully pass it.
    // `<AppleTranslationHost` alone would also match `<AppleTranslationHostView`
    // — the native view, rendered by the component itself — and the test would
    // then be asserting that the wrapper does not use the thing it wraps.
    expect(importersOf("<AppleTranslationHost ?/>")).toEqual([
      "components/TranslationPreferencesBootstrap.tsx"
    ]);
  });

  it("names every remaining caller of the billable client", () => {
    // `api/translation.ts` defines it and `providers/cloud.ts` is the one
    // sanctioned consumer, reached only after the router has decided the
    // request may cost money. `services/translation/index.ts` names it in the
    // comment that explains this rule. No screen appears here, which is the
    // whole of Stage 1 stated as a list.
    //
    // A mention in a comment counts, and should: this is a text scan, so the
    // only way it stays trustworthy is if naming the symbol anywhere is a
    // deliberate act. `ContentTranslation.tsx` describes what it no longer
    // does without naming it, for exactly this reason.
    expect(importersOf("translatePulseContent")).toEqual([
      "api/translation.ts",
      "services/translation/index.ts",
      "services/translation/providers/cloud.ts"
    ]);
  });
});
