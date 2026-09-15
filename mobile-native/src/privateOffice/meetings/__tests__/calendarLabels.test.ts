/**
 * Reading a stored instant back out in the zone it was booked in.
 *
 * `calendar.ts` never touches an instant and `ScheduleWizard.test.tsx` pins
 * what leaves the client. This pins the other direction — the one the edit
 * form depends on — and the mutation it exists to catch is the one that was
 * actually shipped here: rendering canonical UTC with `toLocaleString()`, so
 * a meeting the host booked as 09:30 Tokyo appeared as the device's 00:30 and
 * nothing on screen said which number the other attendees were using.
 *
 * The assertions are written against hand-computed offsets rather than
 * against `Intl` output, because a test that derives its expectation the same
 * way the code does will agree with the code about anything.
 */

import { localStartAt } from "../calendar";
import { civilFromInstant, meetingWhenLabel, timezoneLabel } from "../calendarLabels";

describe("reading an instant back in a zone", () => {
  it("gives the wall clock the host typed, not the device's", () => {
    // 00:30 UTC is 09:30 in Tokyo, which sits at +09:00 all year.
    const read = civilFromInstant("2035-03-14T00:30:00+00:00", "Asia/Tokyo");
    expect(read).not.toBeNull();
    expect(read?.date).toEqual({ year: 2035, month: 3, day: 14 });
    expect(read?.time).toEqual({ hour: 9, minute: 30 });
  });

  it("uses the zone's rules for that date, not its rules for today", () => {
    // The same UTC clock time, six months apart, in a zone that observes DST.
    // New York is -05:00 in January and -04:00 in July; a decomposition that
    // applied one fixed offset would return the same hour for both.
    const winter = civilFromInstant("2035-01-04T16:00:00+00:00", "America/New_York");
    const summer = civilFromInstant("2035-07-04T16:00:00+00:00", "America/New_York");
    expect(winter?.time).toEqual({ hour: 11, minute: 0 });
    expect(summer?.time).toEqual({ hour: 12, minute: 0 });
  });

  it("moves the date, not just the clock, when the zone is a day ahead", () => {
    // 23:00 UTC on the 14th is already the 15th in Tokyo. An edit form that
    // kept the UTC date would quietly reschedule the meeting a day early.
    const read = civilFromInstant("2035-03-14T23:00:00+00:00", "Asia/Tokyo");
    expect(read?.date).toEqual({ year: 2035, month: 3, day: 15 });
    expect(read?.time).toEqual({ hour: 8, minute: 0 });
  });

  it("handles a zone whose offset is not a whole hour", () => {
    const read = civilFromInstant("2035-03-14T00:30:00+00:00", "Asia/Kolkata");
    expect(read?.time).toEqual({ hour: 6, minute: 0 });
  });

  it("round-trips into the naive string the wizard would have sent", () => {
    const read = civilFromInstant("2035-07-04T16:00:00+00:00", "America/New_York");
    expect(read).not.toBeNull();
    expect(localStartAt(read!.date, read!.time)).toBe("2035-07-04T12:00:00");
  });

  it("returns null rather than a guess when there is nothing to read", () => {
    // A pre-filled time that is quietly wrong is worse than an empty field,
    // because the user will not check one that looks plausible.
    expect(civilFromInstant("", "Asia/Tokyo")).toBeNull();
    expect(civilFromInstant("not a date", "Asia/Tokyo")).toBeNull();
    expect(civilFromInstant("2035-03-14T00:30:00+00:00", "Mars/Olympus")).toBeNull();
  });
});

describe("naming an instant on screen", () => {
  it("names the zone it is showing", () => {
    const label = meetingWhenLabel("2035-03-14T00:30:00+00:00", "Asia/Tokyo");
    expect(label).toContain(timezoneLabel("Asia/Tokyo"));
  });

  it("shows the host's hour, not the one the device would have rendered", () => {
    const iso = "2035-03-14T00:30:00+00:00";
    const tokyo = meetingWhenLabel(iso, "Asia/Tokyo");
    const losAngeles = meetingWhenLabel(iso, "America/Los_Angeles");
    // Two viewers of the same instant see two different clock times, and each
    // is told which city it belongs to. `toLocaleString()` gave them both the
    // device's, unlabelled.
    expect(tokyo).not.toBe(losAngeles);
    expect(tokyo).toContain(timezoneLabel("Asia/Tokyo"));
    expect(losAngeles).toContain(timezoneLabel("America/Los_Angeles"));
  });

  it("stays quiet about the zone when the row does not carry one", () => {
    // Rows written before the column existed really are only an instant, so
    // they must not be given a city they were never told.
    const label = meetingWhenLabel("2035-03-14T00:30:00+00:00", "");
    expect(label).not.toBe("");
    expect(label).not.toContain("(");
  });

  it("renders nothing at all for an absent or unusable instant", () => {
    expect(meetingWhenLabel("", "Asia/Tokyo")).toBe("");
    expect(meetingWhenLabel("not a date", "Asia/Tokyo")).toBe("");
  });
});
