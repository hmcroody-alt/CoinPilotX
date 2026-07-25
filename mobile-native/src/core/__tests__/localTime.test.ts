jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

import {
  formatAbsoluteDate,
  formatClockTime,
  formatDateRange,
  formatRelativeTime,
  formatScheduledTime,
  getActiveLocale,
  getActiveTimeZone,
  isValidLocale,
  isValidTimeZone,
  parseServerInstant,
  setManualLocale,
  setManualTimeZone
} from "../localTime";

const INSTANT = "2026-07-20T17:30:00Z"; // 10:30 AM Los Angeles, 6:30 PM London

afterEach(async () => {
  await setManualTimeZone(null);
  await setManualLocale(null);
});

describe("parseServerInstant", () => {
  it("parses explicit-offset and Z timestamps to the same instant", () => {
    expect(parseServerInstant("2026-07-20T10:30:00-07:00")!.getTime()).toBe(
      parseServerInstant("2026-07-20T17:30:00Z")!.getTime()
    );
  });

  it("treats a legacy timestamp with no offset as UTC", () => {
    expect(parseServerInstant("2026-07-20 17:30:00")!.getTime()).toBe(Date.parse("2026-07-20T17:30:00Z"));
  });

  it("returns null for empty or unparseable input", () => {
    expect(parseServerInstant("")).toBeNull();
    expect(parseServerInstant(null)).toBeNull();
    expect(parseServerInstant("not-a-date")).toBeNull();
  });
});

describe("time-zone conversion", () => {
  it("renders the same UTC instant differently per zone", () => {
    expect(formatClockTime(INSTANT, { timeZone: "America/Los_Angeles", locale: "en-US" })).toBe("10:30 AM");
    expect(formatClockTime(INSTANT, { timeZone: "America/New_York", locale: "en-US" })).toBe("1:30 PM");
    expect(formatClockTime(INSTANT, { timeZone: "Europe/London", locale: "en-US" })).toBe("6:30 PM");
    expect(formatClockTime(INSTANT, { timeZone: "Asia/Kolkata", locale: "en-US" })).toBe("11:00 PM");
    expect(formatClockTime(INSTANT, { timeZone: "Asia/Tokyo", locale: "en-US" })).toBe("2:30 AM");
  });

  it("respects an explicit 24-hour preference", () => {
    expect(formatClockTime(INSTANT, { timeZone: "Europe/London", locale: "en-GB", hour12: false })).toBe("18:30");
  });

  it("crosses the date line into the next local day", () => {
    // 17:30Z July 20 is already July 21 in Auckland.
    expect(formatAbsoluteDate(INSTANT, { timeZone: "Pacific/Auckland", locale: "en-US", withYear: false })).toBe(
      "Jul 21"
    );
  });
});

describe("daylight saving", () => {
  it("applies summer offset for a July instant (BST)", () => {
    expect(formatClockTime("2026-07-20T12:00:00Z", { timeZone: "Europe/London", locale: "en-US" })).toBe("1:00 PM");
  });

  it("applies winter offset for a January instant (GMT)", () => {
    expect(formatClockTime("2026-01-20T12:00:00Z", { timeZone: "Europe/London", locale: "en-US" })).toBe("12:00 PM");
  });

  it("applies PDT in summer and PST in winter for Los Angeles", () => {
    expect(formatClockTime("2026-07-20T20:00:00Z", { timeZone: "America/Los_Angeles", locale: "en-US" })).toBe("1:00 PM");
    expect(formatClockTime("2026-01-20T20:00:00Z", { timeZone: "America/Los_Angeles", locale: "en-US" })).toBe("12:00 PM");
  });
});

describe("formatRelativeTime", () => {
  const now = new Date("2026-07-20T17:30:00Z");
  const opts = { timeZone: "America/Los_Angeles", locale: "en-US" };

  it("labels very recent instants as now", () => {
    expect(formatRelativeTime("2026-07-20T17:29:40Z", now, opts)).toBe("now");
  });

  it("labels minutes and hours", () => {
    expect(formatRelativeTime("2026-07-20T17:20:00Z", now, opts)).toBe("10m");
    expect(formatRelativeTime("2026-07-20T15:30:00Z", now, opts)).toBe("2h");
  });

  it("labels the previous local day as Yesterday", () => {
    expect(formatRelativeTime("2026-07-19T17:30:00Z", now, opts)).toBe("Yesterday");
  });

  it("falls back to a localized date for old instants", () => {
    expect(formatRelativeTime("2026-06-01T17:30:00Z", now, opts)).toBe("Jun 1");
  });
});

describe("manual override", () => {
  it("supersedes automatic detection and returns to automatic", async () => {
    await setManualTimeZone("Asia/Tokyo");
    expect(getActiveTimeZone()).toBe("Asia/Tokyo");
    expect(formatClockTime(INSTANT, { locale: "en-US" })).toBe("2:30 AM");
    await setManualTimeZone(null);
    expect(getActiveTimeZone()).not.toBe("Asia/Tokyo");
  });

  it("ignores an invalid override", async () => {
    await setManualTimeZone("Not/AZone");
    expect(getActiveTimeZone()).not.toBe("Not/AZone");
  });
});

describe("locale override", () => {
  it("changes localized formatting immediately and returns to device locale", async () => {
    await setManualLocale("fr-FR");
    expect(getActiveLocale()).toBe("fr-FR");
    expect(formatAbsoluteDate(INSTANT, { timeZone: "Europe/Paris", withYear: false })).toBe("20 juil.");
    await setManualLocale(null);
    expect(getActiveLocale()).not.toBe("fr-FR");
  });

  it("rejects malformed locale identifiers", () => {
    expect(isValidLocale("es-MX")).toBe(true);
    expect(isValidLocale("not_a_locale_%%%")).toBe(false);
  });
});

describe("isValidTimeZone", () => {
  it("accepts IANA zones and rejects junk", () => {
    expect(isValidTimeZone("America/New_York")).toBe(true);
    expect(isValidTimeZone("Mars/Phobos")).toBe(false);
    expect(isValidTimeZone("")).toBe(false);
  });
});

describe("formatScheduledTime", () => {
  it("shows both viewer and event zones when they differ", () => {
    const result = formatScheduledTime(INSTANT, "America/New_York", {
      timeZone: "America/Los_Angeles",
      locale: "en-US"
    });
    expect(result).toContain("your time");
    expect(result).toContain("New York");
    expect(result).toContain("1:30 PM");
  });

  it("shows a single label when zones match", () => {
    const result = formatScheduledTime(INSTANT, "America/Los_Angeles", {
      timeZone: "America/Los_Angeles",
      locale: "en-US"
    });
    expect(result).not.toContain("your time");
  });
});

describe("formatDateRange", () => {
  it("collapses to a single date when start and end share a day", () => {
    const result = formatDateRange("2026-07-20T17:00:00Z", "2026-07-20T18:00:00Z", {
      timeZone: "America/New_York",
      locale: "en-US"
    });
    expect(result).toBe("Jul 20, 1:00 PM – 2:00 PM");
  });
});
