/**
 * The settings map.
 *
 * One declaration per destination drives three things that used to drift apart:
 * the sectioned index, the search index, and the deep-link table. Adding a page
 * means adding an entry here — it then appears in the list, becomes searchable
 * by its keywords, and gains a `pulsesoc://settings/<slug>` URL automatically.
 */

import { Ionicons } from "@expo/vector-icons";
import type { RootStackParamList } from "../navigation/types";
import { translate } from "../i18n/engine";

export type SettingsSectionId = "account" | "preferences" | "privacy" | "support";

export type SettingsEntry = {
  /** Stable slug — also the deep-link path segment *and* the catalog key
   *  segment (`settings:index.entries.<id>.*`). Never rename in place. */
  id: string;
  section: SettingsSectionId;
  icon: keyof typeof Ionicons.glyphMap;
  route: keyof RootStackParamList;
  params?: Record<string, unknown>;
  /** Hidden until the user enables Developer Options. */
  developerOnly?: boolean;
  /** Requires an authenticated session. */
  requiresAuth?: boolean;
};

/**
 * No display strings live in this file.
 *
 * Titles, subtitles and search keywords are all addressed by `id` in
 * `settings:index.*`, so the index list, the search index and the section
 * headings are localized by the same mechanism as everything else, and adding a
 * language never touches this file. The accessors below are the only supported
 * way to read them.
 */
const entryKey = (id: string, field: "title" | "subtitle" | "keywords") => `settings:index.entries.${id}.${field}`;

export function settingsTitle(entry: SettingsEntry): string {
  return translate(entryKey(entry.id, "title"));
}

export function settingsSubtitle(entry: SettingsEntry): string {
  return translate(entryKey(entry.id, "subtitle"));
}

/**
 * Search synonyms, stored as one space-separated string per entry.
 *
 * A flat string rather than a JSON array because translators work in the
 * catalogs, and a language whose synonym set is a different size than English's
 * must be free to say so — "dark mode" is two words in English and one in
 * German. The search tokenizer does not care how many there are.
 */
export function settingsKeywords(entry: SettingsEntry): string {
  return translate(entryKey(entry.id, "keywords"));
}

export function settingsSectionTitle(id: SettingsSectionId): string {
  return translate(`settings:index.sections.${id}`);
}

export const SETTINGS_SECTIONS: { id: SettingsSectionId }[] = [
  { id: "account" },
  { id: "preferences" },
  { id: "privacy" },
  { id: "support" }
];

export const SETTINGS_ENTRIES: SettingsEntry[] = [
  /* ------------------------------- Account -------------------------------- */
  {
    id: "profile",
    section: "account",
    icon: "person-circle-outline",
    route: "ProfileEdit",
    requiresAuth: true
  },
  {
    id: "account",
    section: "account",
    icon: "id-card-outline",
    route: "AccountCenter",
    params: { section: "account" },
    requiresAuth: true
  },
  {
    id: "security",
    section: "account",
    icon: "lock-closed-outline",
    route: "SecuritySettings",
    requiresAuth: true
  },
  {
    id: "sessions",
    section: "account",
    icon: "phone-portrait-outline",
    route: "SessionsDevices",
    requiresAuth: true
  },
  {
    id: "account-health",
    section: "account",
    icon: "shield-half-outline",
    route: "AccountHealth",
    requiresAuth: true
  },

  /* ----------------------------- Preferences ------------------------------ */
  {
    id: "notifications",
    section: "preferences",
    icon: "notifications-outline",
    route: "NotificationSettings",
  },
  {
    id: "appearance",
    section: "preferences",
    icon: "color-palette-outline",
    route: "AppearanceSettings",
  },
  {
    id: "accessibility",
    section: "preferences",
    icon: "accessibility-outline",
    route: "AccessibilitySettings",
  },
  {
    id: "language",
    section: "preferences",
    icon: "language-outline",
    route: "LanguageSettings",
  },
  {
    id: "storage",
    section: "preferences",
    icon: "server-outline",
    route: "StorageSettings",
  },
  {
    id: "permissions",
    section: "preferences",
    icon: "options-outline",
    route: "PermissionsSettings",
  },

  /* ------------------------------- Privacy -------------------------------- */
  {
    id: "privacy",
    section: "privacy",
    icon: "eye-off-outline",
    route: "PrivacySettings",
    requiresAuth: true
  },
  {
    id: "blocked",
    section: "privacy",
    icon: "ban-outline",
    route: "BlockedUsers",
    requiresAuth: true
  },
  {
    id: "muted",
    section: "privacy",
    icon: "volume-mute-outline",
    route: "MutedUsers",
    requiresAuth: true
  },
  {
    id: "data",
    section: "privacy",
    icon: "shield-checkmark-outline",
    route: "DataPrivacySettings",
    requiresAuth: true
  },
  {
    id: "safety",
    section: "privacy",
    icon: "shield-outline",
    route: "SafetyHub",
    requiresAuth: true
  },

  /* ------------------------------- Support -------------------------------- */
  {
    id: "help",
    section: "support",
    icon: "help-circle-outline",
    route: "HelpSettings",
  },
  {
    id: "about",
    section: "support",
    icon: "information-circle-outline",
    route: "AboutSettings",
  },
  {
    id: "legal",
    section: "support",
    icon: "document-text-outline",
    route: "LegalSettings",
  },
  {
    id: "developer",
    section: "support",
    icon: "construct-outline",
    route: "DeveloperSettings",
    developerOnly: true
  }
];

/* ------------------------------------------------------------------ *
 * Search
 * ------------------------------------------------------------------ */

/**
 * Punctuation and symbols, expressed as what to *remove* rather than what to
 * keep.
 *
 * The previous form was `/[^a-z0-9\s]/g`, which is a whitelist of ASCII. Under
 * it, searching settings in Japanese, Korean, Chinese, Hindi or Arabic deleted
 * the entire query and the entire index: every token became empty, so the
 * filter silently returned all 19 rows and the search box appeared to do
 * nothing. Enumerating separators instead means every script survives.
 */
const PUNCTUATION = /[!-/:-@[-`{-~¡-¿،؛؟。、「」！？：；（）［］【】…—–「」]/g;

/** Combining marks left behind by NFD, so "café" and "cafe" match each other. */
const COMBINING = /[̀-ًͯ-ٰٟ]/g;

/**
 * Scripts that do not put spaces between words: CJK ideographs, kana, Hangul,
 * and Thai. A query in one of these arrives as a single token that will never
 * *prefix* a "word", because the whole entry is one word — so those tokens are
 * matched by substring instead.
 */
const UNSEGMENTED = /[぀-ヿ㐀-䶿一-鿿가-힯฀-๿]/;

function fold(text: string): string {
  const lowered = text.toLowerCase();
  // `normalize` is guaranteed by ES2015 but guarded anyway: a stripped Hermes
  // build losing search is worse than one losing accent-insensitivity.
  const decomposed = typeof lowered.normalize === "function" ? lowered.normalize("NFD") : lowered;
  return decomposed.replace(COMBINING, "").replace(PUNCTUATION, " ");
}

/** Every searchable string for an entry, in the active language. */
function haystack(entry: SettingsEntry): string {
  return fold(`${settingsTitle(entry)} ${settingsSubtitle(entry)} ${settingsKeywords(entry)}`);
}

/**
 * Token search over the *translated* index.
 *
 * Every whitespace-separated token in the query must match the entry: by word
 * prefix for space-separated scripts ("dark m" finds Appearance via its "dark
 * mode" keyword, and "mode dark" matches equally), or by substring for scripts
 * that do not separate words. Because the haystack is built from `translate`,
 * a user searching in Japanese searches Japanese text, not romanised English.
 */
export function searchSettings(query: string, entries: SettingsEntry[] = SETTINGS_ENTRIES): SettingsEntry[] {
  const tokens = fold(query).split(/\s+/).filter(Boolean);
  if (!tokens.length) return entries;

  const scored = entries
    .map((entry) => {
      const text = haystack(entry);
      const words = text.split(/\s+/).filter(Boolean);
      const title = fold(settingsTitle(entry));
      let score = 0;
      const matchedAll = tokens.every((token) => {
        const exact = words.some((word) => word === token);
        const prefix = exact || words.some((word) => word.startsWith(token));
        // Substring is the fallback, not the rule: allowing it everywhere would
        // make "on" match "notifications", which is noise in a 19-row list.
        const matched = prefix || (UNSEGMENTED.test(token) && text.includes(token));
        if (exact) score += 3;
        else if (matched) score += 1;
        // A title hit outranks a keyword-only hit.
        if (title.includes(token)) score += 4;
        return matched;
      });
      return matchedAll ? { entry, score } : null;
    })
    .filter((hit): hit is { entry: SettingsEntry; score: number } => Boolean(hit));

  return scored.sort((a, b) => b.score - a.score).map((hit) => hit.entry);
}

/** Entries visible for the current session state. */
export function visibleSettings({
  authenticated,
  developerEnabled
}: {
  authenticated: boolean;
  developerEnabled: boolean;
}): SettingsEntry[] {
  return SETTINGS_ENTRIES.filter((entry) => {
    if (entry.developerOnly && !developerEnabled) return false;
    if (entry.requiresAuth && !authenticated) return false;
    return true;
  });
}

export function groupBySection(entries: SettingsEntry[]) {
  return SETTINGS_SECTIONS.map((section) => ({
    ...section,
    entries: entries.filter((entry) => entry.section === section.id)
  })).filter((section) => section.entries.length > 0);
}

export function findSettingsEntry(id: string): SettingsEntry | undefined {
  return SETTINGS_ENTRIES.find((entry) => entry.id === id);
}
