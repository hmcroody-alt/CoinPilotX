import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AppState, AppStateStatus } from "react-native";

import { CORE_NAMESPACES, EXTENDED_NAMESPACES, getCatalogVersion } from "./catalogs";
import { DetectionResult, detectLocale } from "./detect";
import {
  TranslateOptions,
  activateLocale,
  getMissingKeys,
  onMissingKey,
  preloadNamespaces,
  setActiveRegion,
  translate
} from "./engine";
import {
  DEFAULT_LOCALE,
  LocaleDefinition,
  SUPPORTED_LOCALES,
  TextDirection,
  getLocaleDefinition,
  resolveSupportedLocale,
  toIntlLocale
} from "./locales";
import { applyDirectionForLocale, getLayoutDirection, isNativeDirectionStale } from "./rtl";
import {
  fetchAccountLanguage,
  loadStoredCatalogVersion,
  loadStoredLanguage,
  pushAccountLanguage,
  saveCatalogVersion,
  saveStoredLanguage
} from "./store";

/**
 * The app-wide localization provider.
 *
 * Responsibilities, in launch order:
 *   1. Read the saved preference and run the detection chain.
 *   2. Load the core catalog tier for the resolved language *before* rendering
 *      children, so the first frame is already translated and no screen ever
 *      flashes English then swaps.
 *   3. Render, then warm the extended tier in the background.
 *   4. Re-run detection when the app returns to foreground, so a user who
 *      changed their device language in Settings sees PulseSoc follow — unless
 *      they have pinned a language, which always wins.
 *
 * Changing language mid-session mutates only this provider's state. Navigation
 * state, scroll positions, in-flight requests and the auth session are all
 * untouched, which is what makes the switch feel instant.
 */

export interface I18nContextValue {
  /** Active catalog language, e.g. `"ar"`. */
  locale: string;
  /** Full BCP-47 tag for Intl formatting, e.g. `"ar-EG"`. */
  intlLocale: string;
  /** Registry entry for the active language. */
  definition: LocaleDefinition;
  /** All shipped languages, for the picker. */
  available: readonly LocaleDefinition[];
  direction: TextDirection;
  isRTL: boolean;
  /** True while the user is following their device language. */
  followingDevice: boolean;
  /** Which detection tier chose the current language. */
  detectionSource: DetectionResult["source"];
  /** True once core catalogs are loaded and it is safe to render text. */
  ready: boolean;
  /** True while a language change is being applied. */
  switching: boolean;
  /**
   * True when the native layout flag still disagrees with the rendered
   * direction; the UI offers an optional restart to finish mirroring.
   */
  restartRecommended: boolean;
  /** Bumped on every language change so memoized consumers recompute. */
  revision: number;
  t: (key: string, options?: TranslateOptions) => string;
  /** Pin a language, or pass null to follow the device again. */
  setLanguage: (locale: string | null) => Promise<void>;
  /** Translate into an arbitrary language without switching (for previews). */
  preview: (locale: string, key: string, options?: TranslateOptions) => string;
}

const fallbackDefinition = getLocaleDefinition(DEFAULT_LOCALE) as LocaleDefinition;

const I18nContext = createContext<I18nContextValue>({
  locale: DEFAULT_LOCALE,
  intlLocale: toIntlLocale(DEFAULT_LOCALE),
  definition: fallbackDefinition,
  available: SUPPORTED_LOCALES,
  direction: "ltr",
  isRTL: false,
  followingDevice: true,
  detectionSource: "fallback",
  ready: false,
  switching: false,
  restartRecommended: false,
  revision: 0,
  // The default `t` delegates to the same engine call the provider's does,
  // rather than echoing the key. The provider's job is to re-render consumers
  // when the language changes — not to own translation, which is module-level
  // state. A subtree that renders outside it (a test harness, a modal mounted
  // above the provider, an error boundary's fallback) then still shows copy in
  // the active language instead of "settings:root.title" in the UI.
  t: (key, options) => translate(key, options),
  setLanguage: async () => undefined,
  preview: (locale, key, options) => translate(key, { ...options, locale })
});

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [locale, setLocale] = useState(DEFAULT_LOCALE);
  const [followingDevice, setFollowingDevice] = useState(true);
  const [detectionSource, setDetectionSource] = useState<DetectionResult["source"]>("fallback");
  const [direction, setDirection] = useState<TextDirection>(getLayoutDirection());
  const [restartRecommended, setRestartRecommended] = useState(false);
  const [ready, setReady] = useState(false);
  const [switching, setSwitching] = useState(false);
  const [revision, setRevision] = useState(0);
  const [region, setRegion] = useState("");
  const followingDeviceRef = useRef(true);
  followingDeviceRef.current = followingDevice;

  /**
   * Applies a resolved language: loads its catalogs, flips direction, and only
   * then commits to state. Ordering matters — committing before the catalogs
   * are in memory is exactly what produces a flash of untranslated text.
   */
  const applyLocale = useCallback(
    async (next: string, options: { following: boolean; source: DetectionResult["source"]; detectedRegion?: string }) => {
      const detectedRegion = options.detectedRegion;
      if (detectedRegion !== undefined) {
        setActiveRegion(detectedRegion);
        setRegion(detectedRegion);
      }
      const active = await activateLocale(next, CORE_NAMESPACES);
      const applied = applyDirectionForLocale(active);
      setLocale(active);
      setDirection(applied.direction);
      setRestartRecommended(applied.restartRequired || isNativeDirectionStale());
      setFollowingDevice(options.following);
      setDetectionSource(options.source);
      setRevision((value) => value + 1);
      // The extended tier is not needed for the first frame, so it loads after
      // the language is live and never delays the switch.
      preloadNamespaces(active, EXTENDED_NAMESPACES).catch(() => undefined);
      return active;
    },
    []
  );

  // ---- Launch ----
  useEffect(() => {
    let mounted = true;

    (async () => {
      const stored = await loadStoredLanguage();
      const detection = detectLocale({ savedPreference: stored.language });
      if (!mounted) return;
      await applyLocale(detection.locale, {
        following: stored.language === null,
        source: detection.source,
        detectedRegion: detection.region
      });
      if (!mounted) return;
      setReady(true);

      // Record the catalog version so a future build can detect that keys
      // changed and invalidate any derived caches without a reinstall.
      const shippedVersion = getCatalogVersion(detection.locale);
      const storedVersion = await loadStoredCatalogVersion();
      if (storedVersion !== shippedVersion) await saveCatalogVersion(shippedVersion);

      // If the device has no pinned language, the account's saved language is
      // consulted last. This is the post-reinstall / new-device restore path.
      if (stored.language === null) {
        const accountLanguage = await fetchAccountLanguage();
        if (!mounted || !accountLanguage) return;
        if (accountLanguage === detection.locale) return;
        // The account preference is an explicit past choice by this user, so it
        // outranks device detection, but it does not re-pin the device — the
        // user can still switch back to following their device.
        await applyLocale(accountLanguage, { following: false, source: "account-preference" });
        await saveStoredLanguage(accountLanguage);
      }
    })().catch(() => {
      // Any failure still yields a usable app in the default language.
      if (mounted) setReady(true);
    });

    return () => {
      mounted = false;
    };
  }, [applyLocale]);

  // ---- Follow the device when the user has not pinned a language ----
  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state: AppStateStatus) => {
      if (state !== "active" || !followingDeviceRef.current) return;
      const detection = detectLocale({ savedPreference: null });
      setActiveRegion(detection.region);
      setRegion(detection.region);
      setLocale((current) => {
        if (detection.locale === current) return current;
        applyLocale(detection.locale, {
          following: true,
          source: detection.source,
          detectedRegion: detection.region
        }).catch(() => undefined);
        return current;
      });
    });
    return () => subscription.remove();
  }, [applyLocale]);

  // ---- Missing-key telemetry ----
  useEffect(() => {
    onMissingKey(({ locale: missingLocale, namespace, key }) => {
      // Kept as a dev-time signal by default; a translation-ops backend can be
      // attached here without touching any screen.
      if (typeof __DEV__ !== "undefined" && __DEV__) {
        console.info(`[i18n] fallback used for ${namespace}:${key} (${missingLocale})`);
      }
    });
    return () => onMissingKey(null);
  }, []);

  const setLanguage = useCallback(
    async (next: string | null) => {
      setSwitching(true);
      try {
        if (next === null) {
          const detection = detectLocale({ savedPreference: null });
          await saveStoredLanguage(null);
          await applyLocale(detection.locale, {
            following: true,
            source: detection.source,
            detectedRegion: detection.region
          });
          return;
        }
        const resolved = resolveSupportedLocale(next);
        if (!resolved) return;
        // Persist locally first so the choice survives even if the app is
        // killed mid-sync, then apply, then sync to the account in the
        // background where a failure is invisible to the user.
        await saveStoredLanguage(resolved);
        await applyLocale(resolved, { following: false, source: "saved-preference" });
        pushAccountLanguage(resolved).catch(() => undefined);
      } finally {
        setSwitching(false);
      }
    },
    [applyLocale]
  );

  const t = useCallback(
    (key: string, options?: TranslateOptions) => translate(key, options),
    // `revision` is not read inside, but depending on it gives every memoized
    // consumer a new function identity after a language change, which is what
    // forces re-renders of components that only capture `t`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [revision]
  );

  const preview = useCallback(
    (previewLocale: string, key: string, options?: TranslateOptions) =>
      translate(key, { ...options, locale: previewLocale }),
    []
  );

  const value = useMemo<I18nContextValue>(
    () => ({
      locale,
      intlLocale: toIntlLocale(locale, region),
      definition: getLocaleDefinition(locale) ?? fallbackDefinition,
      available: SUPPORTED_LOCALES,
      direction,
      isRTL: direction === "rtl",
      followingDevice,
      detectionSource,
      ready,
      switching,
      restartRecommended,
      revision,
      t,
      setLanguage,
      preview
    }),
    [locale, region, direction, followingDevice, detectionSource, ready, switching, restartRecommended, revision, t, setLanguage, preview]
  );

  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

/** Full localization state. Use when you need direction, locale or switching. */
export function useI18n(): I18nContextValue {
  return useContext(I18nContext);
}

/**
 * The common case: just the translate function plus the locale metadata a
 * screen typically needs.
 *
 *   const { t } = useTranslation();
 *   <Text>{t("common:actions.save")}</Text>
 */
export function useTranslation(): Pick<I18nContextValue, "t" | "locale" | "intlLocale" | "isRTL" | "direction"> {
  const { t, locale, intlLocale, isRTL, direction } = useContext(I18nContext);
  return useMemo(() => ({ t, locale, intlLocale, isRTL, direction }), [t, locale, intlLocale, isRTL, direction]);
}

/** Every key that fell back this session — powers the QA/dev coverage panel. */
export function useMissingTranslationKeys(): string[] {
  const { revision } = useI18n();
  return useMemo(() => getMissingKeys(), [revision]);
}
