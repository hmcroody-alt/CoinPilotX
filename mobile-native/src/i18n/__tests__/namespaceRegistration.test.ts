/**
 * `t("foo:bar")` resolves `foo` against `CATALOG_NAMESPACES`, and an unknown
 * prefix is not an error — `parseKey` falls back to `common` and carries the
 * whole string along as the key path, which then misses and gets humanized.
 * So `t("translation:a11y.translateTo")` on an unregistered namespace renders
 * "Translate To": title case, plausible English, survives a screenshot and a
 * code review, and is wrong in all eleven languages at once.
 *
 * `i18n:validate` cannot see this. It compares each locale against English and
 * is happy as long as all eleven agree — which they do, because the block is
 * present everywhere and reachable nowhere.
 *
 * Two halves of the same seam:
 *   - every block shipped in a tier file is reachable (strings that exist but
 *     no `t()` call can ever reach)
 *   - every prefix used in source is registered (calls that can never resolve)
 */

import fs from "fs";
import path from "path";
import { execFileSync } from "child_process";
import { CATALOG_NAMESPACES, NAMESPACE_TIER, CatalogTier } from "../catalogs";

const SRC_ROOT = path.resolve(__dirname, "../..");
const CATALOG_ROOT = path.resolve(__dirname, "../catalogs");

/**
 * One defect, seen from both ends.
 *
 * `src/live/` writes `t("extended:live.moderation.title")` — the *tier* file's
 * name where a namespace belongs — so `live` ships strings nothing can reach
 * and `extended` is a prefix that resolves to nothing. Every label in the
 * livestream moderation sheet and stage renders as a humanized key on device.
 *
 * It is real and it is not this file's to fix: `liveMediaOwnership.ts` and
 * `liveSessionLifecycle.ts` are listed in
 * `config/realtime-audio-protected-paths.json`, and the rest is livestream UI.
 * Naming it here rather than widening the assertions keeps it a debt with an
 * owner instead of a silence.
 */
const UNREACHABLE_BLOCKS_BY_EXCEPTION = ["live"];
const UNRESOLVABLE_PREFIXES_BY_EXCEPTION = ["extended"];

function topLevelBlocks(locale: string, tier: CatalogTier): string[] {
  const raw = fs.readFileSync(path.join(CATALOG_ROOT, locale, `${tier}.json`), "utf8");
  return Object.keys(JSON.parse(raw))
    .filter((key) => !key.startsWith("$"))
    .sort();
}

/**
 * Every `t("<prefix>:...")` outside `__tests__`. The leading character class
 * keeps `format(` and `print(` out; without it any identifier ending in `t`
 * matches and the residue fills with words that were never keys.
 */
function prefixesUsedInSource(): string[] {
  let output = "";
  try {
    output = execFileSync(
      "grep",
      [
        "-rhoE",
        "--include=*.ts",
        "--include=*.tsx",
        "--exclude-dir=__tests__",
        '[^a-zA-Z0-9_$.]t\\(\\s*"[a-z]+:',
        "."
      ],
      { cwd: SRC_ROOT, encoding: "utf8" }
    );
  } catch (error) {
    if ((error as { status?: number }).status !== 1) throw error;
  }
  const prefixes = new Set<string>();
  for (const line of output.split("\n")) {
    const match = /"([a-z]+):$/.exec(line.trim());
    if (match) prefixes.add(match[1]);
  }
  return [...prefixes].sort();
}

describe("catalog namespaces", () => {
  it("ships no block that no lookup can reach", () => {
    // English is the source of truth; `i18n:validate` already proves the other
    // ten match it block for block.
    const registered = new Set<string>(CATALOG_NAMESPACES);
    const unreachable: string[] = [];
    for (const tier of ["core", "extended"] as const) {
      for (const block of topLevelBlocks("en", tier)) {
        if (!registered.has(block)) unreachable.push(block);
      }
    }
    expect(unreachable).toEqual(UNREACHABLE_BLOCKS_BY_EXCEPTION);
  });

  it("puts every registered namespace in the tier file it claims", () => {
    for (const namespace of CATALOG_NAMESPACES) {
      const tier = NAMESPACE_TIER[namespace];
      expect(tier).toBeDefined();
      expect(topLevelBlocks("en", tier)).toContain(namespace);
    }
  });

  it("uses no namespace prefix that resolves to nothing", () => {
    const registered = new Set<string>(CATALOG_NAMESPACES);
    const unresolvable = prefixesUsedInSource().filter((prefix) => !registered.has(prefix));
    expect(unresolvable).toEqual(UNRESOLVABLE_PREFIXES_BY_EXCEPTION);
  });

  it("does not accept a tier name where a namespace belongs", () => {
    // The specific mistake `src/live/` made and this migration nearly repeated:
    // `extended` is a file, not a namespace, and the two are one word apart.
    const registered = new Set<string>(CATALOG_NAMESPACES);
    for (const tier of ["core", "extended"] as const) {
      expect(registered.has(tier)).toBe(false);
    }
  });

  it("resolves the namespace it was told to, not the default", () => {
    // The positive control. Without it the three assertions above would still
    // pass if `parseKey` stopped splitting on ":" altogether, which is the
    // exact mechanism that hid the defect in the first place.
    const { parseKey } = require("../engine");
    expect(parseKey("translation:a11y.translateTo")).toEqual({
      namespace: "translation",
      path: "a11y.translateTo"
    });
    expect(parseKey("live:moderation.title")).toEqual({
      namespace: "common",
      path: "live:moderation.title"
    });
  });
});
