/**
 * Stage 5/30/35 regression tests for the shared media cache.
 *
 * The filesystem is faked in memory rather than mocked call-by-call, because the
 * properties worth asserting here — a truncated file reads as a miss, one
 * account's directory is unreachable from another's scope — are about the state
 * the module leaves on disk, not about which function it called.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";

const mockFiles = new Map<string, number>();
const mockDisk = { free: Number.MAX_SAFE_INTEGER };

jest.mock("expo-file-system/legacy", () => ({
  cacheDirectory: "file:///cache/",
  makeDirectoryAsync: jest.fn(async () => undefined),
  getInfoAsync: jest.fn(async (uri: string) =>
    mockFiles.has(uri) ? { exists: true, uri, size: mockFiles.get(uri) } : { exists: false, uri }
  ),
  deleteAsync: jest.fn(async (uri: string) => {
    for (const path of [...mockFiles.keys()]) {
      if (path === uri || path.startsWith(uri.endsWith("/") ? uri : `${uri}/`)) mockFiles.delete(path);
    }
  }),
  moveAsync: jest.fn(async ({ from, to }: { from: string; to: string }) => {
    const size = mockFiles.get(from);
    mockFiles.delete(from);
    mockFiles.set(to, size ?? 0);
  }),
  getFreeDiskStorageAsync: jest.fn(async () => mockDisk.free)
}));

import {
  MediaCacheFullError,
  __resetMediaCacheMemory,
  cacheFileUriFor,
  clearAllMediaCaches,
  commitCachedMedia,
  configureMediaCache,
  ensureRoomFor,
  getMediaCacheScope,
  lookupCachedMedia,
  mediaCacheKey,
  mediaCacheStats,
  setMediaCacheScope
} from "../mediaCache";

/** Advance the wall clock far enough that two writes get distinct timestamps. */
function tick(ms = 3): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function writeCached(key: string, bytes: number) {
  const uri = cacheFileUriFor(key, ".jpg");
  mockFiles.set(uri, bytes);
  return commitCachedMedia({ key, fileUri: uri, mimeType: "image/jpeg" });
}

beforeEach(async () => {
  mockFiles.clear();
  mockDisk.free = Number.MAX_SAFE_INTEGER;
  await AsyncStorage.clear();
  __resetMediaCacheMemory();
});

describe("cache keys", () => {
  it("prefers the canonical media id over the URL", () => {
    expect(mediaCacheKey({ mediaId: 42, url: "https://cdn.pulsesoc.com/a.jpg" })).toBe("id:42");
  });

  it("ignores rotating signed-URL query strings so re-signing is a hit, not a miss", () => {
    const first = mediaCacheKey({ url: "https://cdn.pulsesoc.com/m/9.jpg?sig=aaa&exp=1" });
    const second = mediaCacheKey({ url: "https://cdn.pulsesoc.com/m/9.jpg?sig=bbb&exp=2" });
    expect(first).toBe(second);
    expect(first).not.toBe("");
  });

  it("still separates genuinely different paths", () => {
    expect(mediaCacheKey({ url: "https://cdn.pulsesoc.com/m/9.jpg" })).not.toBe(
      mediaCacheKey({ url: "https://cdn.pulsesoc.com/m/10.jpg" })
    );
  });

  it("returns an empty key when there is nothing to key on", () => {
    expect(mediaCacheKey({ url: "" })).toBe("");
  });
});

describe("account isolation (Stage 35)", () => {
  it("puts the scope in the path, not the key", () => {
    setMediaCacheScope(1234);
    const a = cacheFileUriFor("id:7", ".jpg");
    setMediaCacheScope(5678);
    const b = cacheFileUriFor("id:7", ".jpg");
    expect(a).toContain("/u1234/");
    expect(b).toContain("/u5678/");
    expect(a).not.toBe(b);
  });

  it("normalises a hostile user id rather than letting it escape the root", () => {
    setMediaCacheScope("../../etc");
    expect(getMediaCacheScope()).toBe("uetc");
    expect(cacheFileUriFor("id:1")).toContain("/pulsesoc-media/uetc/");
  });

  it("cannot resolve another account's cached file", async () => {
    setMediaCacheScope(1234);
    await writeCached("id:7", 2048);
    expect(await lookupCachedMedia("id:7")).not.toBeNull();

    setMediaCacheScope(5678);
    expect(await lookupCachedMedia("id:7")).toBeNull();
  });

  it("clearAllMediaCaches removes every account's bytes, not just the active one", async () => {
    setMediaCacheScope(1234);
    await writeCached("id:7", 2048);
    setMediaCacheScope(5678);
    await writeCached("id:8", 2048);
    expect(mockFiles.size).toBe(2);

    await clearAllMediaCaches();

    expect(mockFiles.size).toBe(0);
    const keys = await AsyncStorage.getAllKeys();
    expect(keys.filter((key) => key.startsWith("pulsesoc.native.mediacache.index."))).toHaveLength(0);
  });
});

describe("integrity", () => {
  it("treats a truncated file as a miss instead of handing it to a decoder", async () => {
    const entry = await writeCached("id:7", 4096);
    expect(entry).not.toBeNull();

    // The app was killed mid-write: the file is short of what the index recorded.
    mockFiles.set(entry!.fileUri, 900);

    expect(await lookupCachedMedia("id:7")).toBeNull();
    expect(mockFiles.has(entry!.fileUri)).toBe(false);
  });

  it("treats a vanished file as a miss", async () => {
    const entry = await writeCached("id:7", 4096);
    mockFiles.delete(entry!.fileUri);
    expect(await lookupCachedMedia("id:7")).toBeNull();
  });

  it("refuses to index a zero-byte download (Stage 32)", async () => {
    const uri = cacheFileUriFor("id:9", ".jpg");
    mockFiles.set(uri, 0);
    expect(await commitCachedMedia({ key: "id:9", fileUri: uri })).toBeNull();
    expect(mockFiles.has(uri)).toBe(false);
    expect(await lookupCachedMedia("id:9")).toBeNull();
  });

  it("expires entries past the age ceiling even when under quota", async () => {
    configureMediaCache({ maxAgeMs: 1 });
    await writeCached("id:7", 1024);
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(await lookupCachedMedia("id:7")).toBeNull();
  });
});

describe("bounds", () => {
  it("evicts least-recently-used entries once over quota", async () => {
    configureMediaCache({ maxBytes: 2500 });
    // `lastAccessAt` has millisecond resolution, and three writes in the same
    // tick are a genuine tie — the assertion below would then be testing sort
    // stability rather than the LRU policy. Space them out so "least recently
    // used" is a fact rather than a coincidence.
    await writeCached("id:1", 1000);
    await tick();
    await writeCached("id:2", 1000);
    await tick();
    // Touch the first so the second becomes the least recently used.
    await lookupCachedMedia("id:1");
    await tick();
    await writeCached("id:3", 1000);

    expect(await lookupCachedMedia("id:2")).toBeNull();
    expect(await lookupCachedMedia("id:1")).not.toBeNull();
    expect(await lookupCachedMedia("id:3")).not.toBeNull();

    const stats = await mediaCacheStats();
    expect(stats.bytes).toBeLessThanOrEqual(2500);
  });

  it("refuses a download that would leave the device without headroom", async () => {
    configureMediaCache({ minFreeDiskBytes: 100 });
    mockDisk.free = 150;
    await expect(ensureRoomFor(120)).rejects.toBeInstanceOf(MediaCacheFullError);
  });

  it("allows a download that fits within the headroom", async () => {
    configureMediaCache({ minFreeDiskBytes: 100 });
    mockDisk.free = 5000;
    await expect(ensureRoomFor(120)).resolves.toBeUndefined();
  });
});
