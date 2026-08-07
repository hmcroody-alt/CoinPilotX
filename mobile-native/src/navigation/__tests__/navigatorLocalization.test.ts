/**
 * The navigator is the one place where a missing translation is invisible in
 * review: `options={{ title: "Purchase History" }}` looks exactly like correct
 * code, and the header renders it happily in every language. There are 109 of
 * these, spread over 400 lines, and the list grows every time someone adds a
 * route — so the invariants are asserted against the file's text rather than
 * trusted to a reader.
 *
 * The file is read rather than imported for the same reason `registry.test.ts`
 * reads it: importing AppNavigator pulls in the entire screen graph —
 * expo-notifications, the camera, the call layer — to answer a question that is
 * purely about string literals.
 */

import fs from "fs";
import path from "path";
import { activateLocale, translate } from "../../i18n/engine";
import { SUPPORTED_LOCALES } from "../../i18n/locales";
import { loadCatalogBundle } from "../../i18n/catalogs";

const SOURCE = fs.readFileSync(path.join(__dirname, "..", "AppNavigator.tsx"), "utf8");

/** Every `title:` option in the file, as either a literal or a `t()` key. */
const TITLE_OPTIONS = Array.from(SOURCE.matchAll(/title: (?:route\.params\??\.title \|\| )?(.+?)(?:,| \}| \)\})/g)).map(
  (match) => match[1].trim()
);

beforeAll(async () => {
  await activateLocale("en");
});

describe("navigator header titles", () => {
  it("finds the title options it is meant to be checking", () => {
    // A refactor that changed the `options={{ title: ... }}` shape would make
    // every assertion below vacuously pass. 101 stack screens + 14 tabs, plus
    // the `title` param the header passes when opening the activity inbox.
    expect(TITLE_OPTIONS.length).toBe(118);
  });

  it("has no hardcoded string literal titles", () => {
    const literals = TITLE_OPTIONS.filter((title) => /^["'`]/.test(title));
    expect(literals).toEqual([]);
  });

  it("routes every title through the common namespace", () => {
    const foreign = TITLE_OPTIONS.filter((title) => !/^t\("common:(screens|tabs)\.[a-zA-Z]+"\)$/.test(title));
    expect(foreign).toEqual([]);
  });

  /**
   * The header renders on the very first frame, before the extended tier is
   * warmed. A navigation title parked in `settings:` or `social:` would render
   * as a humanized key fragment on cold start and then silently correct itself,
   * which is the kind of bug that only ever reproduces on a real device.
   */
  it("keeps every title in the core tier", () => {
    const namespaces = new Set(TITLE_OPTIONS.map((title) => title.replace(/^t\("([a-z]+):.*$/, "$1")));
    expect(Array.from(namespaces)).toEqual(["common"]);
  });

  it("resolves every referenced key to real English copy", () => {
    const keys = Array.from(new Set(TITLE_OPTIONS.map((title) => title.slice(3, -2))));
    expect(keys.length).toBeGreaterThan(50);
    keys.forEach((key) => {
      const text = translate(key);
      expect(text.length).toBeGreaterThan(0);
      // The engine's last-resort fallback humanizes the final key segment, so a
      // missing `common:screens.purchaseHistory` renders as "Purchase History"
      // — visually identical to the correct answer, and untranslatable. Compare
      // against the humanization to catch exactly that.
      const humanized = key
        .split(".")
        .pop()!
        .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
        .replace(/^./, (character) => character.toUpperCase());
      expect(text === humanized && !hasKey("en", key)).toBe(false);
    });
  });
});

describe("navigator chrome", () => {
  it("has no untranslated subtitle or identity strings", () => {
    // The helpers at the bottom of the file (`subtitleForTab`,
    // `subtitleForStack`) and the `identity` object are the strings a title
    // sweep misses, because none of them sit next to the word "title".
    const helpers = SOURCE.slice(SOURCE.indexOf("function subtitleForTab"));
    const returned = Array.from(helpers.matchAll(/return ("[^"]+")/g)).map((match) => match[1]);
    expect(returned).toEqual([]);
    expect(SOURCE).not.toContain('"PulseSoc member"');
    expect(SOURCE).not.toContain('{ title: "Activity Inbox" }');
  });

  /**
   * `subtitleForStack` classifies routes by substring, so it silently returns
   * the generic fallback for anything unrecognised. Asserting the keys exist
   * stops a typo from turning a subtitle into a humanized key fragment.
   */
  it("resolves every navigation subtitle key", () => {
    const keys = Array.from(SOURCE.matchAll(/t\("(common:navSubtitles\.[a-zA-Z]+)"\)/g)).map((match) => match[1]);
    expect(keys.length).toBeGreaterThan(0);
    Array.from(new Set(keys)).forEach((key) => expect(hasKey("en", key)).toBe(true));
  });
});

/**
 * Every language ships the same navigation chrome or none of it. A partially
 * translated header is worse than an English one: half the screen titles switch
 * and half do not, which reads as a rendering bug rather than a missing string.
 */
describe("navigation chrome is translated in every shipped language", () => {
  const keys = [
    ...Array.from(SOURCE.matchAll(/t\("(common:(?:screens|tabs|navSubtitles|identity)\.[a-zA-Z]+)"\)/g)).map(
      (match) => match[1]
    )
  ];
  const unique = Array.from(new Set(keys));

  it.each(SUPPORTED_LOCALES.map((locale) => locale.code))("%s", (code) => {
    const missing = unique.filter((key) => !hasKey(code, key));
    expect(missing).toEqual([]);
  });
});

/** Direct catalog probe — deliberately not `translate`, which would fall back. */
function hasKey(locale: string, key: string): boolean {
  const [namespace, dotted] = key.split(":");
  const bundle = loadCatalogBundle(locale, namespace as "common");
  if (!bundle) return false;
  let node: unknown = bundle;
  for (const segment of dotted.split(".")) {
    if (typeof node !== "object" || node === null) return false;
    node = (node as Record<string, unknown>)[segment];
  }
  return typeof node === "string" && node.length > 0;
}
