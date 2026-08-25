/**
 * Date, time and timezone for scheduled orders — as values, not as typing.
 *
 * The checkout used to ask for all three as free text, with the format shown as
 * placeholder: `YYYY-MM-DD`, `HH:MM`, `America/New_York`. Every one of those is
 * a way to fail. A buyer who writes `25/08/2026`, or `10:30 PM`, or their own
 * abbreviation `EST`, is rejected by
 * `services/marketplace_fulfillment.validate_details` — which matches
 * `^\d{4}-\d{2}-\d{2}$`, `^([01]\d|2[0-3]):[0-5]\d$` and a timezone pattern —
 * after they have filled in the rest of the form. And the timezone question is
 * the worst of the three, because the device already knows the answer.
 *
 * So the screen now renders native pickers and this module does the conversion.
 * The wire format is unchanged: the server still receives the same three
 * strings it always validated. What changed is that they are now produced by a
 * calendar, a clock and `Intl`, none of which can produce an invalid one.
 */

/** Zero-padded, local-calendar `YYYY-MM-DD`.
 *
 * Deliberately not `toISOString().slice(0, 10)`: that converts to UTC first, so
 * an 8pm appointment in New York would be submitted as the *following* day. The
 * buyer picked a date on a calendar in their own timezone and that is the date
 * that must travel. */
export function toIsoDate(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

/** 24-hour `HH:MM`, which is what the server's `_HHMM_RE` accepts. The picker
 * may present a 12-hour face with AM/PM depending on device locale; that is
 * presentation, and it never reaches the wire. */
export function toWireTime(value: Date): string {
  return `${String(value.getHours()).padStart(2, "0")}:${String(value.getMinutes()).padStart(2, "0")}`;
}

/** Parse a stored `YYYY-MM-DD` back into a local Date for the picker's initial
 * value. Returns null for anything malformed, including a legacy value typed
 * into the old text field, so a bad string re-opens the picker at today rather
 * than at an Invalid Date. */
export function fromIsoDate(value: string): Date | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value || "").trim());
  if (!match) return null;
  const [, year, month, day] = match;
  const date = new Date(Number(year), Number(month) - 1, Number(day));
  return Number.isNaN(date.getTime()) ? null : date;
}

/** Parse a stored `HH:MM` onto today's date, for the time picker's initial
 * value. Only the clock face is read back, so the date part is irrelevant. */
export function fromWireTime(value: string): Date | null {
  const match = /^([01]\d|2[0-3]):([0-5]\d)$/.exec(String(value || "").trim());
  if (!match) return null;
  const date = new Date();
  date.setHours(Number(match[1]), Number(match[2]), 0, 0);
  return date;
}

/**
 * The buyer's IANA timezone, read from the device.
 *
 * This is the field the buyer should never have been asked for. `Intl` knows
 * it, the answer is right far more often than a typed one, and being wrong here
 * is expensive in a way the buyer cannot see at the time — a consultation
 * booked at "10:30" against the wrong zone is simply a missed appointment.
 *
 * Falls back to UTC, which the server's timezone pattern accepts, so a device
 * with no `Intl` support still produces a submittable order rather than a
 * blocked one.
 */
export function deviceTimezone(): string {
  try {
    const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (zone && /^[A-Za-z0-9_+\-/]{1,64}$/.test(zone)) return zone;
  } catch {
    /* falls through */
  }
  return "UTC";
}

/** `America/New_York` → `New York`. The IANA identifier is the right thing to
 * store and the wrong thing to show; the region reads as a place. */
export function timezoneRegionLabel(zone: string): string {
  const raw = String(zone || "").trim();
  if (!raw) return "";
  const tail = raw.split("/").pop() || raw;
  return tail.replace(/_/g, " ");
}

/**
 * The timezone as the buyer would name it — "Eastern Time", "Pacific Time" —
 * falling back to the region when the platform has no long name for it.
 *
 * Shown as context beside the chosen time, never as an input. The mockup's
 * third scheduling line is this string; it is read-only by design.
 */
export function timezoneDisplayLabel(zone: string, at: Date = new Date()): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: zone,
      timeZoneName: "long"
    }).formatToParts(at);
    const name = parts.find((part) => part.type === "timeZoneName")?.value;
    if (name && !/^GMT/.test(name)) return name;
  } catch {
    /* falls through */
  }
  return timezoneRegionLabel(zone);
}

/** `Tue, Aug 25` — the mockup's date line. Locale-formatted, so it is not an
 * English-only rendering of a value the buyer picked in their own calendar. */
export function formatDateLabel(value: Date): string {
  try {
    return new Intl.DateTimeFormat(undefined, {
      weekday: "short",
      month: "short",
      day: "numeric"
    }).format(value);
  } catch {
    return toIsoDate(value);
  }
}

/** `10:30 AM` where the locale uses one, `10:30` where it does not. */
export function formatTimeLabel(value: Date): string {
  try {
    return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(value);
  } catch {
    return toWireTime(value);
  }
}

/** Earliest date the calendar will offer. An appointment cannot be booked into
 * the past, and letting the picker offer yesterday only moves the rejection to
 * the server. */
export function earliestSchedulableDate(): Date {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  return today;
}
