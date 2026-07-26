/**
 * Coverage is a number users act on: it is the "87%" next to a language in the
 * picker, and it is the CI signal that a key was added to English and nowhere
 * else. Both audiences are harmed by the same class of failure — a number that
 * is confidently wrong — so these tests focus on the arithmetic rather than on
 * any particular translation.
 *
 * The invariant that carries the most weight is plural-family collapsing.
 * Arabic legitimately ships six plural forms where English ships two; counting
 * raw keys would score it above 100%, and would simultaneously penalise
 * Japanese for having a single form. Both distortions are asserted against the
 * real catalogs below, using their real key counts.
 */

import { CATALOG_NAMESPACES, CatalogBundle, CatalogNamespace, loadCatalogBundle } from "../catalogs";
import { DEFAULT_LOCALE, SUPPORTED_LOCALE_CODES } from "../locales";
import {
  CoverageReport,
  getAllCoverage,
  getCoverage,
  isFullyTranslated,
  resetCoverageCache
} from "../coverage";

/** Raw string leaves, i.e. what a naive implementation would count. */
function countRawKeys(locale: string): number {
  let total = 0;
  const walk = (bundle: CatalogBundle): void => {
    for (const [key, value] of Object.entries(bundle)) {
      if (key.startsWith("$")) continue;
      if (typeof value === "string") total += 1;
      else if (value && typeof value === "object") walk(value as CatalogBundle);
    }
  };
  for (const namespace of CATALOG_NAMESPACES) {
    const bundle = loadCatalogBundle(locale, namespace as CatalogNamespace);
    if (bundle) walk(bundle);
  }
  return total;
}

/**
 * The cache is module-level, and one test deliberately clears it. Resetting up
 * front keeps every test measuring from the same starting point regardless of
 * the order jest happens to run them in.
 */
beforeEach(() => {
  resetCoverageCache();
});

describe("the reference catalog", () => {
  const report = getCoverage(DEFAULT_LOCALE);

  it("scores English at 100% with nothing missing or orphaned", () => {
    expect(report.percent).toBe(100);
    expect(report.translated).toBe(report.total);
    expect(report.missing).toEqual([]);
    expect(report.orphaned).toEqual([]);
    expect(isFullyTranslated(DEFAULT_LOCALE)).toBe(true);
  });

  /**
   * `percent` short-circuits to 100 when `total` is 0, so a catalog that failed
   * to load would report every language as perfectly translated. Without this
   * assertion every other test in the file could pass vacuously.
   */
  it("measures against a catalog that actually loaded", () => {
    expect(report.total).toBeGreaterThan(500);
    expect(report.locale).toBe("en");
  });

  it("counts fewer families than raw keys, proving plurals were collapsed", () => {
    // English ships `_one`/`_other` pairs; each pair must fold into one family.
    expect(report.total).toBeLessThan(countRawKeys(DEFAULT_LOCALE));
  });
});

describe("every shipped language", () => {
  const others = SUPPORTED_LOCALE_CODES.filter((code) => code !== DEFAULT_LOCALE);

  it("has the ten non-default languages this suite expects to check", () => {
    // Guards against the list shrinking silently and the it.each below going quiet.
    expect(others).toHaveLength(10);
  });

  it.each(others)("%s reports a sane 0-100 percentage", (code) => {
    const report = getCoverage(code);

    expect(report.locale).toBe(code);
    expect(Number.isInteger(report.percent)).toBe(true);
    expect(report.percent).toBeGreaterThanOrEqual(0);
    expect(report.percent).toBeLessThanOrEqual(100);
    expect(report.translated).toBeGreaterThanOrEqual(0);
    expect(report.translated).toBeLessThanOrEqual(report.total);
    // Every language is scored against the same denominator, or the numbers in
    // the picker are not comparable to each other.
    expect(report.total).toBe(getCoverage(DEFAULT_LOCALE).total);
    expect(report.missing).toHaveLength(report.total - report.translated);
  });

  it("returns missing and orphaned lists in sorted order", () => {
    // The validator prints these; unstable ordering makes its output diff-noisy.
    others.forEach((code) => {
      const { missing, orphaned } = getCoverage(code);
      expect(missing).toEqual([...missing].sort());
      expect(orphaned).toEqual([...orphaned].sort());
    });
  });

  it("normalizes the tag before measuring", () => {
    // The picker persists whatever the platform handed it, which is not always
    // lowercased. `AR` and `ar` must not produce two different numbers.
    expect(getCoverage("AR").locale).toBe("ar");
    expect(getCoverage("AR").percent).toBe(getCoverage("ar").percent);
  });
});

/**
 * THE key invariant. `keyFamilies` strips `_zero|_one|_two|_few|_many|_other`
 * so a plural set counts once no matter how many grammatical forms a language
 * needs. The two languages below sit at opposite extremes of that spread, which
 * is why they are the ones asserted rather than a representative middle case.
 */
describe("plural-family collapsing", () => {
  const reference = getCoverage(DEFAULT_LOCALE).total;

  it("does not reward Arabic for its six plural forms", () => {
    const report = getCoverage("ar");

    // Arabic genuinely ships more raw strings than English — that is the setup
    // for the bug, not a defect.
    expect(countRawKeys("ar")).toBeGreaterThan(countRawKeys(DEFAULT_LOCALE));
    // A raw-key ratio would print something like 108% in the language picker.
    expect(Math.floor((countRawKeys("ar") / countRawKeys(DEFAULT_LOCALE)) * 100)).toBeGreaterThan(100);
    // Family-based scoring keeps it inside the range the UI can render.
    expect(report.percent).toBeLessThanOrEqual(100);
    expect(report.percent).toBeGreaterThanOrEqual(90);
    expect(report.translated).toBeLessThanOrEqual(reference);
  });

  it("does not penalise Japanese for having a single plural form", () => {
    const report = getCoverage("ja");

    // Japanese ships fewer raw strings than English precisely because its
    // grammar has one form; scoring on raw keys would dock it for that.
    expect(countRawKeys("ja")).toBeLessThan(countRawKeys(DEFAULT_LOCALE));
    expect(report.percent).toBeLessThanOrEqual(100);
    expect(report.percent).toBeGreaterThanOrEqual(90);
  });

  it("scores the two extremes identically when both are complete", () => {
    // Six forms and one form must land on the same number, or the picker is
    // reporting grammar rather than translation progress.
    expect(getCoverage("ar").percent).toBe(getCoverage("ja").percent);
  });

  it("keeps no language above 100%", () => {
    // The single assertion that would have failed loudly before collapsing existed.
    SUPPORTED_LOCALE_CODES.forEach((code) => {
      const report = getCoverage(code);
      expect(report.percent).toBeLessThanOrEqual(100);
      expect(report.translated).toBeLessThanOrEqual(report.total);
    });
  });

  it("treats a plural set as one family rather than one per form", () => {
    // Direct evidence from the catalogs: Arabic's extra forms produce extra raw
    // keys but no extra families, so its orphan list stays empty.
    expect(getCoverage("ar").orphaned).toEqual([]);
  });
});

/**
 * The real catalogs are all complete, so the flooring behaviour has no fixture
 * to prove itself against. These tests substitute a synthetic catalog module of
 * a known size — the alternative would be deliberately breaking a shipped
 * translation, which is a much worse thing to have living in the repository.
 */
describe("percent is floored, never rounded", () => {
  function withSyntheticCatalog<T>(
    reference: number,
    translated: number,
    run: (module: typeof import("../coverage"), load: jest.Mock) => T
  ): T {
    jest.resetModules();
    const load = jest.fn((locale: string) => {
      const size = locale === DEFAULT_LOCALE ? reference : translated;
      const bundle: Record<string, string> = {};
      for (let index = 0; index < size; index += 1) bundle[`key${index}`] = "value";
      return bundle;
    });
    jest.doMock("../catalogs", () => ({ CATALOG_NAMESPACES: ["common"], loadCatalogBundle: load }));
    try {
      // eslint-disable-next-line @typescript-eslint/no-var-requires
      return run(require("../coverage") as typeof import("../coverage"), load);
    } finally {
      jest.dontMock("../catalogs");
      jest.resetModules();
    }
  }

  it("reports 99.6% as 99, not 100", () => {
    withSyntheticCatalog(1000, 996, (coverage) => {
      const report = coverage.getCoverage("es");
      expect(report.total).toBe(1000);
      expect(report.translated).toBe(996);
      // Math.round(99.6) is 100. Printing "100% translated" while four strings
      // fall back to English defeats the entire purpose of showing a number.
      expect(report.percent).toBe(99);
      expect(report.percent).not.toBe(Math.round((996 / 1000) * 100));
      expect(coverage.isFullyTranslated("es")).toBe(false);
    });
  });

  it("reports a single missing key out of a thousand as 99", () => {
    withSyntheticCatalog(1000, 999, (coverage) => {
      expect(coverage.getCoverage("es").percent).toBe(99);
      expect(coverage.isFullyTranslated("es")).toBe(false);
    });
  });

  it("reports an empty translation as 0 rather than falling back to 100", () => {
    withSyntheticCatalog(1000, 0, (coverage) => {
      const report = coverage.getCoverage("es");
      expect(report.percent).toBe(0);
      expect(report.translated).toBe(0);
      expect(report.missing).toHaveLength(1000);
    });
  });

  it("measures a locale once and re-reads only after the cache is reset", () => {
    withSyntheticCatalog(10, 8, (coverage, load) => {
      coverage.getCoverage("es");
      const afterFirst = load.mock.calls.length;
      expect(afterFirst).toBeGreaterThan(0);

      coverage.getCoverage("es");
      // Parsing the catalogs is the expensive part and it happens while the
      // picker is animating open; a cache miss per row would be visible.
      expect(load.mock.calls.length).toBe(afterFirst);

      coverage.resetCoverageCache();
      coverage.getCoverage("es");
      expect(load.mock.calls.length).toBeGreaterThan(afterFirst);
    });
  });
});

describe("getAllCoverage", () => {
  it("covers every shipped language, in registry order", () => {
    const reports = getAllCoverage();
    expect(reports.map((report: CoverageReport) => report.locale)).toEqual([...SUPPORTED_LOCALE_CODES]);
  });

  it("agrees with getCoverage for each language", () => {
    // getAllCoverage is what the validator asserts against; if it diverged from
    // the picker's per-language call, CI and the UI would disagree.
    getAllCoverage().forEach((report: CoverageReport) => {
      expect(report).toEqual(getCoverage(report.locale));
    });
  });
});

describe("isFullyTranslated", () => {
  it("is true for every shipped language today", () => {
    // Doubles as a regression guard: this is the assertion that fails the moment
    // an English key lands without its ten translations.
    SUPPORTED_LOCALE_CODES.forEach((code) => expect(isFullyTranslated(code)).toBe(true));
  });

  it("is false for a language with no catalog at all", () => {
    // An unshipped tag must read as 0%, not silently inherit English's score.
    const report = getCoverage("sw");
    expect(report.translated).toBe(0);
    expect(report.percent).toBe(0);
    expect(isFullyTranslated("sw")).toBe(false);
  });
});

describe("resetCoverageCache", () => {
  it("hands back the identical report until it is cleared", () => {
    const first = getCoverage("ar");
    expect(getCoverage("ar")).toBe(first);
  });

  it("re-measures after a reset instead of serving the stale object", () => {
    // A reset that only appeared to work would leave the dev panel showing
    // pre-edit numbers, which is worse than showing none.
    const first = getCoverage("ar");
    resetCoverageCache();
    const second = getCoverage("ar");

    expect(second).not.toBe(first);
    expect(second).toEqual(first);
  });
});
