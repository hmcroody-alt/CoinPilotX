import { CATALOG_NAMESPACES, CatalogBundle, CatalogNamespace, loadCatalogBundle } from "./catalogs";
import { DEFAULT_LOCALE, SUPPORTED_LOCALE_CODES, normalizeTag } from "./locales";

/**
 * Translation coverage measurement.
 *
 * Two audiences, one computation:
 *
 *   - Users see it in the language picker, so a language that is only partly
 *     translated says so up front instead of surprising them with English text
 *     three screens later.
 *   - Whoever adds a language sees it in the CI validator, where a coverage drop
 *     is the signal that a key was added to English and nowhere else.
 *
 * The comparison is against the default locale's key set, because that catalog
 * is the source of truth for what a complete translation contains.
 */

/** Plural suffixes are a grammar artifact, not separate strings to translate. */
const PLURAL_SUFFIX = /_(zero|one|two|few|many|other)$/;

/** Metadata keys (`$version`, `$locale`, `$tier`) are not translatable content. */
const META_PREFIX = "$";

export interface CoverageReport {
  locale: string;
  /** Key families present in this locale. */
  translated: number;
  /** Key families the default locale defines. */
  total: number;
  /** 0–100, rounded down so a single missing key never reads as "100%". */
  percent: number;
  /** Families the default locale has and this one does not. */
  missing: string[];
  /** Families this locale defines that the default locale no longer has. */
  orphaned: string[];
}

/**
 * Flattens a bundle to the set of *key families* it defines.
 *
 * `likes_one` and `likes_other` collapse to one family `likes`, so Arabic —
 * which legitimately needs six plural forms where English needs two — is not
 * scored as 300% translated, and a language that supplies only `_other` is not
 * penalised for a form its grammar does not have.
 */
function keyFamilies(bundle: CatalogBundle | null, prefix = "", out = new Set<string>()): Set<string> {
  if (!bundle) return out;
  for (const [key, value] of Object.entries(bundle)) {
    if (key.startsWith(META_PREFIX)) continue;
    const path = prefix ? `${prefix}.${key}` : key;
    if (typeof value === "string") {
      out.add(path.replace(PLURAL_SUFFIX, ""));
    } else if (value && typeof value === "object") {
      keyFamilies(value as CatalogBundle, path, out);
    }
  }
  return out;
}

function familiesForLocale(locale: string): Set<string> {
  const families = new Set<string>();
  for (const namespace of CATALOG_NAMESPACES) {
    const bundle = loadCatalogBundle(locale, namespace as CatalogNamespace);
    if (!bundle) continue;
    for (const family of keyFamilies(bundle, namespace)) families.add(family);
  }
  return families;
}

/** Reports are stable for the life of a build, so each locale is measured once. */
const reportCache = new Map<string, CoverageReport>();

/**
 * Measures one language against the default catalog.
 *
 * This parses that language's catalog files, so it is called lazily — when the
 * picker opens or the validator runs — never during launch.
 */
export function getCoverage(locale: string): CoverageReport {
  const normalized = normalizeTag(locale);
  const cached = reportCache.get(normalized);
  if (cached) return cached;

  const reference = familiesForLocale(DEFAULT_LOCALE);
  const actual = normalized === DEFAULT_LOCALE ? reference : familiesForLocale(normalized);

  const missing: string[] = [];
  for (const family of reference) {
    if (!actual.has(family)) missing.push(family);
  }
  const orphaned: string[] = [];
  for (const family of actual) {
    if (!reference.has(family)) orphaned.push(family);
  }

  const total = reference.size;
  const translated = total - missing.length;
  const report: CoverageReport = {
    locale: normalized,
    translated,
    total,
    // Floor, not round: 99.6% must not display as "100% translated" when the
    // whole point of the number is to warn that something is missing.
    percent: total === 0 ? 100 : Math.floor((translated / total) * 100),
    missing: missing.sort(),
    orphaned: orphaned.sort()
  };
  reportCache.set(normalized, report);
  return report;
}

/** True when the language covers every key the default catalog defines. */
export function isFullyTranslated(locale: string): boolean {
  return getCoverage(locale).percent >= 100;
}

/** Coverage for every shipped language — used by the validator and dev panel. */
export function getAllCoverage(): CoverageReport[] {
  return SUPPORTED_LOCALE_CODES.map((code) => getCoverage(code));
}

/** Test seam: forces the next `getCoverage` call to re-measure. */
export function resetCoverageCache(): void {
  reportCache.clear();
}
