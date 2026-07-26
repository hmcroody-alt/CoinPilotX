/**
 * The preference store's sync pipeline.
 *
 * This is the part of the settings platform that can lose a user's change, and
 * it is the only part where that failure is silent: a toggle that flips, looks
 * saved, and is gone on next launch produces no error anywhere. So the tests
 * are written around the four moments where a value can be dropped rather than
 * around the store's public methods.
 *
 * **Coalescing.** Dragging a slider fires dozens of updates. They must become
 * one request carrying the last value, not the first — a debounce that sent the
 * leading edge would persist the value the user dragged *away* from.
 *
 * **Mid-flight edits.** A group changed while a request is in the air must stay
 * dirty and must not be overwritten by that request's response. This is the
 * defect most likely to survive review, because it needs two events in a
 * specific order to reproduce.
 *
 * **Rollback.** A permanent failure must return the value to the last state the
 * *server* confirmed, not to the previous local state — those differ whenever
 * two changes failed in a row.
 *
 * **Transient failure.** Must keep the optimistic value and keep retrying, so a
 * change made in a lift is not lost when the doors open.
 *
 * The API and the cache are the only mocks. `normalizePreferences` and the real
 * reducer logic run, because they are what is under test.
 */

import React from "react";
import { Text } from "react-native";
import { act, render, waitFor } from "@testing-library/react-native";

import { DEFAULT_PREFERENCES, normalizePreferences, Preferences } from "../schema";

/* ------------------------------- Test doubles ----------------------------- */

const mockFetchRemotePreferences = jest.fn();
const mockPushPreferencePatch = jest.fn();

jest.mock("../api", () => ({
  __esModule: true,
  fetchRemotePreferences: (...args: unknown[]) => mockFetchRemotePreferences(...args),
  pushPreferencePatch: (...args: unknown[]) => mockPushPreferencePatch(...args),
  PreferenceSyncError: class extends Error {
    permanent: boolean;
    status: number;
    constructor(message: string, status: number, permanent: boolean) {
      super(message);
      this.name = "PreferenceSyncError";
      this.status = status;
      this.permanent = permanent;
    }
  }
}));

/** An in-memory stand-in for AsyncStorage, so persistence is observable. */
const cacheStore = new Map<string, unknown>();
const mockReadJsonCache = jest.fn(async (key: string, normalize?: (value: any) => any) => {
  if (!cacheStore.has(key)) return null;
  const value = cacheStore.get(key);
  return normalize ? normalize(value) : value;
});
const mockWriteJsonCache = jest.fn(async (key: string, value: unknown) => {
  cacheStore.set(key, JSON.parse(JSON.stringify(value)));
});

jest.mock("../../core/cache", () => ({
  __esModule: true,
  readJsonCache: (...args: any[]) => mockReadJsonCache(...(args as [string, any])),
  writeJsonCache: (...args: any[]) => mockWriteJsonCache(...(args as [string, unknown]))
}));

// Imported after the mocks so the store binds to the doubles.
// eslint-disable-next-line import/first
import { PreferencesProvider, usePreferences, __testing } from "../store";

/**
 * The store decides transient-vs-permanent with `instanceof PreferenceSyncError`,
 * so a rejection must be an instance of the *mocked* class the store imported.
 * A look-alike defined in this file is a different constructor, and every
 * permanent failure would be misread as a transient one — the suite would pass
 * while testing nothing about rollback.
 */
const { PreferenceSyncError: SyncError } = jest.requireMock("../api") as {
  PreferenceSyncError: new (message: string, status: number, permanent: boolean) => Error;
};

const { CACHE_KEY, COALESCE_MS } = __testing;

/* --------------------------------- Harness -------------------------------- */

type Handle = ReturnType<typeof usePreferences>;

let handle: Handle;

function Probe() {
  handle = usePreferences();
  return <Text testID="hydrated">{String(handle.hydrated)}</Text>;
}

function envelope(preferences: Partial<Preferences> = {}, revision = 1) {
  return {
    preferences: normalizePreferences({ ...DEFAULT_PREFERENCES, ...preferences }),
    revision,
    updatedAt: null
  };
}

async function mount(enabled = true) {
  const utils = render(
    <PreferencesProvider enabled={enabled}>
      <Probe />
    </PreferencesProvider>
  );
  await waitFor(() => expect(handle.hydrated).toBe(true));
  return utils;
}

/** Let the coalescing timer fire and the resulting promise chain settle. */
async function settle(ms = COALESCE_MS) {
  await act(async () => {
    jest.advanceTimersByTime(ms);
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  jest.useFakeTimers();
  cacheStore.clear();
  mockFetchRemotePreferences.mockReset();
  mockPushPreferencePatch.mockReset();
  mockReadJsonCache.mockClear();
  mockWriteJsonCache.mockClear();
  mockFetchRemotePreferences.mockResolvedValue(envelope());
  mockPushPreferencePatch.mockImplementation(async (patch: Partial<Preferences>) =>
    envelope(patch as Partial<Preferences>, 2)
  );
});

afterEach(() => {
  jest.clearAllTimers();
  jest.useRealTimers();
});

/* ---------------------------------- Tests --------------------------------- */

describe("hydration", () => {
  it("renders before the network answers", async () => {
    // The store must never block first paint on a request. If it did, opening
    // Settings on a bad connection would show a spinner instead of the values
    // already on the device.
    let resolveRemote: (value: unknown) => void = () => undefined;
    mockFetchRemotePreferences.mockImplementation(() => new Promise((resolve) => (resolveRemote = resolve)));
    await mount();
    expect(handle.hydrated).toBe(true);
    await act(async () => {
      resolveRemote(envelope());
      await Promise.resolve();
    });
  });

  it("shows the persisted snapshot rather than the defaults", async () => {
    cacheStore.set(CACHE_KEY, {
      preferences: normalizePreferences({ appearance: { theme: "dark" } }),
      revision: 5,
      pendingGroups: []
    });
    mockFetchRemotePreferences.mockResolvedValue(null);
    await mount();
    expect(handle.preferences.appearance.theme).toBe("dark");
  });

  it("repairs a corrupt snapshot instead of rendering it", async () => {
    // A snapshot written by an older version, or a partially-flushed write.
    cacheStore.set(CACHE_KEY, { preferences: { appearance: "dark" }, revision: "x", pendingGroups: "all" });
    mockFetchRemotePreferences.mockResolvedValue(null);
    await mount();
    expect(handle.preferences.appearance).toEqual(DEFAULT_PREFERENCES.appearance);
    expect(handle.pendingGroups).toEqual([]);
  });

  it("discards a pending group name that is not a real group", async () => {
    // Otherwise it would be added to `dirty` and flushed forever as `undefined`.
    cacheStore.set(CACHE_KEY, {
      preferences: DEFAULT_PREFERENCES,
      revision: 1,
      pendingGroups: ["appearance", "telepathy"]
    });
    mockFetchRemotePreferences.mockResolvedValue(null);
    await mount();
    expect(handle.pendingGroups).toEqual(["appearance"]);
  });

  it("lets the server win on hydration, except where the user has unflushed edits", async () => {
    cacheStore.set(CACHE_KEY, {
      preferences: normalizePreferences({
        appearance: { theme: "dark" },
        language: { appLanguage: "fr" }
      }),
      revision: 1,
      // Only `language` was left unsent; `appearance` was already confirmed.
      pendingGroups: ["language"]
    });
    mockFetchRemotePreferences.mockResolvedValue(
      envelope({ appearance: normalizePreferences({ appearance: { theme: "light" } }).appearance }, 9)
    );
    await mount();
    await waitFor(() => expect(handle.preferences.appearance.theme).toBe("light"));
    expect(handle.preferences.language.appLanguage).toBe("fr");
  });

  it("keeps local values and retries the queue when the server is unreachable", async () => {
    cacheStore.set(CACHE_KEY, {
      preferences: normalizePreferences({ appearance: { theme: "dark" } }),
      revision: 3,
      pendingGroups: ["appearance"]
    });
    mockFetchRemotePreferences.mockResolvedValue(null);
    await mount();
    expect(handle.preferences.appearance.theme).toBe("dark");
    await settle();
    expect(mockPushPreferencePatch).toHaveBeenCalled();
  });
});

describe("optimistic update", () => {
  it("applies the change before any request is made", async () => {
    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    expect(handle.preferences.appearance.theme).toBe("dark");
    expect(mockPushPreferencePatch).not.toHaveBeenCalled();
    expect(handle.pendingGroups).toContain("appearance");
  });

  it("merges into the group rather than replacing it", async () => {
    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    expect(handle.preferences.appearance.fontScale).toBe(DEFAULT_PREFERENCES.appearance.fontScale);
  });

  it("normalises the patch, so a screen cannot write an impossible value", async () => {
    await mount();
    await act(async () => {
      await handle.update("appearance", { fontScale: 99 } as never);
    });
    expect(handle.preferences.appearance.fontScale).toBe(1.4);
  });

  it("does nothing at all when the value did not change", async () => {
    // A switch re-rendering with the same value must not mark the group dirty,
    // or Settings would show a permanent "saving" state.
    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: DEFAULT_PREFERENCES.appearance.theme });
    });
    expect(handle.pendingGroups).toEqual([]);
    await settle();
    expect(mockPushPreferencePatch).not.toHaveBeenCalled();
  });

  it("writes the snapshot before the request, so an app kill does not lose it", async () => {
    await mount();
    mockWriteJsonCache.mockClear();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    expect(mockWriteJsonCache).toHaveBeenCalled();
    const snapshot = cacheStore.get(CACHE_KEY) as any;
    expect(snapshot.preferences.appearance.theme).toBe("dark");
    expect(snapshot.pendingGroups).toContain("appearance");
  });
});

describe("coalescing", () => {
  it("sends one request for a burst, carrying the final value", async () => {
    await mount();
    await act(async () => {
      await handle.update("appearance", { fontScale: 1.1 });
      await handle.update("appearance", { fontScale: 1.2 });
      await handle.update("appearance", { fontScale: 1.3 });
    });
    expect(mockPushPreferencePatch).not.toHaveBeenCalled();
    await settle();
    expect(mockPushPreferencePatch).toHaveBeenCalledTimes(1);
    expect(mockPushPreferencePatch.mock.calls[0][0].appearance.fontScale).toBe(1.3);
  });

  it("sends every touched group in one patch", async () => {
    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
      await handle.update("privacy", { onlineStatus: false });
    });
    await settle();
    expect(mockPushPreferencePatch).toHaveBeenCalledTimes(1);
    const [patch] = mockPushPreferencePatch.mock.calls[0];
    expect(Object.keys(patch).sort()).toEqual(["appearance", "privacy"]);
  });

  it("sends only the touched groups, not the whole document", async () => {
    // A full-document PATCH would let one device clobber a section another
    // device just changed.
    await mount();
    await act(async () => {
      await handle.update("privacy", { onlineStatus: false });
    });
    await settle();
    expect(Object.keys(mockPushPreferencePatch.mock.calls[0][0])).toEqual(["privacy"]);
  });

  it("carries the revision the server last gave it", async () => {
    mockFetchRemotePreferences.mockResolvedValue(envelope({}, 7));
    await mount();
    await act(async () => {
      await handle.update("privacy", { onlineStatus: false });
    });
    await settle();
    expect(mockPushPreferencePatch.mock.calls[0][1]).toBe(7);
  });

  it("advances to the revision the response returns", async () => {
    mockFetchRemotePreferences.mockResolvedValue(envelope({}, 7));
    mockPushPreferencePatch.mockResolvedValue(envelope({}, 8));
    await mount();
    await act(async () => {
      await handle.update("privacy", { onlineStatus: false });
    });
    await settle();
    await act(async () => {
      await handle.update("privacy", { readReceipts: false });
    });
    await settle();
    expect(mockPushPreferencePatch.mock.calls[1][1]).toBe(8);
  });

  it("reports saved once the queue is empty", async () => {
    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    expect(handle.status).toBe("saving");
    await settle();
    await waitFor(() => expect(handle.status).toBe("saved"));
    expect(handle.pendingGroups).toEqual([]);
  });
});

describe("edits made while a request is in flight", () => {
  it("keeps the mid-flight group dirty and flushes it afterwards", async () => {
    let release: (value: unknown) => void = () => undefined;
    mockPushPreferencePatch.mockImplementationOnce(
      () => new Promise((resolve) => (release = resolve))
    );

    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    await act(async () => {
      jest.advanceTimersByTime(COALESCE_MS);
      await Promise.resolve();
    });
    expect(mockPushPreferencePatch).toHaveBeenCalledTimes(1);

    // Second change lands while the first request is still open.
    await act(async () => {
      await handle.update("privacy", { onlineStatus: false });
    });
    await act(async () => {
      release(envelope({}, 2));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(handle.pendingGroups).toContain("privacy");
    await settle();
    expect(mockPushPreferencePatch).toHaveBeenCalledTimes(2);
    expect(Object.keys(mockPushPreferencePatch.mock.calls[1][0])).toEqual(["privacy"]);
  });

  it("does not let the response overwrite the mid-flight value", async () => {
    // The response is authoritative for what it carried, and only for that.
    let release: (value: unknown) => void = () => undefined;
    mockPushPreferencePatch.mockImplementationOnce(() => new Promise((resolve) => (release = resolve)));

    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    await act(async () => {
      jest.advanceTimersByTime(COALESCE_MS);
      await Promise.resolve();
    });
    await act(async () => {
      await handle.update("privacy", { onlineStatus: false });
    });
    await act(async () => {
      // A stale server document that still says onlineStatus is on.
      release(envelope({}, 2));
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(handle.preferences.privacy.onlineStatus).toBe(false);
  });
});

describe("permanent failure", () => {
  it("rolls the value back and surfaces the server's reason", async () => {
    mockPushPreferencePatch.mockRejectedValue(new SyncError("That value is not allowed.", 422, true));
    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    await settle();
    await waitFor(() => expect(handle.status).toBe("error"));
    expect(handle.preferences.appearance.theme).toBe(DEFAULT_PREFERENCES.appearance.theme);
    expect(handle.error).toBe("That value is not allowed.");
  });

  it("stops retrying, because the same payload would fail forever", async () => {
    mockPushPreferencePatch.mockRejectedValue(new SyncError("Rejected.", 400, true));
    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    await settle();
    const attempts = mockPushPreferencePatch.mock.calls.length;
    await settle(60_000);
    expect(mockPushPreferencePatch.mock.calls.length).toBe(attempts);
    expect(handle.pendingGroups).toEqual([]);
  });

  it("rolls back only the group that failed", async () => {
    await mount();
    // Confirm `privacy` first so it has a server-confirmed value to keep.
    await act(async () => {
      await handle.update("privacy", { onlineStatus: false });
    });
    await settle();
    mockPushPreferencePatch.mockRejectedValue(new SyncError("Rejected.", 400, true));
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    await settle();
    expect(handle.preferences.appearance.theme).toBe(DEFAULT_PREFERENCES.appearance.theme);
    expect(handle.preferences.privacy.onlineStatus).toBe(false);
  });

  it("persists the rolled-back value, so a relaunch does not resurrect it", async () => {
    mockPushPreferencePatch.mockRejectedValue(new SyncError("Rejected.", 400, true));
    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    await settle();
    const snapshot = cacheStore.get(CACHE_KEY) as any;
    expect(snapshot.preferences.appearance.theme).toBe(DEFAULT_PREFERENCES.appearance.theme);
    expect(snapshot.pendingGroups).toEqual([]);
  });
});

describe("transient failure", () => {
  it("keeps the optimistic value and reports offline", async () => {
    mockPushPreferencePatch.mockRejectedValue(new SyncError("You appear to be offline.", 0, false));
    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    await settle();
    await waitFor(() => expect(handle.status).toBe("offline"));
    // The value the user chose stays on screen — this is the whole point of a
    // local-first store.
    expect(handle.preferences.appearance.theme).toBe("dark");
    expect(handle.pendingGroups).toContain("appearance");
  });

  it("retries with a growing delay rather than hammering", async () => {
    mockPushPreferencePatch.mockRejectedValue(new SyncError("Offline.", 0, false));
    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    await settle();
    const first = mockPushPreferencePatch.mock.calls.length;
    await settle(1000);
    const second = mockPushPreferencePatch.mock.calls.length;
    expect(second).toBeGreaterThan(first);
    // The next attempt must not come at the same 1s interval.
    await settle(1000);
    expect(mockPushPreferencePatch.mock.calls.length).toBe(second);
    await settle(2000);
    expect(mockPushPreferencePatch.mock.calls.length).toBeGreaterThan(second);
  });

  it("eventually succeeds and clears the queue", async () => {
    mockPushPreferencePatch
      .mockRejectedValueOnce(new SyncError("Offline.", 0, false))
      .mockResolvedValue(envelope({ appearance: normalizePreferences({ appearance: { theme: "dark" } }).appearance }, 3));
    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    await settle();
    await settle(1000);
    await waitFor(() => expect(handle.pendingGroups).toEqual([]));
    expect(handle.preferences.appearance.theme).toBe("dark");
  });
});

describe("refresh", () => {
  it("reports offline without discarding anything when the server is unreachable", async () => {
    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    await settle();
    mockFetchRemotePreferences.mockResolvedValue(null);
    await act(async () => {
      await handle.refresh();
    });
    expect(handle.status).toBe("offline");
    expect(handle.preferences.appearance.theme).toBe("dark");
  });

  it("does not discard a pending local edit", async () => {
    mockPushPreferencePatch.mockRejectedValue(new SyncError("Offline.", 0, false));
    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    await settle();
    mockFetchRemotePreferences.mockResolvedValue(envelope({}, 12));
    await act(async () => {
      await handle.refresh();
    });
    expect(handle.preferences.appearance.theme).toBe("dark");
  });
});

describe("resetAll", () => {
  it("returns every value to its default and queues every group", async () => {
    await mount();
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
      await handle.update("privacy", { onlineStatus: false });
    });
    await settle();
    await act(async () => {
      await handle.resetAll();
    });
    expect(handle.preferences).toEqual(DEFAULT_PREFERENCES);
    expect(handle.pendingGroups.sort()).toEqual(Object.keys(DEFAULT_PREFERENCES).sort());
  });
});

describe("disabled provider", () => {
  it("persists locally and never calls the network", async () => {
    // Signed-out state: Settings still works, nothing is sent.
    await mount(false);
    await act(async () => {
      await handle.update("appearance", { theme: "dark" });
    });
    await settle(60_000);
    expect(mockFetchRemotePreferences).not.toHaveBeenCalled();
    expect(mockPushPreferencePatch).not.toHaveBeenCalled();
    expect(handle.preferences.appearance.theme).toBe("dark");
    expect((cacheStore.get(CACHE_KEY) as any).preferences.appearance.theme).toBe("dark");
  });
});

describe("clearError", () => {
  it("dismisses the message without changing any value", async () => {
    mockPushPreferencePatch.mockRejectedValue(new SyncError("Rejected.", 400, true));
    await mount();
    await act(async () => {
      await handle.update("privacy", { onlineStatus: false });
    });
    await settle();
    const before = handle.preferences;
    act(() => handle.clearError());
    expect(handle.error).toBeNull();
    expect(handle.status).toBe("idle");
    expect(handle.preferences).toEqual(before);
  });
});

describe("usePreferences outside a provider", () => {
  it("throws a message naming the fix", async () => {
    const spy = jest.spyOn(console, "error").mockImplementation(() => undefined);
    expect(() => render(<Probe />)).toThrow(/PreferencesProvider/);
    spy.mockRestore();
  });
});
