/**
 * Private Meetings — calendar arithmetic.
 *
 * Pure functions, no React, no network. They exist as their own module because
 * every bug a calendar has is a date bug, and date bugs are cheap to test and
 * expensive to find by tapping around a simulator in March.
 *
 * Two rules run through all of it:
 *
 * **A calendar day is a label, not an instant.** Days are `"YYYY-MM-DD"`
 * strings built from numbers and never round-tripped through `new Date(...)`.
 * `new Date("2035-03-14")` parses as *UTC midnight*, which is the 13th for
 * anyone west of Greenwich — so a grid built that way is off by one for half
 * the planet, and only for half the year in the places that observe DST.
 *
 * **The client does no timezone arithmetic.** The wizard collects a wall clock
 * ("09:30") a day ("2035-03-14") and an IANA zone, and sends those three
 * things. The server resolves them, because it is the only side that can:
 * resolving 2:30 AM on a spring-forward date, or picking which of two 1:30 AMs
 * a fall-back date means, is exactly the work `resolve_schedule` does. A client
 * that computed an instant would have to get DST right in twelve locales, and
 * would disagree with the server the day a zone changes its rules.
 */

/** A civil date with no instant attached. Month is 1-12, not 0-11. */
export type CivilDate = { year: number; month: number; day: number };

/** One cell of the month grid. */
export type CalendarCell = {
  /** `"YYYY-MM-DD"` — the key the server's `days` map is also keyed by. */
  key: string;
  date: CivilDate;
  /** False for the leading/trailing days borrowed from the neighbouring month. */
  inMonth: boolean;
};

/** Six weeks, always. A grid that changes height as you swipe months is a
 *  grid that makes the button under it move while your thumb is travelling. */
export const WEEKS_IN_GRID = 6;
export const DAYS_IN_WEEK = 7;

/** The server refuses a wider window; asking for one is a bug, not a retry. */
export const MAX_WINDOW_DAYS = 400;

export function pad2(value: number): string {
  return value < 10 ? `0${value}` : String(value);
}

export function dayKey(date: CivilDate): string {
  return `${date.year}-${pad2(date.month)}-${pad2(date.day)}`;
}

export function parseDayKey(key: string): CivilDate | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(key);
  if (!match) return null;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (month < 1 || month > 12) return null;
  if (day < 1 || day > daysInMonth(year, month)) return null;
  return { year, month, day };
}

/** Proleptic Gregorian, which is what every civil calendar in the app means.
 *  Divisible by 4, except centuries, except every fourth century: 2100 is not
 *  a leap year and 2400 is, and a calendar that gets that wrong is wrong for
 *  the whole of the February the user is looking at. */
export function isLeapYear(year: number): boolean {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

export function daysInMonth(year: number, month: number): number {
  if (month === 2) return isLeapYear(year) ? 29 : 28;
  return [4, 6, 9, 11].includes(month) ? 30 : 31;
}

/**
 * Day of week, 0 = Sunday, via Sakamoto's method.
 *
 * Deliberately not `new Date(y, m, d).getDay()`. That works, but it works by
 * constructing a local instant, and the one thing this module promises is that
 * no cell of the grid is ever derived from an instant.
 */
export function weekdayIndex(date: CivilDate): number {
  const shift = [0, 3, 2, 5, 0, 3, 5, 1, 4, 6, 2, 4];
  let year = date.year;
  if (date.month < 3) year -= 1;
  const value =
    year +
    Math.floor(year / 4) -
    Math.floor(year / 100) +
    Math.floor(year / 400) +
    shift[date.month - 1] +
    date.day;
  return ((value % 7) + 7) % 7;
}

/** Move a civil date by whole days without leaving civil arithmetic. */
export function addDays(date: CivilDate, delta: number): CivilDate {
  let { year, month, day } = date;
  day += delta;
  while (day > daysInMonth(year, month)) {
    day -= daysInMonth(year, month);
    month += 1;
    if (month > 12) {
      month = 1;
      year += 1;
    }
  }
  while (day < 1) {
    month -= 1;
    if (month < 1) {
      month = 12;
      year -= 1;
    }
    day += daysInMonth(year, month);
  }
  return { year, month, day };
}

/**
 * Move by whole months, clamping the day.
 *
 * 31 January plus one month is 28 (or 29) February, not 3 March. The naive
 * version overshoots into the month after the one the user asked for, so
 * pressing "next" from a 31-day month silently skips February.
 */
export function addMonths(date: CivilDate, delta: number): CivilDate {
  const total = date.year * 12 + (date.month - 1) + delta;
  const year = Math.floor(total / 12);
  const month = (total % 12) + 1;
  return { year, month, day: Math.min(date.day, daysInMonth(year, month)) };
}

/** Negative before, zero equal, positive after. Ordering only — no instants. */
export function compareDates(a: CivilDate, b: CivilDate): number {
  if (a.year !== b.year) return a.year - b.year;
  if (a.month !== b.month) return a.month - b.month;
  return a.day - b.day;
}

/**
 * The 42 cells of a month grid, Sunday-first, including the neighbouring days
 * that fill the first and last rows.
 *
 * Those neighbours are real, selectable dates. Greying them out but letting
 * them be tapped is what every calendar does, and it is what makes the 1st of
 * next month reachable without a swipe.
 */
export function monthGrid(year: number, month: number): CalendarCell[] {
  const first: CivilDate = { year, month, day: 1 };
  const lead = weekdayIndex(first);
  const start = addDays(first, -lead);
  const cells: CalendarCell[] = [];
  for (let index = 0; index < WEEKS_IN_GRID * DAYS_IN_WEEK; index += 1) {
    const date = addDays(start, index);
    cells.push({
      key: dayKey(date),
      date,
      inMonth: date.year === year && date.month === month
    });
  }
  return cells;
}

/**
 * Today, as a civil date in the device's own zone.
 *
 * `getFullYear`/`getMonth`/`getDate` are the *local* accessors, which is the
 * right question here: "what day is it where the user is standing", not "what
 * day is it in UTC". This is the only place an instant becomes a civil date.
 */
export function todayIn(now: Date = new Date()): CivilDate {
  return { year: now.getFullYear(), month: now.getMonth() + 1, day: now.getDate() };
}

/**
 * The device's IANA zone name, or `""` if the runtime cannot say.
 *
 * Empty is a legitimate answer the server understands — it means "the host
 * expressed no preference" and the meeting is read as UTC. Inventing a zone
 * would be worse: every invitee would be shown a time wrong by the fabricated
 * offset, which looks exactly like a working feature.
 */
export function deviceTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "";
  } catch {
    return "";
  }
}

/**
 * The UTC window covering a grid, padded a day at each end.
 *
 * The padding is what lets the client stay out of timezone arithmetic. The
 * server groups results into local days using the zone we pass; a cell's local
 * day can begin up to fourteen hours either side of its UTC midnight, so we
 * ask for slightly more than the grid shows and simply ignore any `days` key
 * that is not a cell. Asking for exactly the grid would drop the first or last
 * row's meetings for most of the world.
 */
export function gridWindow(cells: CalendarCell[]): { start: string; end: string } {
  const first = cells[0].date;
  const last = cells[cells.length - 1].date;
  const from = addDays(first, -1);
  const to = addDays(last, 2);
  return { start: utcMidnight(from), end: utcMidnight(to) };
}

function utcMidnight(date: CivilDate): string {
  return `${date.year}-${pad2(date.month)}-${pad2(date.day)}T00:00:00+00:00`;
}

/**
 * A wall-clock time with no date and no zone: the "09:30" half of a booking.
 */
export type WallClock = { hour: number; minute: number };

export function formatWallClock(time: WallClock): string {
  return `${pad2(time.hour)}:${pad2(time.minute)}`;
}

/**
 * The string the server parses: a naive local datetime, no offset.
 *
 * The absence of an offset is the point. Appending `Z`, or the device's
 * current offset, would pin the instant using *today's* rules for a meeting
 * that may be in 2035 — and would be an hour wrong for every booking on the
 * far side of a DST change. Naive plus a zone name is the only form that
 * survives the rules changing between now and then.
 */
export function localStartAt(date: CivilDate, time: WallClock): string {
  return `${dayKey(date)}T${formatWallClock(time)}:00`;
}

/**
 * Is this civil date + wall clock already behind the device's clock?
 *
 * A local comparison, and only a hint: it is used to grey out days the user
 * cannot book, not to decide whether a booking is legal. The server makes that
 * call, in the meeting's own zone, with a tolerance for clock skew. A phone
 * running four minutes fast must not be able to refuse a valid meeting, and a
 * phone running four minutes slow must not be able to authorise an invalid one.
 */
export function isPastLocally(
  date: CivilDate,
  time: WallClock | null,
  now: Date = new Date()
): boolean {
  const today = todayIn(now);
  const dayOrder = compareDates(date, today);
  if (dayOrder !== 0) return dayOrder < 0;
  if (!time) return false;
  const minutes = time.hour * 60 + time.minute;
  return minutes <= now.getHours() * 60 + now.getMinutes();
}

/** Duration choices, in minutes. Custom entry is handled by the wizard; these
 *  are the one-tap ones the brief names. */
export const DURATION_CHOICES = [15, 30, 45, 60, 90, 120];

export const MIN_DURATION_MINUTES = 5;
export const MAX_DURATION_MINUTES = 1440;

/**
 * The years offered by the jump-to-year picker.
 *
 * Forward-only and deliberately long. The product requirement is that a user
 * can say "2045" without swiping five hundred times, and a picker that stops
 * at "this year plus five" is the mutation this list exists to fail. One past
 * year is kept so that someone who jumped forward can get back to a meeting
 * that has since happened.
 */
export function yearChoices(now: Date = new Date(), span = 25): number[] {
  const current = todayIn(now).year;
  const years: number[] = [];
  for (let year = current - 1; year <= current + span; year += 1) {
    years.push(year);
  }
  return years;
}
