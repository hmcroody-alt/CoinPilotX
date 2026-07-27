/**
 * `format.ts` is the single place where every user-visible number, date and
 * measurement in the app is rendered, and it is the only module that has to
 * survive a Hermes build with reduced ICU data. Every `Intl` constructor it
 * touches is probed at runtime and backed by either a catalog template or a
 * hand-rolled string, so the interesting failures are not "wrong digit grouping"
 * but "the fallback path renders `undefined`, `NaN` or a raw catalog key on a
 * real device". These tests exercise both halves: the happy path under Node's
 * full ICU, and the degraded path with the relevant `Intl` constructor removed.
 *
 * Two determinism rules hold throughout:
 *
 *   1. No `new Date()` and no reliance on the host clock. Every instant is a
 *      fixed UTC timestamp and every date assertion passes an explicit
 *      `timeZone` plus an explicit `withYear`, because `formatAbsoluteDate`
 *      silently adds the year only when the instant is not in the *current*
 *      year — an assertion without `withYear` would start failing on New Year.
 *   2. No reliance on the host's locale. `activateLocale` + `setActiveRegion`
 *      pin the active formatting tag, and the currency tests pin
 *      `localTime`'s manual locale rather than reading the device's.
 */

import {
  activateLocale,
  getActiveTranslationLocale,
  preloadNamespaces,
  setActiveRegion
} from "../engine";
import { setManualCurrency, setManualLocale } from "../../core/localTime";
import {
  activeFormattingLocale,
  formatAccessibleDateTime,
  formatCount,
  formatCurrencyAmount,
  formatDate,
  formatDateTime,
  formatDay,
  formatDistance,
  formatDuration,
  formatFileSize,
  formatList,
  formatMeasurement,
  formatNumber,
  formatOrdinal,
  formatPercent,
  formatRange,
  formatRelative,
  formatRelativeLong,
  formatScheduled,
  formatTemperature,
  formatTime,
  formatWeight,
  monthNames,
  regionDisplayName,
  timeZoneLabel,
  usesImperialUnits,
  weekdayNames
} from "../format";

/* ------------------------------------------------------------------ *
 * Fixtures
 * ------------------------------------------------------------------ */

/** Friday 15 March 2024, 14:30 UTC. Chosen because it is unambiguous in every
 *  date order (15 > 12, so a d/m vs m/d swap is visible) and is a weekday. */
const INSTANT = "2024-03-15T14:30:00Z";
const NOW = new Date(INSTANT);
const UTC = "UTC";
const NEW_YORK = "America/New_York";

const SECOND = 1000;
const MINUTE = 60 * SECOND;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;
const WEEK = 7 * DAY;

/** An instant `ms` before NOW. Positive `ms` means "in the past". */
const ago = (ms: number): Date => new Date(NOW.getTime() - ms);
/** An instant `ms` after NOW. */
const ahead = (ms: number): Date => new Date(NOW.getTime() + ms);

/**
 * Collapses every space-like separator to a plain ASCII space.
 *
 * ICU does not use U+0020 for digit grouping: fr-FR emits U+202F (narrow
 * no-break space) and pt-BR emits U+00A0 between the currency symbol and the
 * amount. Which one a given ICU version picks has changed more than once, and
 * an invisible character pasted into a test file is impossible to review. So
 * assertions normalize whitespace and compare shapes, never raw bytes.
 */
const flat = (value: string): string => value.replace(/\s+/g, " ");

/** Every string this module produces must be safe to put straight on screen. */
const expectRenderable = (value: string): void => {
  expect(typeof value).toBe("string");
  expect(value).not.toContain("undefined");
  expect(value).not.toContain("NaN");
  expect(value).not.toContain("Invalid Date");
  // A raw catalog key leaking through looks like `common:units.kilometer`.
  expect(value).not.toMatch(/\bcommon:[a-z]/i);
  expect(value).not.toContain("{{");
};

/**
 * The formatters read month names, relative-time abbreviations, unit suffixes
 * and list connectors out of the catalogs, and the engine only serves a
 * namespace once it has been loaded. Without this the whole suite would be
 * asserting against `humanizeKey` output ("Kilometer", "Minutes short").
 */
beforeAll(async () => {
  await activateLocale("en");
  // Several assertions request a non-active language explicitly (a language
  // preview, or a row rendered for another user). `resolveTemplate` only reads
  // catalogs that are already in the cache, so without this preload those
  // lookups would silently fall back to English and the tests would pass while
  // asserting nothing about French, German, Japanese or Arabic copy.
  await Promise.all(["fr", "de", "ja", "ar", "pt", "hi"].map((code) => preloadNamespaces(code)));
  setActiveRegion("US");
});

/* ------------------------------------------------------------------ *
 * Numbers
 * ------------------------------------------------------------------ */

describe("formatNumber", () => {
  /**
   * The four conventions that actually differ structurally, not just
   * cosmetically: ASCII dot-decimal, swapped separators, space grouping, and
   * the Indian 2-2-3 grouping. A regression that hardcodes `toLocaleString()`
   * with no locale would pass an en-only test and fail all three others.
   */
  it("uses the dot-decimal, comma-grouped convention for en", () => {
    expect(formatNumber(1234.56, { locale: "en" })).toBe("1,234.56");
  });

  it("swaps the separators for de", () => {
    expect(formatNumber(1234.56, { locale: "de" })).toBe("1.234,56");
  });

  it("groups with a space and a comma decimal for fr", () => {
    // fr-FR's group separator is U+202F on modern ICU and was U+00A0 before,
    // so the separator is normalized rather than pasted in literally.
    expect(flat(formatNumber(1234.56, { locale: "fr" }))).toBe("1 234,56");
  });

  it("uses lakh/crore grouping for hi", () => {
    // 1,23,456 rather than 123,456 — the one locale in the set whose grouping
    // is not uniform 3-digit, and the one a naive regex-based formatter breaks.
    expect(formatNumber(123456, { locale: "hi" })).toBe("1,23,456");
    expect(formatNumber(12345678, { locale: "hi" })).toBe("1,23,45,678");
  });

  it("renders Arabic-Indic digits for ar", () => {
    const value = formatNumber(1234.56, { locale: "ar" });
    // Asserting on the digit *set* rather than the exact string: ar-SA uses
    // U+066C as a thousands separator and U+066B as a decimal, both invisible
    // in a diff. What matters is that no ASCII digit survived.
    expect(value).toMatch(/[٠-٩]/);
    expect(value).not.toMatch(/[0-9]/);
    expectRenderable(value);
  });

  it("returns an empty string for non-finite input rather than 'NaN'", () => {
    // Callers pass server values straight through; `NaN` on screen is a bug
    // report, an empty cell is not.
    expect(formatNumber(Number.NaN)).toBe("");
    expect(formatNumber(Number.POSITIVE_INFINITY)).toBe("");
  });

  it("honours signDisplay and fraction-digit bounds", () => {
    expect(formatNumber(5, { locale: "en", signDisplay: "always" })).toBe("+5");
    expect(formatNumber(5, { locale: "en", minimumFractionDigits: 2 })).toBe("5.00");
    expect(formatNumber(5.6789, { locale: "en", maximumFractionDigits: 1 })).toBe("5.7");
  });
});

describe("formatCount", () => {
  /**
   * The default `compactFrom` is 10000, not 1000. That is deliberate (a feed
   * showing "1.2K" for 1,200 reads worse than "1,200") but it is also the most
   * commonly mis-assumed constant in the module, so it is pinned.
   */
  it("stays long below the default 10k threshold", () => {
    expect(formatCount(999, { locale: "en" })).toBe("999");
    expect(formatCount(1000, { locale: "en" })).toBe("1,000");
    expect(formatCount(9999, { locale: "en" })).toBe("9,999");
  });

  it("switches to compact notation at exactly the threshold", () => {
    // 9999 -> "9,999" and 10000 -> "10K": the boundary is inclusive on the
    // compact side (`>= threshold`), which is where an off-by-one would hide.
    expect(formatCount(10000, { locale: "en" })).toBe("10K");
  });

  it("abbreviates with one fraction digit when compactFrom is lowered", () => {
    expect(formatCount(1200, { locale: "en", compactFrom: 1000 })).toBe("1.2K");
    expect(formatCount(3400000, { locale: "en", compactFrom: 1000 })).toBe("3.4M");
  });

  it("rounds 999,999 up to 1M", () => {
    // Documented, not accidental: with maximumFractionDigits 1 there is no way
    // to render 999.999K, so the display "loses" a follower just below a
    // million. Pinned so a future change to the fraction digits is a conscious
    // one rather than a surprise in the UI.
    expect(formatCount(999999, { locale: "en" })).toBe("1M");
    expect(formatCount(1000000, { locale: "en" })).toBe("1M");
  });

  it("compacts negative counts by magnitude", () => {
    // The threshold test is `Math.abs(...)`, so a negative delta must compact
    // too rather than rendering as a raw -25000.
    expect(formatCount(-25000, { locale: "en" })).toBe("-25K");
  });

  it("returns an empty string for non-finite input", () => {
    expect(formatCount(Number.NaN)).toBe("");
  });
});

describe("formatCurrencyAmount", () => {
  afterEach(async () => {
    // These tests move `localTime`'s module-level locale/currency state, which
    // every other formatter in the process can see.
    await setManualLocale(null);
    await setManualCurrency(null);
  });

  it("places the symbol per locale for USD and EUR", () => {
    expect(formatCurrencyAmount(1234.5, { locale: "en", currency: "USD" })).toBe("$1,234.50");
    // German puts the symbol last and separates it with a no-break space.
    expect(flat(formatCurrencyAmount(1234.5, { locale: "de", currency: "EUR" }))).toBe("1.234,50 €");
  });

  it("drops the fraction entirely for JPY", () => {
    // JPY has zero minor units in CLDR. A formatter that hardcoded two decimal
    // places would render "¥1,234.50", which is not a valid yen amount.
    const yen = formatCurrencyAmount(1234.5, { locale: "ja", currency: "JPY" });
    expect(yen).not.toContain(".");
    expect(yen).toContain("1,235");
  });

  it("formats BRL with Brazilian separators", () => {
    expect(flat(formatCurrencyAmount(1234.5, { locale: "pt", currency: "BRL" }))).toBe("R$ 1.234,50");
  });

  /**
   * The default currency is not a constant: it is derived from the *region* of
   * the active locale via `localTime.getActiveCurrency`. A user who switches
   * region must see their own currency without any call site passing one.
   */
  it("derives the default currency from the active region", async () => {
    await setManualLocale("de-DE");
    expect(flat(formatCurrencyAmount(10, { locale: "de" }))).toContain("€");

    await setManualLocale("ja-JP");
    const yen = formatCurrencyAmount(10, { locale: "ja" });
    expect(yen).not.toContain(".");
    expect(yen).toMatch(/[¥￥]/);

    await setManualLocale("en-US");
    expect(formatCurrencyAmount(10, { locale: "en" })).toBe("$10.00");
  });

  it("lets an explicit manual currency override the region default", async () => {
    await setManualLocale("de-DE");
    await setManualCurrency("GBP");
    expect(flat(formatCurrencyAmount(10, { locale: "en" }))).toBe("£10.00");
  });

  it("returns an empty string for non-finite input", () => {
    expect(formatCurrencyAmount(Number.NaN, { currency: "USD" })).toBe("");
  });
});

describe("formatPercent", () => {
  it("treats a bare number as a ratio by default", () => {
    expect(formatPercent(0.42, { locale: "en" })).toBe("42%");
  });

  it("divides by 100 when the caller says the value is already scaled", () => {
    // Both call shapes exist in the codebase; getting `alreadyScaled` backwards
    // turns 42% into 4,200%, which is the failure this pins.
    expect(formatPercent(42, { locale: "en", alreadyScaled: true })).toBe("42%");
    expect(formatPercent(42, { locale: "en" })).toBe("4,200%");
  });

  it("rounds to whole percent unless told otherwise", () => {
    expect(formatPercent(0.4267, { locale: "en" })).toBe("43%");
    expect(formatPercent(0.4267, { locale: "en", maximumFractionDigits: 1 })).toBe("42.7%");
  });

  it("localizes the percent sign placement", () => {
    // fr-FR separates the sign from the number; de-DE does too. Normalized so
    // the assertion does not depend on which space character ICU chose.
    expect(flat(formatPercent(0.42, { locale: "fr" }))).toBe("42 %");
  });

  it("returns an empty string for non-finite input", () => {
    expect(formatPercent(Number.NaN)).toBe("");
  });
});

describe("formatOrdinal", () => {
  it("applies the four English ordinal suffixes", () => {
    expect(formatOrdinal(1, { locale: "en" })).toBe("1st");
    expect(formatOrdinal(2, { locale: "en" })).toBe("2nd");
    expect(formatOrdinal(3, { locale: "en" })).toBe("3rd");
    expect(formatOrdinal(4, { locale: "en" })).toBe("4th");
  });

  it("gets the 11/12/13 exception right", () => {
    // "11st" is the classic ordinal bug: naive `n % 10` logic produces it.
    // `Intl.PluralRules` with type "ordinal" is what prevents it.
    expect(formatOrdinal(11, { locale: "en" })).toBe("11th");
    expect(formatOrdinal(12, { locale: "en" })).toBe("12th");
    expect(formatOrdinal(13, { locale: "en" })).toBe("13th");
    expect(formatOrdinal(21, { locale: "en" })).toBe("21st");
    expect(formatOrdinal(111, { locale: "en" })).toBe("111th");
  });

  it("groups the digits of a large ordinal", () => {
    expect(formatOrdinal(1002, { locale: "en" })).toBe("1,002nd");
  });

  /**
   * The suffix branch is gated on the *translation* locale, not the requested
   * one: `getActiveTranslationLocale() !== "en"` short-circuits to bare digits.
   * With English active, asking for a French rendering still takes the English
   * branch — which is the current, deliberate behaviour, so it is pinned here
   * so a change to the gate is visible.
   */
  it("is gated on the active translation locale, not the requested one", () => {
    expect(getActiveTranslationLocale()).toBe("en");
    expect(formatOrdinal(1, { locale: "fr" })).toBe("1st");
  });

  it("truncates a fractional rank instead of rendering a decimal", () => {
    expect(formatOrdinal(3.7, { locale: "en" })).toBe("3rd");
  });
});

/* ------------------------------------------------------------------ *
 * Units
 * ------------------------------------------------------------------ */

describe("usesImperialUnits", () => {
  it("is true only for the three imperial regions", () => {
    expect(usesImperialUnits("en-US")).toBe(true);
    expect(usesImperialUnits("en-LR")).toBe(true);
    expect(usesImperialUnits("my-MM")).toBe(true);
  });

  it("is false for metric regions", () => {
    expect(usesImperialUnits("fr-FR")).toBe(false);
    expect(usesImperialUnits("de-DE")).toBe(false);
    expect(usesImperialUnits("ja-JP")).toBe(false);
    // en-GB is the trap: English-speaking but metric for these purposes.
    expect(usesImperialUnits("en-GB")).toBe(false);
  });

  it("reads the region out of underscored and script-bearing tags", () => {
    // Android reports `en_US`; a tag can also carry a script subtag before the
    // region. Both must still resolve to US.
    expect(usesImperialUnits("en_US")).toBe(true);
    expect(usesImperialUnits("zh-Hans-CN")).toBe(false);
  });

  it("is false for a bare language tag with no region", () => {
    expect(usesImperialUnits("en")).toBe(false);
    expect(usesImperialUnits("")).toBe(false);
  });

  it("defaults to the active formatting locale", () => {
    // beforeAll pinned the active region to US.
    expect(activeFormattingLocale()).toBe("en-US");
    expect(usesImperialUnits()).toBe(true);
  });
});

describe("formatMeasurement", () => {
  it("attaches the localized short unit", () => {
    expect(flat(formatMeasurement(1.5, "kilometer", { locale: "en" }))).toBe("1.5 km");
    expect(flat(formatMeasurement(1.5, "kilometer", { locale: "de" }))).toBe("1,5 km");
    expect(flat(formatMeasurement(20, "celsius", { locale: "fr", maximumFractionDigits: 0 }))).toBe("20 °C");
  });

  it("returns an empty string for non-finite input", () => {
    expect(formatMeasurement(Number.NaN, "meter")).toBe("");
  });

  /**
   * BUG (characterization, not endorsement).
   *
   * `localeOf` routes an explicit `locale` through `toIntlLocale`, which only
   * recognises the eleven *bare* catalog codes. Any already-regionalized tag
   * ("de-DE", "pt-BR", "en-GB") misses the lookup and silently returns "en-US"
   * — so a caller who passes a full BCP-47 tag gets English formatting with no
   * error anywhere.
   *
   * This is worse than a stray call site: `formatDistance`, `formatWeight` and
   * `formatTemperature` each resolve `locale` to a full tag and then hand that
   * tag to `formatMeasurement`, which resolves it a *second* time. The nested
   * call therefore always formats as en-US, which is why the German and French
   * expectations in those describes below are the English shapes.
   */
  it("collapses an already-regionalized tag to en-US", () => {
    expect(formatNumber(1234.56, { locale: "de" })).toBe("1.234,56");
    expect(formatNumber(1234.56, { locale: "de-DE" })).toBe("1,234.56");
    expect(flat(formatMeasurement(2.5, "kilogram", { locale: "de" }))).toBe("2,5 kg");
    expect(flat(formatMeasurement(2.5, "kilogram", { locale: "de-DE" }))).toBe("2.5 kg");
  });
});

describe("formatDistance", () => {
  it("uses feet then miles in an imperial locale", () => {
    // The switch is at 1000 feet, not at a round metre value, so both sides of
    // it are checked with the conversion factor applied.
    expect(flat(formatDistance(100, { locale: "en" }))).toBe("328 ft");
    expect(flat(formatDistance(5000, { locale: "en" }))).toBe("3.1 mi");
  });

  it("uses metres then kilometres elsewhere", () => {
    expect(flat(formatDistance(500, { locale: "fr" }))).toBe("500 m");
    expect(flat(formatDistance(5000, { locale: "fr" }))).toBe("5 km");
    // Exactly 1000 m must promote to km, not render "1,000 m".
    expect(flat(formatDistance(1000, { locale: "de" }))).toBe("1 km");
  });

  /**
   * The double-resolution bug documented in `formatMeasurement` above bites
   * here: 1500 m in German should read "1,5 km" but the nested call has already
   * been downgraded to en-US. These three assertions happen to be identical in
   * English and the target language, which is exactly why the defect survived —
   * only a fractional value exposes it.
   */
  it("loses the language on the nested measurement call", () => {
    expect(flat(formatDistance(1500, { locale: "de" }))).toBe("1.5 km");
  });

  it("returns an empty string for non-finite input", () => {
    expect(formatDistance(Number.NaN)).toBe("");
  });
});

describe("formatWeight", () => {
  it("uses ounces then pounds in an imperial locale", () => {
    expect(flat(formatWeight(100, { locale: "en" }))).toBe("3.5 oz");
    expect(flat(formatWeight(1000, { locale: "en" }))).toBe("2.2 lb");
  });

  it("uses grams then kilograms elsewhere", () => {
    expect(flat(formatWeight(500, { locale: "de" }))).toBe("500 g");
    // Would be "2,5 kg" if the nested formatMeasurement call had not been
    // downgraded to en-US by the double `toIntlLocale` resolution.
    expect(flat(formatWeight(2500, { locale: "de" }))).toBe("2.5 kg");
  });
});

describe("formatTemperature", () => {
  it("converts to Fahrenheit for imperial regions and leaves Celsius alone otherwise", () => {
    // 20 °C is 68 °F — a conversion that is wrong in both directions if the
    // 9/5 and +32 are ever transposed.
    expect(flat(formatTemperature(20, { locale: "en" }))).toBe("68°F");
    // French renders "20 °C" with a separating space when the locale survives;
    // the nested-call downgrade means en-US's "20°C" comes out instead. The
    // conversion arithmetic — which is what this test is really guarding — is
    // unaffected either way.
    expect(flat(formatTemperature(20, { locale: "fr" }))).toBe("20°C");
  });

  it("handles sub-zero Celsius without producing a stray sign", () => {
    const value = flat(formatTemperature(-10, { locale: "en" }));
    expect(value).toBe("14°F");
    expectRenderable(value);
  });
});

describe("formatFileSize", () => {
  it("steps through the binary units", () => {
    expect(formatFileSize(0)).toBe("0 B");
    expect(formatFileSize(512)).toBe("512 B");
    expect(formatFileSize(1024)).toBe("1 KB");
    expect(formatFileSize(1536)).toBe("1.5 KB");
    expect(formatFileSize(1024 * 1024)).toBe("1 MB");
    expect(formatFileSize(1024 * 1024 * 1024)).toBe("1 GB");
  });

  it("clamps at terabytes rather than running off the unit table", () => {
    // The loop is bounded by `BYTE_UNITS.length - 1`; without that bound a
    // petabyte-scale value would index past the end and render "undefined".
    const petabyte = formatFileSize(1024 ** 5);
    expect(petabyte).toContain("TB");
    expectRenderable(petabyte);
  });

  it("returns an empty string for negative or non-finite input", () => {
    expect(formatFileSize(-1)).toBe("");
    expect(formatFileSize(Number.NaN)).toBe("");
  });
});

/* ------------------------------------------------------------------ *
 * Lists
 * ------------------------------------------------------------------ */

describe("formatList", () => {
  it("uses the language's own conjunction and separator", () => {
    // en uses the serial comma, fr drops it, ja uses the ideographic comma
    // with no conjunction at all. A hardcoded ", " + " and " join is wrong in
    // two of the three.
    expect(formatList(["Ann", "Bo", "Cy"], { locale: "en" })).toBe("Ann, Bo, and Cy");
    expect(formatList(["Ann", "Bo", "Cy"], { locale: "fr" })).toBe("Ann, Bo et Cy");
    expect(formatList(["Ann", "Bo", "Cy"], { locale: "ja" })).toBe("Ann、Bo、Cy");
  });

  it("short-circuits zero- and one-item lists", () => {
    expect(formatList([], { locale: "en" })).toBe("");
    expect(formatList(["Ann"], { locale: "en" })).toBe("Ann");
  });

  it("drops empty and nullish entries before joining", () => {
    // Feed rows pass raw display names, some of which are empty strings for
    // deleted accounts. Those must not become ", , and Cy".
    expect(formatList(["Ann", "", "Cy"], { locale: "en" })).toBe("Ann and Cy");
  });

  it("appends an overflow count once max is exceeded", () => {
    expect(formatList(["Ann", "Bo", "Cy", "Di"], { locale: "en", max: 2 })).toBe("Ann and Bo, +2 more");
  });

  it("does not append an overflow count when the list exactly fits", () => {
    // `max` equal to the length must not render "+0 more".
    expect(formatList(["Ann", "Bo"], { locale: "en", max: 2 })).toBe("Ann and Bo");
  });
});

/* ------------------------------------------------------------------ *
 * Dates and times
 * ------------------------------------------------------------------ */

describe("dates and times", () => {
  it("renders an absolute date in the requested zone", () => {
    expect(formatDate(INSTANT, { timeZone: UTC, withYear: true })).toBe("Mar 15, 2024");
    // 14:30 UTC is 10:30 the same morning in New York — same calendar day, so
    // the day number must not shift.
    expect(formatDate(INSTANT, { timeZone: NEW_YORK, withYear: true })).toBe("Mar 15, 2024");
    // 00:30 UTC on the 16th is still the 15th in New York. This is the case
    // that catches a formatter that converted to UTC-midnight instead of the
    // viewer's zone.
    expect(formatDate("2024-03-16T00:30:00Z", { timeZone: NEW_YORK, withYear: true })).toBe("Mar 15, 2024");
  });

  it("renders the date in the active language", () => {
    expect(formatDate(INSTANT, { timeZone: UTC, withYear: true, locale: "de-DE" })).toBe("15. März 2024");
    expect(formatDate(INSTANT, { timeZone: UTC, withYear: true, locale: "ja-JP" })).toBe("2024年3月15日");
  });

  it("respects the locale's 12/24-hour convention", () => {
    expect(formatTime(INSTANT, { timeZone: UTC })).toBe("2:30 PM");
    expect(formatTime(INSTANT, { timeZone: UTC, locale: "de-DE" })).toBe("14:30");
    expect(formatTime(INSTANT, { timeZone: UTC, hour12: false })).toBe("14:30");
  });

  it("joins date and time through the catalog's connector, not Intl's", () => {
    // The connector is a catalog key so CJK can omit the word entirely; asserting
    // the literal "at" here is what proves the catalog path is being used.
    expect(formatDateTime(INSTANT, { timeZone: UTC, withYear: true })).toBe("Mar 15, 2024 at 2:30 PM");
  });

  it("renders a numeric day in the locale's field order", () => {
    expect(formatDay(INSTANT, { timeZone: UTC })).toBe("03/15/2024");
    expect(formatDay(INSTANT, { timeZone: UTC, dateFormat: "dmy" })).toBe("15/03/2024");
    expect(formatDay(INSTANT, { timeZone: UTC, dateFormat: "ymd" })).toBe("2024/03/15");
  });

  it("collapses a same-day range to a single date plus two clock times", () => {
    const range = formatRange(INSTANT, "2024-03-15T16:00:00Z", { timeZone: UTC, withYear: true });
    expect(range).toContain("Mar 15, 2024");
    expect(range).toContain("2:30 PM");
    expect(range).toContain("4:00 PM");
    // The date must appear once, not twice, when both ends share a day.
    expect(range.match(/Mar 15/g)).toHaveLength(1);
  });

  it("keeps both dates in a multi-day range", () => {
    const range = formatRange(INSTANT, "2024-03-18T16:00:00Z", { timeZone: UTC, withYear: true });
    expect(range).toContain("Mar 15, 2024");
    expect(range).toContain("Mar 18, 2024");
  });

  it("tolerates a half-open range", () => {
    expect(formatRange(INSTANT, null, { timeZone: UTC, withYear: true })).toContain("Mar 15, 2024");
    expect(formatRange(null, INSTANT, { timeZone: UTC, withYear: true })).toContain("Mar 15, 2024");
    expect(formatRange(null, null)).toBe("");
  });

  it("spells out weekday, month and zone for accessibility", () => {
    // VoiceOver reads this verbatim, so an abbreviation or a missing zone is a
    // real defect rather than a cosmetic one.
    const label = formatAccessibleDateTime(INSTANT, { timeZone: UTC });
    expect(label).toContain("Friday");
    expect(label).toContain("March");
    expect(label).toContain("2024");
    expect(label).toContain("UTC");
    expectRenderable(label);
  });

  it("returns an empty string for unparseable input", () => {
    // Server rows carry nulls; every date entry point must absorb them.
    expect(formatDate(null, { timeZone: UTC })).toBe("");
    expect(formatTime(undefined, { timeZone: UTC })).toBe("");
    expect(formatDateTime("not a date", { timeZone: UTC })).toBe("");
    expect(formatDay("", { timeZone: UTC })).toBe("");
    expect(formatAccessibleDateTime(null, { timeZone: UTC })).toBe("");
  });

  it("shows the event's own zone alongside the viewer's when they differ", () => {
    const scheduled = formatScheduled(INSTANT, "Asia/Tokyo", { timeZone: UTC, withYear: true });
    expect(scheduled).toContain("2:30 PM");
    expect(scheduled).toContain("your time");
    expect(scheduled).toContain("11:30 PM");
    expectRenderable(scheduled);
  });

  it("omits the second zone when it is the same wall clock", () => {
    const scheduled = formatScheduled(INSTANT, UTC, { timeZone: UTC, withYear: true });
    expect(scheduled).toBe("Mar 15, 2024 at 2:30 PM");
  });
});

describe("weekdayNames and monthNames", () => {
  it("returns seven weekdays starting on Sunday by default", () => {
    expect(weekdayNames("short", { locale: "en" })).toEqual([
      "Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"
    ]);
  });

  it("rotates to a Monday start on request", () => {
    // The rotation is a slice-and-append; a naive implementation loses Sunday.
    const monday = weekdayNames("short", { locale: "en", startOnMonday: true });
    expect(monday).toHaveLength(7);
    expect(monday[0]).toBe("Mon");
    expect(monday[6]).toBe("Sun");
    expect(new Set(monday).size).toBe(7);
  });

  it("localizes weekday and month names", () => {
    expect(weekdayNames("long", { locale: "fr" })[0]).toBe("dimanche");
    expect(monthNames("long", { locale: "fr" })[0]).toBe("janvier");
    expect(monthNames("long", { locale: "de" })[0]).toBe("Januar");
  });

  it("returns twelve distinct months", () => {
    // The seed dates use day 15 of each month specifically so that no time-zone
    // rollover can push a name into the neighbouring month; if that seed were
    // day 1 or 31 this assertion would catch the resulting duplicate.
    const months = monthNames("long", { locale: "en" });
    expect(months).toHaveLength(12);
    expect(new Set(months).size).toBe(12);
    expect(months[0]).toBe("January");
    expect(months[11]).toBe("December");
  });
});

/* ------------------------------------------------------------------ *
 * Relative time
 * ------------------------------------------------------------------ */

describe("formatRelative", () => {
  const opts = { timeZone: UTC, now: NOW, withYear: true as const };

  it("says 'now' below 45 seconds", () => {
    expect(formatRelative(ago(0), opts)).toBe("now");
    expect(formatRelative(ago(44 * SECOND), opts)).toBe("now");
  });

  /**
   * BUG (characterization, not endorsement): the "now" window ends at 45s but
   * the minute bucket floors, so 45-59 seconds renders "0m". The correct
   * rendering is either "now" or "1m"; "0m" is neither. Pinned here so the
   * behaviour is visible and any fix is a deliberate, reviewed change.
   */
  it("renders '0m' between 45 and 59 seconds", () => {
    expect(formatRelative(ago(45 * SECOND), opts)).toBe("0m");
    expect(formatRelative(ago(59 * SECOND), opts)).toBe("0m");
  });

  it("uses the minute abbreviation up to 59 minutes", () => {
    expect(formatRelative(ago(MINUTE), opts)).toBe("1m");
    expect(formatRelative(ago(59 * MINUTE), opts)).toBe("59m");
  });

  it("switches to hours at exactly one hour, within the same calendar day", () => {
    expect(formatRelative(ago(HOUR), opts)).toBe("1h");
    expect(formatRelative(ago(14 * HOUR), opts)).toBe("14h");
  });

  /**
   * The hour bucket is gated on the calendar day, not only on the elapsed
   * hours. 23 hours before 14:30 on the 15th is 15:30 on the 14th — different
   * day, so it must read "Yesterday" even though `hours < 24`. Dropping the
   * calendar check would show "23h" for something a user filed yesterday.
   */
  it("prefers 'Yesterday' over an hour count across a day boundary", () => {
    expect(formatRelative(ago(23 * HOUR), opts)).toBe("Yesterday");
    expect(formatRelative(ago(25 * HOUR), opts)).toBe("Yesterday");
  });

  it("counts days from two days back up to six", () => {
    expect(formatRelative(ago(2 * DAY), opts)).toBe("2d");
    expect(formatRelative(ago(6 * DAY), opts)).toBe("6d");
  });

  it("switches to weeks at exactly seven days", () => {
    // days < 7 is exclusive, so day 7 must be "1w" and not "7d".
    expect(formatRelative(ago(7 * DAY), opts)).toBe("1w");
    expect(formatRelative(ago(4 * WEEK), opts)).toBe("4w");
  });

  it("falls back to a calendar date at five weeks", () => {
    // weeks < 5 is exclusive: week 5 is the first absolute rendering.
    expect(formatRelative(ago(5 * WEEK), opts)).toBe("Feb 9, 2024");
  });

  it("renders a future instant as an absolute date-time, never 'in ...'", () => {
    // Future rows are scheduled content; "in 2 hours" would be wrong copy for
    // a scheduled post header.
    expect(formatRelative(ahead(2 * HOUR), opts)).toBe("Mar 15, 2024 at 4:30 PM");
  });

  it("speaks the active language", () => {
    expect(formatRelative(ago(5 * MINUTE), { ...opts, locale: "fr" })).toBe("5 min");
    expect(formatRelative(ago(5 * MINUTE), { ...opts, locale: "ja" })).toBe("5分");
    expect(formatRelative(ago(10 * SECOND), { ...opts, locale: "de" })).toBe("jetzt");
    expect(formatRelative(ago(25 * HOUR), { ...opts, locale: "fr" })).toBe("Hier");
  });

  it("returns an empty string for unparseable input", () => {
    expect(formatRelative(null, opts)).toBe("");
  });
});

describe("formatRelativeLong", () => {
  const opts = { timeZone: UTC, now: NOW, withYear: true as const };

  it("says 'Just now' below 45 seconds in the past", () => {
    expect(formatRelativeLong(ago(30 * SECOND), opts)).toBe("Just now");
  });

  it("counts minutes up to the hour boundary", () => {
    expect(formatRelativeLong(ago(MINUTE), opts)).toBe("1 minute ago");
    expect(formatRelativeLong(ago(5 * MINUTE), opts)).toBe("5 minutes ago");
    // 3599s is the last second of the minute bucket; 3600s is the first hour.
    expect(formatRelativeLong(ago(HOUR - SECOND), opts)).toBe("59 minutes ago");
  });

  it("counts hours from exactly one hour to the day boundary", () => {
    expect(formatRelativeLong(ago(HOUR), opts)).toBe("1 hour ago");
    expect(formatRelativeLong(ago(DAY - SECOND), opts)).toBe("23 hours ago");
  });

  /**
   * `Intl.RelativeTimeFormat` is constructed with `numeric: "auto"`, which
   * turns ±1 day into "yesterday"/"tomorrow" rather than "1 day ago". That is
   * intentional and reads better, but it means the string for the day boundary
   * is not "1 day ago" — pinned so nobody "fixes" it into a numeric form.
   */
  it("uses the idiomatic word at exactly one day", () => {
    expect(formatRelativeLong(ago(DAY), opts)).toBe("yesterday");
    expect(formatRelativeLong(ahead(DAY), opts)).toBe("tomorrow");
  });

  it("counts days up to the four-week cutoff", () => {
    expect(formatRelativeLong(ago(2 * DAY), opts)).toBe("2 days ago");
    expect(formatRelativeLong(ago(4 * WEEK - SECOND), opts)).toBe("27 days ago");
  });

  it("falls back to an absolute date at exactly four weeks", () => {
    // seconds < 4 * WEEK is exclusive, so 4 weeks is the first absolute label.
    expect(formatRelativeLong(ago(4 * WEEK), opts)).toBe("Feb 16, 2024");
  });

  it("handles the future with a forward-looking phrase", () => {
    expect(formatRelativeLong(ahead(2 * HOUR), opts)).toBe("in 2 hours");
    expect(formatRelativeLong(ahead(5 * MINUTE), opts)).toBe("in 5 minutes");
  });

  /**
   * The "Just now" short-circuit is gated on `past`, so a near-future instant
   * skips it and is floored up to one minute by `Math.max(1, ...)`. Documented
   * because "in 1 minute" for something 30 seconds away is defensible, but
   * silently changing it to "Just now" would not be.
   */
  it("rounds a sub-minute future instant up to one minute", () => {
    expect(formatRelativeLong(ahead(30 * SECOND), opts)).toBe("in 1 minute");
  });

  it("speaks the active language", () => {
    expect(formatRelativeLong(ago(5 * MINUTE), { ...opts, locale: "fr" })).toBe("il y a 5 minutes");
    expect(formatRelativeLong(ago(3 * HOUR), { ...opts, locale: "de" })).toBe("vor 3 Stunden");
  });

  it("returns an empty string for unparseable input", () => {
    expect(formatRelativeLong(undefined, opts)).toBe("");
  });
});

/* ------------------------------------------------------------------ *
 * Duration, zones and territories
 * ------------------------------------------------------------------ */

describe("formatDuration", () => {
  it("pads seconds but not the leading field", () => {
    expect(formatDuration(64)).toBe("1:04");
    expect(formatDuration(0)).toBe("0:00");
    expect(formatDuration(59)).toBe("0:59");
    expect(formatDuration(600)).toBe("10:00");
  });

  it("adds an hours field only once there is one", () => {
    // 3599s must stay "59:59", not become "0:59:59".
    expect(formatDuration(3599)).toBe("59:59");
    expect(formatDuration(3600)).toBe("1:00:00");
    expect(formatDuration(45005)).toBe("12:30:05");
  });

  it("clamps negative and garbage input to zero rather than rendering NaN", () => {
    expect(formatDuration(-5)).toBe("0:00");
    expect(formatDuration(Number.NaN)).toBe("0:00");
  });

  it("localizes the digits without localizing the colon shape", () => {
    const arabic = formatDuration(64, { locale: "ar" });
    expect(arabic).toContain(":");
    expect(arabic).toMatch(/[٠-٩]/);
    expectRenderable(arabic);
  });
});

describe("timeZoneLabel", () => {
  it("uses the long localized zone name when ICU has one", () => {
    // Standard vs Daylight depends on the current date, which is exactly why
    // this asserts on the stable part of the name.
    expect(timeZoneLabel(NEW_YORK, { locale: "en" })).toMatch(/Eastern .*Time/);
  });

  it("falls back to the city segment for a zone ICU cannot name", () => {
    // A GMT+n offset is not a useful label, and an unknown identifier throws;
    // both routes must land on the readable city.
    expect(timeZoneLabel("Not/A_Real_Zone", { locale: "en" })).toBe("A Real Zone");
  });

  it("returns an empty string for a blank identifier", () => {
    expect(timeZoneLabel("")).toBe("");
    expect(timeZoneLabel("   ")).toBe("");
  });
});

describe("regionDisplayName", () => {
  it("names a territory in the active language", () => {
    expect(regionDisplayName("BR", { locale: "en" })).toBe("Brazil");
    expect(regionDisplayName("DE", { locale: "fr" })).toBe("Allemagne");
  });

  it("accepts a lowercase code", () => {
    expect(regionDisplayName("jp", { locale: "en" })).toBe("Japan");
  });
});

/* ------------------------------------------------------------------ *
 * Reduced-ICU (Hermes) fallbacks
 *
 * Everything above runs on Node's full ICU. On a Hermes build without it these
 * constructors are missing or throw, and the fallback branches below are what
 * actually renders. They have no other coverage anywhere, and a defect in them
 * is invisible in CI and visible on every device — so this is the section that
 * matters most.
 * ------------------------------------------------------------------ */

type MutableIntl = Record<string, unknown>;
const intl = Intl as unknown as MutableIntl;

describe("reduced-ICU fallbacks", () => {
  const originals: Record<string, unknown> = {
    NumberFormat: intl.NumberFormat,
    DateTimeFormat: intl.DateTimeFormat,
    RelativeTimeFormat: intl.RelativeTimeFormat,
    ListFormat: intl.ListFormat,
    DisplayNames: intl.DisplayNames,
    PluralRules: intl.PluralRules
  };

  /** Replaces a constructor with one that throws, as a stripped ICU does. */
  const breakIntl = (name: keyof typeof originals): void => {
    intl[name] = function Broken() {
      throw new TypeError(`${name} is unavailable in this ICU build`);
    };
  };

  /**
   * Makes a constructor unreachable, as a Hermes build that never shipped it
   * does. Assigning `undefined` rather than `delete`-ing exercises exactly the
   * branches that matter — the `if (RelativeTimeFormat)` and `if (DisplayNames)`
   * presence checks, and the `new undefined(...)` TypeError that `formatList`
   * relies on catching.
   */
  const removeIntl = (name: keyof typeof originals): void => {
    intl[name] = undefined;
  };

  // Restoring in afterEach rather than at the end of each test means a failing
  // assertion cannot leave a broken Intl behind and cascade into every later
  // test in the file.
  afterEach(() => {
    Object.entries(originals).forEach(([name, value]) => {
      intl[name] = value;
    });
  });

  it("restores Intl between tests", () => {
    // Guards the guard: if the afterEach ever stopped working, this would be
    // the first test to notice.
    expect(formatNumber(1234.56, { locale: "en" })).toBe("1,234.56");
  });

  describe("without Intl.NumberFormat", () => {
    it("still renders a plain number", () => {
      breakIntl("NumberFormat");
      const value = formatNumber(1234.56, { locale: "de" });
      expect(value).toBe("1234.56");
      expectRenderable(value);
    });

    it("still renders a count instead of dropping it", () => {
      breakIntl("NumberFormat");
      const value = formatCount(25000, { locale: "en" });
      expect(value).toBe("25000");
      expectRenderable(value);
    });

    it("still renders a currency with its code and two decimals", () => {
      breakIntl("NumberFormat");
      const value = formatCurrencyAmount(1234.5, { locale: "en", currency: "USD" });
      // No symbol is available without ICU, so the code leads. "USD 1234.50" is
      // unambiguous; a blank price field is not.
      expect(value).toBe("USD 1234.50");
      expectRenderable(value);
    });

    it("still renders a percentage", () => {
      breakIntl("NumberFormat");
      const value = formatPercent(0.42, { locale: "en" });
      expect(value).toBe("42%");
      expectRenderable(value);
    });

    it("still renders an ordinal, losing only the suffix", () => {
      breakIntl("NumberFormat");
      const value = formatOrdinal(3, { locale: "en" });
      expect(value).toMatch(/^3/);
      expectRenderable(value);
    });

    it("falls back to the catalog unit templates for measurements", () => {
      breakIntl("NumberFormat");
      // This is the branch that reads `common:units.*`. If the catalog key were
      // renamed, the engine would humanize it and this would read "Kilometer".
      expect(formatMeasurement(1.5, "kilometer", { locale: "en" })).toBe("1.5 km");
      expect(formatDistance(5000, { locale: "en" })).toBe("3.1 mi");
      expect(formatDistance(5000, { locale: "fr" })).toBe("5 km");
      expect(formatWeight(2500, { locale: "de" })).toBe("2.5 kg");
      expect(formatTemperature(20, { locale: "en" })).toBe("68°F");
      expect(formatTemperature(20, { locale: "fr" })).toBe("20°C");
    });

    /**
     * BUG (characterization): the catch branch calls
     * `translate("common:units.<unit>", { count })` without forwarding
     * `options.locale`, so the fallback always reads the *active* catalog. An
     * Arabic user on a reduced-ICU build sees the English "km" rather than
     * "كم" — the one place in this module where a fallback silently changes
     * language rather than just losing polish.
     */
    it("ignores the requested locale in the unit fallback", () => {
      breakIntl("NumberFormat");
      const value = formatMeasurement(5, "kilometer", { locale: "ar" });
      expect(value).toBe("5 km");
      expect(value).not.toContain("كم");
      expectRenderable(value);
    });

    it("still renders file sizes", () => {
      breakIntl("NumberFormat");
      expect(formatFileSize(1536)).toBe("1.5 KB");
      expectRenderable(formatFileSize(1024 ** 3));
    });

    it("still renders a zero-padded duration", () => {
      breakIntl("NumberFormat");
      expect(formatDuration(64)).toBe("1:04");
      expect(formatDuration(45005)).toBe("12:30:05");
    });

    it("never throws for any numeric entry point", () => {
      breakIntl("NumberFormat");
      const calls: Array<() => string> = [
        () => formatNumber(1e21, { locale: "ar" }),
        () => formatCount(0, { locale: "hi" }),
        () => formatCurrencyAmount(-0.005, { locale: "ja", currency: "JPY" }),
        () => formatPercent(1.5, { locale: "de" }),
        () => formatOrdinal(0, { locale: "en" }),
        () => formatFileSize(1),
        () => formatDuration(1)
      ];
      calls.forEach((call) => {
        expect(call).not.toThrow();
        expectRenderable(call());
      });
    });
  });

  describe("without Intl.ListFormat", () => {
    it("joins through the catalog's separator and pair templates", () => {
      removeIntl("ListFormat");
      // Loses the serial comma (the catalog pair is "{{first}} and {{second}}")
      // but stays grammatical, which is the whole point of the fallback.
      expect(formatList(["Ann", "Bo", "Cy"], { locale: "en" })).toBe("Ann, Bo and Cy");
      expect(formatList(["Ann", "Bo"], { locale: "en" })).toBe("Ann and Bo");
    });

    it("uses each language's own connector in the fallback too", () => {
      removeIntl("ListFormat");
      expect(formatList(["Ann", "Bo"], { locale: "fr" })).toBe("Ann et Bo");
      expect(formatList(["Ann", "Bo"], { locale: "de" })).toBe("Ann und Bo");
      expect(formatList(["Ann", "Bo"], { locale: "ja" })).toBe("AnnとBo");
    });

    it("still appends the overflow count", () => {
      removeIntl("ListFormat");
      const value = formatList(["Ann", "Bo", "Cy", "Di"], { locale: "en", max: 2 });
      expect(value).toBe("Ann and Bo, +2 more");
      expectRenderable(value);
    });

    it("survives a ListFormat that exists but throws", () => {
      // Some builds ship the constructor and reject the locale, which is a
      // different failure from the constructor being absent.
      breakIntl("ListFormat");
      expect(formatList(["Ann", "Bo", "Cy"], { locale: "en" })).toBe("Ann, Bo and Cy");
    });
  });

  describe("without Intl.RelativeTimeFormat", () => {
    const opts = { timeZone: UTC, now: NOW, withYear: true as const };

    it("falls back to the catalog's plural families", () => {
      removeIntl("RelativeTimeFormat");
      expect(formatRelativeLong(ago(MINUTE), opts)).toBe("1 minute ago");
      expect(formatRelativeLong(ago(5 * MINUTE), opts)).toBe("5 minutes ago");
      expect(formatRelativeLong(ago(HOUR), opts)).toBe("1 hour ago");
      expect(formatRelativeLong(ago(2 * DAY), opts)).toBe("2 days ago");
    });

    it("keeps the catalog fallback in other languages", () => {
      removeIntl("RelativeTimeFormat");
      expect(formatRelativeLong(ago(5 * MINUTE), { ...opts, locale: "fr" })).toBe("Il y a 5 minutes");
      expect(formatRelativeLong(ago(5 * MINUTE), { ...opts, locale: "ja" })).toBe("5分前");
    });

    /**
     * The catalog templates are all past-tense ("{{count}} minutes ago"), so a
     * future instant renders as though it were in the past once
     * RelativeTimeFormat is gone. Characterized, not endorsed: it is a real
     * copy defect on reduced-ICU devices and there is no forward-looking key
     * in the catalogs to fix it with.
     */
    it("loses the future/past distinction, rendering a future instant as past", () => {
      removeIntl("RelativeTimeFormat");
      const value = formatRelativeLong(ahead(2 * HOUR), opts);
      expect(value).toBe("2 hours ago");
      expectRenderable(value);
    });

    it("survives a RelativeTimeFormat that exists but throws", () => {
      breakIntl("RelativeTimeFormat");
      expect(formatRelativeLong(ago(5 * MINUTE), opts)).toBe("5 minutes ago");
    });

    it("does not affect the compact form, which never used Intl", () => {
      removeIntl("RelativeTimeFormat");
      expect(formatRelative(ago(5 * MINUTE), opts)).toBe("5m");
    });
  });

  describe("without Intl.DisplayNames", () => {
    it("falls back to the static English territory map", () => {
      removeIntl("DisplayNames");
      expect(regionDisplayName("BR", { locale: "en" })).toBe("Brazil");
      expect(regionDisplayName("de", { locale: "fr" })).toBe("Germany");
    });

    it("falls back to the bare uppercase code for an unmapped territory", () => {
      removeIntl("DisplayNames");
      // "PT" is recognisable; an empty picker row is not.
      const value = regionDisplayName("pt");
      expect(value).toBe("PT");
      expectRenderable(value);
    });

    it("survives a DisplayNames that exists but throws", () => {
      breakIntl("DisplayNames");
      expect(regionDisplayName("US", { locale: "en" })).toBe("United States");
    });
  });

  describe("without Intl.PluralRules", () => {
    it("still renders an ordinal, dropping only the suffix", () => {
      breakIntl("PluralRules");
      const value = formatOrdinal(21, { locale: "en" });
      expect(value).toBe("21");
      expectRenderable(value);
    });
  });

  describe("without Intl.DateTimeFormat", () => {
    it("still labels a time zone by its city segment", () => {
      breakIntl("DateTimeFormat");
      const value = timeZoneLabel(NEW_YORK, { locale: "en" });
      expect(value).toBe("New York");
      expectRenderable(value);
    });

    /**
     * KNOWN GAP, characterized so it is not discovered in production.
     *
     * Unlike every other Intl surface in this module, `DateTimeFormat` is used
     * with no guard at all in `weekdayNames`, `monthNames` and (via
     * `localTime`) `formatDate`/`formatTime`/`formatRelative`. On an ICU build
     * that lacks it, these throw rather than degrading — a calendar header or a
     * feed row would crash the screen instead of rendering something plain.
     *
     * These assertions pin the *current* behaviour. If a fallback is ever added
     * they will fail, which is the correct signal to update them.
     */
    it("throws from the date formatters, which have no fallback", () => {
      breakIntl("DateTimeFormat");
      expect(() => weekdayNames("short", { locale: "en" })).toThrow();
      expect(() => monthNames("long", { locale: "en" })).toThrow();
      expect(() => formatDate(INSTANT, { timeZone: UTC, withYear: true })).toThrow();
      expect(() => formatTime(INSTANT, { timeZone: UTC })).toThrow();
      expect(() => formatRelative(ago(2 * HOUR), { timeZone: UTC, now: NOW })).toThrow();
    });

    it("still answers the sub-minute relative cases that never touch a date formatter", () => {
      breakIntl("DateTimeFormat");
      // The "now" and minute branches short-circuit before `calendarParts`, so
      // the most common feed timestamps survive even here.
      expect(formatRelative(ago(10 * SECOND), { timeZone: UTC, now: NOW })).toBe("now");
      expect(formatRelative(ago(5 * MINUTE), { timeZone: UTC, now: NOW })).toBe("5m");
    });
  });

  describe("with no Intl at all", () => {
    it("keeps every non-date formatter renderable", () => {
      // The worst realistic case: a build with only PluralRules-free, format-free
      // Intl. Nothing here may throw, and nothing may leak a placeholder.
      breakIntl("NumberFormat");
      breakIntl("PluralRules");
      removeIntl("RelativeTimeFormat");
      removeIntl("ListFormat");
      removeIntl("DisplayNames");

      const outputs = [
        formatNumber(1234.56, { locale: "en" }),
        formatCount(25000, { locale: "en" }),
        formatCurrencyAmount(9.99, { locale: "en", currency: "EUR" }),
        formatPercent(0.42, { locale: "en" }),
        formatOrdinal(2, { locale: "en" }),
        formatMeasurement(3, "mile", { locale: "en" }),
        formatFileSize(2048),
        formatDuration(125),
        formatList(["Ann", "Bo", "Cy"], { locale: "en" }),
        formatRelativeLong(ago(5 * MINUTE), { timeZone: UTC, now: NOW }),
        regionDisplayName("US"),
        timeZoneLabel(NEW_YORK)
      ];

      outputs.forEach((value) => {
        expect(value.length).toBeGreaterThan(0);
        expectRenderable(value);
      });
    });
  });
});
