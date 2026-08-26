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

/**
 * The same text with its comments removed.
 *
 * The title assertions are a claim about the navigator's *code*, and a comment
 * is free to quote code: the block above `PageConnections` explains that every
 * caller already sends `title: page.name`. Read off the raw file that sentence
 * is a 130th header title, and one written as a bare identifier, so it fails
 * the count, the namespace check and the tier check at once — four red tests
 * describing a prose paragraph. Stripping first lets a comment describe the
 * thing it sits above without becoming evidence about it.
 *
 * Safe as a regex here because the file has no `//` inside a string literal;
 * the only slashes it owns are comments and JSX closes.
 */
function stripComments(text: string): string {
  return text.replace(/\/\*[\s\S]*?\*\//g, "").replace(/\/\/.*$/gm, "");
}

const CODE = stripComments(SOURCE);

/** Every `title:` option in the file, as either a literal or a `t()` key. */
const TITLE_OPTIONS = Array.from(CODE.matchAll(/title: (?:route\.params\??\.title \|\| )?(.+?)(?:,| \}| \)\})/g)).map(
  (match) => match[1].trim()
);

beforeAll(async () => {
  await activateLocale("en");
});

describe("the harness reads code, not prose", () => {
  /**
   * Both comment forms are covered because both are ways a real `title:` ends
   * up in the file without being a header title: the block form when a comment
   * quotes what callers pass, and the line form when a route is commented out
   * rather than deleted. Only the block form has bitten so far; the line form
   * is here so that the half of `stripComments` handling it is asserted rather
   * than assumed, and so the day someone parks a route behind `//` the count
   * does not move for a route that no longer renders.
   */
  it("ignores a title written in a comment", () => {
    const stripped = stripComments(
      [
        '{/* every caller sends `title: page.name`, and the screen drops it */}',
        '<Stack.Screen name="Real" options={{ title: t("common:screens.real") }} />',
        '// <Stack.Screen name="Retired" options={{ title: t("common:screens.retired") }} />'
      ].join("\n")
    );
    const found = Array.from(stripped.matchAll(/title: (.+?)(?:,| \}| \)\})/g)).map((match) => match[1].trim());
    expect(found).toEqual(['t("common:screens.real")']);
  });

  it("leaves the code around a comment intact", () => {
    // A stripper greedy across two comment blocks would swallow the route
    // between them, which reads as a route quietly disappearing from the count.
    expect(stripComments('a /* one */ <Stack.Screen name="Kept" /> /* two */ b')).toBe(
      'a  <Stack.Screen name="Kept" />  b'
    );
  });
});

describe("navigator header titles", () => {
  it("finds the title options it is meant to be checking", () => {
    // A refactor that changed the `options={{ title: ... }}` shape would make
    // every assertion below vacuously pass, so the count is pinned exactly:
    // too low means the regex stopped matching, too high means it started
    // matching something that is not a header title.
    //
    // This is every `title:` in AppNavigator.tsx — not one per screen. Some
    // screens take their title from `route.params.title`, and a few set none.
    // Adding a screen with a title moves this by one; that is expected upkeep,
    // and the number is the point of the check.
    //
    // 130 + 3: the premium crypto trio (alert center, alert history, portfolio)
    // arrived on a branch that had pinned 129 against its own smaller navigator.
    // Both lineages added titled routes, so the merged count is the sum, and
    // that it lands exactly on 133 is the evidence the union dropped neither
    // side's screens rather than silently keeping one.
    //
    // 133 + 1: `BusinessOsSection`, the Business OS section landing page. Its
    // title is `route.params.title || t("common:screens.businessOs")` — a
    // titled route, so it counts.
    expect(TITLE_OPTIONS.length).toBe(134);
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
   *
   * Matched as bare string literals rather than `t("…")` calls: the Presence
   * routes hold their keys in a lookup map and pass them to `t()` a line later,
   * which a search for the call shape would walk straight past.
   */
  it("resolves every navigation subtitle key", () => {
    const keys = Array.from(SOURCE.matchAll(/"(common:navSubtitles\.[a-zA-Z]+)"/g)).map((match) => match[1]);
    expect(keys.length).toBeGreaterThan(0);
    Array.from(new Set(keys)).forEach((key) => expect(hasKey("en", key)).toBe(true));
  });

  /**
   * All the Presence routes were answering with `navSubtitles.nativeRoute` —
   * "Native PulseSoc route", a developer placeholder — including the public
   * page a visitor lands on from a shared link. Deriving the expected names
   * from the registered screens means a newly added Presence route fails here
   * rather than quietly inheriting the placeholder; that is exactly what
   * happened when `PageEdit` was added, which is the point of the check.
   */
  it("gives every Presence route a subtitle of its own", () => {
    const registered = Array.from(SOURCE.matchAll(/<Stack\.Screen name="(Presence|Page[A-Za-z]*)"/g))
      .map((match) => match[1])
      .sort();
    expect(registered).toEqual([
      "Page",
      "PageConnections",
      "PageCreate",
      "PageEdit",
      "PageTeam",
      "PagesHub",
      "Presence"
    ]);

    const block = SOURCE.slice(SOURCE.indexOf("const PRESENCE_ROUTE_SUBTITLES"));
    const entries = Array.from(
      block.slice(0, block.indexOf("};")).matchAll(/(\w+): "(common:navSubtitles\.[a-zA-Z]+)"/g)
    );
    expect(entries.map((entry) => entry[1]).sort()).toEqual(registered);
    // One answer per route, all distinct, none of them the placeholder.
    expect(new Set(entries.map((entry) => entry[2])).size).toBe(registered.length);
    entries.forEach((entry) => expect(entry[2]).not.toBe("common:navSubtitles.nativeRoute"));
  });

  /**
   * The Presence routes that act on one presence say which one.
   *
   * These four take a `pageId` and change or display that page: its details,
   * its connections, who can act for it. Every caller already passes the name
   * alongside the id — `PageScreen` and `PagesHubScreen` both send
   * `title: page.name` — and three of the four dropped it, leaving a header
   * that read "Team & access" above the subtitle "Who can act for this
   * presence" with no antecedent for "this" anywhere on screen. A member with
   * an artist page and a restaurant could not tell from the header which team
   * they were about to change.
   *
   * The remaining three Presence routes are asserted *not* to do this, which is
   * the half that stops the rule being applied by reflex. `Presence` and
   * `PagesHub` are about all of a member's presences at once and `PageCreate`
   * is about one that does not exist yet, so a name in the header would either
   * be wrong or be a lie about what the screen operates on.
   *
   * `title?: string` is a repo-wide param convention on around a hundred
   * routes, most of which ignore it deliberately, so this is scoped to the
   * routes where a name is the difference between editing the right presence
   * and the wrong one rather than asserted globally.
   */
  it("titles a per-presence route with the presence, and the rest with their function", () => {
    const titleFor = (route: string) => {
      const match = new RegExp(`<Stack\\.Screen name="${route}"[^\\n]*`).exec(SOURCE);
      if (!match) throw new Error(`no <Stack.Screen name="${route}"> in AppNavigator.tsx`);
      return match[0];
    };

    for (const route of ["Page", "PageEdit", "PageConnections", "PageTeam"]) {
      // The fallback is asserted too: a deep link can arrive with an id and no
      // name, and a header that renders "undefined" is worse than a generic one.
      expect(titleFor(route)).toMatch(/title: route\.params\?\.title \|\| t\("common:screens\./);
    }

    for (const route of ["Presence", "PagesHub", "PageCreate"]) {
      expect(titleFor(route)).not.toMatch(/route\.params\?\.title/);
    }
  });
});

/**
 * Every language ships the same navigation chrome or none of it. A partially
 * translated header is worse than an English one: half the screen titles switch
 * and half do not, which reads as a rendering bug rather than a missing string.
 */
describe("navigation chrome is translated in every shipped language", () => {
  const keys = [
    ...Array.from(SOURCE.matchAll(/"(common:(?:screens|tabs|navSubtitles|identity)\.[a-zA-Z]+)"/g)).map(
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
