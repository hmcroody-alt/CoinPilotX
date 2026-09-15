/**
 * Private Meetings — human-readable names for the calendar's civil dates.
 *
 * Deliberately a separate module from `calendar.ts`. That one promises no cell
 * is ever derived from an instant; this one has to construct instants, because
 * `Intl` only names months and weekdays when handed a `Date`. Keeping the two
 * apart means the promise stays checkable: nothing in `calendar.ts` imports
 * `Date` at all except `todayIn`, and nothing here feeds a value back into the
 * grid arithmetic.
 *
 * Every instant built here is a UTC one, formatted in UTC. "March" and
 * "Wednesday" are the same words in every timezone, so pinning the zone costs
 * nothing and removes the off-by-one that `new Date(2035, 2, 1)` would
 * reintroduce for anyone whose local midnight falls on the other side of the
 * UTC one.
 *
 * `Intl` is preferred over twelve month keys and seven weekday keys per locale
 * because the app already ships eleven catalogs: a hand-translated month table
 * is 228 strings that can drift, and CLDR already has all of them right,
 * including the genitive forms Slavic languages need and the ordering RTL
 * locales expect.
 */

import { getActiveLocale } from "../../core/localTime";
import { CivilDate, DAYS_IN_WEEK, WallClock, pad2 } from "./calendar";

/** A UTC instant standing in for a civil date. Never returned to callers. */
function utcInstant(date: CivilDate): Date {
  return new Date(Date.UTC(date.year, date.month - 1, date.day, 12, 0, 0));
}

function formatter(options: Intl.DateTimeFormatOptions, locale?: string) {
  try {
    return new Intl.DateTimeFormat(locale || getActiveLocale(), {
      timeZone: "UTC",
      ...options
    });
  } catch {
    return new Intl.DateTimeFormat("en", { timeZone: "UTC", ...options });
  }
}

/** "March 2035" — the grid's heading. */
export function monthLabel(year: number, month: number, locale?: string): string {
  return formatter({ month: "long", year: "numeric" }, locale).format(
    utcInstant({ year, month, day: 1 })
  );
}

/** "March" alone, for the month picker where the year is already on screen. */
export function monthName(month: number, locale?: string): string {
  return formatter({ month: "long" }, locale).format(
    utcInstant({ year: 2001, month, day: 1 })
  );
}

/**
 * The seven column headings, Sunday first.
 *
 * 2001-01-07 was a Sunday, which makes the offsets arithmetic rather than a
 * lookup table someone has to keep in step with `weekdayIndex`.
 */
export function weekdayLabels(locale?: string): string[] {
  const shape = formatter({ weekday: "short" }, locale);
  const labels: string[] = [];
  for (let index = 0; index < DAYS_IN_WEEK; index += 1) {
    labels.push(shape.format(new Date(Date.UTC(2001, 0, 7 + index, 12, 0, 0))));
  }
  return labels;
}

/** "Wednesday, 14 March 2035" — the review step's unambiguous restatement. */
export function longDateLabel(date: CivilDate, locale?: string): string {
  return formatter(
    { weekday: "long", day: "numeric", month: "long", year: "numeric" },
    locale
  ).format(utcInstant(date));
}

/**
 * The chosen wall clock, in the locale's own convention.
 *
 * A wall clock has no date and no zone, so it cannot be formatted with the
 * instant formatters in `localTime.ts` — those would resolve it against the
 * device's zone and silently move it. An arbitrary UTC date carries the hour
 * and minute through `Intl` for the 12/24-hour decision and nothing else.
 */
export function wallClockLabel(time: WallClock, locale?: string): string {
  try {
    return formatter({ hour: "numeric", minute: "2-digit" }, locale).format(
      new Date(Date.UTC(2001, 0, 1, time.hour, time.minute, 0))
    );
  } catch {
    return `${pad2(time.hour)}:${pad2(time.minute)}`;
  }
}

/**
 * A stored instant, named in the zone the host booked it in.
 *
 * `new Date(iso).toLocaleString()` renders the canonical UTC instant in the
 * *device's* zone, which silently relabels the meeting. A host who booked
 * 09:00 Tokyo sees "20:00" on a London phone, with nothing on screen saying
 * which of those two numbers the other attendees are working from — and the
 * one they will say out loud is the host's. The zone travels beside the
 * instant precisely so it can be formatted with it.
 *
 * An empty zone is a row written before that column existed. Those genuinely
 * are only an instant, so they keep the device-zone rendering rather than
 * being told a Tokyo they were never given.
 */
export function meetingWhenLabel(iso: string, zone: string, locale?: string): string {
  if (!iso) return "";
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return "";
  const shape: Intl.DateTimeFormatOptions = {
    dateStyle: "medium",
    timeStyle: "short"
  };
  if (!zone) {
    try {
      return new Intl.DateTimeFormat(locale || getActiveLocale(), shape).format(moment);
    } catch {
      return moment.toLocaleString();
    }
  }
  try {
    const stamp = new Intl.DateTimeFormat(locale || getActiveLocale(), {
      ...shape,
      timeZone: zone
    }).format(moment);
    return `${stamp} (${timezoneLabel(zone)})`;
  } catch {
    return moment.toLocaleString();
  }
}

/**
 * The civil date and wall clock a stored instant reads as in a given zone.
 *
 * The inverse of what the wizard sends, and the only way to seed an edit with
 * what the host actually chose. `Intl` resolves it against that zone's rules
 * *for that date*, not against the device's current offset, so a meeting on
 * the far side of a changeover comes back as the clock time the host typed.
 *
 * Returns `null` rather than a guess when the instant or the zone is
 * unusable: an edit form pre-filled with a wrong time is worse than one the
 * user has to fill in, because they will not check a field that looks right.
 */
export function civilFromInstant(
  iso: string,
  zone: string
): { date: CivilDate; time: WallClock } | null {
  if (!iso) return null;
  const moment = new Date(iso);
  if (Number.isNaN(moment.getTime())) return null;
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: zone || undefined,
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      hourCycle: "h23"
    }).formatToParts(moment);
    const read = (type: string) => {
      const found = parts.find((part) => part.type === type);
      return found ? Number(found.value) : NaN;
    };
    const year = read("year");
    const month = read("month");
    const day = read("day");
    const hour = read("hour");
    const minute = read("minute");
    if ([year, month, day, hour, minute].some((value) => !Number.isFinite(value))) {
      return null;
    }
    return { date: { year, month, day }, time: { hour, minute } };
  } catch {
    return null;
  }
}

/**
 * A timezone as a person reads it: "New York" rather than "America/New_York".
 *
 * The region half is dropped because it is the half that is never in doubt —
 * nobody picking "Tokyo" needs to be told it is in Asia — and keeping it makes
 * every entry in the list start with the same four words.
 */
export function timezoneLabel(zone: string): string {
  if (!zone) return "";
  const city = zone.split("/").slice(-1)[0] || zone;
  return city.replace(/_/g, " ");
}

/**
 * The current UTC offset of a zone, as "UTC+05:30".
 *
 * A hint next to the name, not an input to anything. It is computed for *now*
 * rather than for the meeting's date on purpose: it exists so a user can tell
 * two similarly-named zones apart, and a value that shifted as they scrolled
 * through months would be worse at that job. The meeting's real offset is
 * resolved server-side from the zone name and the date.
 */
export function timezoneOffsetLabel(zone: string, now: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      timeZoneName: "longOffset"
    }).formatToParts(now);
    const found = parts.find((part) => part.type === "timeZoneName");
    return found ? found.value : "";
  } catch {
    return "";
  }
}
