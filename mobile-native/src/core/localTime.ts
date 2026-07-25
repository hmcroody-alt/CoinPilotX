import AsyncStorage from "@react-native-async-storage/async-storage";

/**
 * Centralized date-and-time system for PulseSOC Native.
 *
 * Core principle: every timestamp is an absolute UTC instant; it is only
 * converted into the viewer's local time zone at display time. Conversion,
 * daylight-saving and locale rules are delegated to the JS engine's Intl
 * implementation so we never hard-code offsets or perform manual arithmetic.
 */

export type TimeInput = string | number | Date | null | undefined;

const OVERRIDE_STORAGE_KEY = "pulsesoc.localtime.timezoneOverride.v1";
const LOCALE_OVERRIDE_STORAGE_KEY = "pulsesoc.locale.override.v1";

// The user's manual IANA override, or null when following the device ("Automatic").
let manualTimeZone: string | null = null;
// Cached device zone so conversions don't re-query Intl on every format call.
let cachedDeviceTimeZone: string | null = null;
let cachedLocale: string | null = null;
let manualLocale: string | null = null;

export function getDeviceTimeZone(): string {
  if (cachedDeviceTimeZone) return cachedDeviceTimeZone;
  try {
    const resolved = new Intl.DateTimeFormat().resolvedOptions().timeZone;
    cachedDeviceTimeZone = resolved || "UTC";
  } catch {
    cachedDeviceTimeZone = "UTC";
  }
  return cachedDeviceTimeZone;
}

export function getResolvedLocale(): string {
  if (cachedLocale) return cachedLocale;
  try {
    cachedLocale = new Intl.DateTimeFormat().resolvedOptions().locale || "en-US";
  } catch {
    cachedLocale = "en-US";
  }
  return cachedLocale;
}

export function getActiveLocale(): string {
  return manualLocale || getResolvedLocale();
}

export function getManualLocale(): string | null {
  return manualLocale;
}

export function getManualTimeZone(): string | null {
  return manualTimeZone;
}

/** The zone all display conversions use: manual override, else device, else UTC. */
export function getActiveTimeZone(): string {
  return manualTimeZone || getDeviceTimeZone();
}

/** Alias matching the mission's recommended API. */
export function getCurrentUserTimeZone(): string {
  return getActiveTimeZone();
}

/**
 * Re-read the device time zone (call on foreground / resume). Returns the
 * active zone after refresh so callers can detect a change and re-render.
 */
export function refreshTimeZoneContext(): string {
  cachedDeviceTimeZone = null;
  cachedLocale = null;
  getDeviceTimeZone();
  getResolvedLocale();
  return getActiveTimeZone();
}

export async function loadTimeZonePreference(): Promise<string | null> {
  try {
    const stored = await AsyncStorage.getItem(OVERRIDE_STORAGE_KEY);
    manualTimeZone = stored && isValidTimeZone(stored) ? stored : null;
  } catch {
    manualTimeZone = null;
  }
  return manualTimeZone;
}

export async function loadLocalePreference(): Promise<string | null> {
  try {
    const stored = await AsyncStorage.getItem(LOCALE_OVERRIDE_STORAGE_KEY);
    manualLocale = stored && isValidLocale(stored) ? stored : null;
  } catch {
    manualLocale = null;
  }
  return manualLocale;
}

export async function setManualTimeZone(zone: string | null): Promise<string | null> {
  if (zone && isValidTimeZone(zone)) {
    manualTimeZone = zone;
    await AsyncStorage.setItem(OVERRIDE_STORAGE_KEY, zone).catch(() => undefined);
  } else {
    manualTimeZone = null;
    await AsyncStorage.removeItem(OVERRIDE_STORAGE_KEY).catch(() => undefined);
  }
  return manualTimeZone;
}

export async function setManualLocale(locale: string | null): Promise<string | null> {
  if (locale && isValidLocale(locale)) {
    manualLocale = locale;
    await AsyncStorage.setItem(LOCALE_OVERRIDE_STORAGE_KEY, locale).catch(() => undefined);
  } else {
    manualLocale = null;
    await AsyncStorage.removeItem(LOCALE_OVERRIDE_STORAGE_KEY).catch(() => undefined);
  }
  return manualLocale;
}

export function isValidLocale(locale: string): boolean {
  if (!locale || locale.length > 35) return false;
  try {
    new Intl.DateTimeFormat(locale).format(new Date());
    return true;
  } catch {
    return false;
  }
}

export function isValidTimeZone(zone: string): boolean {
  if (!zone) return false;
  try {
    new Intl.DateTimeFormat("en-US", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

/**
 * Parse a server value into an absolute instant. Values carrying an explicit
 * `Z` or numeric offset are unambiguous. A bare ISO string with no offset is a
 * legacy timestamp and is normalized as UTC (never as device-local), matching
 * the backend contract. Returns null for unparseable input.
 */
export function parseServerInstant(value: TimeInput): Date | null {
  if (value == null || value === "") return null;
  if (value instanceof Date) return Number.isNaN(value.getTime()) ? null : value;
  if (typeof value === "number") {
    const fromNumber = new Date(value);
    return Number.isNaN(fromNumber.getTime()) ? null : fromNumber;
  }
  const raw = String(value).trim();
  if (!raw) return null;
  const normalized = needsUtcAssumption(raw) ? `${raw.replace(" ", "T")}Z` : raw;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

// True for date-time strings that carry no zone designator (legacy rows).
function needsUtcAssumption(raw: string): boolean {
  const isoLike = /^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}/.test(raw);
  if (!isoLike) return false;
  return !/[zZ]|[+-]\d{2}:?\d{2}$/.test(raw);
}

export interface FormatOptions {
  timeZone?: string;
  locale?: string;
  hour12?: boolean;
  withTime?: boolean;
  withYear?: boolean | "auto";
  withZoneName?: boolean;
}

function resolve(options?: FormatOptions) {
  return {
    timeZone: options?.timeZone || getActiveTimeZone(),
    locale: options?.locale || getActiveLocale()
  };
}

/** Localized calendar date, optionally with time. Adds the year automatically
 *  when the instant is not in the viewer's current year. */
export function formatAbsoluteDate(value: TimeInput, options?: FormatOptions): string {
  const date = parseServerInstant(value);
  if (!date) return "";
  const { timeZone, locale } = resolve(options);
  const includeYear =
    options?.withYear === true ||
    (options?.withYear !== false && !isSameYear(date, new Date(), timeZone));
  const intlOptions: Intl.DateTimeFormatOptions = {
    timeZone,
    month: "short",
    day: "numeric",
    ...(includeYear ? { year: "numeric" } : {}),
    ...(options?.withTime ? { hour: "numeric", minute: "2-digit" } : {}),
    ...(options?.hour12 != null ? { hour12: options.hour12 } : {}),
    ...(options?.withZoneName ? { timeZoneName: "short" } : {})
  };
  return new Intl.DateTimeFormat(locale, intlOptions).format(date);
}

/** Just the clock time in the viewer's zone (respects 12/24h locale default). */
export function formatClockTime(value: TimeInput, options?: FormatOptions): string {
  const date = parseServerInstant(value);
  if (!date) return "";
  const { timeZone, locale } = resolve(options);
  return new Intl.DateTimeFormat(locale, {
    timeZone,
    hour: "numeric",
    minute: "2-digit",
    ...(options?.hour12 != null ? { hour12: options.hour12 } : {}),
    ...(options?.withZoneName ? { timeZoneName: "short" } : {})
  }).format(date);
}

/**
 * Relative label for recent activity ("now", "2m", "3h", "Yesterday") and a
 * localized calendar date for anything older than a day.
 */
export function formatRelativeTime(value: TimeInput, now: Date = new Date(), options?: FormatOptions): string {
  const date = parseServerInstant(value);
  if (!date) return "";
  const diffMs = now.getTime() - date.getTime();
  if (diffMs < 0) {
    // Future instant — fall through to a scheduled-style absolute label.
    return formatAbsoluteDate(date, { ...options, withTime: true });
  }
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 45) return "now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  const { timeZone } = resolve(options);
  if (hours < 24 && isSameCalendarDay(date, now, timeZone)) return `${hours}h`;
  if (isYesterday(date, now, timeZone)) return "Yesterday";
  const days = Math.floor(hours / 24);
  if (days < 7) return `${days}d`;
  return formatAbsoluteDate(date, options);
}

/** Short timestamp used across feeds/lists — relative when recent, otherwise a
 *  localized calendar date. Drop-in for the legacy formatShortTime. */
export function formatShortTimestamp(value: TimeInput, options?: FormatOptions): string {
  return formatRelativeTime(value, new Date(), options);
}

/** A verbose, unambiguous label for accessibility (VoiceOver) and detail views. */
export function formatAccessibleTimestamp(value: TimeInput, options?: FormatOptions): string {
  const date = parseServerInstant(value);
  if (!date) return "";
  const { timeZone, locale } = resolve(options);
  return new Intl.DateTimeFormat(locale, {
    timeZone,
    weekday: "long",
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
    ...(options?.hour12 != null ? { hour12: options.hour12 } : {})
  }).format(date);
}

/** Localized start–end range, collapsing shared date/zone parts where possible. */
export function formatDateRange(start: TimeInput, end: TimeInput, options?: FormatOptions): string {
  const startDate = parseServerInstant(start);
  const endDate = parseServerInstant(end);
  if (!startDate && !endDate) return "";
  if (!startDate) return formatAbsoluteDate(endDate, { ...options, withTime: true });
  if (!endDate) return formatAbsoluteDate(startDate, { ...options, withTime: true });
  const { timeZone } = resolve(options);
  if (isSameCalendarDay(startDate, endDate, timeZone)) {
    return `${formatAbsoluteDate(startDate, { ...options, withTime: true })} – ${formatClockTime(endDate, options)}`;
  }
  return `${formatAbsoluteDate(startDate, { ...options, withTime: true })} – ${formatAbsoluteDate(endDate, { ...options, withTime: true })}`;
}

/**
 * Scheduled (wall-clock) event display. The instant is shown in the viewer's
 * local time; when the event's own IANA zone differs it is appended so users
 * see both, e.g. "1:00 PM your time · 4:00 PM New York".
 */
export function formatScheduledTime(value: TimeInput, eventTimeZone?: string | null, options?: FormatOptions): string {
  const date = parseServerInstant(value);
  if (!date) return "";
  const viewerZone = options?.timeZone || getActiveTimeZone();
  const local = formatAbsoluteDate(date, { ...options, withTime: true, timeZone: viewerZone });
  if (!eventTimeZone || !isValidTimeZone(eventTimeZone) || sameZoneWallClock(date, viewerZone, eventTimeZone)) {
    return local;
  }
  const eventClock = formatClockTime(date, { ...options, timeZone: eventTimeZone });
  return `${local} your time · ${eventClock} ${shortZoneLabel(eventTimeZone)}`;
}

function shortZoneLabel(zone: string): string {
  const city = zone.split("/").pop() || zone;
  return city.replace(/_/g, " ");
}

function partsFor(date: Date, timeZone: string): Record<string, string> {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false
  }).formatToParts(date);
  const out: Record<string, string> = {};
  for (const part of parts) out[part.type] = part.value;
  return out;
}

function isSameYear(a: Date, b: Date, timeZone: string): boolean {
  return partsFor(a, timeZone).year === partsFor(b, timeZone).year;
}

function isSameCalendarDay(a: Date, b: Date, timeZone: string): boolean {
  const pa = partsFor(a, timeZone);
  const pb = partsFor(b, timeZone);
  return pa.year === pb.year && pa.month === pb.month && pa.day === pb.day;
}

function isYesterday(date: Date, now: Date, timeZone: string): boolean {
  const yesterday = new Date(now.getTime() - 24 * 60 * 60 * 1000);
  return isSameCalendarDay(date, yesterday, timeZone);
}

function sameZoneWallClock(date: Date, zoneA: string, zoneB: string): boolean {
  const a = partsFor(date, zoneA);
  const b = partsFor(date, zoneB);
  return a.hour === b.hour && a.minute === b.minute && a.day === b.day;
}
