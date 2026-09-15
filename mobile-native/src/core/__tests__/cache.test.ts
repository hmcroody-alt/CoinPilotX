/**
 * Performance regression tests for the JSON cache memory tier.
 *
 * These assert on OBSERVABLE COST — how many times the native AsyncStorage
 * bridge is crossed — not on wall-clock timings, which are not reproducible in
 * CI. A regression that reintroduces a bridge round trip per read will fail
 * here even though nothing user-visible changes, which is the point: the whole
 * value of the memory tier is a cost that is invisible until you count it.
 *
 * They also pin the correctness properties that make the tier safe to have at
 * all: callers never share a mutable object, and a key removed behind the
 * cache's back must not keep being served.
 */

jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  invalidateJsonCache,
  readJsonCache,
  readJsonCacheEntry,
  resetJsonCacheMemory,
  writeJsonCache
} from "../cache";

// The mock has to be reached through the import rather than captured in a
// module-scope const: `jest.mock` is hoisted above every declaration, so a const
// referenced from inside the factory is still uninitialised when it runs.
const mockStorage = AsyncStorage as unknown as {
  getItem: jest.Mock;
  setItem: jest.Mock;
  removeItem: jest.Mock;
};

type Profile = { id: number; name: string; tags?: string[] };
const identity = (value: Profile) => value;

beforeEach(() => {
  resetJsonCacheMemory();
  mockStorage.getItem.mockReset();
  mockStorage.setItem.mockReset().mockResolvedValue(undefined);
  mockStorage.removeItem.mockReset().mockResolvedValue(undefined);
});

describe("readJsonCache memory tier", () => {
  it("crosses the native bridge once for repeated reads of the same key", async () => {
    mockStorage.getItem.mockResolvedValue(JSON.stringify({ id: 1, name: "Ada" }));

    const first = await readJsonCache<Profile>("k", identity);
    const second = await readJsonCache<Profile>("k", identity);
    const third = await readJsonCache<Profile>("k", identity);

    expect(first).toEqual({ id: 1, name: "Ada" });
    expect(second).toEqual({ id: 1, name: "Ada" });
    expect(third).toEqual({ id: 1, name: "Ada" });
    // The regression this guards: 3 reads, 1 bridge hop. Hot screens read the
    // same key several times inside one interaction (a mount, a focus, a parent
    // and a child header).
    expect(mockStorage.getItem).toHaveBeenCalledTimes(1);
  });

  it("serves a written value without reading it back from disk", async () => {
    await writeJsonCache("k", { id: 2, name: "Grace" });
    const value = await readJsonCache<Profile>("k", identity);

    expect(value).toEqual({ id: 2, name: "Grace" });
    expect(mockStorage.getItem).not.toHaveBeenCalled();
  });

  it("hands every caller its own object, so one screen cannot mutate another's", async () => {
    await writeJsonCache("k", { id: 3, name: "Hedy", tags: ["a"] });

    const a = await readJsonCache<Profile>("k", identity);
    const b = await readJsonCache<Profile>("k", identity);

    expect(a).not.toBe(b);
    expect(a!.tags).not.toBe(b!.tags);
    a!.tags!.push("mutated");
    expect(b!.tags).toEqual(["a"]);
  });

  it("applies normalize on every read, including memory hits", async () => {
    await writeJsonCache("k", { id: 4, name: "raw" });
    const normalize = jest.fn((value: Profile) => ({ ...value, name: value.name.toUpperCase() }));

    const first = await readJsonCache<Profile>("k", normalize);
    const second = await readJsonCache<Profile>("k", normalize);

    expect(first?.name).toBe("RAW");
    expect(second?.name).toBe("RAW");
    expect(normalize).toHaveBeenCalledTimes(2);
  });

  it("honours maxAgeMs against the memory tier and falls back to disk", async () => {
    const now = jest.spyOn(Date, "now");
    now.mockReturnValue(1_000);
    await writeJsonCache("k", { id: 5, name: "fresh" });

    now.mockReturnValue(1_500);
    expect(await readJsonCache<Profile>("k", identity, { maxAgeMs: 1_000 })).toEqual({ id: 5, name: "fresh" });
    expect(mockStorage.getItem).not.toHaveBeenCalled();

    now.mockReturnValue(5_000);
    mockStorage.getItem.mockResolvedValue(JSON.stringify({ id: 5, name: "from-disk" }));
    expect(await readJsonCache<Profile>("k", identity, { maxAgeMs: 1_000 })).toEqual({ id: 5, name: "from-disk" });
    expect(mockStorage.getItem).toHaveBeenCalledTimes(1);

    now.mockRestore();
  });

  it("bounds resident memory by evicting least-recently-used keys", async () => {
    // 64 is the cap. Write 65 keys, keeping key 0 hot, and the cold key 1 is
    // the one that must be evicted — a long session that visits many profiles
    // must not grow the resident set without limit.
    for (let index = 0; index < 64; index += 1) {
      await writeJsonCache(`key-${index}`, { id: index, name: `n${index}` });
    }
    await readJsonCache<Profile>("key-0", identity);
    await writeJsonCache("key-64", { id: 64, name: "n64" });

    mockStorage.getItem.mockResolvedValue(null);
    await readJsonCache<Profile>("key-0", identity);
    expect(mockStorage.getItem).not.toHaveBeenCalled();

    await readJsonCache<Profile>("key-1", identity);
    expect(mockStorage.getItem).toHaveBeenCalledTimes(1);
  });
});

describe("cache invalidation", () => {
  it("stops serving a key that was invalidated", async () => {
    await writeJsonCache("draft", { id: 6, name: "in progress" });
    invalidateJsonCache("draft");

    mockStorage.getItem.mockResolvedValue(null);
    expect(await readJsonCache<Profile>("draft", identity)).toBeNull();
    expect(mockStorage.getItem).toHaveBeenCalledTimes(1);
  });

  it("clears the whole tier on reset", async () => {
    await writeJsonCache("a", { id: 7, name: "a" });
    await writeJsonCache("b", { id: 8, name: "b" });
    resetJsonCacheMemory();

    mockStorage.getItem.mockResolvedValue(null);
    expect(await readJsonCache<Profile>("a", identity)).toBeNull();
    expect(await readJsonCache<Profile>("b", identity)).toBeNull();
    expect(mockStorage.getItem).toHaveBeenCalledTimes(2);
  });
});

describe("record age", () => {
  it("reports the age of a value it wrote", async () => {
    const now = jest.spyOn(Date, "now");
    now.mockReturnValue(10_000);
    await writeJsonCache("k", { id: 1, name: "Ada" });

    now.mockReturnValue(10_000 + 12 * 60 * 1000);
    const entry = await readJsonCacheEntry<Profile>("k", identity);

    expect(entry?.value).toEqual({ id: 1, name: "Ada" });
    expect(entry?.storedAt).toBe(10_000);
    // This is the number behind "Last updated 12 min ago". Before the envelope
    // there was nothing to compute it from.
    expect(entry?.ageMs).toBe(12 * 60 * 1000);
    now.mockRestore();
  });

  it("persists the timestamp to disk, not just to memory", async () => {
    const now = jest.spyOn(Date, "now");
    now.mockReturnValue(4_242);
    await writeJsonCache("k", { id: 2, name: "Grace" });

    // Written age has to survive a process restart, so it must be in the
    // serialized form rather than only in the resident entry.
    const written = JSON.parse(mockStorage.setItem.mock.calls[0][1] as string);
    expect(written).toEqual({ v: 1, storedAt: 4_242, value: { id: 2, name: "Grace" } });

    resetJsonCacheMemory();
    mockStorage.getItem.mockResolvedValue(JSON.stringify(written));
    now.mockReturnValue(4_242 + 5_000);
    expect((await readJsonCacheEntry<Profile>("k", identity))?.ageMs).toBe(5_000);
    now.mockRestore();
  });

  it("reports an entry from an earlier build as unknown age, never as fresh", async () => {
    // The trap: defaulting a legacy entry's timestamp to "now" would make a
    // value cached six months ago announce itself as seconds old. Unknown has
    // to stay unknown so callers are forced to handle it.
    mockStorage.getItem.mockResolvedValue(JSON.stringify({ id: 3, name: "legacy" }));
    const entry = await readJsonCacheEntry<Profile>("k", identity);

    expect(entry?.value).toEqual({ id: 3, name: "legacy" });
    expect(entry?.storedAt).toBeNull();
    expect(entry?.ageMs).toBeNull();
  });

  it("keeps readJsonCache returning the bare value for existing callers", async () => {
    await writeJsonCache("k", { id: 4, name: "unchanged" });
    expect(await readJsonCache<Profile>("k", identity)).toEqual({ id: 4, name: "unchanged" });

    resetJsonCacheMemory();
    mockStorage.getItem.mockResolvedValue(JSON.stringify({ id: 5, name: "legacy" }));
    expect(await readJsonCache<Profile>("k", identity)).toEqual({ id: 5, name: "legacy" });
  });

  it("does not mistake a cached record that happens to carry a v field", async () => {
    // A loose discriminator would unwrap this and hand the caller `undefined`.
    const record = { v: 1, id: 6, name: "not an envelope" };
    mockStorage.getItem.mockResolvedValue(JSON.stringify(record));

    const entry = await readJsonCacheEntry<typeof record>("k", (value) => value);
    expect(entry?.value).toEqual(record);
    expect(entry?.storedAt).toBeNull();
  });
});

describe("maxDiskAgeMs", () => {
  it("drops a disk entry older than the opt-in ceiling", async () => {
    const now = jest.spyOn(Date, "now");
    now.mockReturnValue(1_000);
    await writeJsonCache("k", { id: 7, name: "old" });
    resetJsonCacheMemory();

    mockStorage.getItem.mockResolvedValue(JSON.stringify({ v: 1, storedAt: 1_000, value: { id: 7, name: "old" } }));
    now.mockReturnValue(1_000 + 60_000);

    expect(await readJsonCache<Profile>("k", identity, { maxDiskAgeMs: 30_000 })).toBeNull();
    expect(mockStorage.removeItem).toHaveBeenCalledWith("k");
    now.mockRestore();
  });

  it("keeps a disk entry inside the ceiling", async () => {
    const now = jest.spyOn(Date, "now");
    mockStorage.getItem.mockResolvedValue(JSON.stringify({ v: 1, storedAt: 1_000, value: { id: 8, name: "fresh" } }));
    now.mockReturnValue(1_000 + 10_000);

    expect(await readJsonCache<Profile>("k", identity, { maxDiskAgeMs: 30_000 })).toEqual({ id: 8, name: "fresh" });
    now.mockRestore();
  });

  it("never expires an entry whose age is unknown", async () => {
    // Unknown is not old. Treating it as expired would blank every screen once
    // on upgrade — the exact cold-start emptiness this cache prevents.
    mockStorage.getItem.mockResolvedValue(JSON.stringify({ id: 9, name: "legacy" }));

    expect(await readJsonCache<Profile>("k", identity, { maxDiskAgeMs: 1 })).toEqual({ id: 9, name: "legacy" });
    expect(mockStorage.removeItem).not.toHaveBeenCalled();
  });

  it("is off unless asked for", async () => {
    mockStorage.getItem.mockResolvedValue(JSON.stringify({ v: 1, storedAt: 0, value: { id: 10, name: "ancient" } }));
    // The default response to a stale cache is to show it and revalidate, not
    // to withhold it.
    expect(await readJsonCache<Profile>("k", identity)).toEqual({ id: 10, name: "ancient" });
  });
});

describe("corruption handling", () => {
  it("drops a corrupt disk entry instead of serving it forever", async () => {
    mockStorage.getItem.mockResolvedValue("{not json");
    expect(await readJsonCache<Profile>("k", identity)).toBeNull();
    expect(mockStorage.removeItem).toHaveBeenCalledWith("k");

    // And having failed, it must not have poisoned the memory tier.
    mockStorage.getItem.mockResolvedValue(JSON.stringify({ id: 9, name: "recovered" }));
    expect(await readJsonCache<Profile>("k", identity)).toEqual({ id: 9, name: "recovered" });
  });
});
