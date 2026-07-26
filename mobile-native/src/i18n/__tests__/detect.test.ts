/**
 * Language detection is the only i18n code that runs before the user can touch
 * anything, on every single launch, and it reads from native modules that are
 * absent under Jest, absent in Expo Go and inconsistent across OS versions. A
 * regression here is invisible in review and shows up as "the app is in English
 * in Mexico".
 *
 * These tests pin down two things the spec is strict about: the priority order
 * of the six detection tiers, and the fact that `region` survives a language
 * fallback so `Intl` still formats currency locally. Every device value is
 * mocked explicitly — nothing here may depend on the locale of the machine
 * running the suite, or CI in one region will disagree with a laptop in another.
 */

import { NativeModules, Platform } from "react-native";

import {
  detectLocale,
  getDeviceLanguages,
  getDeviceLocale,
  getDeviceRegion
} from "../detect";
import {
  DEFAULT_LOCALE,
  SUPPORTED_LOCALES,
  directionOf,
  foldForSearch,
  isRtlLanguage,
  isSupportedLocale,
  languageOf,
  localeForRegion,
  normalizeTag,
  regionOf,
  resolveSupportedLocale,
  searchLocales,
  toIntlLocale
} from "../locales";

const nativeModules = NativeModules as Record<string, any>;

const originalPlatformOS = Platform.OS;
const originalSettingsManager = nativeModules.SettingsManager;
const originalI18nManager = nativeModules.I18nManager;
const originalDateTimeFormat = Intl.DateTimeFormat;
const originalNavigator = (globalThis as { navigator?: unknown }).navigator;

/** iOS reports an ordered preference list through SettingsManager. */
function mockIos(appleLanguages: unknown, appleLocale?: unknown): void {
  (Platform as { OS: string }).OS = "ios";
  nativeModules.SettingsManager = { settings: { AppleLanguages: appleLanguages, AppleLocale: appleLocale } };
}

/** Android reports a single active locale through I18nManager. */
function mockAndroid(localeIdentifier: unknown): void {
  (Platform as { OS: string }).OS = "android";
  nativeModules.I18nManager = { localeIdentifier };
  nativeModules.SettingsManager = undefined;
}

/** The device reports nothing at all — the Expo Go / stripped-build case. */
function mockNoNativeLanguages(): void {
  (Platform as { OS: string }).OS = "ios";
  nativeModules.SettingsManager = undefined;
}

/**
 * `getDeviceLocale` reads `Intl`, which resolves to whatever the host machine
 * is set to. Left unmocked, every fallback assertion below would pass on a
 * US laptop and fail on a French one.
 */
function mockIntlLocale(locale: string): void {
  (Intl as { DateTimeFormat: unknown }).DateTimeFormat = function DateTimeFormatStub() {
    return { resolvedOptions: () => ({ locale }) };
  };
}

function mockIntlThrows(): void {
  (Intl as { DateTimeFormat: unknown }).DateTimeFormat = function DateTimeFormatStub() {
    throw new Error("Intl unavailable in this build");
  };
}

afterEach(() => {
  (Platform as { OS: string }).OS = originalPlatformOS;
  nativeModules.SettingsManager = originalSettingsManager;
  nativeModules.I18nManager = originalI18nManager;
  (Intl as { DateTimeFormat: unknown }).DateTimeFormat = originalDateTimeFormat;
  Object.defineProperty(globalThis, "navigator", {
    value: originalNavigator,
    writable: true,
    configurable: true
  });
  jest.restoreAllMocks();
});

describe("getDeviceLanguages", () => {
  it("honors the ordered iOS preference list", () => {
    // iOS users can rank languages. A user whose first choice we do not ship
    // must get their second choice, not English — so order is load-bearing.
    mockIos(["ht-HT", "fr-FR", "en-US"]);
    expect(getDeviceLanguages()).toEqual(["ht-ht", "fr-fr", "en-us"]);
  });

  it("appends AppleLocale after the preference list", () => {
    mockIos(["fr-FR"], "en_US");
    expect(getDeviceLanguages()).toEqual(["fr-fr", "en-us"]);
  });

  it("collapses duplicates so a tier is never consulted twice", () => {
    mockIos(["fr-FR", "fr_FR", "FR-fr"], "fr-FR");
    expect(getDeviceLanguages()).toEqual(["fr-fr"]);
  });

  it("drops empty, null and non-string entries instead of emitting blanks", () => {
    // A blank tag would be recorded as a candidate with raw "" and pollute the
    // audit trail; `normalizeTag` is expected to filter it out first.
    mockIos(["", null, undefined, "  ", 0, "ja-JP"]);
    expect(getDeviceLanguages()).toEqual(["0", "ja-jp"]);
  });

  it("reads I18nManager.localeIdentifier on Android", () => {
    mockAndroid("pt_BR");
    expect(getDeviceLanguages()).toEqual(["pt-br"]);
  });

  it("reads navigator.languages on web", () => {
    (Platform as { OS: string }).OS = "web";
    Object.defineProperty(globalThis, "navigator", {
      value: { languages: ["de-DE", "en-GB"], language: "de-DE" },
      writable: true,
      configurable: true
    });
    expect(getDeviceLanguages()).toEqual(["de-de", "en-gb"]);
  });

  it("returns an empty list rather than throwing when the native module is absent", () => {
    mockNoNativeLanguages();
    expect(getDeviceLanguages()).toEqual([]);
  });

  it("swallows a throwing native module so launch is never blocked", () => {
    // Some OS versions expose SettingsManager but throw when `settings` is
    // read. That must degrade to the Intl tiers, not crash the app at startup.
    (Platform as { OS: string }).OS = "ios";
    nativeModules.SettingsManager = {
      get settings(): never {
        throw new Error("bridge not ready");
      }
    };
    expect(() => getDeviceLanguages()).not.toThrow();
    expect(getDeviceLanguages()).toEqual([]);
  });

  it("ignores AppleLanguages when it is not an array", () => {
    mockIos("fr-FR", "de-DE");
    expect(getDeviceLanguages()).toEqual(["de-de"]);
  });
});

describe("getDeviceLocale", () => {
  it("normalizes whatever Intl resolves", () => {
    mockIntlLocale("PT_BR");
    expect(getDeviceLocale()).toBe("pt-br");
  });

  it("falls back to the default locale when Intl throws", () => {
    // Hermes builds with the Intl variant stripped throw here. Returning the
    // default keeps this tier a floor rather than a crash.
    mockIntlThrows();
    expect(getDeviceLocale()).toBe(DEFAULT_LOCALE);
  });
});

describe("getDeviceRegion", () => {
  it("prefers the region from the device language list", () => {
    // The language list carries the user's own choice; Intl carries the OS's.
    mockIos(["es-MX"]);
    mockIntlLocale("en-US");
    expect(getDeviceRegion()).toBe("MX");
  });

  it("skips region-less tags to find the first tag that has one", () => {
    mockIos(["ja", "ko", "fr-CA"]);
    mockIntlLocale("en-US");
    expect(getDeviceRegion()).toBe("CA");
  });

  it("backfills from the resolved Intl locale when no language carries a region", () => {
    mockIos(["ja", "ko"]);
    mockIntlLocale("en-GB");
    expect(getDeviceRegion()).toBe("GB");
  });

  it("returns an empty string when nothing reports a region", () => {
    mockNoNativeLanguages();
    mockIntlLocale("en");
    expect(getDeviceRegion()).toBe("");
  });
});

describe("detectLocale priority chain", () => {
  /**
   * Every tier below the one under test is given a *different*, valid answer,
   * so a test can only pass if the chain stopped at the right rung. Asserting
   * "the winner is es" against a device that also says es proves nothing.
   */

  it("lets the saved preference beat every lower tier", () => {
    mockIos(["fr-FR"]);
    mockIntlLocale("de-DE");
    const result = detectLocale({ savedPreference: "es", accountPreference: "ja" });
    expect(result.locale).toBe("es");
    expect(result.source).toBe("saved-preference");
    expect(result.candidates).toEqual([{ source: "saved-preference", raw: "es", resolved: "es" }]);
  });

  it("lets the account preference beat the device but lose to the saved one", () => {
    mockIos(["fr-FR"]);
    mockIntlLocale("de-DE");

    const restored = detectLocale({ savedPreference: null, accountPreference: "ja" });
    expect(restored.locale).toBe("ja");
    expect(restored.source).toBe("account-preference");

    const overridden = detectLocale({ savedPreference: "ko", accountPreference: "ja" });
    expect(overridden.locale).toBe("ko");
    expect(overridden.source).toBe("saved-preference");
  });

  it("lets the device language beat the device locale but lose to both preferences", () => {
    mockIos(["ko-KR"]);
    mockIntlLocale("de-DE");

    const device = detectLocale();
    expect(device.locale).toBe("ko");
    expect(device.source).toBe("device-language");

    expect(detectLocale({ accountPreference: "hi" }).source).toBe("account-preference");
    expect(detectLocale({ savedPreference: "hi" }).source).toBe("saved-preference");
  });

  it("falls to the device locale only when no device language resolves", () => {
    mockIos(["xx-YY"]);
    mockIntlLocale("pt-BR");
    const result = detectLocale();
    expect(result.locale).toBe("pt");
    expect(result.source).toBe("device-locale");
  });

  it("falls to the system fallback when nothing resolves", () => {
    mockNoNativeLanguages();
    mockIntlLocale("xx-YY");
    const result = detectLocale();
    expect(result.locale).toBe(DEFAULT_LOCALE);
    expect(result.source).toBe("fallback");
  });

  /**
   * The `device-region` tier is currently unreachable as a *winner*, and that is
   * worth pinning rather than leaving as a surprise: `region` is always derived
   * with `regionOf` from a tag that the `device-language` or `device-locale`
   * tier already ran through `resolveSupportedLocale`, which itself consults the
   * same region table. So any region that `localeForRegion` could resolve has
   * already produced a winner one rung higher. It still appears in the audit
   * trail, which is what this asserts. See the report accompanying this file.
   */
  it("records device-region as an unresolved candidate rather than a winner", () => {
    mockNoNativeLanguages();
    mockIntlLocale("xx-YY");
    const result = detectLocale();
    expect(result.candidates.map((candidate) => candidate.source)).toEqual([
      "device-locale",
      "device-region",
      "fallback"
    ]);
    expect(result.candidates.find((candidate) => candidate.source === "device-region")).toEqual({
      source: "device-region",
      raw: "YY",
      resolved: null
    });
  });

  it("skips the device-region tier entirely when no region was reported", () => {
    mockNoNativeLanguages();
    mockIntlLocale("xx");
    const result = detectLocale();
    expect(result.region).toBe("");
    expect(result.candidates.map((candidate) => candidate.source)).toEqual(["device-locale", "fallback"]);
  });
});

describe("detectLocale candidate audit trail", () => {
  /**
   * `candidates` is what the settings screen shows when a user asks "why is my
   * app in this language". If a tier stops being recorded, or the order drifts,
   * that explanation becomes wrong while the chosen locale stays right — so the
   * whole array is asserted, not just the winner.
   */
  it("records every consulted tier in priority order with its resolution", () => {
    mockIos(["qq-QQ", "fr-CA"]);
    mockIntlLocale("de-DE");
    const result = detectLocale({ savedPreference: "xx-YY", accountPreference: "zz" });

    expect(result.candidates).toEqual([
      { source: "saved-preference", raw: "xx-yy", resolved: null },
      { source: "account-preference", raw: "zz", resolved: null },
      { source: "device-language", raw: "qq-qq", resolved: null },
      { source: "device-language", raw: "fr-ca", resolved: "fr" }
    ]);
    expect(result.locale).toBe("fr");
    expect(result.source).toBe("device-language");
  });

  it("does not record a tier that reported nothing", () => {
    // An absent saved preference is not a "consulted tier" — emitting a
    // candidate with raw "" would make the settings explanation nonsense.
    mockIos(["ja-JP"]);
    mockIntlLocale("en-US");
    const result = detectLocale({ savedPreference: null, accountPreference: "   " });
    expect(result.candidates).toEqual([{ source: "device-language", raw: "ja-jp", resolved: "ja" }]);
  });

  it("normalizes the raw tag it records so the trail is comparable", () => {
    mockIos([]);
    mockIntlLocale("en-US");
    const result = detectLocale({ savedPreference: "  PT_br  " });
    expect(result.candidates[0]).toEqual({ source: "saved-preference", raw: "pt-br", resolved: "pt" });
  });

  it("records the fallback tier explicitly so the trail is never empty", () => {
    mockNoNativeLanguages();
    mockIntlLocale("xx");
    const result = detectLocale();
    expect(result.candidates[result.candidates.length - 1]).toEqual({
      source: "fallback",
      raw: DEFAULT_LOCALE,
      resolved: DEFAULT_LOCALE
    });
  });
});

describe("detectLocale region preservation", () => {
  /**
   * The documented case from detect.ts: "an `es` speaker in `MX` still gets
   * MXN". The catalog falls back to the base `es` bundle because we ship no
   * es-MX translation, but if `region` were dropped along with the subtag,
   * `Intl` would format Mexican prices as euros.
   */
  it("keeps region MX when es-MX collapses to the base es catalog", () => {
    mockIos(["es-MX"]);
    mockIntlLocale("en-US");
    const result = detectLocale();
    expect(result.locale).toBe("es");
    expect(result.region).toBe("MX");
    expect(toIntlLocale(result.locale, result.region)).toBe("es-MX");
  });

  it("keeps region MX when the language itself is unsupported", () => {
    // Nahuatl in Mexico: the language is not shipped, so the region table picks
    // Spanish — but the region must stay MX, not become ES.
    mockIos(["nah-MX"]);
    mockIntlLocale("en-US");
    const result = detectLocale();
    expect(result.locale).toBe("es");
    expect(result.region).toBe("MX");
    expect(toIntlLocale(result.locale, result.region)).toBe("es-MX");
  });

  it("keeps the device region even when a saved preference overrides the language", () => {
    // A user in Mexico who picked English still wants pesos.
    mockIos(["es-MX"]);
    mockIntlLocale("en-US");
    const result = detectLocale({ savedPreference: "en" });
    expect(result.locale).toBe("en");
    expect(result.region).toBe("MX");
  });

  it("keeps the device region all the way down to the system fallback", () => {
    mockNoNativeLanguages();
    mockIntlLocale("xx-YY");
    const result = detectLocale();
    expect(result.source).toBe("fallback");
    expect(result.region).toBe("YY");
  });
});

describe("detectLocale with garbage device values", () => {
  const garbage: Array<[string, unknown]> = [
    ["unsupported tag", "xx-YY"],
    ["empty string", ""],
    ["whitespace only", "   "],
    ["null", null],
    ["undefined", undefined],
    ["lone separator", "-"],
    ["trailing separator", "xx-"],
    ["numeric", 12345],
    ["object", { toString: () => "xx" }],
    ["over-long subtag", "qqqqqqqqqqqq-ZZZZ"],
    ["underscore soup", "__xx__YY__"]
  ];

  it.each(garbage)("never throws and still returns a supported locale for %s", (_label, value) => {
    mockIos([value]);
    mockIntlLocale("xx-ZZ");
    let result: ReturnType<typeof detectLocale> | undefined;
    expect(() => {
      result = detectLocale({ savedPreference: value as string, accountPreference: value as string });
    }).not.toThrow();
    expect(isSupportedLocale(result!.locale)).toBe(true);
    expect(result!.locale).toBe(DEFAULT_LOCALE);
  });

  it("survives a device language list that is not a list", () => {
    mockIos({ 0: "fr" } as unknown);
    mockIntlLocale("xx-ZZ");
    expect(detectLocale().locale).toBe(DEFAULT_LOCALE);
  });

  it("survives every device source failing at once", () => {
    // Stripped Hermes plus a missing bridge: the worst realistic launch.
    mockNoNativeLanguages();
    mockIntlThrows();
    const result = detectLocale();
    expect(result.locale).toBe(DEFAULT_LOCALE);
    // Intl threw, so getDeviceLocale returned the default, which resolves —
    // the chain stops one rung above the explicit fallback.
    expect(result.source).toBe("device-locale");
    expect(result.region).toBe("");
  });
});

describe("regional variants resolve to the shipped base catalog", () => {
  const variants: Array<[string, string]> = [
    ["pt-BR", "pt"],
    ["pt-PT", "pt"],
    ["zh-Hant", "zh"],
    ["zh-TW", "zh"],
    ["zh-Hans-CN", "zh"],
    ["es-419", "es"],
    ["es-MX", "es"],
    ["en-GB", "en"],
    ["fr-CA", "fr"],
    ["ar-EG", "ar"]
  ];

  it.each(variants)("detects %s as %s", (tag, expected) => {
    mockIos([tag]);
    mockIntlLocale("xx-ZZ");
    const result = detectLocale();
    expect(result.locale).toBe(expected);
    expect(result.source).toBe("device-language");
  });

  it.each(variants)("resolves %s to %s directly", (tag, expected) => {
    expect(resolveSupportedLocale(tag)).toBe(expected);
  });
});

describe("normalizeTag", () => {
  it.each([
    ["pt_BR", "pt-br"],
    ["PT-br", "pt-br"],
    ["  zh_Hans_CN  ", "zh-hans-cn"],
    ["en", "en"],
    ["", ""]
  ])("normalizes %p to %p", (input, expected) => {
    expect(normalizeTag(input)).toBe(expected);
  });

  it("returns an empty string for nullish input rather than the string 'null'", () => {
    // `String(null)` is "null", which would resolve to nothing but would still
    // be recorded as a candidate and shown to the user.
    expect(normalizeTag(null)).toBe("");
    expect(normalizeTag(undefined)).toBe("");
  });
});

describe("languageOf", () => {
  it.each([
    ["zh-Hans-CN", "zh"],
    ["pt_BR", "pt"],
    ["EN", "en"],
    ["es-419", "es"],
    ["", ""]
  ])("extracts the primary subtag of %p as %p", (input, expected) => {
    expect(languageOf(input)).toBe(expected);
  });

  it("returns an empty string for nullish input", () => {
    expect(languageOf(null)).toBe("");
  });
});

describe("regionOf", () => {
  it.each([
    ["en-US", "US"],
    ["pt_br", "BR"],
    ["zh-Hans-CN", "CN"],
    ["es-419", "419"],
    ["en", ""],
    ["zh-Hant", ""],
    ["", ""]
  ])("extracts the region of %p as %p", (input, expected) => {
    expect(regionOf(input)).toBe(expected);
  });

  it("does not mistake a four-letter script subtag for a region", () => {
    // "Hant" is a script, not a region. Treating it as one would send every
    // Traditional Chinese user through the region table.
    expect(regionOf("zh-Hant-TW")).toBe("TW");
  });

  it("returns an empty string for nullish input", () => {
    expect(regionOf(undefined)).toBe("");
  });
});

describe("isSupportedLocale", () => {
  it("accepts every shipped code, in any casing", () => {
    SUPPORTED_LOCALES.forEach((locale) => {
      expect(isSupportedLocale(locale.code)).toBe(true);
      expect(isSupportedLocale(locale.code.toUpperCase())).toBe(true);
    });
  });

  it("rejects regional variants, which are resolved rather than shipped", () => {
    // Catalog keys are bare language codes; "pt-BR" has no bundle of its own.
    expect(isSupportedLocale("pt-BR")).toBe(false);
    expect(isSupportedLocale("en-US")).toBe(false);
  });

  it("rejects unknown and nullish input", () => {
    expect(isSupportedLocale("xx")).toBe(false);
    expect(isSupportedLocale("")).toBe(false);
    expect(isSupportedLocale(null)).toBe(false);
  });
});

describe("resolveSupportedLocale", () => {
  it("returns shipped codes unchanged", () => {
    SUPPORTED_LOCALES.forEach((locale) => {
      expect(resolveSupportedLocale(locale.code)).toBe(locale.code);
    });
  });

  it("maps three-letter tags some Android builds report", () => {
    expect(resolveSupportedLocale("hat")).toBe("ht");
    expect(resolveSupportedLocale("jpn")).toBe("ja");
    expect(resolveSupportedLocale("zho")).toBe("zh");
    expect(resolveSupportedLocale("ger")).toBe("de");
  });

  it("falls back to the region's dominant language for an unshipped language", () => {
    // Basque in Spain, Nahuatl in Mexico: better Spanish than English.
    expect(resolveSupportedLocale("eu-ES")).toBe("es");
    expect(resolveSupportedLocale("nah-MX")).toBe("es");
    expect(resolveSupportedLocale("br-FR")).toBe("fr");
  });

  it("returns null so the caller can keep walking the chain", () => {
    expect(resolveSupportedLocale("xx-YY")).toBeNull();
    expect(resolveSupportedLocale("")).toBeNull();
    expect(resolveSupportedLocale(null)).toBeNull();
    expect(resolveSupportedLocale("   ")).toBeNull();
  });
});

describe("localeForRegion", () => {
  it.each([
    ["MX", "es"],
    ["mx", "es"],
    ["  br  ", "pt"],
    ["HT", "ht"],
    ["JP", "ja"],
    ["TW", "zh"],
    ["BE", "fr"],
    ["IN", "hi"]
  ])("maps region %p to %p", (region, expected) => {
    expect(localeForRegion(region)).toBe(expected);
  });

  it("returns null for regions with no shipped language", () => {
    expect(localeForRegion("ZZ")).toBeNull();
    expect(localeForRegion("")).toBeNull();
    expect(localeForRegion(null)).toBeNull();
  });

  it("only ever names a language the app actually ships", () => {
    // A typo in the region table would otherwise route users to a catalog that
    // does not exist and blank the UI.
    const codes = new Set(SUPPORTED_LOCALES.map((locale) => locale.code));
    ["AE", "AR", "BR", "CN", "DE", "FR", "HT", "IN", "JP", "KR", "MX", "PT", "SG"].forEach((region) => {
      expect(codes.has(localeForRegion(region)!)).toBe(true);
    });
  });
});

describe("text direction", () => {
  it("marks Arabic as RTL and nothing else", () => {
    expect(isRtlLanguage("ar")).toBe(true);
    SUPPORTED_LOCALES.filter((locale) => locale.code !== "ar").forEach((locale) => {
      expect(isRtlLanguage(locale.code)).toBe(false);
    });
  });

  it("treats unknown and nullish codes as LTR rather than throwing", () => {
    // A stored preference from a future build must not flip the whole layout.
    expect(isRtlLanguage("xx")).toBe(false);
    expect(isRtlLanguage(null)).toBe(false);
    expect(directionOf("xx")).toBe("ltr");
    expect(directionOf(undefined)).toBe("ltr");
  });

  it("agrees with the registry for every shipped locale", () => {
    SUPPORTED_LOCALES.forEach((locale) => {
      expect(directionOf(locale.code)).toBe(locale.direction);
    });
  });
});

describe("toIntlLocale", () => {
  it("prefers the device region over the language default", () => {
    // The whole point of carrying `region` through detection: MXN, not EUR.
    expect(toIntlLocale("es", "MX")).toBe("es-MX");
    expect(toIntlLocale("en", "GB")).toBe("en-GB");
  });

  it("uppercases a lowercase region", () => {
    expect(toIntlLocale("es", "mx")).toBe("es-MX");
  });

  it("accepts three-digit UN region codes", () => {
    expect(toIntlLocale("es", "419")).toBe("es-419");
  });

  it("uses the language's default region when none is supplied", () => {
    expect(toIntlLocale("es")).toBe("es-ES");
    expect(toIntlLocale("pt")).toBe("pt-BR");
    expect(toIntlLocale("zh")).toBe("zh-CN");
  });

  it("ignores a malformed region instead of emitting an invalid tag", () => {
    // `new Intl.NumberFormat("es-nonsense")` throws a RangeError, which would
    // crash whichever screen was formatting a price.
    expect(toIntlLocale("es", "nonsense")).toBe("es-ES");
    expect(toIntlLocale("es", "")).toBe("es-ES");
    expect(toIntlLocale("es", null)).toBe("es-ES");
  });

  it("returns en-US for an unknown language", () => {
    expect(toIntlLocale("xx")).toBe("en-US");
    expect(toIntlLocale(null)).toBe("en-US");
  });

  it("produces a tag Intl actually accepts for every shipped locale", () => {
    SUPPORTED_LOCALES.forEach((locale) => {
      expect(() => new originalDateTimeFormat(toIntlLocale(locale.code))).not.toThrow();
    });
  });
});

describe("foldForSearch", () => {
  it("lowercases and trims", () => {
    expect(foldForSearch("  ENGLISH  ")).toBe("english");
  });

  it("strips diacritics so romanised queries match endonyms", () => {
    expect(foldForSearch("Français")).toBe("francais");
    expect(foldForSearch("Español")).toBe("espanol");
    expect(foldForSearch("Português")).toBe("portugues");
    expect(foldForSearch("Kreyòl ayisyen")).toBe("kreyol ayisyen");
  });

  it("folds precomposed and decomposed forms to the same string", () => {
    // The same glyph arrives precomposed from a keyboard and decomposed from
    // some IMEs and from iOS pasteboard. Without NFD they would not match.
    const precomposed = "café";
    const decomposed = "café";
    expect(precomposed).not.toBe(decomposed);
    expect(foldForSearch(precomposed)).toBe(foldForSearch(decomposed));
    expect(foldForSearch(precomposed)).toBe("cafe");
  });

  it("leaves non-Latin scripts intact", () => {
    // The bug being guarded: a fold that whitelists ASCII deletes these
    // entirely, the needle becomes "", and the picker silently returns all
    // eleven languages. Only combining marks may be removed.
    expect(foldForSearch("日本語")).toBe("日本語");
    expect(foldForSearch("中文")).toBe("中文");
    expect(foldForSearch("العربية")).toBe("العربية");
    expect(foldForSearch("हिन्दी")).toBe("हिन्दी");
  });

  it("decomposes Hangul to Jamo without losing any of it", () => {
    // NFD splits precomposed Hangul syllables into Jamo (U+1100 block), which
    // the combining-mark strip does not touch. The folded form is therefore
    // longer than the input but still round-trips — and because the endonym in
    // the registry is folded the same way, substring search still matches.
    // Asserting byte equality with the precomposed input here would be wrong.
    const folded = foldForSearch("한국어");
    expect(folded).not.toBe("");
    expect(folded).toBe("한국어".normalize("NFD"));
    expect(folded.normalize("NFC")).toBe("한국어");
  });

  it("returns an empty string for blank and nullish input", () => {
    expect(foldForSearch("")).toBe("");
    expect(foldForSearch("   ")).toBe("");
    expect(foldForSearch(null as unknown as string)).toBe("");
  });

  it("is idempotent", () => {
    expect(foldForSearch(foldForSearch("Français"))).toBe("francais");
  });
});

describe("searchLocales", () => {
  const codesFor = (query: string) => searchLocales(query).map((locale) => locale.code);

  it("returns every locale for an empty or whitespace query", () => {
    expect(searchLocales("")).toHaveLength(SUPPORTED_LOCALES.length);
    expect(searchLocales("   ")).toHaveLength(SUPPORTED_LOCALES.length);
  });

  it("returns a copy, not the frozen registry array", () => {
    // A caller sorting the picker in place must not reorder the source of truth.
    expect(searchLocales("")).not.toBe(SUPPORTED_LOCALES);
  });

  it("matches accent-insensitively against the endonym", () => {
    // Typing "Francais" on a US keyboard is the common case, and the one that
    // regressed before: without NFD folding it matched nothing.
    expect(codesFor("Francais")).toContain("fr");
    expect(codesFor("Espanol")).toContain("es");
    expect(codesFor("Portugues")).toContain("pt");
    expect(codesFor("Kreyol")).toContain("ht");
  });

  it("still matches the accented endonym as typed", () => {
    expect(codesFor("Français")).toContain("fr");
    expect(codesFor("Español")).toContain("es");
  });

  /**
   * The regression this locks in: searching in one's own script. A fold that
   * whitelists ASCII (`/[^a-z0-9\s]/g`) deletes a CJK, Hangul, Arabic or
   * Devanagari query entirely, the needle becomes "", and the picker appears to
   * ignore typing while quietly showing all eleven languages. Each of these
   * must return exactly the one language whose endonym was typed.
   */
  it.each([
    ["日本語", "ja"],
    ["한국어", "ko"],
    ["中文", "zh"],
    ["العربية", "ar"],
    ["हिन्दी", "hi"]
  ])("finds %s by its native script", (query, expected) => {
    expect(codesFor(query)).toEqual([expected]);
  });

  it.each([
    ["日本", "ja"],
    ["한국", "ko"],
    ["عرب", "ar"],
    ["हिन", "hi"]
  ])("matches the partial native query %s as a substring", (query, expected) => {
    // Unsegmented scripts have no word boundaries, so a partial query is the
    // norm — a prefix-only matcher would fail every one of these.
    expect(codesFor(query)).toContain(expected);
  });

  it("matches the English exonym", () => {
    expect(codesFor("Japanese")).toEqual(["ja"]);
    expect(codesFor("Haitian")).toEqual(["ht"]);
  });

  it("matches aliases and common romanisations", () => {
    expect(codesFor("nihongo")).toEqual(["ja"]);
    expect(codesFor("hangul")).toEqual(["ko"]);
    expect(codesFor("mandarin")).toEqual(["zh"]);
    expect(codesFor("castellano")).toEqual(["es"]);
    expect(codesFor("devanagari")).toEqual(["hi"]);
  });

  it("matches by locale code prefix", () => {
    expect(codesFor("ht")).toEqual(["ht"]);
    expect(codesFor("ja")).toContain("ja");
  });

  it("returns nothing for a query that matches nothing", () => {
    expect(searchLocales("zzzznotalanguage")).toHaveLength(0);
  });

  /**
   * Documented, not endorsed: unlike the settings registry's tokenised search,
   * `searchLocales` is a single substring match, so a multi-word query only
   * matches if it appears verbatim. "chinese simplified" misses because the
   * exonym is "Chinese (Simplified)" with a parenthesis in between.
   */
  it("is substring-based rather than tokenised", () => {
    expect(codesFor("chinese")).toEqual(["zh"]);
    expect(codesFor("chinese (simplified)")).toEqual(["zh"]);
    expect(searchLocales("chinese simplified")).toHaveLength(0);
  });
});
