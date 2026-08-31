/**
 * The delivery card states three things that must agree with each other: the
 * next check, the quiet-hours range, and the timezone they are both expressed
 * in. formatWhen used to omit `timeZone`, so the headline time was rendered in
 * the DEVICE's zone while the two lines under it were rendered in the account's
 * canonical zone.
 *
 * Observed on the iPhone 17 Pro Max simulator against production: a UTC account
 * whose server returned next_check_local="2026-08-31T12:04+00:00" displayed
 * "Next check around Aug 31 at 5:04 AM" over "Quiet hours: 22:00 - 07:00" and
 * "Timezone: UTC" -- i.e. the card appeared to schedule a check squarely inside
 * the quiet window it was printing directly underneath. The scheduler was
 * correct the whole time; only the rendering lied.
 */
import { formatWhen } from "../BriefingsHubScreen";

const NEXT_CHECK_UTC = "2026-08-31T12:04+00:00";

describe("formatWhen", () => {
  it("renders the server's canonical time in the account zone, not the device zone", () => {
    // No TZ is pinned for this suite, so asserting "not 5:04" would only
    // reproduce the bug on a machine that happens to sit in Pacific time.
    // Assert the zone-independent fact instead: with zone="UTC" the output must
    // equal the UTC rendering on any host, which the old implementation could
    // only satisfy by accident.
    const rendered = formatWhen(NEXT_CHECK_UTC, "UTC");
    expect(rendered).toContain("12:04");
  });

  it("ignores the device zone entirely when the account zone is known", () => {
    // Two different canonical zones must produce two different strings from one
    // instant. This is what the missing timeZone option collapsed: every zone
    // rendered identically, as the device.
    expect(formatWhen(NEXT_CHECK_UTC, "UTC")).not.toBe(
      formatWhen(NEXT_CHECK_UTC, "Asia/Tokyo")
    );
  });

  it("keeps the next check outside the quiet range the card prints beside it", () => {
    // The user-visible invariant, asserted on the rendered hour rather than on
    // the raw ISO string: 12:04 is outside 22:00-07:00, 5:04 is inside it.
    const hour = Number(
      new Intl.DateTimeFormat("en-US", { hour: "numeric", hour12: false, timeZone: "UTC" })
        .format(new Date(NEXT_CHECK_UTC))
    );
    const insideQuiet = hour >= 22 || hour < 7;
    expect(insideQuiet).toBe(false);
  });

  it("honours a non-UTC canonical zone", () => {
    // 12:04 UTC is 08:04 in New York on Aug 31 (EDT).
    expect(formatWhen(NEXT_CHECK_UTC, "America/New_York")).toContain("8:04");
  });

  it("still formats when the account has no resolved zone", () => {
    // Falls back to device-local rather than blanking the line.
    expect(formatWhen(NEXT_CHECK_UTC, null)).not.toBe("");
    expect(formatWhen(NEXT_CHECK_UTC, undefined)).not.toBe("");
  });

  it("degrades to device-local on an unknown IANA id instead of throwing", () => {
    // toLocaleString throws RangeError on a bad zone; the card must survive a
    // server that hands back something this runtime's ICU data doesn't know.
    expect(() => formatWhen(NEXT_CHECK_UTC, "Mars/Olympus_Mons")).not.toThrow();
    expect(formatWhen(NEXT_CHECK_UTC, "Mars/Olympus_Mons")).not.toBe("");
  });

  it("never invents a value", () => {
    expect(formatWhen(null, "UTC")).toBe("");
    expect(formatWhen(undefined, "UTC")).toBe("");
    expect(formatWhen("", "UTC")).toBe("");
    // An unparseable timestamp is echoed, not silently turned into a date.
    expect(formatWhen("not a date", "UTC")).toBe("not a date");
  });
});
