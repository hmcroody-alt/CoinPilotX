import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AppState, AppStateStatus, I18nManager } from "react-native";
import {
  FormatOptions,
  formatAbsoluteDate,
  formatAccessibleTimestamp,
  formatDateRange,
  formatRelativeTime,
  formatScheduledTime,
  formatShortTimestamp,
  formatCurrency,
  formatNumericDate,
  formatPlural,
  getActiveCurrency,
  getActiveDateFormat,
  getActiveTimeZone,
  getActiveLocale,
  getManualCurrency,
  getManualDateFormat,
  getManualLocale,
  getManualTimeZone,
  getWritingDirection,
  isRtlLocale,
  getResolvedLocale,
  loadLocalePreference,
  loadRegionFormatPreferences,
  loadTimeZonePreference,
  refreshTimeZoneContext,
  setManualTimeZone,
  setManualLocale,
  setManualCurrency,
  setManualDateFormat,
  DateFormatPreference,
  TimeInput
} from "./localTime";

interface TimeZoneContextValue {
  timeZone: string;
  locale: string;
  manualTimeZone: string | null;
  manualLocale: string | null;
  currency: string;
  manualCurrency: string | null;
  dateFormat: Exclude<DateFormatPreference, "auto">;
  manualDateFormat: DateFormatPreference;
  isRTL: boolean;
  writingDirection: "rtl" | "ltr";
  rtlRestartRequired: boolean;
  automatic: boolean;
  // Increments whenever the active zone changes (foreground/travel/override),
  // so consumers of the formatters re-render with fresh local values.
  revision: number;
  setTimeZoneOverride: (zone: string | null) => Promise<void>;
  setLocaleOverride: (locale: string | null) => Promise<void>;
  setCurrencyOverride: (currency: string | null) => Promise<void>;
  setDateFormatOverride: (dateFormat: DateFormatPreference) => Promise<void>;
}

const TimeZoneContext = createContext<TimeZoneContextValue>({
  timeZone: getActiveTimeZone(),
  locale: getActiveLocale(),
  manualTimeZone: null,
  manualLocale: null,
  currency: getActiveCurrency(),
  manualCurrency: null,
  dateFormat: getActiveDateFormat(),
  manualDateFormat: "auto",
  isRTL: isRtlLocale(),
  writingDirection: getWritingDirection(),
  rtlRestartRequired: false,
  automatic: true,
  revision: 0,
  setTimeZoneOverride: async () => undefined,
  setLocaleOverride: async () => undefined,
  setCurrencyOverride: async () => undefined,
  setDateFormatOverride: async () => undefined
});

export function TimeZoneProvider({ children }: { children: React.ReactNode }) {
  const [timeZone, setTimeZone] = useState(getActiveTimeZone());
  const [manual, setManual] = useState<string | null>(getManualTimeZone());
  const [locale, setLocale] = useState(getActiveLocale());
  const [manualLocale, setManualLocaleState] = useState<string | null>(getManualLocale());
  const [currency, setCurrency] = useState(getActiveCurrency());
  const [manualCurrency, setManualCurrencyState] = useState<string | null>(getManualCurrency());
  const [dateFormat, setDateFormat] = useState(getActiveDateFormat());
  const [manualDateFormat, setManualDateFormatState] = useState<DateFormatPreference>(getManualDateFormat());
  const [rtlRestartRequired, setRtlRestartRequired] = useState(false);
  const [revision, setRevision] = useState(0);
  const timeZoneRef = useRef(timeZone);
  timeZoneRef.current = timeZone;

  const applyActiveZone = useCallback(() => {
    const active = getActiveTimeZone();
    if (active !== timeZoneRef.current) {
      setTimeZone(active);
      setRevision((value) => value + 1);
    }
  }, []);

  useEffect(() => {
    let mounted = true;
    Promise.all([loadTimeZonePreference(), loadLocalePreference(), loadRegionFormatPreferences()]).then(() => {
      if (!mounted) return;
      setManual(getManualTimeZone());
      setManualLocaleState(getManualLocale());
      setLocale(getActiveLocale());
      setManualCurrencyState(getManualCurrency());
      setCurrency(getActiveCurrency());
      setManualDateFormatState(getManualDateFormat());
      setDateFormat(getActiveDateFormat());
      applyNativeLayoutDirection(getActiveLocale(), setRtlRestartRequired);
      applyActiveZone();
    });
    return () => {
      mounted = false;
    };
  }, [applyActiveZone]);

  useEffect(() => {
    // Detect travel / device time-zone changes when the app returns to foreground.
    const subscription = AppState.addEventListener("change", (state: AppStateStatus) => {
      if (state === "active") {
        refreshTimeZoneContext();
        setLocale(getActiveLocale());
        setCurrency(getActiveCurrency());
        setDateFormat(getActiveDateFormat());
        applyNativeLayoutDirection(getActiveLocale(), setRtlRestartRequired);
        applyActiveZone();
      }
    });
    return () => subscription.remove();
  }, [applyActiveZone]);

  const setTimeZoneOverride = useCallback(
    async (zone: string | null) => {
      await setManualTimeZone(zone);
      setManual(getManualTimeZone());
      applyActiveZone();
      // Override may resolve to the same zone but preference display still changed.
      setRevision((value) => value + 1);
    },
    [applyActiveZone]
  );

  const setLocaleOverride = useCallback(async (nextLocale: string | null) => {
    await setManualLocale(nextLocale);
    setManualLocaleState(getManualLocale());
    setLocale(getActiveLocale());
    setCurrency(getActiveCurrency());
    setDateFormat(getActiveDateFormat());
    applyNativeLayoutDirection(getActiveLocale(), setRtlRestartRequired);
    setRevision((value) => value + 1);
  }, []);

  const setCurrencyOverride = useCallback(async (nextCurrency: string | null) => {
    await setManualCurrency(nextCurrency);
    setManualCurrencyState(getManualCurrency());
    setCurrency(getActiveCurrency());
    setRevision((value) => value + 1);
  }, []);

  const setDateFormatOverride = useCallback(async (nextDateFormat: DateFormatPreference) => {
    await setManualDateFormat(nextDateFormat);
    setManualDateFormatState(getManualDateFormat());
    setDateFormat(getActiveDateFormat());
    setRevision((value) => value + 1);
  }, []);

  const value = useMemo<TimeZoneContextValue>(
    () => ({
      timeZone,
      locale,
      manualTimeZone: manual,
      manualLocale,
      currency,
      manualCurrency,
      dateFormat,
      manualDateFormat,
      isRTL: isRtlLocale(locale),
      writingDirection: getWritingDirection(locale),
      rtlRestartRequired,
      automatic: manual == null,
      revision,
      setTimeZoneOverride,
      setLocaleOverride,
      setCurrencyOverride,
      setDateFormatOverride
    }),
    [timeZone, locale, manual, manualLocale, currency, manualCurrency, dateFormat, manualDateFormat, rtlRestartRequired, revision, setTimeZoneOverride, setLocaleOverride, setCurrencyOverride, setDateFormatOverride]
  );

  return <TimeZoneContext.Provider value={value}>{children}</TimeZoneContext.Provider>;
}

export function useTimeZonePreference() {
  return useContext(TimeZoneContext);
}

/**
 * Formatters bound to the current time-zone context. The `revision` dependency
 * ensures memoized callers recompute after a zone change.
 */
export function useLocalTime() {
  const { timeZone, locale, currency, dateFormat, revision } = useContext(TimeZoneContext);
  return useMemo(
    () => ({
      timeZone,
      locale,
      currency,
      dateFormat,
      revision,
      short: (value: TimeInput, options?: FormatOptions) => formatShortTimestamp(value, { timeZone, locale, ...options }),
      relative: (value: TimeInput, now?: Date, options?: FormatOptions) =>
        formatRelativeTime(value, now ?? new Date(), { timeZone, locale, ...options }),
      absolute: (value: TimeInput, options?: FormatOptions) => formatAbsoluteDate(value, { timeZone, locale, ...options }),
      range: (start: TimeInput, end: TimeInput, options?: FormatOptions) =>
        formatDateRange(start, end, { timeZone, locale, ...options }),
      scheduled: (value: TimeInput, eventTimeZone?: string | null, options?: FormatOptions) =>
        formatScheduledTime(value, eventTimeZone, { timeZone, locale, ...options }),
      accessible: (value: TimeInput, options?: FormatOptions) =>
        formatAccessibleTimestamp(value, { timeZone, locale, ...options }),
      money: (amount: number, options?: { currency?: string; locale?: string; minimumFractionDigits?: number; maximumFractionDigits?: number }) =>
        formatCurrency(amount, { currency, locale, ...options }),
      numericDate: (value: TimeInput, options?: FormatOptions & { dateFormat?: DateFormatPreference }) =>
        formatNumericDate(value, { timeZone, locale, dateFormat, ...options }),
      plural: (count: number, forms: Partial<Record<Intl.LDMLPluralRule, string>> & { other: string }, options?: { locale?: string; includeCount?: boolean }) =>
        formatPlural(count, forms, { locale, ...options })
    }),
    [timeZone, locale, currency, dateFormat, revision]
  );
}

function applyNativeLayoutDirection(locale: string, setRestartRequired: (required: boolean) => void) {
  const desired = isRtlLocale(locale);
  I18nManager.allowRTL(true);
  if (I18nManager.isRTL === desired) {
    setRestartRequired(false);
    return;
  }
  I18nManager.forceRTL(desired);
  setRestartRequired(true);
}

/**
 * Live relative label that recalculates on an interval while the screen is
 * mounted, so "1m" never lingers as it ages. Coarser cadence for older content.
 */
export function useRelativeTime(value: TimeInput, options?: FormatOptions): string {
  const { timeZone, locale, revision } = useContext(TimeZoneContext);
  const [label, setLabel] = useState(() => formatRelativeTime(value, new Date(), { timeZone, locale, ...options }));

  useEffect(() => {
    let cancelled = false;
    const tick = () => {
      if (!cancelled) setLabel(formatRelativeTime(value, new Date(), { timeZone, locale, ...options }));
    };
    tick();
    const interval = setInterval(tick, 30_000);
    return () => {
      cancelled = true;
      clearInterval(interval);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value, timeZone, locale, revision]);

  return label;
}
