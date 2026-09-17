import { useEffect, useMemo } from "react";
import { clearTranslationPreferenceCache, getTranslationPreference } from "../api/translation";
import { useTimeZonePreference } from "../core/TimeZoneContext";
import {
  AppleTranslationHost,
  clearTranslationCache,
  hydrateCloudBudget,
  hydrateTranslationCache,
  setTranslationCacheScope
} from "../services/translation";

type TranslationPreferencesBootstrapProps = {
  /** The signed-in user. Scopes the on-device translation cache. */
  userId?: string | number | null;
};

/**
 * Session-scoped translation setup, mounted once at the app root.
 *
 * Three things happen here rather than in a screen. The cache is scoped to the
 * authenticated user, so one account can never read another's translated
 * private messages off a shared device (Stage 6). The persisted cloud budget is
 * loaded, so the day's spend survives a relaunch and cannot be reset by
 * force-quitting (Stage 8). And Apple's translation host is mounted — exactly
 * once, because the native coordinator multiplexes every language pair behind a
 * single host and a second one would be a second session owner for the same
 * pairs.
 *
 * Neither hydration is awaited before the tree renders. An unhydrated cache
 * reads as a miss and an unhydrated budget reads as zero spend; both are
 * recoverable in a way that a delayed first frame is not.
 */
export function TranslationPreferencesBootstrap({ userId }: TranslationPreferencesBootstrapProps) {
  const { locale } = useTimeZonePreference();
  const targetLanguage = useMemo(() => locale.replace("_", "-").toLowerCase(), [locale]);
  const scope = userId == null ? "" : String(userId);

  useEffect(() => {
    getTranslationPreference("auto", targetLanguage).catch(() => undefined);
    return () => clearTranslationPreferenceCache();
  }, [targetLanguage]);

  useEffect(() => {
    setTranslationCacheScope(scope);
    void hydrateTranslationCache();
    void hydrateCloudBudget();
    // Unmounting this component means the session ended — App.tsx renders it
    // only while signed in. Clearing here is what makes logout a cache
    // boundary rather than a UI transition.
    return () => clearTranslationCache("session_ended");
  }, [scope]);

  return <AppleTranslationHost />;
}
