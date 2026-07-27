import { NativeModules, Platform } from "react-native";

import {
  DEFAULT_LOCALE,
  localeForRegion,
  normalizeTag,
  regionOf,
  resolveSupportedLocale
} from "./locales";

/**
 * Device-language detection for PulseSoc Native.
 *
 * Reads the device's own language preferences straight from the platform,
 * without a native dependency: iOS exposes the ordered `AppleLanguages` list
 * through `SettingsManager`, Android exposes the active locale through
 * `I18nManager.localeIdentifier`, and web exposes `navigator.languages`. Every
 * read is defensive because these modules are absent under Jest, in Expo Go
 * edge cases and on older OS versions — in which case we fall back to the
 * `Intl` resolved locale, which the JS engine always provides.
 */

export type DetectionSource =
  | "saved-preference"
  | "account-preference"
  | "device-language"
  | "device-locale"
  | "device-region"
  | "fallback";

export interface DetectionCandidate {
  source: DetectionSource;
  /** The raw tag the source reported, kept for diagnostics and tests. */
  raw: string;
  /** The shipped catalog code the raw tag resolved to, or null if unsupported. */
  resolved: string | null;
}

export interface DetectionResult {
  /** The catalog code the app should activate. Always a supported locale. */
  locale: string;
  /** Which tier of the priority chain produced the answer. */
  source: DetectionSource;
  /**
   * Region reported by the device, used to keep Intl formatting regional even
   * when the language falls back (an `es` speaker in `MX` still gets MXN).
   */
  region: string;
  /** Every tier that was consulted, in priority order. */
  candidates: DetectionCandidate[];
}

/**
 * The device's ordered language preference list, most-preferred first.
 *
 * iOS users can rank several languages in Settings; honoring that order means a
 * user whose first choice we do not ship still gets their second choice rather
 * than English.
 */
export function getDeviceLanguages(): string[] {
  const tags: string[] = [];
  const push = (value: unknown) => {
    const normalized = normalizeTag(value);
    if (normalized && !tags.includes(normalized)) tags.push(normalized);
  };

  try {
    if (Platform.OS === "ios") {
      const settings = (NativeModules as Record<string, any>)?.SettingsManager?.settings;
      const appleLanguages = settings?.AppleLanguages;
      if (Array.isArray(appleLanguages)) appleLanguages.forEach(push);
      push(settings?.AppleLocale);
    } else if (Platform.OS === "android") {
      const i18nModule = (NativeModules as Record<string, any>)?.I18nManager;
      push(i18nModule?.localeIdentifier);
      const settingsModule = (NativeModules as Record<string, any>)?.SettingsManager?.settings;
      push(settingsModule?.AppleLocale);
    } else if (typeof navigator !== "undefined") {
      const navigatorLanguages = (navigator as Navigator & { languages?: string[] }).languages;
      if (Array.isArray(navigatorLanguages)) navigatorLanguages.forEach(push);
      push((navigator as Navigator).language);
    }
  } catch {
    // A missing or throwing native module must never block app launch; the
    // Intl-based tiers below still produce a usable answer.
  }
  return tags;
}

/** The JS engine's resolved locale — the always-available detection floor. */
export function getDeviceLocale(): string {
  try {
    const resolved = new Intl.DateTimeFormat().resolvedOptions().locale;
    if (resolved) return normalizeTag(resolved);
  } catch {
    // Intl is unavailable or misconfigured; fall through to the default.
  }
  return DEFAULT_LOCALE;
}

/**
 * The device's region, preferred from the language list (which carries the
 * user's own choice) and backfilled from the resolved Intl locale.
 */
export function getDeviceRegion(): string {
  for (const tag of getDeviceLanguages()) {
    const region = regionOf(tag);
    if (region) return region;
  }
  return regionOf(getDeviceLocale());
}

/**
 * Runs the full priority chain and returns the locale to activate.
 *
 * Priority, per the localization spec:
 *   1. the user's saved PulseSoc preference
 *   2. the account preference synced from the server (post-reinstall restore)
 *   3. the device's language list
 *   4. the device's resolved locale
 *   5. the device's region
 *   6. the system fallback language
 *
 * Every tier is recorded in `candidates` so the settings screen and tests can
 * explain *why* a language was chosen.
 */
export function detectLocale(options?: {
  savedPreference?: string | null;
  accountPreference?: string | null;
}): DetectionResult {
  const candidates: DetectionCandidate[] = [];
  const region = getDeviceRegion();

  const consider = (source: DetectionSource, raw: unknown): string | null => {
    const normalized = normalizeTag(raw);
    if (!normalized) return null;
    const resolved = resolveSupportedLocale(normalized);
    candidates.push({ source, raw: normalized, resolved });
    return resolved;
  };

  const saved = consider("saved-preference", options?.savedPreference);
  if (saved) return { locale: saved, source: "saved-preference", region, candidates };

  const account = consider("account-preference", options?.accountPreference);
  if (account) return { locale: account, source: "account-preference", region, candidates };

  for (const tag of getDeviceLanguages()) {
    const matched = consider("device-language", tag);
    if (matched) return { locale: matched, source: "device-language", region, candidates };
  }

  const fromLocale = consider("device-locale", getDeviceLocale());
  if (fromLocale) return { locale: fromLocale, source: "device-locale", region, candidates };

  if (region) {
    const fromRegion = localeForRegion(region);
    candidates.push({ source: "device-region", raw: region, resolved: fromRegion });
    if (fromRegion) return { locale: fromRegion, source: "device-region", region, candidates };
  }

  candidates.push({ source: "fallback", raw: DEFAULT_LOCALE, resolved: DEFAULT_LOCALE });
  return { locale: DEFAULT_LOCALE, source: "fallback", region, candidates };
}
