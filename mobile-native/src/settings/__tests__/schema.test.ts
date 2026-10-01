/**
 * The client half of the normalizer pair.
 *
 * `services/pulse_settings_routes.py` holds the other half, and the two are
 * tested separately in the two languages rather than against each other,
 * because the sandbox cannot import Flask. What both files claim is the same
 * property, so it is stated the same way here as it is there: the normalizer is
 * *total*. Any input at all — `null`, an array, a document written by a future
 * app version, a response crafted by someone who is not the server — must
 * produce a complete, in-range `Preferences`.
 *
 * Totality is worth testing exhaustively rather than by example because a
 * normalizer that is merely "usually right" is indistinguishable from a correct
 * one in review, and the way it fails is a screen rendering `undefined` or a
 * slider bound to `NaN`. So the tests below feed it garbage of every shape and
 * then assert the *whole* result equals the defaults, rather than checking the
 * two or three keys the author happened to think of.
 */

import {
  CACHE_LIMIT_MAX_MB,
  CACHE_LIMIT_MIN_MB,
  DEFAULT_PREFERENCES,
  FONT_SCALE_MAX,
  FONT_SCALE_MIN,
  NOTIFICATION_CATEGORIES,
  NOTIFICATION_CATEGORY_LABELS,
  DEVICE_LOCAL_KEYS,
  Preferences,
  PreferenceGroup,
  isDeviceLocalGroup,
  normalizePreferences,
  preferencesEqual,
  quantizeFontScale,
  stripDeviceLocal,
  withDeviceLocal
} from "../schema";

const GROUPS = Object.keys(DEFAULT_PREFERENCES) as (keyof Preferences)[];

describe("normalizePreferences — totality", () => {
  // Every value a caller could plausibly hand this function that is not a
  // preferences document. Each must produce the defaults rather than throw,
  // because each one is something a corrupt cache or a proxy actually returns.
  const NON_DOCUMENTS: [string, unknown][] = [
    ["null", null],
    ["undefined", undefined],
    ["a number", 42],
    ["a string", "preferences"],
    ["an empty string", ""],
    ["a boolean", true],
    ["an array", [{ appearance: { theme: "dark" } }]],
    ["an empty array", []],
    ["a function", () => ({ appearance: { theme: "dark" } })],
    ["NaN", NaN]
  ];

  it.each(NON_DOCUMENTS)("returns the complete defaults for %s", (_label, input) => {
    expect(normalizePreferences(input)).toEqual(DEFAULT_PREFERENCES);
  });

  it("returns every group even when the document is empty", () => {
    // A missing group must not become `undefined`: a screen reading
    // `preferences.storage.cacheLimitMb` off an absent group crashes on render.
    const result = normalizePreferences({});
    for (const group of GROUPS) {
      expect(result[group]).toBeDefined();
      expect(Object.keys(result[group]).sort()).toEqual(Object.keys(DEFAULT_PREFERENCES[group]).sort());
    }
  });

  it.each(GROUPS)("replaces a non-object %s group with its defaults", (group) => {
    for (const bad of [null, "dark", 7, [], true]) {
      expect(normalizePreferences({ [group]: bad })[group]).toEqual(DEFAULT_PREFERENCES[group]);
    }
  });

  it("drops keys it does not know about rather than passing them through", () => {
    // The store persists this verbatim and PATCHes it back. An unrecognised key
    // that survived normalisation would be written to the server forever.
    const result = normalizePreferences({
      appearance: { theme: "dark", secretFlag: true },
      somethingEntirelyNew: { enabled: true }
    });
    expect(result).not.toHaveProperty("somethingEntirelyNew");
    expect(result.appearance).not.toHaveProperty("secretFlag");
    expect(result.appearance.theme).toBe("dark");
  });

  it("is idempotent", () => {
    // Normalising a normalised document must be a no-op, or the store's
    // equality check would see a change on every hydration and flush forever.
    const once = normalizePreferences({ appearance: { theme: "dark", fontScale: 1.23 } });
    expect(normalizePreferences(once)).toEqual(once);
  });

  it("does not mutate the input or the defaults", () => {
    const input = { appearance: { theme: "dark" } };
    const snapshot = JSON.stringify(DEFAULT_PREFERENCES);
    normalizePreferences(input);
    expect(input).toEqual({ appearance: { theme: "dark" } });
    expect(JSON.stringify(DEFAULT_PREFERENCES)).toBe(snapshot);
  });

  it("returns a document that survives a JSON round trip unchanged", () => {
    // The persistence contract: every value must be JSON-serializable, because
    // the snapshot is written to AsyncStorage as a string.
    const result = normalizePreferences({ appearance: { theme: "dark" } });
    expect(JSON.parse(JSON.stringify(result))).toEqual(result);
  });
});

describe("normalizePreferences — the base argument", () => {
  it("falls back to the caller's base rather than the shipped defaults", () => {
    // This is how a partial server response merges over what the user already
    // has: unspecified keys keep the current value, not the factory one.
    const base = normalizePreferences({ appearance: { theme: "dark" }, language: { appLanguage: "fr" } });
    const result = normalizePreferences({ appearance: { fontScale: 1.2 } }, base);
    expect(result.appearance.theme).toBe("dark");
    expect(result.language.appLanguage).toBe("fr");
  });

  it("still refuses an out-of-range value that arrives with a valid base", () => {
    const base = normalizePreferences({ storage: { cacheLimitMb: 2048 } });
    expect(normalizePreferences({ storage: { cacheLimitMb: -5 } }, base).storage.cacheLimitMb).toBe(
      CACHE_LIMIT_MIN_MB
    );
  });
});

describe("booleans", () => {
  it.each([
    [true, true],
    [1, true],
    ["1", true],
    ["true", true],
    ["on", true],
    ["yes", true],
    [false, false],
    [0, false],
    ["0", false],
    ["false", false],
    ["off", false],
    ["no", false]
  ])("reads %p as %p", (input, expected) => {
    expect(normalizePreferences({ accessibility: { reduceMotion: input } }).accessibility.reduceMotion).toBe(
      expected
    );
  });

  it.each([["maybe"], [null], [{}], [[]], [2], [""], ["TRUE"]])(
    "falls back rather than guessing for %p",
    (input) => {
      // "TRUE" is included deliberately: the coercion is case-sensitive, and a
      // test that only tried lowercase would not notice if that changed.
      expect(normalizePreferences({ accessibility: { boldText: input } }).accessibility.boldText).toBe(
        DEFAULT_PREFERENCES.accessibility.boldText
      );
    }
  );
});

describe("enumerations", () => {
  it("accepts a known value in any case, with surrounding space", () => {
    expect(normalizePreferences({ appearance: { theme: "  DARK " } }).appearance.theme).toBe("dark");
  });

  it.each([["midnight"], [""], [null], [7], [["dark"]]])("refuses %p", (input) => {
    expect(normalizePreferences({ appearance: { theme: input } }).appearance.theme).toBe(
      DEFAULT_PREFERENCES.appearance.theme
    );
  });

  it("keeps each audience field independent", () => {
    // One bad field must not reset the others: a hostile payload that sets
    // `lastSeen: "public"` should not also widen `allowDirectMessages`.
    const result = normalizePreferences({
      privacy: { lastSeen: "public", allowDirectMessages: "nobody" }
    });
    expect(result.privacy.lastSeen).toBe(DEFAULT_PREFERENCES.privacy.lastSeen);
    expect(result.privacy.allowDirectMessages).toBe("nobody");
  });
});

describe("font scale", () => {
  it("clamps below the minimum and above the maximum", () => {
    expect(normalizePreferences({ appearance: { fontScale: 0.1 } }).appearance.fontScale).toBe(FONT_SCALE_MIN);
    expect(normalizePreferences({ appearance: { fontScale: 99 } }).appearance.fontScale).toBe(FONT_SCALE_MAX);
  });

  it.each([[NaN], [Infinity], [-Infinity], ["large"], [null], [{}]])(
    "falls back for %p rather than letting it reach the slider",
    (input) => {
      // The failure this prevents is specific: `NaN` survives both `Math.min`
      // and `Math.max`, so an unguarded value flows into the slider's position
      // and the label a screen reader announces.
      const result = normalizePreferences({ appearance: { fontScale: input } }).appearance.fontScale;
      expect(Number.isFinite(result)).toBe(true);
      expect(result).toBe(DEFAULT_PREFERENCES.appearance.fontScale);
    }
  );

  it("reads a numeric string, because that is what a form sends", () => {
    expect(normalizePreferences({ appearance: { fontScale: "1.2" } }).appearance.fontScale).toBe(1.2);
  });

  it("snaps to the step grid so a drag produces stable values", () => {
    expect(quantizeFontScale(1.011)).toBe(1);
    expect(quantizeFontScale(1.03)).toBe(1.05);
    expect(quantizeFontScale(0.4)).toBe(FONT_SCALE_MIN);
    expect(quantizeFontScale(9)).toBe(FONT_SCALE_MAX);
  });

  it("only ever produces values on the grid", () => {
    for (let raw = 0.5; raw <= 2; raw += 0.017) {
      const value = normalizePreferences({ appearance: { fontScale: raw } }).appearance.fontScale;
      const steps = (value - FONT_SCALE_MIN) / 0.05;
      expect(Math.abs(steps - Math.round(steps))).toBeLessThan(1e-6);
      expect(value).toBeGreaterThanOrEqual(FONT_SCALE_MIN);
      expect(value).toBeLessThanOrEqual(FONT_SCALE_MAX);
    }
  });
});

describe("cache limit", () => {
  it("clamps to the supported window and rounds to whole megabytes", () => {
    expect(normalizePreferences({ storage: { cacheLimitMb: 0 } }).storage.cacheLimitMb).toBe(CACHE_LIMIT_MIN_MB);
    expect(normalizePreferences({ storage: { cacheLimitMb: 1e9 } }).storage.cacheLimitMb).toBe(CACHE_LIMIT_MAX_MB);
    expect(normalizePreferences({ storage: { cacheLimitMb: 1024.7 } }).storage.cacheLimitMb).toBe(1025);
  });

  it("never yields a fractional or non-finite limit", () => {
    for (const input of [NaN, Infinity, "lots", null, -0.5, 512.499]) {
      const value = normalizePreferences({ storage: { cacheLimitMb: input } }).storage.cacheLimitMb;
      expect(Number.isInteger(value)).toBe(true);
    }
  });
});

describe("quiet hours", () => {
  it.each([
    ["9:05", "09:05"],
    ["09:05", "09:05"],
    ["23:59", "23:59"],
    ["00:00", "00:00"],
    ["  7:30  ", "07:30"]
  ])("reads %p as %p", (input, expected) => {
    expect(normalizePreferences({ notifications: { quietHoursStart: input } }).notifications.quietHoursStart).toBe(
      expected
    );
  });

  it.each([["24:00"], ["12:60"], ["12"], ["12:5"], ["noon"], [""], [null], [1200], ["12:00:00"]])(
    "refuses %p",
    (input) => {
      expect(normalizePreferences({ notifications: { quietHoursEnd: input } }).notifications.quietHoursEnd).toBe(
        DEFAULT_PREFERENCES.notifications.quietHoursEnd
      );
    }
  );
});

describe("language tags", () => {
  it.each([
    ["EN", "en"],
    ["fr", "fr"],
    ["pt-BR", "pt-br"],
    ["zh-Hans-CN", "zh-hans-cn"],
    ["  ar  ", "ar"]
  ])("reads %p as %p", (input, expected) => {
    expect(normalizePreferences({ language: { appLanguage: input } }).language.appLanguage).toBe(expected);
  });

  it.each([["e"], ["english!"], ["en_US"], [""], [null], [12], ["-en"]])("refuses %p", (input) => {
    expect(normalizePreferences({ language: { appLanguage: input } }).language.appLanguage).toBe(
      DEFAULT_PREFERENCES.language.appLanguage
    );
  });

  it("keeps the good tags out of a mixed list and drops the rest", () => {
    expect(
      normalizePreferences({ language: { contentLanguages: ["en", "not a tag", "FR", 9, null, "es"] } }).language
        .contentLanguages
    ).toEqual(["en", "fr", "es"]);
  });

  it("deduplicates case-insensitively, because the list drives a checklist", () => {
    expect(
      normalizePreferences({ language: { contentLanguages: ["en", "EN", "  en  "] } }).language.contentLanguages
    ).toEqual(["en"]);
  });

  it("never leaves the content list empty", () => {
    // An empty list would mean "translate nothing", which is not what a user
    // who unchecked the last box meant, and not a state any screen renders.
    for (const input of [[], ["!!!", 5], "en", null]) {
      expect(
        normalizePreferences({ language: { contentLanguages: input } }).language.contentLanguages.length
      ).toBeGreaterThan(0);
    }
  });
});

describe("notification categories", () => {
  it("returns every declared category even when the payload has none", () => {
    const categories = normalizePreferences({ notifications: {} }).notifications.categories;
    expect(Object.keys(categories).sort()).toEqual([...NOTIFICATION_CATEGORIES].sort());
  });

  it("gives every category all three channels as real booleans", () => {
    const categories = normalizePreferences({
      notifications: { categories: { likes: { push: "yes" }, comments: null, mentions: [] } }
    }).notifications.categories;
    for (const category of NOTIFICATION_CATEGORIES) {
      const channels = categories[category];
      expect(typeof channels.push).toBe("boolean");
      expect(typeof channels.email).toBe("boolean");
      expect(typeof channels.inApp).toBe("boolean");
    }
  });

  it("ignores a category the server invented", () => {
    const categories = normalizePreferences({
      notifications: { categories: { telepathy: { push: true } } }
    }).notifications.categories;
    expect(categories).not.toHaveProperty("telepathy");
  });

  it("keeps a partial channel object's unspecified channels at their defaults", () => {
    const categories = normalizePreferences({
      notifications: { categories: { security: { push: false } } }
    }).notifications.categories;
    expect(categories.security.push).toBe(false);
    expect(categories.security.email).toBe(DEFAULT_PREFERENCES.notifications.categories.security.email);
    expect(categories.security.inApp).toBe(DEFAULT_PREFERENCES.notifications.categories.security.inApp);
  });

  it("does not alias one category's channels to another's", () => {
    // The defaults are built by spreading a shared object; if that spread were
    // ever removed, changing one category would change all twelve.
    const result = normalizePreferences({ notifications: { categories: { likes: { push: false } } } });
    expect(result.notifications.categories.likes.push).toBe(false);
    expect(result.notifications.categories.comments.push).toBe(
      DEFAULT_PREFERENCES.notifications.categories.comments.push
    );
  });

  it("has a label for every category, so no row renders untitled", () => {
    for (const category of NOTIFICATION_CATEGORIES) {
      expect(NOTIFICATION_CATEGORY_LABELS[category]?.title).toBeTruthy();
      expect(NOTIFICATION_CATEGORY_LABELS[category]?.description).toBeTruthy();
    }
    expect(Object.keys(NOTIFICATION_CATEGORY_LABELS).sort()).toEqual([...NOTIFICATION_CATEGORIES].sort());
  });
});

describe("preferencesEqual", () => {
  it("is true for a value and its own normalisation", () => {
    expect(preferencesEqual(DEFAULT_PREFERENCES, normalizePreferences(DEFAULT_PREFERENCES))).toBe(true);
  });

  it("notices a change nested inside a category", () => {
    const changed = normalizePreferences({
      notifications: { categories: { likes: { inApp: !DEFAULT_PREFERENCES.notifications.categories.likes.inApp } } }
    });
    expect(preferencesEqual(DEFAULT_PREFERENCES, changed)).toBe(false);
  });
});

describe("defaults", () => {
  it("are already normalised", () => {
    // If they were not, the very first hydration would report a change and
    // flush a patch the user never made.
    expect(normalizePreferences(DEFAULT_PREFERENCES)).toEqual(DEFAULT_PREFERENCES);
  });

  it("ship security off and the protective options on", () => {
    // Stated as a test rather than left to review: these four are the ones a
    // careless refactor of the defaults block would silently invert.
    expect(DEFAULT_PREFERENCES.security.twoFactorEnabled).toBe(false);
    expect(DEFAULT_PREFERENCES.security.biometricUnlock).toBe(false);
    expect(DEFAULT_PREFERENCES.security.loginAlerts).toBe(true);
    expect(DEFAULT_PREFERENCES.security.requirePasswordForSensitiveChanges).toBe(true);
  });

  it("do not make a new account searchable by phone number", () => {
    expect(DEFAULT_PREFERENCES.privacy.searchableByPhone).toBe(false);
  });

  it("keep developer tooling off", () => {
    expect(DEFAULT_PREFERENCES.developer).toEqual({
      enabled: false,
      showPerfOverlay: false,
      verboseApiLogging: false
    });
  });
});

/* -------------------------------------------------------------------------- */

/**
 * Device-local classification.
 *
 * Four preferences describe *this handset* rather than the account, and sending
 * them to the server is not a harmless extra field — it is a false claim made
 * to every other signed-in device. `security.biometricUnlock` says "Face ID is
 * enrolled", which is true of one phone and unknowable for the others; the
 * storage caps budget one device's free space; `developer` is a debugging
 * affordance for the handset it was switched on from.
 *
 * The failure mode without this split is not a wrong toggle, it is a loop: two
 * devices hydrate, each overwrites the shared value with its own answer, and
 * each launch flips the other. So the tests below assert both halves — that the
 * keys leave on the way out (`stripDeviceLocal`) and that they survive on the
 * way back in (`withDeviceLocal`) — because either one alone still loses.
 */
describe("device-local classification", () => {
  it("names only keys that actually exist in their group", () => {
    // A typo here fails open: the key would be silently synced forever.
    (Object.keys(DEVICE_LOCAL_KEYS) as PreferenceGroup[]).forEach((group) => {
      const declared = DEVICE_LOCAL_KEYS[group] as readonly string[];
      declared.forEach((key) => {
        expect(Object.keys(DEFAULT_PREFERENCES[group])).toContain(key);
      });
    });
  });

  it("treats `developer` as wholly local and `security` as only partly local", () => {
    expect(isDeviceLocalGroup("developer")).toBe(true);
    expect(isDeviceLocalGroup("security")).toBe(false);
    expect(isDeviceLocalGroup("storage")).toBe(false);
    expect(isDeviceLocalGroup("appearance")).toBe(false);
  });

  it("keeps auto-download on the account, because it states an intent, not a capacity", () => {
    // Easy to lump in with the cache caps. It is different in kind: "never use
    // cellular data for video" is a decision about the user's data plan, and it
    // should follow them onto a new phone.
    const storage = (DEVICE_LOCAL_KEYS.storage ?? []) as readonly string[];
    expect(storage).not.toContain("autoDownloadPhotos");
    expect(storage).not.toContain("autoDownloadVideos");
    expect(storage).toEqual(expect.arrayContaining(["cacheLimitMb", "autoClearCache"]));
  });

  describe("stripDeviceLocal", () => {
    it("removes a local key but keeps its synced siblings", () => {
      const out = stripDeviceLocal({
        security: { twoFactorEnabled: true, biometricUnlock: true, loginAlerts: false } as any
      });
      expect(out.security).toEqual({ twoFactorEnabled: true, loginAlerts: false });
    });

    it("drops the group entirely when nothing synced is left", () => {
      // Not the same as sending `{security: {}}`. The server answers an empty
      // patch with 400, which the store reads as permanent and rolls back — so
      // an empty group here would revert a change that in fact succeeded.
      const out = stripDeviceLocal({ security: { biometricUnlock: true } as any });
      expect(out).toEqual({});
      expect(Object.keys(out)).toHaveLength(0);
    });

    it("removes the whole developer group, which is local in its entirety", () => {
      expect(stripDeviceLocal({ developer: DEFAULT_PREFERENCES.developer })).toEqual({});
    });

    it("removes both cache keys and keeps the download policy", () => {
      const out = stripDeviceLocal({
        storage: { cacheLimitMb: 900, autoClearCache: true, autoDownloadPhotos: "wifi" } as any
      });
      expect(out.storage).toEqual({ autoDownloadPhotos: "wifi" });
    });

    it("passes a group with no local keys through untouched", () => {
      const patch = { appearance: DEFAULT_PREFERENCES.appearance };
      expect(stripDeviceLocal(patch)).toEqual(patch);
    });

    it("does not mutate the patch it was given", () => {
      const patch = { developer: { ...DEFAULT_PREFERENCES.developer } };
      const before = JSON.stringify(patch);
      stripDeviceLocal(patch);
      expect(JSON.stringify(patch)).toBe(before);
    });

    it("ignores a group whose value is not an object", () => {
      expect(stripDeviceLocal({ appearance: null as any })).toEqual({});
      expect(stripDeviceLocal({} as any)).toEqual({});
    });
  });

  describe("withDeviceLocal", () => {
    const remote = normalizePreferences({
      ...DEFAULT_PREFERENCES,
      security: { ...DEFAULT_PREFERENCES.security, twoFactorEnabled: true, biometricUnlock: true },
      storage: { ...DEFAULT_PREFERENCES.storage, cacheLimitMb: 2048 },
      developer: { ...DEFAULT_PREFERENCES.developer, enabled: true }
    });
    const local = normalizePreferences({
      ...DEFAULT_PREFERENCES,
      security: { ...DEFAULT_PREFERENCES.security, twoFactorEnabled: false, biometricUnlock: false },
      storage: { ...DEFAULT_PREFERENCES.storage, cacheLimitMb: 256 },
      developer: { ...DEFAULT_PREFERENCES.developer, enabled: false }
    });

    it("lets the server win on everything it owns", () => {
      expect(withDeviceLocal(remote, local).security.twoFactorEnabled).toBe(true);
    });

    it("keeps this device's answer for a hardware fact the account cannot know", () => {
      // The other phone having Face ID enrolled says nothing about this one.
      expect(withDeviceLocal(remote, local).security.biometricUnlock).toBe(false);
    });

    it("keeps this device's cache budget and developer state", () => {
      const merged = withDeviceLocal(remote, local);
      expect(merged.storage.cacheLimitMb).toBe(256);
      expect(merged.developer.enabled).toBe(false);
    });

    it("differs from the remote document in exactly the declared keys", () => {
      const merged = withDeviceLocal(remote, local);
      const differing: string[] = [];
      (Object.keys(DEFAULT_PREFERENCES) as PreferenceGroup[]).forEach((group) => {
        Object.keys(DEFAULT_PREFERENCES[group]).forEach((key) => {
          const a = JSON.stringify((merged[group] as any)[key]);
          const b = JSON.stringify((remote[group] as any)[key]);
          if (a !== b) differing.push(`${group}.${key}`);
        });
      });
      expect(differing.sort()).toEqual([
        "developer.enabled",
        "security.biometricUnlock",
        "storage.cacheLimitMb"
      ]);
    });

    it("is idempotent when the two documents already agree", () => {
      expect(withDeviceLocal(remote, remote)).toEqual(remote);
    });

    it("does not mutate either input", () => {
      const a = JSON.stringify(remote);
      const b = JSON.stringify(local);
      withDeviceLocal(remote, local);
      expect(JSON.stringify(remote)).toBe(a);
      expect(JSON.stringify(local)).toBe(b);
    });

    it("returns a complete preference document", () => {
      const merged = withDeviceLocal(remote, local);
      expect(Object.keys(merged).sort()).toEqual(Object.keys(DEFAULT_PREFERENCES).sort());
      expect(normalizePreferences(merged)).toEqual(merged);
    });
  });

  it("round-trips: what is stripped on the way out is restored on the way back", () => {
    // The property that makes the pair safe. Anything `stripDeviceLocal` refuses
    // to send must be something `withDeviceLocal` refuses to accept back, or the
    // value is simply lost on the next reconcile.
    const localDoc = normalizePreferences({
      ...DEFAULT_PREFERENCES,
      security: { ...DEFAULT_PREFERENCES.security, biometricUnlock: true },
      storage: { ...DEFAULT_PREFERENCES.storage, cacheLimitMb: 512, autoClearCache: true },
      developer: { enabled: true, showPerfOverlay: true, verboseApiLogging: true }
    });
    const sent = stripDeviceLocal(localDoc);
    (Object.keys(DEVICE_LOCAL_KEYS) as PreferenceGroup[]).forEach((group) => {
      ((DEVICE_LOCAL_KEYS[group] ?? []) as readonly string[]).forEach((key) => {
        expect((sent as any)[group]?.[key]).toBeUndefined();
      });
    });
    // The server echoes defaults for everything it was never told about.
    const merged = withDeviceLocal(DEFAULT_PREFERENCES, localDoc);
    expect(merged.security.biometricUnlock).toBe(true);
    expect(merged.storage.cacheLimitMb).toBe(512);
    expect(merged.developer).toEqual(localDoc.developer);
  });
});
