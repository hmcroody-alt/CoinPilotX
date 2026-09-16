/**
 * Stage 6/14/30/36/38 regression tests for the shared download engine.
 *
 * The four properties asserted here are the four the mission calls out by name:
 * one transfer per file however many callers ask, bytes staged in `.part` so a
 * kill cannot produce a truncated "success", retries bounded and only for
 * reasons that can plausibly succeed, and a disk-full refusal that arrives
 * before the network is touched.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";

const mockFiles = new Map<string, number>();
const mockDisk = { free: Number.MAX_SAFE_INTEGER };

type ScriptedResponse = { status: number; bytes: number } | Error;
const mockResponses: ScriptedResponse[] = [];
const mockCreateCalls: string[] = [];
/** The URL each transfer was actually pointed at, in order. */
const mockCreateUrls: string[] = [];

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
  getFreeDiskStorageAsync: jest.fn(async () => mockDisk.free),
  createDownloadResumable: jest.fn((url: string, destination: string) => {
    mockCreateCalls.push(destination);
    mockCreateUrls.push(url);
    const run = async () => {
      const next = mockResponses.shift();
      if (!next) throw new Error("No scripted download response");
      if (next instanceof Error) throw next;
      if (next.bytes > 0) mockFiles.set(destination, next.bytes);
      return { status: next.status, uri: destination };
    };
    return {
      downloadAsync: run,
      resumeAsync: run,
      pauseAsync: jest.fn(async () => ({ resumeData: "offset" })),
      cancelAsync: jest.fn(async () => undefined),
      savable: jest.fn(() => ({ resumeData: "offset" }))
    };
  })
}));

import { __resetMediaCacheMemory, configureMediaCache, lookupCachedMedia, mediaCacheKey } from "../mediaCache";
import { MediaDownloadError, __mediaDownloaderState, downloadMedia, downloadMessageFor } from "../mediaDownloader";

beforeEach(async () => {
  mockFiles.clear();
  mockResponses.length = 0;
  mockCreateCalls.length = 0;
  mockCreateUrls.length = 0;
  mockDisk.free = Number.MAX_SAFE_INTEGER;
  await AsyncStorage.clear();
  __resetMediaCacheMemory();
});

const IMAGE = { url: "https://cdn.pulsesoc.com/m/7.jpg?sig=abc", mediaId: 7, mimeType: "image/jpeg" as const };

describe("happy path", () => {
  it("resolves with a complete local file and caches it", async () => {
    mockResponses.push({ status: 200, bytes: 4096 });
    const entry = await downloadMedia(IMAGE);
    expect(entry.bytes).toBe(4096);
    expect(entry.fileUri).toMatch(/\.jpg$/);
    expect(entry.fileUri).not.toMatch(/\.part$/);
    // Derived, not spelled out: the key belongs to the identity authority, and a
    // literal here would silently stop testing the cache the downloader writes to
    // the moment that derivation changes.
    expect(await lookupCachedMedia(mediaCacheKey(IMAGE))).not.toBeNull();
  });

  it("serves the second request from cache without a second transfer", async () => {
    mockResponses.push({ status: 200, bytes: 4096 });
    await downloadMedia(IMAGE);
    await downloadMedia(IMAGE);
    expect(mockCreateCalls).toHaveLength(1);
  });
});

describe("idempotency (Stage 14)", () => {
  it("collapses concurrent requests for the same media into one transfer", async () => {
    mockResponses.push({ status: 200, bytes: 4096 });
    const results = await Promise.all([
      downloadMedia(IMAGE),
      downloadMedia(IMAGE),
      downloadMedia(IMAGE),
      downloadMedia({ ...IMAGE, url: "https://cdn.pulsesoc.com/m/7.jpg?sig=rotated" })
    ]);
    expect(mockCreateCalls).toHaveLength(1);
    expect(new Set(results.map((entry) => entry.fileUri)).size).toBe(1);
    expect(__mediaDownloaderState().active).toBe(0);
  });
});

describe("phantom success (Stage 38)", () => {
  it("stages bytes in a .part file rather than at the final path", async () => {
    mockResponses.push({ status: 200, bytes: 4096 });
    await downloadMedia(IMAGE);
    expect(mockCreateCalls[0]).toMatch(/\.part$/);
  });

  it("leaves nothing at the final path when the transfer fails", async () => {
    mockResponses.push({ status: 404, bytes: 120 });
    await expect(downloadMedia(IMAGE)).rejects.toBeInstanceOf(MediaDownloadError);
    expect(mockFiles.size).toBe(0);
    expect(await lookupCachedMedia("id:7")).toBeNull();
  });

  it("reports an empty response as corrupt rather than as a saved file", async () => {
    mockResponses.push({ status: 200, bytes: 0 });
    await expect(downloadMedia(IMAGE)).rejects.toMatchObject({ reason: "corrupt" });
    expect(await lookupCachedMedia("id:7")).toBeNull();
  });
});

describe("bounded retry", () => {
  it("retries a network failure and succeeds", async () => {
    mockResponses.push(new TypeError("Network request failed"), { status: 200, bytes: 2048 });
    const entry = await downloadMedia(IMAGE);
    expect(entry.bytes).toBe(2048);
    expect(mockCreateCalls).toHaveLength(2);
  });

  it("does not retry a 403 — that is a loop, not resilience", async () => {
    mockResponses.push({ status: 403, bytes: 40 });
    await expect(downloadMedia(IMAGE)).rejects.toMatchObject({ reason: "forbidden" });
    expect(mockCreateCalls).toHaveLength(1);
  });

  it("gives up after a bounded number of attempts", async () => {
    mockResponses.push(
      new TypeError("Network request failed"),
      new TypeError("Network request failed"),
      new TypeError("Network request failed")
    );
    await expect(downloadMedia(IMAGE)).rejects.toMatchObject({ reason: "network" });
    expect(mockCreateCalls).toHaveLength(3);
  });
});

/**
 * §8: an access URL is a fifteen-minute credential, and Save/Share happen
 * whenever the user taps. A 403 on a file the viewer is currently displaying is
 * an expired grant, not a missing permission, and telling the user they lack
 * access to a photo they are looking at is the wrong answer to the wrong
 * question.
 */
describe("expired access URL (§8)", () => {
  const PROTECTED = {
    url: "https://pulsesoc.com/api/messages/media/601/download?mt=stale",
    mediaId: "media_upload:87",
    mimeType: "image/jpeg" as const
  };

  it("re-mints the URL once on a 403 and completes the transfer", async () => {
    mockResponses.push({ status: 403, bytes: 40 }, { status: 200, bytes: 4096 });
    const refreshUrl = jest.fn(async () => "https://pulsesoc.com/api/messages/media/601/download?mt=fresh");

    const entry = await downloadMedia({ ...PROTECTED, refreshUrl });

    expect(entry.bytes).toBe(4096);
    expect(refreshUrl).toHaveBeenCalledTimes(1);
    expect(mockCreateUrls).toEqual([
      "https://pulsesoc.com/api/messages/media/601/download?mt=stale",
      "https://pulsesoc.com/api/messages/media/601/download?mt=fresh"
    ]);
  });

  it("does the same for a 401, which media routes must never answer with", async () => {
    // The server deliberately never answers media with 401 — that would trip
    // session recovery — but a proxy or an origin can, and the download path
    // must not treat "your credential lapsed" differently depending on which
    // number a middlebox chose for it.
    mockResponses.push({ status: 401, bytes: 40 }, { status: 200, bytes: 2048 });
    const refreshUrl = jest.fn(async () => "https://pulsesoc.com/api/messages/media/601/download?mt=fresh");

    await expect(downloadMedia({ ...PROTECTED, refreshUrl })).resolves.toMatchObject({ bytes: 2048 });
    expect(refreshUrl).toHaveBeenCalledTimes(1);
  });

  it("spends the refresh at most once — a second 403 is a real denial", async () => {
    mockResponses.push({ status: 403, bytes: 40 }, { status: 403, bytes: 40 }, { status: 200, bytes: 4096 });
    const refreshUrl = jest.fn(async () => "https://pulsesoc.com/api/messages/media/601/download?mt=fresh");

    await expect(downloadMedia({ ...PROTECTED, refreshUrl })).rejects.toMatchObject({ reason: "forbidden" });
    expect(refreshUrl).toHaveBeenCalledTimes(1);
    expect(mockCreateUrls).toHaveLength(2);
  });

  it("reports the original failure when the refresh cannot produce a URL", async () => {
    // An item with no resolvable foundation id. Returning "" must surface the
    // 403 the server actually sent, not a fabricated second failure mode.
    mockResponses.push({ status: 403, bytes: 40 });
    const refreshUrl = jest.fn(async () => "");

    await expect(downloadMedia({ ...PROTECTED, refreshUrl })).rejects.toMatchObject({ reason: "forbidden" });
    expect(mockCreateUrls).toHaveLength(1);
  });

  it("reports the original failure when the refresh itself throws", async () => {
    mockResponses.push({ status: 403, bytes: 40 });
    const refreshUrl = jest.fn(async () => {
      throw new Error("grant endpoint unreachable");
    });

    await expect(downloadMedia({ ...PROTECTED, refreshUrl })).rejects.toMatchObject({ reason: "forbidden" });
  });

  it("does not refresh a 404 — the media is gone, not the credential", async () => {
    mockResponses.push({ status: 404, bytes: 40 });
    const refreshUrl = jest.fn(async () => "https://pulsesoc.com/api/messages/media/601/download?mt=fresh");

    await expect(downloadMedia({ ...PROTECTED, refreshUrl })).rejects.toMatchObject({ reason: "not_found" });
    expect(refreshUrl).not.toHaveBeenCalled();
  });

  it("keys the cache on media identity, never on the signed URL (§7)", async () => {
    // The whole point of refreshing: the entry written under the stale URL's
    // transfer must be findable by the item whose URL has since rotated.
    mockResponses.push({ status: 403, bytes: 40 }, { status: 200, bytes: 4096 });
    await downloadMedia({
      ...PROTECTED,
      refreshUrl: async () => "https://pulsesoc.com/api/messages/media/601/download?mt=fresh"
    });

    await downloadMedia({ ...PROTECTED, url: "https://pulsesoc.com/api/messages/media/601/download?mt=rotated_again" });
    expect(mockCreateUrls).toHaveLength(2);
  });
});

/**
 * The cached file's *name* is load-bearing, and only for one consumer: the photo
 * library write routes on the extension rather than on the bytes and refuses a
 * file that has none. A Messenger access URL's path ends in `/download`, so when
 * the MIME type is also missing there is nothing left to derive a name from —
 * which is how a valid JPEG ends up unsaveable.
 */
describe("cache file naming", () => {
  const UNNAMED = {
    url: "https://pulsesoc.com/api/messages/media/601/download?mt=abc",
    mediaId: "media_upload:87"
  };

  it("names an extensionless transfer from the media kind", async () => {
    mockResponses.push({ status: 200, bytes: 4096 });
    await expect(downloadMedia({ ...UNNAMED, kind: "image" })).resolves.toMatchObject({
      fileUri: expect.stringMatching(/\.jpg$/)
    });
  });

  it("names a video .mp4 rather than leaving Photos to guess", async () => {
    mockResponses.push({ status: 200, bytes: 4096 });
    await expect(downloadMedia({ ...UNNAMED, kind: "video" })).resolves.toMatchObject({
      fileUri: expect.stringMatching(/\.mp4$/)
    });
  });

  it("still prefers the MIME type when there is one", async () => {
    // The kind fallback is a floor, not a replacement: a PNG must stay a PNG.
    mockResponses.push({ status: 200, bytes: 4096 });
    await expect(downloadMedia({ ...UNNAMED, kind: "image", mimeType: "image/png" })).resolves.toMatchObject({
      fileUri: expect.stringMatching(/\.png$/)
    });
  });

  it("leaves a document unnamed rather than mislabelling it", async () => {
    // A document of unknown type cannot go to Photos anyway, and inventing a
    // suffix would misrepresent it in the share sheet.
    mockResponses.push({ status: 200, bytes: 4096 });
    const entry = await downloadMedia({ ...UNNAMED, kind: "file" });
    expect(entry.fileUri).not.toMatch(/\.[A-Za-z0-9]{1,5}$/);
  });
});

describe("disk pressure (Stage 30)", () => {
  it("refuses before touching the network when the device is full", async () => {
    configureMediaCache({ minFreeDiskBytes: 1000 });
    mockDisk.free = 1200;
    await expect(downloadMedia({ ...IMAGE, expectedBytes: 900 })).rejects.toMatchObject({
      reason: "no_disk_space"
    });
    expect(mockCreateCalls).toHaveLength(0);
  });
});

describe("user-facing messages", () => {
  it("derives text from the reason code, never from the thrown message", () => {
    expect(downloadMessageFor("no_disk_space")).toMatch(/storage/i);
    expect(downloadMessageFor("forbidden")).toMatch(/access/i);
    expect(downloadMessageFor("not_found")).toMatch(/no longer available/i);
  });

  it("never leaks a URL into a user-facing message", () => {
    const reasons = ["network", "timeout", "cancelled", "not_found", "forbidden", "corrupt", "too_large", "no_disk_space", "unknown"] as const;
    for (const reason of reasons) {
      expect(downloadMessageFor(reason)).not.toMatch(/https?:/);
    }
  });
});
