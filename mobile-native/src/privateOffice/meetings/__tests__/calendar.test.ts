/**
 * The month grid, and the two rules it exists to keep.
 *
 * A calendar day is a label, not an instant — so nothing here is allowed to be
 * derived from `new Date("YYYY-MM-DD")`, which parses as UTC midnight and is
 * the previous day for everyone west of Greenwich. The tests that pin this run
 * under a deliberately hostile fake timezone.
 *
 * And the client does no timezone arithmetic — it sends a civil date, a wall
 * clock and a zone name, and the server resolves them. `localStartAt` must
 * therefore emit a string with no offset on it at all.
 */

import {
  addDays,
  addMonths,
  compareDates,
  dayKey,
  DAYS_IN_WEEK,
  daysInMonth,
  gridWindow,
  isLeapYear,
  isPastLocally,
  localStartAt,
  monthGrid,
  parseDayKey,
  todayIn,
  WEEKS_IN_GRID,
  weekdayIndex,
  yearChoices
} from "../calendar";

describe("leap years", () => {
  it("follows the century rule, not just the divisible-by-four one", () => {
    expect(isLeapYear(2032)).toBe(true);
    expect(isLeapYear(2033)).toBe(false);
    expect(isLeapYear(2100)).toBe(false); // century, not divisible by 400
    expect(isLeapYear(2400)).toBe(true); // century, divisible by 400
  });

  it("gives February the right length in each case", () => {
    expect(daysInMonth(2048, 2)).toBe(29);
    expect(daysInMonth(2047, 2)).toBe(28);
    expect(daysInMonth(2100, 2)).toBe(28);
    expect(daysInMonth(2400, 2)).toBe(29);
  });

  it("knows the short months", () => {
    expect([4, 6, 9, 11].map((m) => daysInMonth(2035, m))).toEqual([30, 30, 30, 30]);
    expect([1, 3, 5, 7, 8, 10, 12].map((m) => daysInMonth(2035, m))).toEqual([
      31, 31, 31, 31, 31, 31, 31
    ]);
  });
});

describe("weekdayIndex", () => {
  it("agrees with the platform Date for a spread of known days", () => {
    const samples = [
      { year: 2026, month: 9, day: 14 },
      { year: 2030, month: 1, day: 1 },
      { year: 2032, month: 2, day: 29 },
      { year: 2035, month: 3, day: 14 },
      { year: 2045, month: 12, day: 31 },
      { year: 2100, month: 3, day: 1 }
    ];
    for (const date of samples) {
      // The platform Date is only a reference here, built from *local*
      // components so no UTC shift creeps into the comparison.
      const reference = new Date(date.year, date.month - 1, date.day).getDay();
      expect(weekdayIndex(date)).toBe(reference);
    }
  });
});

describe("addDays", () => {
  it("crosses a month boundary", () => {
    expect(addDays({ year: 2035, month: 1, day: 31 }, 1)).toEqual({
      year: 2035,
      month: 2,
      day: 1
    });
  });

  it("crosses a year boundary in both directions", () => {
    expect(addDays({ year: 2035, month: 12, day: 31 }, 1)).toEqual({
      year: 2036,
      month: 1,
      day: 1
    });
    expect(addDays({ year: 2036, month: 1, day: 1 }, -1)).toEqual({
      year: 2035,
      month: 12,
      day: 31
    });
  });

  it("walks through a leap day and a non-leap February", () => {
    expect(addDays({ year: 2048, month: 2, day: 28 }, 1)).toEqual({
      year: 2048,
      month: 2,
      day: 29
    });
    expect(addDays({ year: 2047, month: 2, day: 28 }, 1)).toEqual({
      year: 2047,
      month: 3,
      day: 1
    });
  });

  it("survives a multi-month jump", () => {
    expect(addDays({ year: 2035, month: 1, day: 1 }, 365)).toEqual({
      year: 2036,
      month: 1,
      day: 1
    });
  });
});

describe("addMonths", () => {
  it("clamps rather than overshooting into the following month", () => {
    // The mutation: 31 Jan + 1 month landing on 3 March, which makes the
    // "next month" button skip February entirely.
    expect(addMonths({ year: 2035, month: 1, day: 31 }, 1)).toEqual({
      year: 2035,
      month: 2,
      day: 28
    });
    expect(addMonths({ year: 2048, month: 1, day: 31 }, 1)).toEqual({
      year: 2048,
      month: 2,
      day: 29
    });
  });

  it("steps backwards across a year boundary", () => {
    expect(addMonths({ year: 2035, month: 1, day: 15 }, -1)).toEqual({
      year: 2034,
      month: 12,
      day: 15
    });
  });

  it("jumps a decade without drifting", () => {
    expect(addMonths({ year: 2035, month: 6, day: 10 }, 120)).toEqual({
      year: 2045,
      month: 6,
      day: 10
    });
  });
});

describe("monthGrid", () => {
  it("is always six full weeks", () => {
    for (const [year, month] of [
      [2035, 2],
      [2035, 3],
      [2048, 2],
      [2045, 12]
    ]) {
      expect(monthGrid(year, month)).toHaveLength(WEEKS_IN_GRID * DAYS_IN_WEEK);
    }
  });

  it("starts on a Sunday and ends on a Saturday", () => {
    const cells = monthGrid(2035, 3);
    expect(weekdayIndex(cells[0].date)).toBe(0);
    expect(weekdayIndex(cells[cells.length - 1].date)).toBe(6);
  });

  it("contains every day of the month exactly once", () => {
    const cells = monthGrid(2048, 2);
    const own = cells.filter((cell) => cell.inMonth).map((cell) => cell.date.day);
    expect(own).toEqual(Array.from({ length: 29 }, (_, i) => i + 1));
  });

  it("marks the borrowed neighbours as out of month but keeps them real", () => {
    const cells = monthGrid(2035, 3); // 1 March 2035 is a Thursday
    const leading = cells.filter((cell) => !cell.inMonth && cell.date.month === 2);
    expect(leading.map((cell) => cell.key)).toEqual([
      "2035-02-25",
      "2035-02-26",
      "2035-02-27",
      "2035-02-28"
    ]);
  });

  it("is continuous across its whole span", () => {
    const cells = monthGrid(2035, 12);
    for (let i = 1; i < cells.length; i += 1) {
      expect(cells[i].key).toBe(dayKey(addDays(cells[i - 1].date, 1)));
    }
  });

  it("handles a month that begins on a Sunday without a blank first row", () => {
    // 2035-04-01 is a Sunday; the grid must not open with a wasted week.
    const cells = monthGrid(2035, 4);
    expect(cells[0].key).toBe("2035-04-01");
    expect(cells[0].inMonth).toBe(true);
  });

  it("reaches the far future unchanged", () => {
    const cells = monthGrid(2045, 3);
    expect(cells.some((cell) => cell.key === "2045-03-19")).toBe(true);
  });
});

describe("dayKey / parseDayKey", () => {
  it("round-trips", () => {
    const date = { year: 2045, month: 3, day: 7 };
    expect(parseDayKey(dayKey(date))).toEqual(date);
    expect(dayKey(date)).toBe("2045-03-07");
  });

  it("refuses dates that do not exist rather than rolling them over", () => {
    expect(parseDayKey("2047-02-29")).toBeNull();
    expect(parseDayKey("2035-13-01")).toBeNull();
    expect(parseDayKey("2035-00-10")).toBeNull();
    expect(parseDayKey("2035-04-31")).toBeNull();
    expect(parseDayKey("not-a-date")).toBeNull();
  });

  it("accepts the leap day that does exist", () => {
    expect(parseDayKey("2048-02-29")).toEqual({ year: 2048, month: 2, day: 29 });
  });
});

describe("the grid is not built from instants", () => {
  /**
   * The bug this guards: `new Date("2035-03-01")` is UTC midnight, which is
   * 2035-02-28 19:00 in New York. A grid derived from that is one day out for
   * every user west of Greenwich — and the tests pass in London.
   */
  const REAL_OFFSET = Date.prototype.getTimezoneOffset;

  afterEach(() => {
    Date.prototype.getTimezoneOffset = REAL_OFFSET;
  });

  it("produces the same cells regardless of the device offset", () => {
    const baseline = monthGrid(2035, 3).map((cell) => cell.key);
    for (const offsetMinutes of [-840, -720, -300, 0, 330, 720, 840]) {
      Date.prototype.getTimezoneOffset = () => offsetMinutes;
      expect(monthGrid(2035, 3).map((cell) => cell.key)).toEqual(baseline);
    }
  });

  it("never renders a key that disagrees with its own civil date", () => {
    for (const cell of monthGrid(2035, 3)) {
      expect(parseDayKey(cell.key)).toEqual(cell.date);
    }
  });
});

describe("gridWindow", () => {
  it("covers the whole grid with a day of slack at each end", () => {
    const cells = monthGrid(2035, 3);
    const { start, end } = gridWindow(cells);
    expect(cells[0].key).toBe("2035-02-25");
    expect(cells[41].key).toBe("2035-04-07");
    // A day before the first cell, and — because the end is exclusive — a full
    // day after the last one. A cell's local day can start fourteen hours
    // either side of its UTC midnight, so the slack is what keeps the first
    // and last rows from losing their meetings west and east of Greenwich.
    expect(start).toBe("2035-02-24T00:00:00+00:00");
    expect(end).toBe("2035-04-09T00:00:00+00:00");
  });

  it("stays far inside the server's maximum span", () => {
    const { start, end } = gridWindow(monthGrid(2045, 12));
    const days = (Date.parse(end) - Date.parse(start)) / 86400000;
    expect(days).toBeLessThan(60);
  });

  it("asks for more than the grid, never less", () => {
    const cells = monthGrid(2036, 1);
    const { start, end } = gridWindow(cells);
    expect(start < `${cells[0].key}T00:00:00+00:00`).toBe(true);
    expect(end > `${cells[cells.length - 1].key}T23:59:59+00:00`).toBe(true);
  });
});

describe("localStartAt", () => {
  it("carries no offset, because the server owns the resolution", () => {
    const at = localStartAt({ year: 2035, month: 3, day: 14 }, { hour: 9, minute: 30 });
    expect(at).toBe("2035-03-14T09:30:00");
    expect(at).not.toMatch(/[Zz]$/);
    expect(at).not.toMatch(/[+-]\d{2}:\d{2}$/);
  });

  it("pads single digits so the server's parser accepts it", () => {
    expect(localStartAt({ year: 2035, month: 3, day: 4 }, { hour: 9, minute: 5 })).toBe(
      "2035-03-04T09:05:00"
    );
  });

  it("expresses a time inside a DST gap verbatim rather than guessing", () => {
    // 2:30 AM does not exist on 2032-03-14 in New York. The client's job is to
    // say what the user picked; resolving it is the server's.
    expect(localStartAt({ year: 2032, month: 3, day: 14 }, { hour: 2, minute: 30 })).toBe(
      "2032-03-14T02:30:00"
    );
  });
});

describe("isPastLocally", () => {
  const now = new Date(2026, 8, 14, 13, 30); // 14 Sep 2026, 13:30 local

  it("is a day comparison when the days differ", () => {
    expect(isPastLocally({ year: 2026, month: 9, day: 13 }, null, now)).toBe(true);
    expect(isPastLocally({ year: 2026, month: 9, day: 15 }, null, now)).toBe(false);
  });

  it("treats today as bookable when no time has been picked yet", () => {
    expect(isPastLocally({ year: 2026, month: 9, day: 14 }, null, now)).toBe(false);
  });

  it("compares the clock once a time is picked", () => {
    const today = { year: 2026, month: 9, day: 14 };
    expect(isPastLocally(today, { hour: 12, minute: 0 }, now)).toBe(true);
    expect(isPastLocally(today, { hour: 14, minute: 0 }, now)).toBe(false);
  });

  it("says the far future is not past", () => {
    expect(isPastLocally({ year: 2045, month: 3, day: 19 }, { hour: 9, minute: 0 }, now)).toBe(
      false
    );
  });
});

describe("yearChoices", () => {
  it("reaches decades ahead, not a handful of years", () => {
    // The mutation: future-year navigation limited to the current year.
    const years = yearChoices(new Date(2026, 0, 1));
    expect(years).toContain(2030);
    expect(years).toContain(2035);
    expect(years).toContain(2045);
  });

  it("keeps one year of hindsight so a past meeting stays reachable", () => {
    expect(yearChoices(new Date(2026, 0, 1))[0]).toBe(2025);
  });

  it("is strictly ascending with no gaps", () => {
    const years = yearChoices(new Date(2026, 0, 1));
    for (let i = 1; i < years.length; i += 1) {
      expect(years[i]).toBe(years[i - 1] + 1);
    }
  });
});

describe("compareDates / todayIn", () => {
  it("orders across every field", () => {
    expect(compareDates({ year: 2035, month: 1, day: 1 }, { year: 2036, month: 1, day: 1 })).toBeLessThan(0);
    expect(compareDates({ year: 2035, month: 2, day: 1 }, { year: 2035, month: 1, day: 1 })).toBeGreaterThan(0);
    expect(compareDates({ year: 2035, month: 1, day: 5 }, { year: 2035, month: 1, day: 5 })).toBe(0);
  });

  it("reads today from the local components, not the UTC ones", () => {
    // 1 Jan 2035 00:30 local. In any zone ahead of UTC this instant is still
    // 31 December in UTC — `todayIn` must say the 1st.
    expect(todayIn(new Date(2035, 0, 1, 0, 30))).toEqual({ year: 2035, month: 1, day: 1 });
  });
});
