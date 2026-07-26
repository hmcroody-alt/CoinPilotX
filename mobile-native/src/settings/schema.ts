/**
 * PulseSoc native settings — single typed source of truth.
 *
 * Every user-facing preference in the app is declared here exactly once. The
 * store (`./store`), the backend adapters (`./api`), and every settings screen
 * read from this module, so adding a preference is a one-line change that
 * automatically gains persistence, hydration, optimistic sync, and rollback.
 *
 * Invariants:
 *  - `DEFAULT_PREFERENCES` is exhaustive. A missing key is a type error.
 *  - Every value is JSON-serializable (persisted verbatim to AsyncStorage).
 *  - `normalizePreferences` is total: it accepts arbitrary untrusted input
 *    (stale cache from an older app version, a partial/hostile API response)
 *    and always returns a fully-populated, in-range object.
 */

export type ThemeMode = "system" | "light" | "dark";
export type Audience = "everyone" | "followers" | "nobody";
export type ProfileVisibility = "public" | "followers" | "private";
export type AutoDownloadPolicy = "always" | "wifi" | "never";
export type MediaQuality = "auto" | "data_saver" | "high";
export type TimeFormat = "12h" | "24h";
export type ChannelKey = "push" | "email" | "inApp";

/** Notification categories the backend fans out on. */
export const NOTIFICATION_CATEGORIES = [
  "likes",
  "comments",
  "mentions",
  "follows",
  "messages",
  "calls",
  "live",
  "reels",
  "groups",
  "marketplace",
  "security",
  "product"
] as const;

export type NotificationCategory = (typeof NOTIFICATION_CATEGORIES)[number];

export type CategoryChannels = { push: boolean; email: boolean; inApp: boolean };

export type AppearancePreferences = {
  theme: ThemeMode;
  fontScale: number;
  reduceTransparency: boolean;
  compactDensity: boolean;
};

export type AccessibilityPreferences = {
  reduceMotion: boolean;
  boldText: boolean;
  highContrast: boolean;
  captionsEnabled: boolean;
  hapticFeedback: boolean;
  screenReaderHints: boolean;
};

export type LanguagePreferences = {
  appLanguage: string;
  contentLanguages: string[];
  autoTranslate: boolean;
  region: string;
  timeFormat: TimeFormat;
};

export type NotificationPreferences = {
  pushEnabled: boolean;
  emailEnabled: boolean;
  smsEnabled: boolean;
  sound: boolean;
  vibration: boolean;
  previewText: boolean;
  quietHoursEnabled: boolean;
  quietHoursStart: string;
  quietHoursEnd: string;
  categories: Record<NotificationCategory, CategoryChannels>;
};

export type PrivacyPreferences = {
  accountVisibility: ProfileVisibility;
  lastSeen: Audience;
  onlineStatus: boolean;
  readReceipts: boolean;
  storyAudience: Audience;
  liveAudience: Audience;
  allowTagging: Audience;
  allowMentions: Audience;
  allowDirectMessages: Audience;
  searchableByEmail: boolean;
  searchableByPhone: boolean;
};

export type SecurityPreferences = {
  twoFactorEnabled: boolean;
  biometricUnlock: boolean;
  loginAlerts: boolean;
  requirePasswordForSensitiveChanges: boolean;
};

export type StoragePreferences = {
  autoDownloadPhotos: AutoDownloadPolicy;
  autoDownloadVideos: AutoDownloadPolicy;
  autoDownloadAudio: AutoDownloadPolicy;
  mediaQuality: MediaQuality;
  cacheLimitMb: number;
  autoClearCache: boolean;
};

export type DataPreferences = {
  personalizedAds: boolean;
  shareAnalytics: boolean;
  shareCrashReports: boolean;
  activityStatusSharing: boolean;
};

export type DeveloperPreferences = {
  enabled: boolean;
  showPerfOverlay: boolean;
  verboseApiLogging: boolean;
};

export type Preferences = {
  appearance: AppearancePreferences;
  accessibility: AccessibilityPreferences;
  language: LanguagePreferences;
  notifications: NotificationPreferences;
  privacy: PrivacyPreferences;
  security: SecurityPreferences;
  storage: StoragePreferences;
  data: DataPreferences;
  developer: DeveloperPreferences;
};

export type PreferenceGroup = keyof Preferences;

/** Font scale bounds. Below 0.85 rows clip; above 1.4 native controls overflow. */
export const FONT_SCALE_MIN = 0.85;
export const FONT_SCALE_MAX = 1.4;
export const FONT_SCALE_STEP = 0.05;

export const CACHE_LIMIT_MIN_MB = 128;
export const CACHE_LIMIT_MAX_MB = 8192;

function allCategories(value: CategoryChannels): Record<NotificationCategory, CategoryChannels> {
  return NOTIFICATION_CATEGORIES.reduce((acc, category) => {
    acc[category] = { ...value };
    return acc;
  }, {} as Record<NotificationCategory, CategoryChannels>);
}

export const DEFAULT_PREFERENCES: Preferences = {
  appearance: {
    theme: "system",
    fontScale: 1,
    reduceTransparency: false,
    compactDensity: false
  },
  accessibility: {
    reduceMotion: false,
    boldText: false,
    highContrast: false,
    captionsEnabled: true,
    hapticFeedback: true,
    screenReaderHints: true
  },
  language: {
    appLanguage: "en",
    contentLanguages: ["en"],
    autoTranslate: false,
    region: "auto",
    timeFormat: "12h"
  },
  notifications: {
    pushEnabled: true,
    emailEnabled: true,
    smsEnabled: false,
    sound: true,
    vibration: true,
    previewText: true,
    quietHoursEnabled: false,
    quietHoursStart: "22:00",
    quietHoursEnd: "07:00",
    categories: {
      ...allCategories({ push: true, email: false, inApp: true }),
      messages: { push: true, email: false, inApp: true },
      calls: { push: true, email: false, inApp: true },
      security: { push: true, email: true, inApp: true },
      product: { push: false, email: true, inApp: false }
    }
  },
  privacy: {
    accountVisibility: "public",
    lastSeen: "followers",
    onlineStatus: true,
    readReceipts: true,
    storyAudience: "followers",
    liveAudience: "everyone",
    allowTagging: "everyone",
    allowMentions: "everyone",
    allowDirectMessages: "everyone",
    searchableByEmail: true,
    searchableByPhone: false
  },
  security: {
    twoFactorEnabled: false,
    biometricUnlock: false,
    loginAlerts: true,
    requirePasswordForSensitiveChanges: true
  },
  storage: {
    autoDownloadPhotos: "wifi",
    autoDownloadVideos: "wifi",
    autoDownloadAudio: "wifi",
    mediaQuality: "auto",
    cacheLimitMb: 1024,
    autoClearCache: false
  },
  data: {
    personalizedAds: true,
    shareAnalytics: true,
    shareCrashReports: true,
    activityStatusSharing: true
  },
  developer: {
    enabled: false,
    showPerfOverlay: false,
    verboseApiLogging: false
  }
};

/* -------------------------------------------------------------------------- */
/*                                 Coercion                                    */
/* -------------------------------------------------------------------------- */

function bool(value: unknown, fallback: boolean): boolean {
  if (typeof value === "boolean") return value;
  if (value === 1 || value === "1" || value === "true" || value === "on" || value === "yes") return true;
  if (value === 0 || value === "0" || value === "false" || value === "off" || value === "no") return false;
  return fallback;
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[], fallback: T): T {
  const text = typeof value === "string" ? value.trim().toLowerCase() : "";
  const match = allowed.find((option) => option.toLowerCase() === text);
  return match ?? fallback;
}

function clampNumber(value: unknown, min: number, max: number, fallback: number): number {
  const parsed = typeof value === "number" ? value : Number.parseFloat(String(value ?? ""));
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

/** Snap a font scale to the nearest step so slider drags produce stable values. */
export function quantizeFontScale(value: number): number {
  const clamped = Math.min(FONT_SCALE_MAX, Math.max(FONT_SCALE_MIN, value));
  const steps = Math.round((clamped - FONT_SCALE_MIN) / FONT_SCALE_STEP);
  return Number((FONT_SCALE_MIN + steps * FONT_SCALE_STEP).toFixed(2));
}

/** `HH:MM` in 24-hour form; anything else falls back. */
function timeOfDay(value: unknown, fallback: string): string {
  const text = String(value ?? "").trim();
  const match = /^(\d{1,2}):(\d{2})$/.exec(text);
  if (!match) return fallback;
  const hours = Number(match[1]);
  const minutes = Number(match[2]);
  if (hours > 23 || minutes > 59) return fallback;
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}`;
}

function languageTag(value: unknown, fallback: string): string {
  const text = String(value ?? "").trim();
  if (!/^[a-zA-Z]{2,3}(-[a-zA-Z0-9]{2,8})*$/.test(text)) return fallback;
  return text.toLowerCase();
}

function languageTags(value: unknown, fallback: string[]): string[] {
  if (!Array.isArray(value)) return [...fallback];
  const tags = value
    .map((entry) => languageTag(entry, ""))
    .filter((entry): entry is string => Boolean(entry));
  const unique = Array.from(new Set(tags));
  return unique.length ? unique : [...fallback];
}

function normalizeChannels(value: unknown, fallback: CategoryChannels): CategoryChannels {
  const source = (value && typeof value === "object" ? value : {}) as Partial<Record<ChannelKey, unknown>>;
  return {
    push: bool(source.push, fallback.push),
    email: bool(source.email, fallback.email),
    inApp: bool(source.inApp, fallback.inApp)
  };
}

/**
 * Total normalizer. Accepts anything (including `null`, arrays, or a response
 * shaped for a different app version) and returns a complete `Preferences`.
 */
export function normalizePreferences(input: unknown, base: Preferences = DEFAULT_PREFERENCES): Preferences {
  const raw = (input && typeof input === "object" && !Array.isArray(input) ? input : {}) as Record<string, any>;
  const group = <K extends PreferenceGroup>(key: K): Record<string, any> => {
    const value = raw[key];
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  };

  const appearance = group("appearance");
  const accessibility = group("accessibility");
  const language = group("language");
  const notifications = group("notifications");
  const privacy = group("privacy");
  const security = group("security");
  const storage = group("storage");
  const data = group("data");
  const developer = group("developer");

  const rawCategories =
    notifications.categories && typeof notifications.categories === "object" && !Array.isArray(notifications.categories)
      ? (notifications.categories as Record<string, unknown>)
      : {};

  return {
    appearance: {
      theme: oneOf<ThemeMode>(appearance.theme, ["system", "light", "dark"], base.appearance.theme),
      fontScale: quantizeFontScale(clampNumber(appearance.fontScale, FONT_SCALE_MIN, FONT_SCALE_MAX, base.appearance.fontScale)),
      reduceTransparency: bool(appearance.reduceTransparency, base.appearance.reduceTransparency),
      compactDensity: bool(appearance.compactDensity, base.appearance.compactDensity)
    },
    accessibility: {
      reduceMotion: bool(accessibility.reduceMotion, base.accessibility.reduceMotion),
      boldText: bool(accessibility.boldText, base.accessibility.boldText),
      highContrast: bool(accessibility.highContrast, base.accessibility.highContrast),
      captionsEnabled: bool(accessibility.captionsEnabled, base.accessibility.captionsEnabled),
      hapticFeedback: bool(accessibility.hapticFeedback, base.accessibility.hapticFeedback),
      screenReaderHints: bool(accessibility.screenReaderHints, base.accessibility.screenReaderHints)
    },
    language: {
      appLanguage: languageTag(language.appLanguage, base.language.appLanguage),
      contentLanguages: languageTags(language.contentLanguages, base.language.contentLanguages),
      autoTranslate: bool(language.autoTranslate, base.language.autoTranslate),
      region: String(language.region ?? base.language.region).trim() || base.language.region,
      timeFormat: oneOf<TimeFormat>(language.timeFormat, ["12h", "24h"], base.language.timeFormat)
    },
    notifications: {
      pushEnabled: bool(notifications.pushEnabled, base.notifications.pushEnabled),
      emailEnabled: bool(notifications.emailEnabled, base.notifications.emailEnabled),
      smsEnabled: bool(notifications.smsEnabled, base.notifications.smsEnabled),
      sound: bool(notifications.sound, base.notifications.sound),
      vibration: bool(notifications.vibration, base.notifications.vibration),
      previewText: bool(notifications.previewText, base.notifications.previewText),
      quietHoursEnabled: bool(notifications.quietHoursEnabled, base.notifications.quietHoursEnabled),
      quietHoursStart: timeOfDay(notifications.quietHoursStart, base.notifications.quietHoursStart),
      quietHoursEnd: timeOfDay(notifications.quietHoursEnd, base.notifications.quietHoursEnd),
      categories: NOTIFICATION_CATEGORIES.reduce((acc, category) => {
        acc[category] = normalizeChannels(rawCategories[category], base.notifications.categories[category]);
        return acc;
      }, {} as Record<NotificationCategory, CategoryChannels>)
    },
    privacy: {
      accountVisibility: oneOf<ProfileVisibility>(
        privacy.accountVisibility,
        ["public", "followers", "private"],
        base.privacy.accountVisibility
      ),
      lastSeen: oneOf<Audience>(privacy.lastSeen, ["everyone", "followers", "nobody"], base.privacy.lastSeen),
      onlineStatus: bool(privacy.onlineStatus, base.privacy.onlineStatus),
      readReceipts: bool(privacy.readReceipts, base.privacy.readReceipts),
      storyAudience: oneOf<Audience>(privacy.storyAudience, ["everyone", "followers", "nobody"], base.privacy.storyAudience),
      liveAudience: oneOf<Audience>(privacy.liveAudience, ["everyone", "followers", "nobody"], base.privacy.liveAudience),
      allowTagging: oneOf<Audience>(privacy.allowTagging, ["everyone", "followers", "nobody"], base.privacy.allowTagging),
      allowMentions: oneOf<Audience>(privacy.allowMentions, ["everyone", "followers", "nobody"], base.privacy.allowMentions),
      allowDirectMessages: oneOf<Audience>(
        privacy.allowDirectMessages,
        ["everyone", "followers", "nobody"],
        base.privacy.allowDirectMessages
      ),
      searchableByEmail: bool(privacy.searchableByEmail, base.privacy.searchableByEmail),
      searchableByPhone: bool(privacy.searchableByPhone, base.privacy.searchableByPhone)
    },
    security: {
      twoFactorEnabled: bool(security.twoFactorEnabled, base.security.twoFactorEnabled),
      biometricUnlock: bool(security.biometricUnlock, base.security.biometricUnlock),
      loginAlerts: bool(security.loginAlerts, base.security.loginAlerts),
      requirePasswordForSensitiveChanges: bool(
        security.requirePasswordForSensitiveChanges,
        base.security.requirePasswordForSensitiveChanges
      )
    },
    storage: {
      autoDownloadPhotos: oneOf<AutoDownloadPolicy>(
        storage.autoDownloadPhotos,
        ["always", "wifi", "never"],
        base.storage.autoDownloadPhotos
      ),
      autoDownloadVideos: oneOf<AutoDownloadPolicy>(
        storage.autoDownloadVideos,
        ["always", "wifi", "never"],
        base.storage.autoDownloadVideos
      ),
      autoDownloadAudio: oneOf<AutoDownloadPolicy>(
        storage.autoDownloadAudio,
        ["always", "wifi", "never"],
        base.storage.autoDownloadAudio
      ),
      mediaQuality: oneOf<MediaQuality>(storage.mediaQuality, ["auto", "data_saver", "high"], base.storage.mediaQuality),
      cacheLimitMb: Math.round(
        clampNumber(storage.cacheLimitMb, CACHE_LIMIT_MIN_MB, CACHE_LIMIT_MAX_MB, base.storage.cacheLimitMb)
      ),
      autoClearCache: bool(storage.autoClearCache, base.storage.autoClearCache)
    },
    data: {
      personalizedAds: bool(data.personalizedAds, base.data.personalizedAds),
      shareAnalytics: bool(data.shareAnalytics, base.data.shareAnalytics),
      shareCrashReports: bool(data.shareCrashReports, base.data.shareCrashReports),
      activityStatusSharing: bool(data.activityStatusSharing, base.data.activityStatusSharing)
    },
    developer: {
      enabled: bool(developer.enabled, base.developer.enabled),
      showPerfOverlay: bool(developer.showPerfOverlay, base.developer.showPerfOverlay),
      verboseApiLogging: bool(developer.verboseApiLogging, base.developer.verboseApiLogging)
    }
  };
}

/** Deep structural equality for preference groups (all values are primitives, arrays, or flat records). */
export function preferencesEqual(a: Preferences, b: Preferences): boolean {
  return JSON.stringify(a) === JSON.stringify(b);
}

export const NOTIFICATION_CATEGORY_LABELS: Record<NotificationCategory, { title: string; description: string }> = {
  likes: { title: "Likes", description: "When someone likes your posts, reels, or comments." },
  comments: { title: "Comments", description: "Replies and comments on content you posted." },
  mentions: { title: "Mentions & tags", description: "When someone @mentions or tags you." },
  follows: { title: "Followers", description: "New followers and follow requests." },
  messages: { title: "Messages", description: "Direct messages and group chat activity." },
  calls: { title: "Calls", description: "Incoming voice and video calls." },
  live: { title: "Live", description: "When accounts you follow start a live broadcast." },
  reels: { title: "Reels", description: "Activity on your reels and suggested reels." },
  groups: { title: "Groups", description: "Posts, invites, and requests in your groups." },
  marketplace: { title: "Marketplace", description: "Orders, listings, and buyer or seller messages." },
  security: { title: "Security alerts", description: "Sign-ins, password changes, and account recovery." },
  product: { title: "Product updates", description: "New PulseSoc features, tips, and announcements." }
};
