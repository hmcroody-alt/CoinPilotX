/**
 * The engine is the only layer between a `t()` call and what a user reads, and
 * its failure mode is silence: every unresolved lookup still returns a plausible
 * English sentence. Nothing crashes, nothing logs in release, and a screen that
 * is 100% untranslated looks identical to one that is 100% translated as long as
 * the reviewer speaks English. These tests exist to make that silence loud.
 *
 * The engine holds module-level mutable state — the catalog cache, the active
 * locale, the active region and the reported-missing set — none of which Jest
 * resets between tests in the same file. Every `beforeEach` below resets all
 * four; a test that skips one has historically failed only when the suite is run
 * in a different order.
 */

import * as catalogsModule from "../catalogs";
import type { CatalogNamespace } from "../catalogs";
import {
  activateLocale,
  ensureNamespace,
  getActiveIntlLocale,
  getActiveTranslationLocale,
  getMissingKeys,
  hasTranslation,
  humanizeKey,
  interpolate,
  isNamespaceLoaded,
  onMissingKey,
  parseKey,
  preloadNamespaces,
  resetCatalogCache,
  resolveTemplate,
  selectPluralCategory,
  setActiveRegion,
  translate
} from "../engine";
import type { CatalogBundle } from "../engine";

/**
 * No shipped catalog contains a gender/context variant, so the variant ordering
 * described in `keyVariants` cannot be observed against real data — only its
 * fall-through can. The loader is wrapped (defaulting to the real
 * implementation) so one describe block can hand the engine a synthetic bundle
 * that exercises the full ordering. No source file is changed by this; the
 * seam is the module boundary the engine already loads through.
 */
jest.mock("../catalogs", () => {
  const actual = jest.requireActual("../catalogs");
  return { __esModule: true, ...actual, loadCatalogBundle: jest.fn(actual.loadCatalogBundle) };
});

const realCatalogs = jest.requireActual<typeof import("../catalogs")>("../catalogs");
const loadCatalogBundle = catalogsModule.loadCatalogBundle as jest.MockedFunction<
  typeof catalogsModule.loadCatalogBundle
>;

/** `reportMissing` warns under `__DEV__`, which jest-expo sets. */
let warnSpy: jest.SpyInstance;

beforeAll(() => {
  warnSpy = jest.spyOn(console, "warn").mockImplementation(() => {});
});

afterAll(() => {
  warnSpy.mockRestore();
});

beforeEach(async () => {
  loadCatalogBundle.mockImplementation(realCatalogs.loadCatalogBundle);
  resetCatalogCache();
  // `activeRegion` is global, survives a cache reset, and is fed straight into
  // every `Intl` constructor — a leaked "DE" from an earlier test silently
  // changes number grouping in a completely unrelated assertion.
  setActiveRegion("");
  onMissingKey(null);
  await activateLocale("en");
});

afterEach(() => {
  onMissingKey(null);
  resetCatalogCache();
});

/* ------------------------------------------------------------------ *
 * 1. Fallback chain
 * ------------------------------------------------------------------ */

describe("fallback chain", () => {
  it("serves the active locale when it has the key", async () => {
    await activateLocale("fr");
    expect(translate("common:actions.save")).toBe("Enregistrer");
  });

  /**
   * Rung two. Constructed by loading only the default catalog rather than by
   * relying on a real gap between two shipped languages: any such gap is one
   * translation PR away from closing, which would make this assertion pass for
   * the wrong reason.
   */
  it("falls through to the default locale when the active locale has no bundle", async () => {
    resetCatalogCache();
    await activateLocale("fr", []);
    await ensureNamespace("en", "common");

    expect(getActiveTranslationLocale()).toBe("fr");
    expect(isNamespaceLoaded("fr", "common")).toBe(false);
    expect(translate("common:actions.save")).toBe("Save");
  });

  it("reports which locale actually served the string", async () => {
    resetCatalogCache();
    await activateLocale("fr", []);
    await ensureNamespace("en", "common");
    expect(resolveTemplate("common:actions.save").usedLocale).toBe("en");

    await activateLocale("fr", ["common"]);
    expect(resolveTemplate("common:actions.save").usedLocale).toBe("fr");
  });

  it("uses defaultValue only after both catalogs miss", () => {
    expect(translate("common:actions.save", { defaultValue: "SHOULD NOT WIN" })).toBe("Save");
    expect(translate("common:nope.notAKey", { defaultValue: "Fallback copy" })).toBe("Fallback copy");
  });

  it("interpolates defaultValue too", () => {
    expect(translate("common:nope.notAKey", { defaultValue: "Hello {{name}}", name: "Ada" })).toBe("Hello Ada");
  });

  /**
   * `typeof defaultValue === "string"` accepts the empty string, so an
   * accidental `defaultValue: ""` (a common shape when the value comes from a
   * prop) renders blank text instead of the humanized fallback. Pinned because
   * blank copy in a header reads as a layout bug, not a translation bug.
   */
  it("lets an empty defaultValue win over humanization", () => {
    expect(translate("common:nope.notAKey", { defaultValue: "" })).toBe("");
  });

  /**
   * The last rung, and the reason this whole file exists: a key that resolves
   * nowhere renders as English-looking prose. `common:screens.purchaseHistory`
   * humanizes to exactly the string the real catalog holds, so a missed
   * migration is invisible to an English-speaking reviewer.
   */
  it("humanizes the final key segment as a last resort", () => {
    expect(translate("common:screens.missingScreen")).toBe("Missing Screen");
    expect(humanizeKey("common:screens.purchaseHistory")).toBe("Purchase History");
  });

  it("produces a humanization that is indistinguishable from the real English copy", async () => {
    const real = translate("common:screens.purchaseHistory");
    resetCatalogCache();
    await activateLocale("en", []);
    const humanized = translate("common:screens.purchaseHistory");

    expect(humanized).toBe(real);
    expect(hasTranslation("common:screens.purchaseHistory")).toBe(false);
  });
});

describe("humanizeKey", () => {
  /**
   * The JSDoc on `humanizeKey` promises `"selectTitle" -> "Select title"`, but
   * the split only inserts a space — it never lowercases the second word. The
   * real output is title-cased, which is what the navigator comparison in
   * `navigatorLocalization.test.ts` relies on, so the doc comment is what is
   * wrong here, not the code.
   */
  it("splits camelCase without lowercasing the trailing word", () => {
    expect(humanizeKey("settings:language.selectTitle")).toBe("Select Title");
  });

  it("splits underscores and dashes", () => {
    expect(humanizeKey("common:a.b.snake_case_leaf")).toBe("Snake case leaf");
    expect(humanizeKey("common:a.kebab-case-leaf")).toBe("Kebab case leaf");
  });

  /**
   * The camelCase split needs a lowercase character on the left, so a run of
   * capitals is never broken up. Any key with an inline acronym humanizes into
   * something visibly mangled rather than plausible English — which, for once,
   * is the failure mode we want.
   */
  it("does not split a run of consecutive capitals", () => {
    expect(humanizeKey("common:screens.undxActionCenter")).toBe("Undx Action Center");
    expect(humanizeKey("common:nope.notAKey")).toBe("Not AKey");
  });

  it("strips a trailing plural suffix so a plural miss does not read as 'Items other'", () => {
    expect(humanizeKey("common:counts.items_other")).toBe("Items");
    expect(humanizeKey("common:counts.results_zero")).toBe("Results");
  });

  /**
   * Only plural suffixes are stripped. A gender variant leaks the variant name
   * into the UI, which at least makes that class of miss visible.
   */
  it("does not strip a gender suffix", () => {
    expect(humanizeKey("social:invite.sent_female")).toBe("Sent female");
  });

  it("returns the key unchanged when there is nothing to humanize", () => {
    expect(humanizeKey("")).toBe("");
  });
});

/* ------------------------------------------------------------------ *
 * 2. Key-variant selection order
 * ------------------------------------------------------------------ */

describe("key variant ordering", () => {
  /**
   * Synthetic because no shipped catalog has gender variants yet. Every branch
   * of `keyVariants` is represented exactly once so a reordering of that list
   * fails a specific assertion rather than all of them.
   */
  const VARIANT_FIXTURE = {
    invite: {
      sent: "base",
      sent_one: "plural one",
      sent_other: "plural other",
      sent_male: "gender male",
      sent_female: "gender female",
      sent_female_one: "gender female one",
      sent_female_other: "gender female other"
    }
  } as unknown as CatalogBundle;

  beforeEach(async () => {
    loadCatalogBundle.mockImplementation((locale: string, namespace: CatalogNamespace) =>
      locale === "en" && namespace === "social" ? VARIANT_FIXTURE : null
    );
    resetCatalogCache();
    await activateLocale("en", ["social"]);
  });

  it("prefers gender+plural over everything else", () => {
    expect(translate("social:invite.sent", { count: 1, context: "female" })).toBe("gender female one");
    expect(translate("social:invite.sent", { count: 5, context: "female" })).toBe("gender female other");
  });

  it("prefers plural over the base key when no context is given", () => {
    expect(translate("social:invite.sent", { count: 1 })).toBe("plural one");
    expect(translate("social:invite.sent", { count: 5 })).toBe("plural other");
  });

  it("prefers gender over the base key when no count is given", () => {
    expect(translate("social:invite.sent", { context: "female" })).toBe("gender female");
  });

  it("lands on the base key when neither count nor context is given", () => {
    expect(translate("social:invite.sent")).toBe("base");
  });

  /**
   * The surprising rung. With both a count and a context, the whole plural
   * group is tried before the gender-only form — so a catalog that ships
   * `sent_male` but no `sent_male_one` loses the gender entirely and renders
   * the generic plural. Documented here because it is the opposite of what
   * "progressively more generic" suggests to a translator reading the source.
   */
  it("drops gender in favour of a plural form when the gender+plural variant is absent", () => {
    expect(translate("social:invite.sent", { count: 1, context: "male" })).toBe("plural one");
  });

  it("falls all the way through for an unknown context", () => {
    expect(translate("social:invite.sent", { count: 5, context: "nonbinary" })).toBe("plural other");
  });

  it("ignores a count that is not a finite number", () => {
    expect(translate("social:invite.sent", { count: Number.NaN })).toBe("base");
    expect(translate("social:invite.sent", { count: Number.POSITIVE_INFINITY })).toBe("base");
    expect(translate("social:invite.sent", { count: "3" as unknown as number })).toBe("base");
  });

  it("ignores a whitespace-only context", () => {
    expect(translate("social:invite.sent", { context: "   " })).toBe("base");
  });
});

/* ------------------------------------------------------------------ *
 * 3. Plural selection
 * ------------------------------------------------------------------ */

describe("selectPluralCategory", () => {
  it("returns the full six-bucket CLDR set for Arabic", async () => {
    await activateLocale("ar");
    expect([0, 1, 2, 3, 11, 100].map((n) => selectPluralCategory(n, "ar"))).toEqual([
      "zero",
      "one",
      "two",
      "few",
      "many",
      "other"
    ]);
  });

  it("returns only 'other' for Japanese", () => {
    expect([0, 1, 2, 11, 100].map((n) => selectPluralCategory(n, "ja"))).toEqual([
      "other",
      "other",
      "other",
      "other",
      "other"
    ]);
  });

  it("returns one/other for English", () => {
    expect([0, 1, 2].map((n) => selectPluralCategory(n, "en"))).toEqual(["other", "one", "other"]);
  });

  /**
   * French counts zero as singular and has a `many` bucket for large magnitudes
   * — the two places a translator ported from English gets it wrong.
   */
  it("treats zero as 'one' and large magnitudes as 'many' in French", () => {
    expect(selectPluralCategory(0, "fr")).toBe("one");
    expect(selectPluralCategory(1, "fr")).toBe("one");
    expect(selectPluralCategory(2, "fr")).toBe("other");
    expect(selectPluralCategory(1_000_000, "fr")).toBe("many");
  });

  it("defaults to the active locale", async () => {
    await activateLocale("ar");
    expect(selectPluralCategory(2)).toBe("two");
    await activateLocale("en");
    expect(selectPluralCategory(2)).toBe("other");
  });
});

describe("plural selection through translate", () => {
  it("selects each Arabic bucket from the catalog", async () => {
    await activateLocale("ar");
    expect(translate("common:counts.items", { count: 0 })).toBe("لا توجد عناصر");
    expect(translate("common:counts.items", { count: 1 })).toBe("عنصر واحد");
    expect(translate("common:counts.items", { count: 2 })).toBe("عنصران");
    expect(translate("common:counts.items", { count: 3 })).toContain("عناصر");
    expect(translate("common:counts.items", { count: 11 })).toContain("عنصرًا");
  });

  /**
   * `results_zero` ships in en, fr, de, es, ja and ht, but `Intl.PluralRules`
   * only ever returns "zero" for languages that have the bucket. In every one
   * of those catalogs the zero copy is unreachable, and a count of 0 renders
   * the "other" (or, in French, the "one") form instead.
   */
  it("never reaches a `_zero` form in a language without a zero bucket", async () => {
    expect(translate("common:counts.results", { count: 0 })).toBe("0 results");

    await activateLocale("ja");
    expect(translate("common:counts.results", { count: 0 })).toBe("0件の結果");

    await activateLocale("fr");
    expect(translate("common:counts.results", { count: 0 })).toBe("0 résultat");

    await activateLocale("ar");
    expect(translate("common:counts.results", { count: 0 })).toBe("لا توجد نتائج");
  });

  /**
   * The French catalog has no `items_many`, and French asks for one above a
   * million. Without the `_other` retry inside `keyVariants` this would fall to
   * English or to the humanized "Items" — a number-formatted French sentence
   * that abruptly turns into an English noun.
   */
  it("falls back from a missing `_many` to `_other` rather than to English", async () => {
    await activateLocale("fr");
    const rendered = translate("common:counts.items", { count: 1_000_000 });

    expect(selectPluralCategory(1_000_000, "fr")).toBe("many");
    expect(rendered.endsWith("éléments")).toBe(true);
    expect(rendered).not.toBe("Items");
    expect(rendered).not.toContain("items");
  });

  it("renders the same string for every count in a language with one bucket", async () => {
    await activateLocale("ja");
    expect(translate("common:counts.items", { count: 1 })).toBe("1件");
    expect(translate("common:counts.items", { count: 7 })).toBe("7件");
  });
});

/* ------------------------------------------------------------------ *
 * 4. Interpolation
 * ------------------------------------------------------------------ */

describe("interpolate", () => {
  it("substitutes named placeholders", () => {
    expect(interpolate("{{first}} and {{second}}", { first: "Ada", second: "Grace" })).toBe("Ada and Grace");
  });

  it("tolerates whitespace inside the braces", () => {
    expect(interpolate("Hi {{ name }}", { name: "Ada" })).toBe("Hi Ada");
  });

  /**
   * Leaving the placeholder visible is deliberate: a hole in a sentence reads
   * as a copy error nobody files a bug for, whereas `{{name}}` on screen gets
   * reported the first time QA sees it.
   */
  it("leaves a missing or null param in place rather than blanking it", () => {
    expect(interpolate("Hi {{name}}", { other: 1 })).toBe("Hi {{name}}");
    expect(interpolate("Hi {{name}}", { name: null })).toBe("Hi {{name}}");
    expect(interpolate("Hi {{name}}", { name: undefined })).toBe("Hi {{name}}");
  });

  it("returns the template untouched when there are no params at all", () => {
    expect(interpolate("Hi {{name}}")).toBe("Hi {{name}}");
  });

  it("locale-formats numeric params", () => {
    expect(interpolate("{{count}}", { count: 1234 }, "en")).toBe("1,234");
    expect(interpolate("{{count}}", { count: 1234 }, "de")).toBe("1.234");
  });

  it("stringifies non-numeric params without formatting", () => {
    expect(interpolate("{{flag}}", { flag: true })).toBe("true");
  });

  it("carries the number formatting through translate", () => {
    expect(translate("common:counts.items", { count: 1234 })).toBe("1,234 items");
  });

  it("formats through the active region, not just the language", () => {
    setActiveRegion("DE");
    expect(getActiveIntlLocale()).toBe("en-DE");
    expect(translate("common:counts.items", { count: 1234 })).toBe("1.234 items");
  });

  /**
   * Characterization, not endorsement: interpolation uses the locale the
   * *template* came from, so a German UI falling back to an English string
   * formats its numbers as en-US. The sentence is English either way, but the
   * same coupling applies to any locale pair where only the copy is missing.
   */
  it("formats numbers in the locale that served the template, not the active one", async () => {
    resetCatalogCache();
    await activateLocale("de", []);
    await ensureNamespace("en", "common");

    expect(getActiveTranslationLocale()).toBe("de");
    expect(translate("common:counts.items", { count: 1234 })).toBe("1,234 items");
  });
});

/* ------------------------------------------------------------------ *
 * 5. Namespace loading
 * ------------------------------------------------------------------ */

describe("namespace loading", () => {
  it("reports a namespace as unloaded until it is fetched", async () => {
    resetCatalogCache();
    expect(isNamespaceLoaded("en", "common")).toBe(false);
    await ensureNamespace("en", "common");
    expect(isNamespaceLoaded("en", "common")).toBe(true);
  });

  it("memoizes a loaded bundle instead of re-parsing it", async () => {
    resetCatalogCache();
    loadCatalogBundle.mockClear();
    const first = await ensureNamespace("en", "common");
    const second = await ensureNamespace("en", "common");

    expect(first).toBe(second);
    expect(loadCatalogBundle).toHaveBeenCalledTimes(1);
  });

  it("dedupes concurrent loads of the same locale and namespace", async () => {
    resetCatalogCache();
    loadCatalogBundle.mockClear();
    const [first, second] = await Promise.all([
      ensureNamespace("en", "common"),
      ensureNamespace("en", "common")
    ]);

    expect(first).toBe(second);
    expect(loadCatalogBundle).toHaveBeenCalledTimes(1);
  });

  /**
   * A corrupt catalog file must degrade to the fallback locale, never reject —
   * `ensureNamespace` is awaited during app startup, so a rejection would take
   * the splash screen with it.
   */
  it("resolves to null instead of throwing when the loader blows up", async () => {
    resetCatalogCache();
    loadCatalogBundle.mockImplementation(() => {
      throw new Error("corrupt catalog");
    });

    await expect(ensureNamespace("en", "common")).resolves.toBeNull();
    expect(isNamespaceLoaded("en", "common")).toBe(false);
  });

  it("does not cache a failed load, so a later attempt can succeed", async () => {
    resetCatalogCache();
    loadCatalogBundle.mockImplementationOnce(() => null);
    await expect(ensureNamespace("en", "common")).resolves.toBeNull();

    await expect(ensureNamespace("en", "common")).resolves.not.toBeNull();
    expect(isNamespaceLoaded("en", "common")).toBe(true);
  });

  it("loads every requested namespace with preloadNamespaces", async () => {
    resetCatalogCache();
    await preloadNamespaces("es", ["common", "auth"]);

    expect(isNamespaceLoaded("es", "common")).toBe(true);
    expect(isNamespaceLoaded("es", "auth")).toBe(true);
    expect(isNamespaceLoaded("es", "settings")).toBe(false);
  });

  it("keeps the default catalog resident alongside a non-default language", async () => {
    resetCatalogCache();
    await activateLocale("fr", ["common"]);

    expect(isNamespaceLoaded("fr", "common")).toBe(true);
    expect(isNamespaceLoaded("en", "common")).toBe(true);
  });

  it("falls back to the default locale for an unsupported tag", async () => {
    await expect(activateLocale("xx")).resolves.toBe("en");
    expect(getActiveTranslationLocale()).toBe("en");
  });

  /**
   * `activateLocale` only accepts an exact supported code — it does not run the
   * `resolveSupportedLocale` chain from locales.ts. A regional tag straight off
   * the device therefore activates English rather than the language it names.
   */
  it("does not resolve a regional tag to its base language", async () => {
    await expect(activateLocale("pt-BR")).resolves.toBe("en");
    await expect(activateLocale("FR")).resolves.toBe("fr");
  });

  it("clears cache, inflight loads and reported keys on reset", () => {
    translate("common:nope.gone");
    expect(getMissingKeys().length).toBeGreaterThan(0);

    resetCatalogCache();

    expect(isNamespaceLoaded("en", "common")).toBe(false);
    expect(getMissingKeys()).toEqual([]);
  });

  /**
   * THE failure mode. The engine serves nothing from an unloaded namespace, so
   * every lookup drops straight to `humanizeKey` and returns readable English
   * — while a non-English locale is active and while the translation exists on
   * disk. Nothing throws, nothing renders a raw key, and the screen looks
   * correct to anyone who reads English.
   */
  it("silently returns plausible English for an unloaded namespace", async () => {
    resetCatalogCache();
    await activateLocale("es", []);

    expect(getActiveTranslationLocale()).toBe("es");
    expect(isNamespaceLoaded("es", "common")).toBe(false);
    expect(isNamespaceLoaded("en", "common")).toBe(false);

    expect(translate("common:actions.save")).toBe("Save");
    expect(translate("common:screens.purchaseHistory")).toBe("Purchase History");
    expect(translate("common:screens.securityCenter")).toBe("Security Center");

    await preloadNamespaces("es", ["common"]);
    expect(translate("common:actions.save")).toBe("Guardar");
    expect(translate("common:screens.purchaseHistory")).toBe("Historial de compras");
  });

  it("reports the humanized-through miss to the missing-key channel", async () => {
    resetCatalogCache();
    await activateLocale("es", []);
    translate("common:actions.save");

    expect(getMissingKeys()).toContain("es:common:actions.save");
  });
});

/* ------------------------------------------------------------------ *
 * 6. Missing-key reporting
 * ------------------------------------------------------------------ */

describe("missing-key reporting", () => {
  it("fires once for a genuinely missing key", () => {
    const seen: Array<{ locale: string; namespace: string; key: string }> = [];
    onMissingKey((info) => seen.push(info));

    translate("common:nope.notAKey");

    expect(seen).toEqual([{ locale: "en", namespace: "common", key: "nope.notAKey" }]);
  });

  /**
   * A missing key in a list row is looked up once per item per render. Without
   * the dedupe the listener — which in the app ships to analytics — would fire
   * thousands of times for a single bad key.
   */
  it("does not fire again for a repeat lookup of the same key", () => {
    const seen: unknown[] = [];
    onMissingKey((info) => seen.push(info));

    translate("common:nope.notAKey");
    translate("common:nope.notAKey");
    translate("common:nope.notAKey", { count: 3 });
    translate("common:nope.notAKey", { defaultValue: "x" });

    expect(seen).toHaveLength(1);
    expect(getMissingKeys()).toEqual(["en:common:nope.notAKey"]);
  });

  it("treats the same key in a different locale as a separate miss", async () => {
    translate("common:nope.notAKey");
    await activateLocale("fr");
    translate("common:nope.notAKey");

    expect(getMissingKeys().sort()).toEqual(["en:common:nope.notAKey", "fr:common:nope.notAKey"]);
  });

  it("never fires for a key that resolves", () => {
    const listener = jest.fn();
    onMissingKey(listener);

    translate("common:actions.save");

    expect(listener).not.toHaveBeenCalled();
    expect(getMissingKeys()).toEqual([]);
  });

  it("survives a listener that throws", () => {
    onMissingKey(() => {
      throw new Error("analytics is down");
    });

    expect(() => translate("common:nope.notAKey")).not.toThrow();
    expect(translate("common:nope.notAKey")).toBe("Not AKey");
  });

  it("stops reporting once the listener is detached", () => {
    const listener = jest.fn();
    onMissingKey(listener);
    onMissingKey(null);

    translate("common:nope.anotherKey");

    expect(listener).not.toHaveBeenCalled();
    expect(getMissingKeys()).toEqual(["en:common:nope.anotherKey"]);
  });

  /**
   * `resolveTemplate` bails before `reportMissing` when the path is empty, so a
   * key built from an undefined variable is dropped on the floor instead of
   * surfacing in the dev panel. A whitespace-only key is echoed back verbatim
   * because `humanizeKey` returns the original key when there is nothing left
   * to humanize.
   */
  it("does not report an empty key", () => {
    expect(translate("")).toBe("");
    expect(translate("   ")).toBe("   ");
    expect(getMissingKeys()).toEqual([]);
  });
});

/* ------------------------------------------------------------------ *
 * 7. parseKey and hasTranslation
 * ------------------------------------------------------------------ */

describe("parseKey", () => {
  it("splits a namespaced key", () => {
    expect(parseKey("settings:language.title")).toEqual({ namespace: "settings", path: "language.title" });
  });

  it("assumes the common namespace for a bare key", () => {
    expect(parseKey("actions.save")).toEqual({ namespace: "common", path: "actions.save" });
    expect(translate("actions.save")).toBe("Save");
  });

  it("trims surrounding whitespace", () => {
    expect(parseKey("  common:actions.save  ")).toEqual({ namespace: "common", path: "actions.save" });
  });

  /**
   * An unregistered namespace is not rejected — the whole raw string becomes a
   * path inside `common`, which cannot match, so the key humanizes. A typo in
   * the namespace of `t("setting:index.save.title")` therefore renders "Title"
   * rather than anything that looks wrong.
   */
  it("keeps an unregistered namespace prefix inside the path", () => {
    expect(parseKey("nope:actions.save")).toEqual({ namespace: "common", path: "nope:actions.save" });
    expect(translate("nope:actions.save")).toBe("Save");
    expect(translate("nope:screens.purchaseHistory")).toBe("Purchase History");
  });

  it("handles an empty or nullish key without throwing", () => {
    expect(parseKey("")).toEqual({ namespace: "common", path: "" });
    expect(parseKey(undefined as unknown as string)).toEqual({ namespace: "common", path: "" });
  });

  it("splits on the first separator only", () => {
    expect(parseKey("common:a:b")).toEqual({ namespace: "common", path: "a:b" });
  });
});

describe("hasTranslation", () => {
  it("is true for a key the loaded locale supplies", () => {
    expect(hasTranslation("common:actions.save")).toBe(true);
    expect(hasTranslation("actions.save")).toBe(true);
  });

  it("is false for a key nothing supplies", () => {
    expect(hasTranslation("common:nope.notAKey")).toBe(false);
    expect(hasTranslation("nope:actions.save")).toBe(false);
  });

  it("is false for a group node, which is not a renderable string", () => {
    expect(hasTranslation("common:actions")).toBe(false);
  });

  /**
   * It probes the cache, not the catalog files: a language that ships the key
   * but has not been loaded reads as untranslated. Anything auditing coverage
   * has to preload first (as `navigatorLocalization.test.ts` does by going
   * straight to `loadCatalogBundle`).
   */
  it("is false for a locale that is not loaded, even though the file has the key", async () => {
    expect(hasTranslation("common:actions.save", "fr")).toBe(false);
    await preloadNamespaces("fr", ["common"]);
    expect(hasTranslation("common:actions.save", "fr")).toBe(true);
  });

  /**
   * It reads the base path only and knows nothing about `keyVariants`, so every
   * pluralized key in the catalogs reports as missing even though `translate`
   * resolves it. A coverage sweep built on this would flag all of `counts.*`.
   */
  it("is false for a pluralized key that translate resolves fine", () => {
    expect(translate("common:counts.items", { count: 2 })).toBe("2 items");
    expect(hasTranslation("common:counts.items")).toBe(false);
    expect(hasTranslation("common:counts.items_other")).toBe(true);
  });

  it("normalizes the locale tag it is given", async () => {
    await preloadNamespaces("fr", ["common"]);
    expect(hasTranslation("common:actions.save", "FR")).toBe(true);
  });
});

/* ------------------------------------------------------------------ *
 * 8. Per-lookup locale override
 * ------------------------------------------------------------------ */

describe("locale override", () => {
  it("translates in the requested locale without changing the active one", async () => {
    await preloadNamespaces("fr", ["common"]);

    expect(translate("common:actions.save", { locale: "fr" })).toBe("Enregistrer");
    expect(getActiveTranslationLocale()).toBe("en");
    expect(translate("common:actions.save")).toBe("Save");
  });

  it("uses the override locale's plural rules, not the active locale's", async () => {
    await preloadNamespaces("ar", ["common"]);

    expect(translate("common:counts.items", { count: 2, locale: "ar" })).toBe("عنصران");
    expect(translate("common:counts.items", { count: 2 })).toBe("2 items");
  });

  it("normalizes and case-folds the override tag", async () => {
    await preloadNamespaces("fr", ["common"]);
    expect(translate("common:actions.save", { locale: "FR" })).toBe("Enregistrer");
  });

  it("falls back to the default locale for an unsupported override", () => {
    expect(translate("common:actions.save", { locale: "xx" })).toBe("Save");
  });

  /**
   * The language picker previews a locale by passing the device's tag through.
   * A regional tag is not a supported code, so the preview silently renders
   * English — the one string in that screen that must not be English.
   */
  it("renders English for a regional override tag", async () => {
    await preloadNamespaces("pt", ["common"]);
    expect(translate("common:actions.save", { locale: "pt" })).toBe("Salvar");
    expect(translate("common:actions.save", { locale: "pt-BR" })).toBe("Save");
  });

  it("still falls back to the default catalog when the override locale lacks the bundle", () => {
    expect(isNamespaceLoaded("ja", "common")).toBe(false);
    expect(translate("common:actions.save", { locale: "ja" })).toBe("Save");
  });
});
