import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { AppState, AppStateStatus } from "react-native";
import {
  FormatOptions,
  formatAbsoluteDate,
  formatAccessibleTimestamp,
  formatDateRange,
  formatRelativeTime,
  formatScheduledTime,
  formatShortTimestamp,
  getActiveTimeZone,
  getActiveLocale,
  getManualLocale,
  getManualTimeZone,
  getResolvedLocale,
  loadLocalePreference,
  loadTimeZonePreference,
  refreshTimeZoneContext,
  setManualTimeZone,
  setManualLocale,
  TimeInput
} from "./localTime";

interface TimeZoneContextValue {
  timeZone: string;
  locale: string;
  manualTimeZone: string | null;
  manualLocale: string | null;
  automatic: boolean;
  // Increments whenever the active zone changes (foreground/travel/override),
  // so consumers of the formatters re-render with fresh local values.
  revision: number;
  setTimeZoneOverride: (zone: string | null) => Promise<void>;
  setLocaleOverride: (locale: string | null) => Promise<void>;
}

const TimeZoneContext = createContext<TimeZoneContextValue>({
  timeZone: getActiveTimeZone(),
  locale: getActiveLocale(),
  manualTimeZone: null,
  manualLocale: null,
  automatic: true,
  revision: 0,
  setTimeZoneOverride: async () => undefined,
  setLocaleOverride: async () => undefined
});

export function TimeZoneProvider({ children }: { children: React.ReactNode }) {
  const [timeZone, setTimeZone] = useState(getActiveTimeZone());
  const [manual, setManual] = useState<string | null>(getManualTimeZone());
  const [locale, setLocale] = useState(getActiveLocale());
  const [manualLocale, setManualLocaleState] = useState<string | null>(getManualLocale());
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
    Promise.all([loadTimeZonePreference(), loadLocalePreference()]).then(() => {
      if (!mounted) return;
      setManual(getManualTimeZone());
      setManualLocaleState(getManualLocale());
      setLocale(getActiveLocale());
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
    setRevision((value) => value + 1);
  }, []);

  const value = useMemo<TimeZoneContextValue>(
    () => ({
      timeZone,
      locale,
      manualTimeZone: manual,
      manualLocale,
      automatic: manual == null,
      revision,
      setTimeZoneOverride,
      setLocaleOverride
    }),
    [timeZone, locale, manual, manualLocale, revision, setTimeZoneOverride, setLocaleOverride]
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
  const { timeZone, locale, revision } = useContext(TimeZoneContext);
  return useMemo(
    () => ({
      timeZone,
      locale,
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
        formatAccessibleTimestamp(value, { timeZone, locale, ...options })
    }),
    [timeZone, locale, revision]
  );
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
