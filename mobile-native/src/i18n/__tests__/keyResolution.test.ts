/**
 * The third way a key renders as plausible English, and the only one nothing
 * else in the repo can see.
 *
 *   1. Unregistered prefix — `namespaceRegistration.test.ts` owns it.
 *   2. Locale drift — `npm run i18n:validate` owns it.
 *   3. **A registered namespace with an absent key.** The prefix resolves, the
 *      path misses, and `translate()` humanizes the last segment. `i18n:validate`
 *      reports 100% because the key is missing from all eleven locales equally,
 *      so parity is perfect. `namespaceRegistration.test.ts` is happy because the
 *      prefix resolved. Nothing fails.
 *
 * That is how `commerce:marketplace.status.*` shipped: ten seller-inventory
 * pills drew title-cased key segments ("Out Of Stock", "Store Name Needed") in
 * every language for sixteen days, with both gates green the whole time.
 *
 * The only signal was the engine's `__DEV__` warning, which is a log line, not a
 * gate — and until `jest.setup.js` warmed English it was drowned in 515 warnings
 * per run from suites that simply had no catalogs loaded.
 *
 * What the sweep below cannot see: a key that is not a literal inside `t(...)`.
 * An interpolated path — which is what the status pills used — and a key held in
 * a lookup table both reach `t()` as a variable. Each such site needs its own
 * assertion; the seller-store pills get theirs in the second block.
 */

import fs from "fs";
import path from "path";

const SRC_ROOT = path.resolve(__dirname, "../..");
const CATALOG_ROOT = path.resolve(__dirname, "../catalogs");

const LOCALES = fs
  .readdirSync(CATALOG_ROOT, { withFileTypes: true })
  .filter((entry) => entry.isDirectory())
  .map((entry) => entry.name)
  .sort();

function catalog(locale: string): Record<string, unknown> {
  const merged: Record<string, unknown> = {};
  for (const tier of ["core", "extended"]) {
    Object.assign(merged, JSON.parse(fs.readFileSync(path.join(CATALOG_ROOT, locale, `${tier}.json`), "utf8")));
  }
  return merged;
}

const EN = catalog("en");

/**
 * Mirrors `resolveVariants` in `engine.ts`: a call passing `count` or `context`
 * is served by `path_one` / `path_female_other` and the bare path may not exist
 * at all. Treating those as absent reported 47 false positives on a green tree.
 */
function resolves(key: string, root: Record<string, unknown> = EN): boolean {
  const separator = key.indexOf(":");
  if (separator < 0) return false;
  const segments = key.slice(separator + 1).split(".");
  let node: unknown = root[key.slice(0, separator)];
  for (const segment of segments.slice(0, -1)) {
    if (!node || typeof node !== "object") return false;
    node = (node as Record<string, unknown>)[segment];
  }
  if (!node || typeof node !== "object") return false;
  const parent = node as Record<string, unknown>;
  const leaf = segments[segments.length - 1];
  if (typeof parent[leaf] === "string") return true;
  return Object.keys(parent).some((sibling) => sibling.startsWith(`${leaf}_`) && typeof parent[sibling] === "string");
}

function sourceFiles(dir: string, found: string[] = []): string[] {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (entry.isDirectory()) {
      if (entry.name !== "__tests__" && full !== CATALOG_ROOT) sourceFiles(full, found);
    } else if (/\.tsx?$/.test(entry.name)) {
      found.push(full);
    }
  }
  return found;
}

interface Call {
  key: string;
  file: string;
  hasDefaultValue: boolean;
}

/**
 * The leading `\b` plus the required `"` keeps `format(`, `print(` and any
 * identifier ending in `t` out of the results.
 */
function staticCalls(): Call[] {
  const calls: Call[] = [];
  for (const file of sourceFiles(SRC_ROOT)) {
    const source = fs.readFileSync(file, "utf8");
    const pattern = /\bt\(\s*"([a-zA-Z]+:[A-Za-z0-9_.]+)"\s*(,[^)]*)?\)/g;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(source)) !== null) {
      calls.push({
        key: match[1],
        file: path.relative(SRC_ROOT, file),
        hasDefaultValue: /defaultValue/.test(match[2] ?? ""),
      });
    }
  }
  return calls;
}

/**
 * `src/live/` writes the tier filename where a namespace belongs, so all twelve
 * of its keys are unresolvable. Skipped by prefix rather than listed key by key,
 * because `namespaceRegistration.test.ts` already owns that debt and explains
 * why it is not ours to fix: the surrounding files sit in
 * `config/realtime-audio-protected-paths.json`.
 */
const PREFIXES_OWNED_ELSEWHERE = ["extended"];

/**
 * `defaultValue` is the quiet way to ship English into eleven languages: the
 * screen renders real words, so nothing looks broken, and the string never
 * enters a catalog where a translator would see it. Pinned rather than merely
 * skipped, so a third one has to be argued for.
 */
const DEFAULT_VALUE_ONLY = ["premium:plans.missingAnnual", "premium:plans.missingMonthly"];

describe("every static translation key resolves", () => {
  const calls = staticCalls();

  it("finds the call sites at all", () => {
    // Without this the regex could stop matching and every assertion below
    // would pass over an empty list.
    expect(calls.length).toBeGreaterThan(2000);
  });

  it("has no key whose namespace resolves but whose path is absent", () => {
    const unresolved = calls
      .filter((call) => !PREFIXES_OWNED_ELSEWHERE.includes(call.key.split(":")[0]))
      .filter((call) => !call.hasDefaultValue)
      .filter((call) => !resolves(call.key));

    expect(unresolved.map((call) => `${call.key} (${call.file})`).sort()).toEqual([]);
  });

  it("pins the keys that exist only as a defaultValue", () => {
    const withDefault = calls.filter((call) => call.hasDefaultValue && !resolves(call.key));
    expect([...new Set(withDefault.map((call) => call.key))].sort()).toEqual(DEFAULT_VALUE_ONLY);
  });

  it("keeps the exempted prefix genuinely exempt", () => {
    // A stale exception is a silent widening: once `src/live/` is migrated this
    // list must shrink, or the next unresolvable `extended:` key inherits a
    // waiver nobody granted.
    const stillBroken = calls.filter(
      (call) => PREFIXES_OWNED_ELSEWHERE.includes(call.key.split(":")[0]) && !resolves(call.key)
    );
    expect(stillBroken.length).toBeGreaterThan(0);
  });
});

/**
 * Keys held in a lookup table, which the sweep above is also blind to — it only
 * sees a literal inside `t(...)`, and these reach `t()` as a variable.
 *
 * `SellerStoreScreen` maps a listing's publication state onto one of ten pills.
 * All ten shipped with no catalog copy for sixteen days because the table used
 * to be a single interpolated template. Read from the screen's own table rather
 * than a copy of it, so a pill added there without copy fails here.
 */
describe("seller store status pills", () => {
  const screen = fs.readFileSync(path.join(SRC_ROOT, "screens/SellerStoreScreen.tsx"), "utf8");

  const labelKeys = (() => {
    const block = /const STATUS_LABEL_KEYS: Record<string, string> = \{([\s\S]*?)\n\};/.exec(screen);
    if (!block) throw new Error("STATUS_LABEL_KEYS is no longer parseable — update this test");
    const entries = [...block[1].matchAll(/^\s*([a-z_]+): "([^"]+)",$/gm)].map((m) => [m[1], m[2]] as const);
    return new Map(entries);
  })();

  it("names every pill with a literal key", () => {
    expect(labelKeys.size).toBe(10);
    // Reverting to the interpolated form would drop entries out of the parse
    // above rather than fail here, so the count is the real guard; this states
    // what a well-formed entry is.
    for (const [pill, key] of labelKeys) {
      expect({ pill, literal: /^[a-z]+:[A-Za-z0-9_.]+$/.test(key) }).toEqual({ pill, literal: true });
    }
  });

  it("resolves every pill in every locale", () => {
    for (const locale of LOCALES) {
      const root = catalog(locale);
      for (const [pill, key] of labelKeys) {
        expect({ locale, pill, resolved: resolves(key, root) }).toEqual({ locale, pill, resolved: true });
      }
    }
  });
});
