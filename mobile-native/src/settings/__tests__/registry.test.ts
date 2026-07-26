/**
 * The registry is load-bearing: it is simultaneously the index list, the search
 * index, and the deep-link table. These tests guard the invariants that make
 * that safe — chiefly that every entry points somewhere real, and that the
 * visibility filter can never be defeated by search.
 */

import fs from "fs";
import path from "path";
import {
  SETTINGS_ENTRIES,
  SETTINGS_SECTIONS,
  findSettingsEntry,
  groupBySection,
  searchSettings,
  settingsKeywords,
  settingsSectionTitle,
  settingsSubtitle,
  settingsTitle,
  visibleSettings
} from "../registry";
import { activateLocale } from "../../i18n/engine";

/**
 * The registry now reads its display strings from the catalogs, and the engine
 * only serves a namespace once it has been loaded. In the app the provider does
 * this before the first frame; here it has to be done explicitly, or every
 * lookup falls through to the humanized-key path and the search tests would be
 * matching against the word "Title".
 */
beforeAll(async () => {
  await activateLocale("en");
});

describe("settings registry integrity", () => {
  it("has unique ids", () => {
    const ids = SETTINGS_ENTRIES.map((entry) => entry.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("assigns every entry to a declared section", () => {
    const sections = new Set(SETTINGS_SECTIONS.map((section) => section.id));
    SETTINGS_ENTRIES.forEach((entry) => expect(sections.has(entry.section)).toBe(true));
  });

  it("uses ids that are valid deep-link path segments", () => {
    // `settingsDeepLink` in linking.ts matches /^\/?settings\/([a-z0-9-]+)\/?$/i.
    // An id with an underscore or a space would be silently unreachable.
    SETTINGS_ENTRIES.forEach((entry) => expect(entry.id).toMatch(/^[a-z0-9-]+$/));
  });

  /**
   * The registry holds no display strings — the id is also the catalog key
   * segment. A missing key would surface as a humanized fallback ("Blocked"
   * rendered from `blocked.title`), which looks close enough to correct to ship
   * unnoticed, so it is asserted rather than eyeballed.
   */
  it("has a translated title, subtitle and keyword set for every entry", () => {
    SETTINGS_ENTRIES.forEach((entry) => {
      const fields = { title: settingsTitle(entry), subtitle: settingsSubtitle(entry), keywords: settingsKeywords(entry) };
      Object.entries(fields).forEach(([field, text]) => {
        expect(text.length).toBeGreaterThan(0);
        // The engine's last-resort fallback humanizes the final key segment, so
        // a missing key renders as the literal word "Title" or "Keywords".
        expect(text.toLowerCase()).not.toBe(field);
        expect(text).not.toContain("settings:index");
      });
    });
  });

  it("has a translated title for every section", () => {
    SETTINGS_SECTIONS.forEach((section) => {
      expect(settingsSectionTitle(section.id).length).toBeGreaterThan(0);
    });
  });

  /**
   * Read rather than imported: importing AppNavigator would pull in the whole
   * screen graph — expo-notifications, the camera, the call layer — to answer a
   * question that is purely about two lists of strings.
   */
  it("routes only to screens the navigator actually registers", () => {
    const navigator = fs.readFileSync(
      path.join(__dirname, "..", "..", "navigation", "AppNavigator.tsx"),
      "utf8"
    );
    const registered = new Set(
      Array.from(navigator.matchAll(/<Stack\.Screen name="([A-Za-z]+)"/g)).map((match) => match[1])
    );
    const unregistered = SETTINGS_ENTRIES.filter((entry) => !registered.has(entry.route)).map(
      (entry) => `${entry.id} -> ${entry.route}`
    );
    expect(unregistered).toEqual([]);
  });

  it("declares every route in RootStackParamList", () => {
    const types = fs.readFileSync(path.join(__dirname, "..", "..", "navigation", "types.ts"), "utf8");
    SETTINGS_ENTRIES.forEach((entry) => {
      expect(types).toMatch(new RegExp(`^\\s*${entry.route}:`, "m"));
    });
  });
});

describe("visibility", () => {
  const signedOut = visibleSettings({ authenticated: false, developerEnabled: false });
  const signedIn = visibleSettings({ authenticated: true, developerEnabled: false });

  it("hides authenticated-only entries when signed out", () => {
    expect(signedOut.some((entry) => entry.requiresAuth)).toBe(false);
    expect(signedIn.some((entry) => entry.requiresAuth)).toBe(true);
  });

  it("hides developer entries until developer options are on", () => {
    expect(signedIn.some((entry) => entry.developerOnly)).toBe(false);
    expect(
      visibleSettings({ authenticated: true, developerEnabled: true }).some((entry) => entry.developerOnly)
    ).toBe(true);
  });

  /**
   * The index filters visibility first and searches second. If that order were
   * ever reversed, typing "blocked" while signed out would surface a row that
   * navigates to an authenticated-only screen.
   */
  it("cannot surface a hidden entry through search", () => {
    const hidden = SETTINGS_ENTRIES.filter((entry) => entry.requiresAuth || entry.developerOnly);
    expect(hidden.length).toBeGreaterThan(0);
    hidden.forEach((entry) => {
      const results = searchSettings(settingsTitle(entry), signedOut);
      expect(results.some((hit) => hit.id === entry.id)).toBe(false);
    });
  });
});

describe("search", () => {
  it("returns everything for an empty or whitespace query", () => {
    expect(searchSettings("")).toHaveLength(SETTINGS_ENTRIES.length);
    expect(searchSettings("   ")).toHaveLength(SETTINGS_ENTRIES.length);
  });

  it("matches on keywords, not just titles", () => {
    // "dark mode" appears nowhere in the Appearance title or subtitle.
    expect(searchSettings("dark").map((entry) => entry.id)).toContain("appearance");
    expect(searchSettings("2fa").map((entry) => entry.id)).toContain("security");
    expect(searchSettings("gdpr").map((entry) => entry.id)).toContain("data");
  });

  it("is order-independent and prefix-based", () => {
    expect(searchSettings("mode dark").map((entry) => entry.id)).toContain("appearance");
    expect(searchSettings("dark m").map((entry) => entry.id)).toContain("appearance");
  });

  it("ignores case and punctuation", () => {
    expect(searchSettings("TWO-FACTOR").map((entry) => entry.id)).toContain("security");
  });

  it("ranks a title hit above a keyword-only hit", () => {
    // "Notifications" is a title; it is also a keyword-ish term elsewhere.
    const results = searchSettings("notification");
    expect(results[0].id).toBe("notifications");
  });

  it("returns nothing for a query that matches nothing", () => {
    expect(searchSettings("zzzznotasetting")).toHaveLength(0);
  });

  it("requires every token to match, not just one", () => {
    // "dark" hits Appearance, "gdpr" hits Data — together they must hit neither.
    expect(searchSettings("dark gdpr")).toHaveLength(0);
  });
});

describe("grouping", () => {
  it("preserves section order and drops empty sections", () => {
    const grouped = groupBySection(searchSettings("dark"));
    expect(grouped).toHaveLength(1);
    expect(grouped[0].id).toBe("preferences");
  });

  it("accounts for every entry exactly once", () => {
    const grouped = groupBySection(SETTINGS_ENTRIES);
    const flattened = grouped.flatMap((section) => section.entries);
    expect(flattened).toHaveLength(SETTINGS_ENTRIES.length);
  });
});

describe("findSettingsEntry", () => {
  it("resolves each declared id", () => {
    SETTINGS_ENTRIES.forEach((entry) => expect(findSettingsEntry(entry.id)?.route).toBe(entry.route));
  });

  it("returns undefined for an unknown id", () => {
    expect(findSettingsEntry("not-a-real-page")).toBeUndefined();
  });
});
