import { useEffect, useMemo } from "react";
import { clearTranslationPreferenceCache, getTranslationPreference } from "../api/translation";
import { useTimeZonePreference } from "../core/TimeZoneContext";

export function TranslationPreferencesBootstrap() {
  const { locale } = useTimeZonePreference();
  const targetLanguage = useMemo(() => locale.replace("_", "-").toLowerCase(), [locale]);

  useEffect(() => {
    getTranslationPreference("auto", targetLanguage).catch(() => undefined);
    return () => clearTranslationPreferenceCache();
  }, [targetLanguage]);

  return null;
}
