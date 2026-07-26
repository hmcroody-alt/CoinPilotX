/**
 * Supported-locale registry for PulseSoc Native.
 *
 * This file is the single source of truth for which languages the app ships.
 * Adding a language is a data change here plus a catalog file — no component,
 * navigation or screen code has to be touched. `catalogs/index.ts` validates at
 * module load that every entry below has a registered bundle loader.
 */

export type TextDirection = "ltr" | "rtl";

export interface LocaleDefinition {
  /** BCP-47 language tag used as the catalog key and persisted preference. */
  code: string;
  /** Endonym — the language's name written in its own script. */
  nativeName: string;
  /** English exonym, shown as a secondary label so users can cross-reference. */
  englishName: string;
  direction: TextDirection;
  /**
   * Region appended when a bare language tag needs a full locale for Intl
   * formatting (dates, currency, numbers). Chosen as the most populous locale
   * for that language so defaults feel natural.
   */
  defaultRegion: string;
  /**
   * Extra lowercase strings the language picker's search box matches against
   * (romanizations, alternate names, common misspellings).
   */
  searchAliases: string[];
}

/**
 * Ordered so English leads and the remainder follow the platform's largest
 * audiences. The language picker sorts by native name at render time, so this
 * order only affects fallback resolution scans.
 */
export const SUPPORTED_LOCALES: readonly LocaleDefinition[] = Object.freeze([
  {
    code: "en",
    nativeName: "English",
    englishName: "English",
    direction: "ltr",
    defaultRegion: "US",
    searchAliases: ["english", "en", "us", "uk", "british", "american"]
  },
  {
    code: "es",
    nativeName: "Español",
    englishName: "Spanish",
    direction: "ltr",
    defaultRegion: "ES",
    searchAliases: ["espanol", "espagnol", "spanish", "castellano", "es"]
  },
  {
    code: "fr",
    nativeName: "Français",
    englishName: "French",
    direction: "ltr",
    defaultRegion: "FR",
    searchAliases: ["francais", "french", "fr"]
  },
  {
    code: "ht",
    nativeName: "Kreyòl ayisyen",
    englishName: "Haitian Creole",
    direction: "ltr",
    defaultRegion: "HT",
    searchAliases: ["kreyol", "creole", "haitian", "haiti", "ayisyen", "ht"]
  },
  {
    code: "pt",
    nativeName: "Português",
    englishName: "Portuguese",
    direction: "ltr",
    defaultRegion: "BR",
    searchAliases: ["portugues", "portuguese", "brasil", "brazil", "pt"]
  },
  {
    code: "de",
    nativeName: "Deutsch",
    englishName: "German",
    direction: "ltr",
    defaultRegion: "DE",
    searchAliases: ["deutsch", "german", "germany", "de"]
  },
  {
    code: "ar",
    nativeName: "العربية",
    englishName: "Arabic",
    direction: "rtl",
    defaultRegion: "SA",
    searchAliases: ["arabic", "arabiya", "arabe", "ar"]
  },
  {
    code: "hi",
    nativeName: "हिन्दी",
    englishName: "Hindi",
    direction: "ltr",
    defaultRegion: "IN",
    searchAliases: ["hindi", "india", "devanagari", "hi"]
  },
  {
    code: "ja",
    nativeName: "日本語",
    englishName: "Japanese",
    direction: "ltr",
    defaultRegion: "JP",
    searchAliases: ["japanese", "nihongo", "japan", "ja", "jp"]
  },
  {
    code: "ko",
    nativeName: "한국어",
    englishName: "Korean",
    direction: "ltr",
    defaultRegion: "KR",
    searchAliases: ["korean", "hangul", "hangugeo", "korea", "ko", "kr"]
  },
  {
    code: "zh",
    nativeName: "中文",
    englishName: "Chinese (Simplified)",
    direction: "ltr",
    defaultRegion: "CN",
    searchAliases: ["chinese", "mandarin", "zhongwen", "simplified", "china", "zh", "cn"]
  }
]);

/** The language every lookup falls back to when nothing else resolves. */
export const DEFAULT_LOCALE = "en";

const LOCALE_BY_CODE: ReadonlyMap<string, LocaleDefinition> = new Map(
  SUPPORTED_LOCALES.map((locale) => [locale.code, locale])
);

export const SUPPORTED_LOCALE_CODES: readonly string[] = Object.freeze(SUPPORTED_LOCALES.map((locale) => locale.code));

/**
 * Language tags that are not shipped as their own catalog but should resolve to
 * one we do ship. Keeps `zh-Hant`, `pt-PT`, regional Arabic and the like from
 * silently dropping to English.
 */
const LANGUAGE_ALIASES: Readonly<Record<string, string>> = Object.freeze({
  // Chinese script and regional variants all map to the Simplified catalog
  // until a Traditional catalog exists.
  cmn: "zh",
  yue: "zh",
  wuu: "zh",
  // Haitian Creole is written "ht" in BCP-47 but some devices report "hat".
  hat: "ht",
  // Norwegian/Filipino style three-letter tags occasionally seen from Android.
  spa: "es",
  fra: "fr",
  fre: "fr",
  deu: "de",
  ger: "de",
  por: "pt",
  ara: "ar",
  hin: "hi",
  jpn: "ja",
  kor: "ko",
  eng: "en",
  zho: "zh",
  chi: "zh"
});

/**
 * Region-to-language map used as the fourth detection tier: when a device
 * reports a language we do not ship (or none at all) but does report a region,
 * we infer the most likely supported language for that region.
 */
const REGION_LANGUAGE: Readonly<Record<string, string>> = Object.freeze({
  AE: "ar", AR: "es", AT: "de", BH: "ar", BO: "es", BR: "pt", CH: "de", CL: "es",
  CN: "zh", CO: "es", CR: "es", CU: "es", DE: "de", DO: "es", DZ: "ar", EC: "es",
  EG: "ar", ES: "es", FR: "fr", GQ: "es", GT: "es", HK: "zh", HN: "es", HT: "ht",
  IN: "hi", IQ: "ar", JO: "ar", JP: "ja", KP: "ko", KR: "ko", KW: "ar", LB: "ar",
  LI: "de", LY: "ar", MA: "ar", MC: "fr", MO: "zh", MX: "es", NI: "es", OM: "ar",
  PA: "es", PE: "es", PR: "es", PS: "ar", PT: "pt", PY: "es", QA: "ar", SA: "ar",
  SG: "zh", SN: "fr", SV: "es", SY: "ar", TD: "ar", TN: "ar", TW: "zh", UY: "es",
  VE: "es", YE: "ar",
  // Francophone and lusophone regions where the local language is not shipped
  // but French/Portuguese is the working language of the platform.
  AO: "pt", BE: "fr", BF: "fr", BI: "fr", BJ: "fr", CD: "fr", CF: "fr", CG: "fr",
  CI: "fr", CM: "fr", CV: "pt", DJ: "fr", GA: "fr", GN: "fr", GW: "pt", KM: "fr",
  LU: "fr", MG: "fr", ML: "fr", MQ: "fr", MU: "fr", MZ: "pt", NE: "fr", RW: "fr",
  ST: "pt", TG: "fr", TL: "pt", VU: "fr"
});

/** Normalizes any tag shape (`pt_BR`, `PT-br`, `zh-Hans-CN`) to lowercase dashes. */
export function normalizeTag(tag: unknown): string {
  return String(tag ?? "")
    .trim()
    .replace(/_/g, "-")
    .toLowerCase();
}

/** The primary language subtag of an arbitrary locale tag. */
export function languageOf(tag: unknown): string {
  const normalized = normalizeTag(tag);
  if (!normalized) return "";
  return normalized.split("-", 1)[0];
}

/** The region subtag (uppercased) of an arbitrary locale tag, or "" if absent. */
export function regionOf(tag: unknown): string {
  const normalized = normalizeTag(tag);
  if (!normalized) return "";
  const parts = normalized.split("-");
  for (const part of parts.slice(1)) {
    // A region subtag is either two letters (US) or three digits (419).
    if (/^[a-z]{2}$/.test(part) || /^\d{3}$/.test(part)) return part.toUpperCase();
  }
  return "";
}

export function isSupportedLocale(code: unknown): boolean {
  return LOCALE_BY_CODE.has(normalizeTag(code));
}

export function getLocaleDefinition(code: unknown): LocaleDefinition | null {
  return LOCALE_BY_CODE.get(normalizeTag(code)) ?? null;
}

/**
 * Resolves an arbitrary language tag to a shipped catalog code.
 *
 * Tries, in order: exact match, alias table, primary subtag, then the region's
 * dominant language. Returns null when nothing matches so callers can decide
 * whether to continue down the detection chain or fall back.
 */
export function resolveSupportedLocale(tag: unknown): string | null {
  const normalized = normalizeTag(tag);
  if (!normalized) return null;
  if (LOCALE_BY_CODE.has(normalized)) return normalized;

  const language = languageOf(normalized);
  if (LOCALE_BY_CODE.has(language)) return language;

  const aliased = LANGUAGE_ALIASES[language];
  if (aliased && LOCALE_BY_CODE.has(aliased)) return aliased;

  const region = regionOf(normalized);
  if (region) {
    const regional = REGION_LANGUAGE[region];
    if (regional && LOCALE_BY_CODE.has(regional)) return regional;
  }
  return null;
}

/** Language inferred purely from a region code, used by the detection chain. */
export function localeForRegion(region: unknown): string | null {
  const normalized = String(region ?? "").trim().toUpperCase();
  if (!normalized) return null;
  const language = REGION_LANGUAGE[normalized];
  return language && LOCALE_BY_CODE.has(language) ? language : null;
}

export function isRtlLanguage(code: unknown): boolean {
  return getLocaleDefinition(code)?.direction === "rtl";
}

export function directionOf(code: unknown): TextDirection {
  return isRtlLanguage(code) ? "rtl" : "ltr";
}

/**
 * The full BCP-47 tag handed to `Intl` for a shipped language. Preserves an
 * explicit region when the user's device supplied one (so a Spanish speaker in
 * Mexico gets `es-MX` date and currency formatting, not `es-ES`), otherwise
 * falls back to the language's default region.
 */
export function toIntlLocale(code: unknown, preferredRegion?: unknown): string {
  const definition = getLocaleDefinition(code);
  if (!definition) return "en-US";
  const region = String(preferredRegion ?? "").trim().toUpperCase();
  if (/^([A-Z]{2}|\d{3})$/.test(region)) return `${definition.code}-${region}`;
  return `${definition.code}-${definition.defaultRegion}`;
}

/**
 * Case- and accent-insensitive search over native name, English name and
 * aliases. Powers the language picker's search field.
 */
export function searchLocales(query: string): LocaleDefinition[] {
  const needle = foldForSearch(query);
  if (!needle) return [...SUPPORTED_LOCALES];
  return SUPPORTED_LOCALES.filter((locale) => {
    if (foldForSearch(locale.nativeName).includes(needle)) return true;
    if (foldForSearch(locale.englishName).includes(needle)) return true;
    if (locale.code.startsWith(needle)) return true;
    return locale.searchAliases.some((alias) => alias.includes(needle));
  });
}

/**
 * Lowercases and strips diacritics so "espanol" matches "Español" and
 * "francais" matches "Français". Falls back to a plain lowercase when the
 * engine lacks Unicode normalization.
 */
export function foldForSearch(value: string): string {
  const lowered = String(value ?? "").trim().toLowerCase();
  if (!lowered) return "";
  try {
    return lowered.normalize("NFD").replace(/[\u0300-\u036f]/g, "");
  } catch {
    return lowered;
  }
}
