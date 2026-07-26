import {
  DEFAULT_LOCALE,
  isSupportedLocale,
  normalizeTag,
  toIntlLocale
} from "./locales";
import {
  CATALOG_NAMESPACES,
  CatalogBundle,
  CatalogNamespace,
  CatalogValue,
  loadCatalogBundle
} from "./catalogs";

/**
 * PulseSoc Native translation engine.
 *
 * A dependency-free ICU-subset implementation: namespace-scoped catalogs, lazy
 * bundle loading, `{{placeholder}}` interpolation, `Intl.PluralRules`-driven
 * plural selection, gender/context variants, and a deterministic fallback chain
 * that guarantees a lookup never renders a raw key to a user when the default
 * catalog has the string.
 *
 * The engine is deliberately synchronous at call time: `t()` reads from an
 * in-memory cache that the provider warms before it renders children. Screens
 * therefore never deal with loading states for text.
 */

/** A leaf value in a catalog, or a nested group of them. Owned by the catalog
 *  registry and re-exported here so consumers only import from the engine. */
export type { CatalogBundle, CatalogValue };

export interface TranslateOptions {
  /** Interpolated into `{{name}}` placeholders. Numbers are locale-formatted. */
  [param: string]: unknown;
  /** Selects a plural form via `Intl.PluralRules`. */
  count?: number;
  /** Selects a gender/context variant, e.g. `key_female`. */
  context?: string;
  /** Returned verbatim when the key is missing from every catalog. */
  defaultValue?: string;
  /** Overrides the active locale for a single lookup (used by previews). */
  locale?: string;
}

type MissingKeyListener = (info: { locale: string; namespace: string; key: string }) => void;

const NAMESPACE_SEPARATOR = ":";
const DEFAULT_NAMESPACE: CatalogNamespace = "common";
const KEY_PATH_SEPARATOR = ".";

/** locale -> namespace -> parsed bundle. */
const catalogCache = new Map<string, Map<string, CatalogBundle>>();
/** Dedupes concurrent loads of the same locale/namespace pair. */
const inflightLoads = new Map<string, Promise<CatalogBundle | null>>();
/** Keys already reported missing, so a re-render does not spam the listener. */
const reportedMissing = new Set<string>();

let activeLocale: string = DEFAULT_LOCALE;
let activeRegion = "";
let missingKeyListener: MissingKeyListener | null = null;

/* ------------------------------------------------------------------ *
 * Active locale
 * ------------------------------------------------------------------ */

export function getActiveTranslationLocale(): string {
  return activeLocale;
}

/**
 * The full BCP-47 tag used for `Intl` formatting. Combines the active language
 * with the device's region so a Portuguese speaker in Portugal gets EUR and
 * `dd/mm/yyyy` while one in Brazil gets BRL.
 */
export function getActiveIntlLocale(): string {
  return toIntlLocale(activeLocale, activeRegion);
}

export function setActiveRegion(region: string): void {
  activeRegion = String(region ?? "").trim().toUpperCase();
}

/**
 * Activates a locale, loading every namespace it needs first.
 *
 * Loading before switching is what makes language changes feel instant and
 * flicker-free: by the time the provider re-renders, every string for the new
 * language is already in memory.
 */
export async function activateLocale(locale: string, namespaces: readonly CatalogNamespace[] = CATALOG_NAMESPACES): Promise<string> {
  const normalized = normalizeTag(locale);
  const target = isSupportedLocale(normalized) ? normalized : DEFAULT_LOCALE;
  await Promise.all([
    preloadNamespaces(target, namespaces),
    // The default catalog is the last line of defense for any key a translation
    // has not covered yet, so it stays resident alongside the active language.
    target === DEFAULT_LOCALE ? Promise.resolve() : preloadNamespaces(DEFAULT_LOCALE, namespaces)
  ]);
  activeLocale = target;
  return target;
}

export async function preloadNamespaces(locale: string, namespaces: readonly CatalogNamespace[] = CATALOG_NAMESPACES): Promise<void> {
  await Promise.all(namespaces.map((namespace) => ensureNamespace(locale, namespace)));
}

/* ------------------------------------------------------------------ *
 * Catalog loading
 * ------------------------------------------------------------------ */

function cacheKey(locale: string, namespace: string): string {
  return `${locale}${NAMESPACE_SEPARATOR}${namespace}`;
}

export function isNamespaceLoaded(locale: string, namespace: string): boolean {
  return Boolean(catalogCache.get(normalizeTag(locale))?.has(namespace));
}

/**
 * Loads one namespace bundle for one locale, memoized. Resolves to null when
 * the bundle is unavailable; callers treat that as "fall back", never as an
 * error, so a missing translation file can never crash a screen.
 */
export async function ensureNamespace(locale: string, namespace: CatalogNamespace): Promise<CatalogBundle | null> {
  const normalized = normalizeTag(locale);
  const existing = catalogCache.get(normalized)?.get(namespace);
  if (existing) return existing;

  const key = cacheKey(normalized, namespace);
  const inflight = inflightLoads.get(key);
  if (inflight) return inflight;

  const load = Promise.resolve()
    .then(() => loadCatalogBundle(normalized, namespace))
    .then((bundle) => {
      if (!bundle) return null;
      const byNamespace = catalogCache.get(normalized) ?? new Map<string, CatalogBundle>();
      byNamespace.set(namespace, bundle);
      catalogCache.set(normalized, byNamespace);
      return bundle;
    })
    .catch(() => null)
    .finally(() => {
      inflightLoads.delete(key);
    });

  inflightLoads.set(key, load);
  return load;
}

/** Drops cached bundles. Used by tests and by the catalog-version migration. */
export function resetCatalogCache(): void {
  catalogCache.clear();
  inflightLoads.clear();
  reportedMissing.clear();
}

/* ------------------------------------------------------------------ *
 * Lookup
 * ------------------------------------------------------------------ */

export function onMissingKey(listener: MissingKeyListener | null): void {
  missingKeyListener = listener;
}

/** Splits `"settings:language.title"` into its namespace and dotted key path. */
export function parseKey(key: string): { namespace: CatalogNamespace; path: string } {
  const raw = String(key ?? "").trim();
  const separatorIndex = raw.indexOf(NAMESPACE_SEPARATOR);
  if (separatorIndex === -1) return { namespace: DEFAULT_NAMESPACE, path: raw };
  const namespace = raw.slice(0, separatorIndex) as CatalogNamespace;
  const path = raw.slice(separatorIndex + 1);
  if (!CATALOG_NAMESPACES.includes(namespace)) return { namespace: DEFAULT_NAMESPACE, path: raw };
  return { namespace, path };
}

function readPath(bundle: CatalogBundle | undefined, path: string): string | null {
  if (!bundle || !path) return null;
  let cursor: CatalogValue | undefined = bundle;
  for (const segment of path.split(KEY_PATH_SEPARATOR)) {
    if (typeof cursor !== "object" || cursor === null) return null;
    cursor = (cursor as Record<string, CatalogValue>)[segment];
    if (cursor === undefined) return null;
  }
  return typeof cursor === "string" ? cursor : null;
}

/**
 * Builds the ordered list of key variants to try for one lookup.
 *
 * Most specific first: gender+plural, then plural, then gender, then the base
 * key. This lets a catalog supply `invited_female_one` where it matters and
 * omit it everywhere else, falling back through progressively more generic
 * forms rather than to English.
 */
function keyVariants(path: string, locale: string, options?: TranslateOptions): string[] {
  const variants: string[] = [];
  const context = options?.context ? String(options.context).trim() : "";
  const count = typeof options?.count === "number" && Number.isFinite(options.count) ? options.count : null;
  const pluralCategory = count === null ? "" : selectPluralCategory(count, locale);

  if (context && pluralCategory) {
    variants.push(`${path}_${context}_${pluralCategory}`);
    // `other` is the universal plural bucket; try it before dropping gender.
    if (pluralCategory !== "other") variants.push(`${path}_${context}_other`);
  }
  if (pluralCategory) {
    variants.push(`${path}_${pluralCategory}`);
    if (pluralCategory !== "other") variants.push(`${path}_other`);
  }
  if (context) variants.push(`${path}_${context}`);
  variants.push(path);
  return variants;
}

export function selectPluralCategory(count: number, locale: string = activeLocale): Intl.LDMLPluralRule {
  try {
    return new Intl.PluralRules(toIntlLocale(locale, activeRegion)).select(count);
  } catch {
    // Deterministic English-style fallback when Intl.PluralRules is unavailable.
    return count === 1 ? "one" : "other";
  }
}

/**
 * Resolves a key to a raw (uninterpolated) template.
 *
 * Fallback chain: active locale, then the default locale, then `defaultValue`,
 * then null. The default-locale hop is what satisfies "gracefully fall back
 * when no translation exists" — a half-translated catalog renders English for
 * the untranslated keys instead of showing raw identifiers.
 */
export function resolveTemplate(key: string, options?: TranslateOptions): { template: string | null; usedLocale: string | null } {
  const requested = options?.locale ? normalizeTag(options.locale) : activeLocale;
  const locale = isSupportedLocale(requested) ? requested : DEFAULT_LOCALE;
  const { namespace, path } = parseKey(key);
  if (!path) return { template: null, usedLocale: null };

  const chain = locale === DEFAULT_LOCALE ? [locale] : [locale, DEFAULT_LOCALE];
  for (const candidateLocale of chain) {
    const bundle = catalogCache.get(candidateLocale)?.get(namespace);
    if (!bundle) continue;
    for (const variant of keyVariants(path, candidateLocale, options)) {
      const value = readPath(bundle, variant);
      if (value !== null) return { template: value, usedLocale: candidateLocale };
    }
  }

  reportMissing(locale, namespace, path);
  return { template: null, usedLocale: null };
}

function reportMissing(locale: string, namespace: string, key: string): void {
  const signature = `${locale}${NAMESPACE_SEPARATOR}${namespace}${NAMESPACE_SEPARATOR}${key}`;
  if (reportedMissing.has(signature)) return;
  reportedMissing.add(signature);
  if (missingKeyListener) {
    try {
      missingKeyListener({ locale, namespace, key });
    } catch {
      // A listener must never break rendering.
    }
  }
  if (typeof __DEV__ !== "undefined" && __DEV__) {
    console.warn(`[i18n] missing key "${namespace}${NAMESPACE_SEPARATOR}${key}" for locale "${locale}"`);
  }
}

/** Every key reported missing this session — surfaced by the i18n dev panel. */
export function getMissingKeys(): string[] {
  return [...reportedMissing];
}

/* ------------------------------------------------------------------ *
 * Interpolation
 * ------------------------------------------------------------------ */

const PLACEHOLDER = /\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}/g;

/**
 * Substitutes `{{name}}` placeholders. Numeric values are run through
 * `Intl.NumberFormat` so counts embedded in a sentence are grouped and digited
 * per locale (`1,024` vs `1.024` vs `١٬٠٢٤`). Unknown placeholders are left in
 * place rather than blanked, which makes a catalog bug visible in QA instead of
 * silently producing a sentence with a hole in it.
 */
export function interpolate(template: string, params?: TranslateOptions, locale: string = activeLocale): string {
  if (!template || !params || template.indexOf("{{") === -1) return template;
  return template.replace(PLACEHOLDER, (match, name: string) => {
    const value = (params as Record<string, unknown>)[name];
    if (value === undefined || value === null) return match;
    if (typeof value === "number") return formatNumberForLocale(value, locale);
    return String(value);
  });
}

function formatNumberForLocale(value: number, locale: string): string {
  try {
    return new Intl.NumberFormat(toIntlLocale(locale, activeRegion)).format(value);
  } catch {
    return String(value);
  }
}

/* ------------------------------------------------------------------ *
 * Public translate
 * ------------------------------------------------------------------ */

/**
 * Translates a key.
 *
 * Never throws and never returns undefined: an unresolved key yields
 * `defaultValue` when supplied, otherwise the final segment of the key path
 * humanized, so a missed migration degrades to readable text rather than a
 * developer-looking identifier in the UI.
 */
export function translate(key: string, options?: TranslateOptions): string {
  const { template, usedLocale } = resolveTemplate(key, options);
  if (template === null) {
    if (typeof options?.defaultValue === "string") {
      return interpolate(options.defaultValue, options, activeLocale);
    }
    return humanizeKey(key);
  }
  return interpolate(template, options, usedLocale ?? activeLocale);
}

/** `"settings:language.selectTitle"` -> `"Select title"`. */
export function humanizeKey(key: string): string {
  const { path } = parseKey(key);
  const leaf = path.split(KEY_PATH_SEPARATOR).pop() || path;
  const spaced = leaf
    .replace(/_(one|two|few|many|other|zero)$/, "")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .replace(/[_-]+/g, " ")
    .trim();
  if (!spaced) return key;
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

/** True when the active locale itself (not the fallback) supplies the key. */
export function hasTranslation(key: string, locale: string = activeLocale): boolean {
  const { namespace, path } = parseKey(key);
  const bundle = catalogCache.get(normalizeTag(locale))?.get(namespace);
  return readPath(bundle, path) !== null;
}
