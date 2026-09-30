/**
 * The UNDX header's connection claim.
 *
 * The rule being enforced: "Always available · PulseSoc Intelligence" may only
 * appear when a load has actually come back from the network. Everything else
 * here exists to pin the boundary around that one state, because it is the only
 * one whose wrongness is visible to a user as a false statement rather than as
 * a cosmetic glitch.
 */

import {
  ASSISTANT_CONNECTION_KEYS,
  AssistantConnectionInput,
  assistantConnectionDegraded,
  assistantConnectionState
} from "../assistantConnection";

/** A conversation that has loaded successfully from the network. */
const LIVE: AssistantConnectionInput = {
  error: false,
  loading: false,
  initialFetchComplete: true,
  usingCachedMessages: false
};

const state = (overrides: Partial<AssistantConnectionInput> = {}) =>
  assistantConnectionState({ ...LIVE, ...overrides });

describe("assistantConnectionState", () => {
  it("claims availability only after a load has come back", () => {
    expect(state()).toBe("live");
  });

  describe("the regression: availability claimed before anything was reached", () => {
    /**
     * This is the shape the old nested ternary got wrong. On a cold open,
     * `usingCachedMessages` is false because nothing has been cached *yet*, not
     * because the service answered — and asking that flag first turned "we have
     * not tried" into "always available".
     */
    it("says connecting, not available, while the first load is in flight", () => {
      expect(state({ loading: true, initialFetchComplete: false })).toBe("connecting");
    });

    it("says connecting even once loading has been set but the fetch has not resolved", () => {
      // The two flags are set by different effects and are briefly out of step.
      // Either one still unsettled has to mean "not yet known".
      expect(state({ loading: false, initialFetchComplete: false })).toBe("connecting");
      expect(state({ loading: true, initialFetchComplete: true })).toBe("connecting");
    });

    it("never reports live while either flag is unsettled, for any combination", () => {
      // Exhaustive over the two flags that gate the claim, so no future
      // reordering can reintroduce the bug for one particular pairing.
      for (const loading of [true, false]) {
        for (const initialFetchComplete of [true, false]) {
          const result = state({ loading, initialFetchComplete });
          if (!initialFetchComplete || loading) expect(result).not.toBe("live");
        }
      }
    });
  });

  it("reports cached history when a failed load fell back to stored messages", () => {
    expect(state({ usingCachedMessages: true })).toBe("cached");
  });

  it("reports reconnecting when a load failed with nothing to show", () => {
    expect(state({ error: true })).toBe("reconnecting");
  });

  it("lets an outright failure outrank every softer description", () => {
    // There is nothing on screen, so "connecting" or "cached history" would
    // both be describing content the person cannot see.
    expect(state({ error: true, loading: true, initialFetchComplete: false })).toBe("reconnecting");
    expect(state({ error: true, usingCachedMessages: true })).toBe("reconnecting");
  });
});

describe("the status dot agrees with the words beside it", () => {
  it("warns in every state except live", () => {
    expect(assistantConnectionDegraded("live")).toBe(false);
    for (const degraded of ["connecting", "cached", "reconnecting"] as const) {
      expect(assistantConnectionDegraded(degraded)).toBe(true);
    }
  });

  it("no longer reads live-green next to cached history", () => {
    // The contradiction this replaces: the dot keyed on `error` only, so a
    // conversation showing "Cached history" also showed a healthy indicator.
    expect(assistantConnectionDegraded(assistantConnectionState({ ...LIVE, usingCachedMessages: true }))).toBe(true);
  });
});

describe("key mapping", () => {
  it("maps every state to a distinct key", () => {
    const keys = Object.values(ASSISTANT_CONNECTION_KEYS);
    expect(new Set(keys).size).toBe(keys.length);
  });

  it("only the live state maps to the availability claim", () => {
    const claim = ASSISTANT_CONNECTION_KEYS.live;
    expect(claim).toBe("messaging:chat.assistantAlwaysAvailable");
    for (const [name, key] of Object.entries(ASSISTANT_CONNECTION_KEYS)) {
      if (name !== "live") expect(key).not.toBe(claim);
    }
  });

  it("resolves every key against the shipped English catalog", () => {
    // A state whose key does not exist would render the raw key string in the
    // header, which the i18n gate cannot catch on its own — nothing here is a
    // hardcoded string, it is a lookup that silently misses.
    const catalog = require("../../i18n/catalogs/en/extended.json");
    for (const key of Object.values(ASSISTANT_CONNECTION_KEYS)) {
      const [namespace, path] = key.split(":");
      const value = path.split(".").reduce<any>((node, part) => node?.[part], catalog[namespace]);
      expect(typeof value).toBe("string");
      expect(value.length).toBeGreaterThan(0);
    }
  });
});
