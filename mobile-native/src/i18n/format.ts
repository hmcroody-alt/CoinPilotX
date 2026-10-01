import {
  DateFormatPreference,
  FormatOptions,
  TimeInput,
  formatAbsoluteDate,
  formatAccessibleTimestamp,
  formatClockTime,
  formatDateRange,
  formatNumericDate,
  getActiveCurrency,
  getActiveTimeZone,
  isValidTimeZone,
  parseServerInstant
} from "../core/localTime";
import { getActiveIntlLocale, getActiveTranslationLocale, translate } from "./engine";
import { toIntlLocale } from "./locales";

/**
 * Locale-aware formatting.
 *
 * `localTime.ts` already knows how to render an instant correctly for a given
 * BCP-47 tag and IANA zone. What it could not do is speak the user's language:
 * its relative labels were hardcoded English ("now", "Yesterday", "your time").
 * This module closes that gap by binding those primitives to the active
 * translation locale and routing every human-readable fragment through the
 * catalogs.
 *
 * Two rules hold throughout:
 *
 *   1. Nothing here ever returns an English string that was not looked up. Every
 *      word comes from a catalog key, so adding a language adds these formats
 *      for free.
 *   2. `Intl` is used where it exists and a catalog key backs it where it does
 *      not. Hermes ships a reduced ICU on some builds, so `RelativeTimeFormat`,
 *      `ListFormat` and `NumberFormat`'s unit/compact notations are all probed
 *      at runtime and degrade to translated templates rather than throwing.
 */

/* ------------------------------------------------------------------ *
 * Locale resolution
 * ------------------------------------------------------------------ */

/**
 * The tag every formatter below defaults to: the active *translation* language
 * combined with the user's region. This is deliberately not `getActiveLocale()`
 * from localTime — that reflects the device/manual override, while this follows
 * the language the user actually chose in PulseSoc.
 */
export function activeFormattingLocale(): string {
  return getActiveIntlLocale();
}

function localeOf(options?: { locale?: string }): string {
  return options?.locale ? toIntlLocale(options.locale) : activeFormattingLocale();
}

/** Merges the active language into a localTime call that did not specify one. */
function withLocale(options?: FormatOptions): FormatOptions {
  return { ...options, locale: options?.locale || activeFormattingLocale() };
}

/* ------------------------------------------------------------------ *
 * Numbers
 * ------------------------------------------------------------------ */

export interface NumberFormatOptions {
  locale?: string;
  minimumFractionDigits?: number;
  maximumFractionDigits?: number;
  /** `1.2K` / `1,2 Tsd.` / `1.2万` — used for follower and view counts. */
  compact?: boolean;
  signDisplay?: "auto" | "never" | "always" | "exceptZero";
}

export function formatNumber(value: number, options?: NumberFormatOptions): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "";
  const locale = localeOf(options);
  try {
    return new Intl.NumberFormat(locale, {
      ...(options?.compact ? { notation: "compact", compactDisplay: "short" } : {}),
      ...(options?.minimumFractionDigits != null ? { minimumFractionDigits: options.minimumFractionDigits } : {}),
      ...(options?.maximumFractionDigits != null ? { maximumFractionDigits: options.maximumFractionDigits } : {}),
      ...(options?.signDisplay ? { signDisplay: options.signDisplay } : {})
    } as Intl.NumberFormatOptions).format(numeric);
  } catch {
    // Older ICU builds reject `notation: "compact"`. Retry plainly rather than
    // dropping the number entirely.
    try {
      return new Intl.NumberFormat(locale).format(numeric);
    } catch {
      return String(numeric);
    }
  }
}

/** Follower/like/view counts. Compact by default above a thousand. */
export function formatCount(value: number, options?: { locale?: string; compactFrom?: number }): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "";
  const threshold = options?.compactFrom ?? 10000;
  const compact = Math.abs(numeric) >= threshold;
  return formatNumber(numeric, { locale: options?.locale, compact, maximumFractionDigits: compact ? 1 : 0 });
}

export function formatPercent(
  value: number,
  options?: { locale?: string; maximumFractionDigits?: number; alreadyScaled?: boolean }
): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "";
  // Callers hand us either 0.42 or 42; `alreadyScaled` says which.
  const ratio = options?.alreadyScaled === true ? numeric / 100 : numeric;
  try {
    return new Intl.NumberFormat(localeOf(options), {
      style: "percent",
      maximumFractionDigits: options?.maximumFractionDigits ?? 0
    }).format(ratio);
  } catch {
    return `${formatNumber(Math.round(ratio * 100), options)}%`;
  }
}

export function formatCurrencyAmount(
  amount: number,
  options?: { locale?: string; currency?: string; maximumFractionDigits?: number; compact?: boolean }
): string {
  const numeric = Number(amount);
  if (!Number.isFinite(numeric)) return "";
  const locale = localeOf(options);
  const currency = options?.currency || getActiveCurrency();
  try {
    return new Intl.NumberFormat(locale, {
      style: "currency",
      currency,
      ...(options?.compact ? { notation: "compact", compactDisplay: "short" } : {}),
      ...(options?.maximumFractionDigits != null ? { maximumFractionDigits: options.maximumFractionDigits } : {})
    } as Intl.NumberFormatOptions).format(numeric);
  } catch {
    return `${currency} ${numeric.toFixed(2)}`;
  }
}

/** `1st` / `1.º` / `1er` — used for rankings and leaderboard positions. */
export function formatOrdinal(value: number, options?: { locale?: string }): string {
  const numeric = Math.trunc(Number(value));
  if (!Number.isFinite(numeric)) return "";
  const locale = localeOf(options);
  const digits = formatNumber(numeric, { locale });
  try {
    const rule = new Intl.PluralRules(locale, { type: "ordinal" }).select(numeric);
    // Only English pins a distinct suffix per ordinal category in our set; every
    // other shipped language either uses one suffix or none at all, which the
    // catalog-free digit rendering above already produces correctly.
    if (getActiveTranslationLocale() !== "en") return digits;
    const suffixes: Record<string, string> = { one: "st", two: "nd", few: "rd", other: "th" };
    return `${digits}${suffixes[rule] ?? "th"}`;
  } catch {
    return digits;
  }
}

/* ------------------------------------------------------------------ *
 * Units
 * ------------------------------------------------------------------ */

const IMPERIAL_REGIONS = new Set(["US", "LR", "MM"]);

/** True when the user's region expects miles/pounds/Fahrenheit. */
export function usesImperialUnits(locale: string = activeFormattingLocale()): boolean {
  const match = String(locale).replace(/_/g, "-").match(/-([A-Za-z]{2})(?:-|$)/);
  return IMPERIAL_REGIONS.has(String(match?.[1] || "").toUpperCase());
}

type MeasurementUnit =
  | "kilometer"
  | "meter"
  | "mile"
  | "foot"
  | "kilogram"
  | "gram"
  | "pound"
  | "ounce"
  | "celsius"
  | "fahrenheit";

const INTL_UNIT_NAME: Record<MeasurementUnit, string> = {
  kilometer: "kilometer",
  meter: "meter",
  mile: "mile",
  foot: "foot",
  kilogram: "kilogram",
  gram: "gram",
  pound: "pound",
  ounce: "ounce",
  celsius: "celsius",
  fahrenheit: "fahrenheit"
};

/**
 * A measurement with its unit. Prefers `Intl`'s unit style (which knows the
 * correct abbreviation and placement per language) and falls back to the
 * `common:units.*` catalog entries when the runtime's ICU lacks it.
 */
export function formatMeasurement(
  value: number,
  unit: MeasurementUnit,
  options?: { locale?: string; maximumFractionDigits?: number }
): string {
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return "";
  const locale = localeOf(options);
  const maximumFractionDigits = options?.maximumFractionDigits ?? 1;
  try {
    return new Intl.NumberFormat(locale, {
      style: "unit",
      unit: INTL_UNIT_NAME[unit],
      unitDisplay: "short",
      maximumFractionDigits
    } as Intl.NumberFormatOptions).format(numeric);
  } catch {
    return translate(`common:units.${unit}`, {
      count: Number(numeric.toFixed(maximumFractionDigits))
    });
  }
}

/** Distance in the user's own system — km for most of the world, miles in the US. */
export function formatDistance(meters: number, options?: { locale?: string }): string {
  const numeric = Number(meters);
  if (!Number.isFinite(numeric)) return "";
  const locale = localeOf(options);
  if (usesImperialUnits(locale)) {
    const feet = numeric * 3.28084;
    if (feet < 1000) return formatMeasurement(Math.round(feet), "foot", { locale, maximumFractionDigits: 0 });
    return formatMeasurement(numeric / 1609.344, "mile", { locale });
  }
  if (numeric < 1000) return formatMeasurement(Math.round(numeric), "meter", { locale, maximumFractionDigits: 0 });
  return formatMeasurement(numeric / 1000, "kilometer", { locale });
}

export function formatWeight(grams: number, options?: { locale?: string }): string {
  const numeric = Number(grams);
  if (!Number.isFinite(numeric)) return "";
  const locale = localeOf(options);
  if (usesImperialUnits(locale)) {
    const ounces = numeric / 28.3495;
    if (ounces < 16) return formatMeasurement(ounces, "ounce", { locale });
    return formatMeasurement(ounces / 16, "pound", { locale });
  }
  if (numeric < 1000) return formatMeasurement(Math.round(numeric), "gram", { locale, maximumFractionDigits: 0 });
  return formatMeasurement(numeric / 1000, "kilogram", { locale });
}

export function formatTemperature(celsius: number, options?: { locale?: string }): string {
  const numeric = Number(celsius);
  if (!Number.isFinite(numeric)) return "";
  const locale = localeOf(options);
  if (usesImperialUnits(locale)) {
    return formatMeasurement(Math.round(numeric * 9 / 5 + 32), "fahrenheit", { locale, maximumFractionDigits: 0 });
  }
  return formatMeasurement(Math.round(numeric), "celsius", { locale, maximumFractionDigits: 0 });
}

const BYTE_UNITS = ["byte", "kilobyte", "megabyte", "gigabyte", "terabyte"] as const;

/** Upload sizes and data-usage figures. */
export function formatFileSize(bytes: number, options?: { locale?: string }): string {
  const numeric = Number(bytes);
  if (!Number.isFinite(numeric) || numeric < 0) return "";
  let value = numeric;
  let index = 0;
  while (value >= 1024 && index < BYTE_UNITS.length - 1) {
    value /= 1024;
    index += 1;
  }
  const rounded = index === 0 ? Math.round(value) : Number(value.toFixed(value < 10 ? 1 : 0));
  return translate(`common:units.${BYTE_UNITS[index]}`, { count: rounded, locale: options?.locale });
}

/* ------------------------------------------------------------------ *
 * Lists
 * ------------------------------------------------------------------ */

/**
 * Joins names the way the language does — "A, B and C", "A、B、C", "A و B".
 * Used for "liked by" rows and group-chat participant lines.
 */
export function formatList(items: readonly string[], options?: { locale?: string; max?: number }): string {
  const values = (items || []).map((item) => String(item ?? "")).filter(Boolean);
  if (values.length === 0) return "";
  if (values.length === 1) return values[0];

  const locale = localeOf(options);
  const max = options?.max;
  const overflow = max != null && values.length > max ? values.length - max : 0;
  const visible = overflow > 0 ? values.slice(0, max) : values;

  let joined: string;
  if (visible.length === 1) {
    joined = visible[0];
  } else {
    try {
      joined = new (Intl as unknown as { ListFormat: new (l: string, o: object) => { format(v: string[]): string } })
        .ListFormat(locale, { style: "long", type: "conjunction" })
        .format(visible);
    } catch {
      const separator = translate("common:list.separator", { locale: options?.locale });
      const head = visible.slice(0, -1).join(separator);
      joined = translate("common:list.pair", {
        first: head,
        second: visible[visible.length - 1],
        locale: options?.locale
      });
    }
  }

  if (overflow <= 0) return joined;
  const more = translate("common:list.more", { count: overflow, locale: options?.locale });
  const separator = translate("common:list.separator", { locale: options?.locale });
  return `${joined}${separator}${more}`;
}

/* ------------------------------------------------------------------ *
 * Dates and times
 * ------------------------------------------------------------------ */

export function formatDate(value: TimeInput, options?: FormatOptions): string {
  return formatAbsoluteDate(value, withLocale(options));
}

export function formatTime(value: TimeInput, options?: FormatOptions): string {
  return formatClockTime(value, withLocale(options));
}

export function formatDateTime(value: TimeInput, options?: FormatOptions): string {
  const date = parseServerInstant(value);
  if (!date) return "";
  // Built from the catalog's `at` pattern rather than Intl's combined format so
  // languages that place the time first (or omit a connector, as CJK does) read
  // naturally instead of always following English word order.
  return translate("common:time.at", {
    date: formatAbsoluteDate(date, withLocale(options)),
    time: formatClockTime(date, withLocale(options))
  });
}

export function formatDay(value: TimeInput, options?: FormatOptions & { dateFormat?: DateFormatPreference }): string {
  return formatNumericDate(value, withLocale(options));
}

export function formatRange(start: TimeInput, end: TimeInput, options?: FormatOptions): string {
  return formatDateRange(start, end, withLocale(options));
}

export function formatAccessibleDateTime(value: TimeInput, options?: FormatOptions): string {
  return formatAccessibleTimestamp(value, withLocale(options));
}

/** Localized weekday and month names, for pickers and calendar headers. */
export function weekdayNames(
  style: "long" | "short" | "narrow" = "short",
  options?: { locale?: string; startOnMonday?: boolean }
): string[] {
  const locale = localeOf(options);
  const formatter = new Intl.DateTimeFormat(locale, { weekday: style, timeZone: "UTC" });
  // 2023-01-01 was a Sunday, giving a stable seven-day window to label.
  const days = Array.from({ length: 7 }, (_, index) =>
    formatter.format(new Date(Date.UTC(2023, 0, 1 + index)))
  );
  return options?.startOnMonday ? [...days.slice(1), days[0]] : days;
}

export function monthNames(style: "long" | "short" = "long", options?: { locale?: string }): string[] {
  const locale = localeOf(options);
  const formatter = new Intl.DateTimeFormat(locale, { month: style, timeZone: "UTC" });
  return Array.from({ length: 12 }, (_, index) => formatter.format(new Date(Date.UTC(2023, index, 15))));
}

/* ------------------------------------------------------------------ *
 * Relative time
 * ------------------------------------------------------------------ */

const MINUTE = 60;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;

/**
 * The compact timestamp feeds and lists use: "now", "5m", "3h", "Yesterday",
 * "2d", then a calendar date.
 *
 * This is the localized replacement for `localTime.formatRelativeTime`, whose
 * "now" and "Yesterday" were hardcoded English. `Intl.RelativeTimeFormat` is
 * deliberately *not* used here: it produces long forms ("5 minutes ago") that
 * do not fit a feed row. The compact abbreviations live in the catalogs, where
 * each language picks its own convention (`5 min`, `5分`, `٥ د`).
 */
export function formatRelative(
  value: TimeInput,
  options?: FormatOptions & { now?: Date }
): string {
  const date = parseServerInstant(value);
  if (!date) return "";
  const now = options?.now ?? new Date();
  const diffMs = now.getTime() - date.getTime();
  const timeZone = options?.timeZone || getActiveTimeZone();

  // Future instants are scheduled content, not history — show when, not "in".
  if (diffMs < 0) return formatDateTime(date, options);

  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 45) return translate("common:time.now", { locale: options?.locale });

  const minutes = Math.floor(seconds / MINUTE);
  if (minutes < 60) return translate("common:time.minutesShort", { count: minutes, locale: options?.locale });

  const hours = Math.floor(seconds / HOUR);
  if (hours < 24 && isSameCalendarDay(date, now, timeZone)) {
    return translate("common:time.hoursShort", { count: hours, locale: options?.locale });
  }
  if (isYesterday(date, now, timeZone)) return translate("common:time.yesterday", { locale: options?.locale });

  const days = Math.floor(seconds / DAY);
  if (days < 7) return translate("common:time.daysShort", { count: days, locale: options?.locale });

  const weeks = Math.floor(seconds / WEEK);
  if (weeks < 5) return translate("common:time.weeksShort", { count: weeks, locale: options?.locale });

  return formatAbsoluteDate(date, withLocale(options));
}

/**
 * The long form — "5 minutes ago", "il y a 5 minutes", "٥ دقائق مضت". Used in
 * detail views, notification rows and accessibility labels, where the compact
 * abbreviation would be ambiguous.
 *
 * `Intl.RelativeTimeFormat` handles this natively and knows every language's
 * grammar, so it leads; the catalog's `*Ago` plural families back it up.
 */
export function formatRelativeLong(value: TimeInput, options?: FormatOptions & { now?: Date }): string {
  const date = parseServerInstant(value);
  if (!date) return "";
  const now = options?.now ?? new Date();
  const diffMs = now.getTime() - date.getTime();
  const past = diffMs >= 0;
  const seconds = Math.floor(Math.abs(diffMs) / 1000);

  let amount: number;
  let unit: "minute" | "hour" | "day" | "week";
  let catalogKey: string;
  if (seconds < HOUR) {
    amount = Math.max(1, Math.floor(seconds / MINUTE));
    unit = "minute";
    catalogKey = "common:time.minutesAgo";
  } else if (seconds < DAY) {
    amount = Math.floor(seconds / HOUR);
    unit = "hour";
    catalogKey = "common:time.hoursAgo";
  } else if (seconds < 4 * WEEK) {
    amount = Math.floor(seconds / DAY);
    unit = "day";
    catalogKey = "common:time.daysAgo";
  } else {
    return formatAbsoluteDate(date, withLocale(options));
  }

  if (seconds < 45 && past) return translate("common:time.justNow", { locale: options?.locale });

  try {
    const RelativeTimeFormat = (Intl as unknown as {
      RelativeTimeFormat?: new (l: string, o: object) => { format(v: number, u: string): string };
    }).RelativeTimeFormat;
    if (RelativeTimeFormat) {
      return new RelativeTimeFormat(localeOf(options), { numeric: "auto", style: "long" })
        .format(past ? -amount : amount, unit);
    }
  } catch {
    // Fall through to the catalog templates below.
  }
  return translate(catalogKey, { count: amount, locale: options?.locale });
}

/**
 * A scheduled event's time, shown in the viewer's zone and — when the event has
 * its own zone — alongside it. Replaces `localTime.formatScheduledTime`, whose
 * "your time" connector was hardcoded English.
 */
export function formatScheduled(
  value: TimeInput,
  eventTimeZone?: string | null,
  options?: FormatOptions
): string {
  const date = parseServerInstant(value);
  if (!date) return "";
  const viewerZone = options?.timeZone || getActiveTimeZone();
  const local = formatDateTime(date, { ...options, timeZone: viewerZone });
  if (!eventTimeZone || !isValidTimeZone(eventTimeZone) || sameWallClock(date, viewerZone, eventTimeZone)) {
    return local;
  }
  const eventClock = formatClockTime(date, withLocale({ ...options, timeZone: eventTimeZone }));
  const yourTime = translate("common:time.yourTime", { locale: options?.locale });
  return `${local} ${yourTime} · ${eventClock} ${timeZoneLabel(eventTimeZone, options)}`;
}

/**
 * A human label for an IANA zone. `Intl`'s `long` zone name is localized where
 * available; otherwise the city segment is used, which reads acceptably in
 * every script since it is a proper noun.
 */
export function timeZoneLabel(zone: string, options?: { locale?: string }): string {
  const identifier = String(zone || "").trim();
  if (!identifier) return "";
  try {
    const parts = new Intl.DateTimeFormat(localeOf(options), {
      timeZone: identifier,
      timeZoneName: "long"
    }).formatToParts(new Date());
    const named = parts.find((part) => part.type === "timeZoneName")?.value;
    if (named && !/^GMT[+-]?\d*$/i.test(named)) return named;
  } catch {
    // Unknown zone or reduced ICU — fall back to the city segment.
  }
  return (identifier.split("/").pop() || identifier).replace(/_/g, " ");
}

/** `1:04` / `12:30:05` — media playback and call durations. */
export function formatDuration(totalSeconds: number, options?: { locale?: string }): string {
  const numeric = Math.max(0, Math.floor(Number(totalSeconds) || 0));
  const hours = Math.floor(numeric / HOUR);
  const minutes = Math.floor((numeric % HOUR) / MINUTE);
  const seconds = numeric % MINUTE;
  const locale = localeOf(options);
  // Digits are localized (Arabic-Indic where the locale calls for them) but the
  // colon-separated shape is universal, so it is assembled rather than looked up.
  const digits = (n: number, pad: number) => {
    const padded = String(n).padStart(pad, "0");
    try {
      return new Intl.NumberFormat(locale, { minimumIntegerDigits: pad, useGrouping: false }).format(n);
    } catch {
      return padded;
    }
  };
  if (hours > 0) return `${digits(hours, 1)}:${digits(minutes, 2)}:${digits(seconds, 2)}`;
  return `${digits(minutes, 1)}:${digits(seconds, 2)}`;
}

/* ------------------------------------------------------------------ *
 * Territory names
 * ------------------------------------------------------------------ */

/**
 * English fallbacks for the territories the region picker offers.
 *
 * Deliberately not a catalog group. Country names are the one class of string
 * where `Intl` genuinely knows more than we do — CLDR carries all ~250 of them
 * in every language, translated and kept current, and hand-copying a subset
 * into eleven catalogs would mean re-translating on every geopolitical rename.
 * This map exists only so a Hermes build without `DisplayNames` shows something
 * readable rather than the bare code "AE".
 */
const REGION_FALLBACK: Readonly<Record<string, string>> = Object.freeze({
  AE: "United Arab Emirates",
  BR: "Brazil",
  DE: "Germany",
  ES: "Spain",
  GB: "United Kingdom",
  IN: "India",
  JP: "Japan",
  KE: "Kenya",
  US: "United States"
});

type RegionDisplayNames = new (locales: string, options: { type: "region" }) => { of(code: string): string | undefined };

/**
 * The name of an ISO 3166-1 territory in the active language.
 *
 * Falls back to the uppercase code itself, which is still a usable answer — an
 * unlabelled "PT" is recognisable, an empty row is not.
 */
export function regionDisplayName(region: string, options?: { locale?: string }): string {
  const code = region.toUpperCase();
  try {
    const DisplayNames = (Intl as unknown as { DisplayNames?: RegionDisplayNames }).DisplayNames;
    if (DisplayNames) {
      const name = new DisplayNames(localeOf(options), { type: "region" }).of(code);
      // `of` echoes the input back when it has no data for that territory.
      if (name && name !== code) return name;
    }
  } catch {
    // Reduced-ICU build: fall through to the static map.
  }
  return REGION_FALLBACK[code] ?? code;
}

/* ------------------------------------------------------------------ *
 * Local helpers
 * ------------------------------------------------------------------ */

function calendarParts(date: Date, timeZone: string): { year: string; month: string; day: string; hour: string; minute: string } {
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
  return out as { year: string; month: string; day: string; hour: string; minute: string };
}

function isSameCalendarDay(a: Date, b: Date, timeZone: string): boolean {
  const left = calendarParts(a, timeZone);
  const right = calendarParts(b, timeZone);
  return left.year === right.year && left.month === right.month && left.day === right.day;
}

function isYesterday(date: Date, now: Date, timeZone: string): boolean {
  return isSameCalendarDay(date, new Date(now.getTime() - 24 * 60 * 60 * 1000), timeZone);
}

function sameWallClock(date: Date, zoneA: string, zoneB: string): boolean {
  const a = calendarParts(date, zoneA);
  const b = calendarParts(date, zoneB);
  return a.hour === b.hour && a.minute === b.minute && a.day === b.day;
}
