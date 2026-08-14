import { DEFAULT_LOCALE, SUPPORTED_LOCALE_CODES, normalizeTag } from "../locales";

/**
 * Catalog registry and lazy bundle loader.
 *
 * Translations are addressed by *namespace* (`t("settings:language.title")`)
 * but stored in two physical *tiers* per language:
 *
 *   core     — everything needed to render the first frame and the sign-in
 *              flow: shared chrome, auth, and error/empty-state copy.
 *   extended — everything reachable only after navigation: social surfaces,
 *              messaging, commerce, discovery and settings.
 *
 * Splitting this way is what keeps the "lazy-loaded bundles" requirement
 * meaningful without fragmenting the catalogs into 88 files: launch parses one
 * small tier, and the larger tier is parsed off the critical path once the app
 * is interactive. Because both tiers are inlined in the JS bundle by Metro,
 * every installed language is fully available offline.
 *
 * Metro cannot resolve `require()` with a computed path, so the loader map
 * below is explicit. Adding a language means adding its two files and one entry
 * here — no other code changes anywhere in the app.
 */

export const CATALOG_NAMESPACES = [
  "common",
  "auth",
  "errors",
  "social",
  "messaging",
  "commerce",
  "discovery",
  "settings",
  "progress",
  "premium"
] as const;

export type CatalogNamespace = (typeof CATALOG_NAMESPACES)[number];

export type CatalogTier = "core" | "extended";

/**
 * The shape of catalog data: arbitrarily nested groups bottoming out in strings.
 * Declared here rather than in the engine because it describes the JSON files
 * this module owns; the engine re-exports it for consumers.
 */
export type CatalogValue = string | { [key: string]: CatalogValue };
export type CatalogBundle = Record<string, CatalogValue>;

/** Which physical tier file each namespace lives in. */
export const NAMESPACE_TIER: Readonly<Record<CatalogNamespace, CatalogTier>> = Object.freeze({
  common: "core",
  auth: "core",
  errors: "core",
  social: "extended",
  messaging: "extended",
  commerce: "extended",
  discovery: "extended",
  settings: "extended",
  // Progress OS is reachable only from the profile, well after first frame.
  progress: "extended",
  // Same as Progress: one tile at the end of the profile grid, and the tile's
  // own micro-status is the only part of it that renders before a tap.
  premium: "extended"
});

/** Namespaces the provider warms before it renders children. */
export const CORE_NAMESPACES: readonly CatalogNamespace[] = Object.freeze(
  CATALOG_NAMESPACES.filter((namespace) => NAMESPACE_TIER[namespace] === "core")
);

/** Namespaces warmed in the background once the first frame is on screen. */
export const EXTENDED_NAMESPACES: readonly CatalogNamespace[] = Object.freeze(
  CATALOG_NAMESPACES.filter((namespace) => NAMESPACE_TIER[namespace] === "extended")
);

type TierLoader = () => Record<string, unknown>;

/**
 * Static require map. The arrow functions defer the `require` until the tier is
 * actually needed, which is what makes the extended tier lazy.
 */
const LOADERS: Readonly<Record<string, Readonly<Record<CatalogTier, TierLoader>>>> = Object.freeze({
  en: { core: () => require("./en/core.json"), extended: () => require("./en/extended.json") },
  es: { core: () => require("./es/core.json"), extended: () => require("./es/extended.json") },
  fr: { core: () => require("./fr/core.json"), extended: () => require("./fr/extended.json") },
  ht: { core: () => require("./ht/core.json"), extended: () => require("./ht/extended.json") },
  pt: { core: () => require("./pt/core.json"), extended: () => require("./pt/extended.json") },
  de: { core: () => require("./de/core.json"), extended: () => require("./de/extended.json") },
  ar: { core: () => require("./ar/core.json"), extended: () => require("./ar/extended.json") },
  hi: { core: () => require("./hi/core.json"), extended: () => require("./hi/extended.json") },
  ja: { core: () => require("./ja/core.json"), extended: () => require("./ja/extended.json") },
  ko: { core: () => require("./ko/core.json"), extended: () => require("./ko/extended.json") },
  zh: { core: () => require("./zh/core.json"), extended: () => require("./zh/extended.json") }
});

/** Parsed tier files, keyed `locale:tier`, so each JSON is evaluated once. */
const tierCache = new Map<string, Record<string, unknown> | null>();

function loadTier(locale: string, tier: CatalogTier): Record<string, unknown> | null {
  const key = `${locale}:${tier}`;
  if (tierCache.has(key)) return tierCache.get(key) ?? null;
  const loader = LOADERS[locale]?.[tier];
  let parsed: Record<string, unknown> | null = null;
  if (loader) {
    try {
      const value = loader();
      // Babel's ESM interop can wrap a JSON import in `{ default: ... }`.
      parsed = (value && typeof value === "object" && "default" in value
        ? (value as { default: Record<string, unknown> }).default
        : value) as Record<string, unknown>;
    } catch {
      // A corrupt or missing catalog file degrades to the fallback locale
      // rather than crashing the app at launch.
      parsed = null;
    }
  }
  tierCache.set(key, parsed);
  return parsed;
}

/**
 * Returns the parsed bundle for one namespace of one language, or null when the
 * language does not ship that namespace (the engine then falls back).
 */
export function loadCatalogBundle(locale: string, namespace: CatalogNamespace): CatalogBundle | null {
  const normalized = normalizeTag(locale);
  const tier = NAMESPACE_TIER[namespace];
  if (!tier) return null;
  const parsed = loadTier(normalized, tier);
  const bundle = parsed?.[namespace];
  if (!bundle || typeof bundle !== "object") return null;
  return bundle as CatalogBundle;
}

/** Catalog schema version declared by a language's tier file, for migrations. */
export function getCatalogVersion(locale: string, tier: CatalogTier = "core"): string {
  const parsed = loadTier(normalizeTag(locale), tier);
  const version = parsed?.$version;
  return typeof version === "string" ? version : "0";
}

export function hasCatalog(locale: string): boolean {
  return Boolean(LOADERS[normalizeTag(locale)]);
}

/** Language codes that actually have loader entries. */
export const CATALOG_LOCALE_CODES: readonly string[] = Object.freeze(Object.keys(LOADERS));

/**
 * Fails fast in development when `locales.ts` and this loader map drift apart —
 * the single most likely mistake when adding a language.
 */
if (typeof __DEV__ !== "undefined" && __DEV__) {
  const missing = SUPPORTED_LOCALE_CODES.filter((code) => !LOADERS[code]);
  if (missing.length > 0) {
    console.warn(`[i18n] SUPPORTED_LOCALES declares ${missing.join(", ")} but no catalog loader is registered.`);
  }
  if (!LOADERS[DEFAULT_LOCALE]) {
    console.warn(`[i18n] the default locale "${DEFAULT_LOCALE}" has no catalog loader; every lookup will fall through.`);
  }
}
