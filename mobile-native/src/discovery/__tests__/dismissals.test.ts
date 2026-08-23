/**
 * §3 — "persist dismissals so a dismissed module does not immediately return".
 *
 * The two failures worth guarding are opposites, and both are bad:
 *
 *  - the dismissal does not survive a cold start, so the button reads as broken;
 *  - the dismissal is permanent, so one irritated tap removes a module forever
 *    with no surface anywhere in the app to bring it back.
 *
 * A TTL is the answer to both, which makes the clock the thing under test. Every
 * assertion passes an explicit `now` rather than mocking timers, so these read as
 * arithmetic instead of as timer choreography.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  DISMISSAL_TTL_DAYS,
  __resetDismissals,
  dismissModule,
  dismissPerson,
  loadDismissedModules,
  loadDismissedPeople
} from "../dismissals";

const STORAGE_KEY = "pulse.discovery.dismissed.v1";
const PEOPLE_STORAGE_KEY = "pulse.discovery.dismissedPeople.v1";
const DAY_MS = 24 * 60 * 60 * 1000;
const TTL_MS = DISMISSAL_TTL_DAYS * DAY_MS;
const T0 = Date.parse("2026-06-01T12:00:00Z");

beforeEach(async () => {
  __resetDismissals();
  await AsyncStorage.clear();
  jest.clearAllMocks();
});

describe("reading", () => {
  it("treats an empty store as nothing dismissed", async () => {
    await expect(loadDismissedModules(T0)).resolves.toEqual(new Set());
  });

  it("treats corrupt JSON as nothing dismissed instead of throwing into Home's mount", async () => {
    await AsyncStorage.setItem(STORAGE_KEY, "{not json");

    await expect(loadDismissedModules(T0)).resolves.toEqual(new Set());
  });

  it("treats a stored array as nothing dismissed", async () => {
    // An older shape, or a hand-edited value. Either way it is not a record, and
    // guessing at it would be worse than starting clean.
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(["reels"]));

    await expect(loadDismissedModules(T0)).resolves.toEqual(new Set());
  });

  it("ignores entries whose timestamp is not a finite number", async () => {
    await AsyncStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ reels: "yesterday", people: null, groups: T0 })
    );

    await expect(loadDismissedModules(T0)).resolves.toEqual(new Set(["groups"]));
  });

  it("reads persisted dismissals back after a cold start", async () => {
    dismissModule("reels", T0);
    // A fresh process: in-memory state gone, storage intact.
    __resetDismissals();

    await expect(loadDismissedModules(T0 + DAY_MS)).resolves.toEqual(new Set(["reels"]));
  });
});

describe("the TTL", () => {
  it("honours a dismissal for the whole window", async () => {
    dismissModule("groups", T0);
    __resetDismissals();

    await expect(loadDismissedModules(T0 + TTL_MS - 1)).resolves.toEqual(new Set(["groups"]));
  });

  it("releases the module once the window has passed", async () => {
    dismissModule("groups", T0);
    __resetDismissals();

    await expect(loadDismissedModules(T0 + TTL_MS)).resolves.toEqual(new Set());
  });

  it("expires each kind on its own clock", async () => {
    dismissModule("reels", T0);
    dismissModule("people", T0 + 10 * DAY_MS);
    __resetDismissals();

    // Far enough that the first has lapsed and the second has not.
    await expect(loadDismissedModules(T0 + TTL_MS + DAY_MS)).resolves.toEqual(new Set(["people"]));
  });

  it("restarts the window when a released module is dismissed again", async () => {
    dismissModule("reels", T0);
    const later = T0 + TTL_MS + DAY_MS;

    const set = dismissModule("reels", later);

    expect(set).toEqual(new Set(["reels"]));
    expect(await loadDismissedModules(later + TTL_MS - 1)).toEqual(new Set(["reels"]));
  });
});

describe("writing", () => {
  it("returns the new set synchronously so the tap re-renders on the next frame", () => {
    // The placement engine is pure and synchronous. If this were async, the
    // dismissed module would render once more before disappearing.
    expect(dismissModule("statuses", T0)).toEqual(new Set(["statuses"]));
  });

  it("accumulates across kinds", () => {
    dismissModule("reels", T0);

    expect(dismissModule("groups", T0)).toEqual(new Set(["reels", "groups"]));
  });

  it("persists in the background without the caller awaiting it", async () => {
    dismissModule("people", T0);

    // Written for the *next* launch; the current one already has it in memory.
    await Promise.resolve();
    const raw = await AsyncStorage.getItem(STORAGE_KEY);
    expect(JSON.parse(raw || "{}")).toEqual({ people: T0 });
  });

  it("does not throw when the write fails", async () => {
    // `mockImplementationOnce` rather than `spyOn(...).mockRestore()`: AsyncStorage's
    // jest mock is already a mock function, so spying on it mutates the shared
    // module mock and restoring leaves `setItem` returning `undefined` for every
    // later test in the file.
    (AsyncStorage.setItem as jest.Mock).mockImplementationOnce(() => Promise.reject(new Error("disk full")));

    // A failed write costs the user one extra dismissal after a restart. It must
    // not cost them the tap, and must not surface as an unhandled rejection.
    expect(() => dismissModule("reels", T0)).not.toThrow();
    await Promise.resolve();
  });

  it("takes effect without a prior load", async () => {
    // Home dismisses before hydration finishes if the user is quick. The write
    // must not be clobbered by the read that lands afterwards.
    dismissModule("reels", T0);

    await expect(loadDismissedModules(T0)).resolves.toEqual(new Set(["reels"]));
  });
});

/**
 * Removing one suggestion, as opposed to the whole row.
 *
 * The requirement the user actually feels is "the person I removed does not come
 * back on the next scroll" — and the server has no record of a client-side
 * removal, so it will keep offering the same person. That makes this store the
 * only thing standing between a tap and the card reappearing.
 */
describe("removing one person", () => {
  it("keeps a removal across a cold start", async () => {
    dismissPerson("atlas", T0);
    __resetDismissals();

    await expect(loadDismissedPeople(T0 + DAY_MS)).resolves.toEqual(new Set(["atlas"]));
  });

  it("returns the new set synchronously so the card leaves on the next frame", () => {
    expect(dismissPerson("atlas", T0)).toEqual(new Set(["atlas"]));
  });

  it("accumulates across people", () => {
    dismissPerson("atlas", T0);

    expect(dismissPerson("vega", T0)).toEqual(new Set(["atlas", "vega"]));
  });

  it("ignores an empty key rather than storing one", () => {
    // A suggestion with no profile key is unaddressable anyway; storing `""`
    // would make every later unaddressable card look already-removed.
    expect(dismissPerson("", T0)).toEqual(new Set());
  });

  it("expires each person on their own clock", async () => {
    dismissPerson("atlas", T0);
    dismissPerson("vega", T0 + 10 * DAY_MS);
    __resetDismissals();

    await expect(loadDismissedPeople(T0 + TTL_MS + DAY_MS)).resolves.toEqual(new Set(["vega"]));
  });

  it("releases a person once the window has passed", async () => {
    dismissPerson("atlas", T0);
    __resetDismissals();

    await expect(loadDismissedPeople(T0 + TTL_MS)).resolves.toEqual(new Set());
  });

  it("does not confuse a removed person with a dismissed row", async () => {
    // Two stores, two questions. Removing every person in the row must not read
    // as "the viewer dismissed People", or the row never comes back at all.
    dismissPerson("atlas", T0);

    expect(await loadDismissedModules(T0)).toEqual(new Set());
    expect(await loadDismissedPeople(T0)).toEqual(new Set(["atlas"]));
  });

  it("persists to its own key in the background", async () => {
    dismissPerson("atlas", T0);

    await Promise.resolve();
    expect(JSON.parse((await AsyncStorage.getItem(PEOPLE_STORAGE_KEY)) || "{}")).toEqual({ atlas: T0 });
    expect(await AsyncStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("does not throw when the write fails", async () => {
    (AsyncStorage.setItem as jest.Mock).mockImplementationOnce(() => Promise.reject(new Error("disk full")));

    expect(() => dismissPerson("atlas", T0)).not.toThrow();
    await Promise.resolve();
  });

  it("treats a corrupt store as nothing removed", async () => {
    await AsyncStorage.setItem(PEOPLE_STORAGE_KEY, "{not json");

    await expect(loadDismissedPeople(T0)).resolves.toEqual(new Set());
  });
});
