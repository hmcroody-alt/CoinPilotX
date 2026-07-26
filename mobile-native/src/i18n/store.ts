import AsyncStorage from "@react-native-async-storage/async-storage";

import { getAccountLanguage, updateAccountLanguage } from "../api/account";
import { DEFAULT_LOCALE, normalizeTag, resolveSupportedLocale } from "./locales";

/**
 * Language-preference persistence.
 *
 * Two layers, deliberately:
 *
 *   device  — AsyncStorage, written synchronously with the user's tap. This is
 *             what makes the choice survive an app restart and what the launch
 *             path reads, so startup never blocks on the network.
 *   account — the server's `preferred_language`, written opportunistically.
 *             This is what restores the language after a reinstall or on a new
 *             device, satisfying "persists across sessions and devices".
 *
 * Every server call is best-effort: a signed-out user, an offline device or a
 * failing endpoint must never prevent the language from changing locally.
 */

const LANGUAGE_STORAGE_KEY = "pulsesoc.i18n.language.v1";
const AUTO_FLAG_STORAGE_KEY = "pulsesoc.i18n.followDevice.v1";
const CATALOG_VERSION_STORAGE_KEY = "pulsesoc.i18n.catalogVersion.v1";

export interface StoredLanguagePreference {
  /** The explicitly chosen language, or null when following the device. */
  language: string | null;
  /** True when the user has not overridden the automatically detected language. */
  followDevice: boolean;
}

/* ------------------------------------------------------------------ *
 * Device storage
 * ------------------------------------------------------------------ */

export async function loadStoredLanguage(): Promise<StoredLanguagePreference> {
  try {
    const [language, followDevice] = await Promise.all([
      AsyncStorage.getItem(LANGUAGE_STORAGE_KEY),
      AsyncStorage.getItem(AUTO_FLAG_STORAGE_KEY)
    ]);
    const resolved = resolveSupportedLocale(language);
    // The flag is only meaningful alongside a stored language; a device with a
    // language but no flag predates the flag and is treated as an explicit
    // choice, which is the safer assumption (never silently override a user).
    const follows = followDevice === "1" || !resolved;
    return { language: follows ? null : resolved, followDevice: follows };
  } catch {
    return { language: null, followDevice: true };
  }
}

export async function saveStoredLanguage(language: string | null): Promise<void> {
  try {
    if (language) {
      await AsyncStorage.multiSet([
        [LANGUAGE_STORAGE_KEY, normalizeTag(language)],
        [AUTO_FLAG_STORAGE_KEY, "0"]
      ]);
    } else {
      // "Follow device" clears the pinned language but records the intent, so a
      // later launch does not mistake it for a first run.
      await AsyncStorage.multiSet([[AUTO_FLAG_STORAGE_KEY, "1"]]);
      await AsyncStorage.removeItem(LANGUAGE_STORAGE_KEY);
    }
  } catch {
    // A storage failure degrades to a session-only language change rather than
    // failing the user's action outright.
  }
}

/* ------------------------------------------------------------------ *
 * Catalog versioning
 * ------------------------------------------------------------------ */

/**
 * Records the catalog version the device last ran. When a build ships new or
 * renamed keys, the caller can compare and drop stale derived caches without a
 * reinstall — the hook that lets translations be updated without code changes.
 */
export async function loadStoredCatalogVersion(): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(CATALOG_VERSION_STORAGE_KEY);
  } catch {
    return null;
  }
}

export async function saveCatalogVersion(version: string): Promise<void> {
  try {
    await AsyncStorage.setItem(CATALOG_VERSION_STORAGE_KEY, String(version));
  } catch {
    // Version tracking is diagnostic; failing to record it is not fatal.
  }
}

/* ------------------------------------------------------------------ *
 * Account sync
 * ------------------------------------------------------------------ */

/**
 * Reads the language stored on the account. Returns null for signed-out users,
 * network failures, or a server value we do not ship a catalog for.
 */
export async function fetchAccountLanguage(): Promise<string | null> {
  try {
    const response = await getAccountLanguage();
    const raw = response?.preferred_language || response?.language;
    return resolveSupportedLocale(raw);
  } catch {
    return null;
  }
}

/**
 * Pushes the chosen language to the account so other devices and a future
 * reinstall pick it up. Fire-and-forget by design: the local change has already
 * been applied by the time this runs.
 */
export async function pushAccountLanguage(language: string): Promise<boolean> {
  const normalized = resolveSupportedLocale(language) ?? DEFAULT_LOCALE;
  try {
    await updateAccountLanguage(normalized);
    return true;
  } catch {
    return false;
  }
}

/** Clears every persisted language preference. Used by sign-out and by tests. */
export async function clearStoredLanguage(): Promise<void> {
  try {
    await AsyncStorage.multiRemove([LANGUAGE_STORAGE_KEY, AUTO_FLAG_STORAGE_KEY, CATALOG_VERSION_STORAGE_KEY]);
  } catch {
    // Nothing to recover from; the next read simply falls back to detection.
  }
}
