/**
 * What the device can honestly claim to play with the network gone.
 *
 * The filesystem is faked in memory rather than mocked call-by-call, for the
 * same reason the mediaCache suite does it: the property under test is what the
 * report says about the bytes actually on disk, not which function was called to
 * find out.
 *
 * Several of these are the §91 mutation tests. They are written so that the
 * naive implementation — treat "something is cached" as "playable", or trust the
 * index without checking the file — fails here rather than in someone's feed.
 */
import AsyncStorage from "@react-native-async-storage/async-storage";

const mockFiles = new Map<string, number>();

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
  getFreeDiskStorageAsync: jest.fn(async () => Number.MAX_SAFE_INTEGER)
}));

import {
  __resetMediaCacheMemory,
  cacheFileUriFor,
  commitCachedMedia,
  lookupCachedMedia,
  mediaCacheKey,
  peekCachedMedia
} from "../../../media/mediaCache";
import {
  canPlayOffline,
  hasOfflineVisual,
  needsDownloadForPlayback,
  offlinePlaybackStateFor,
  renditionToMakeOffline
} from "../offlinePlayback";
import type { MediaDescriptor, MediaRendition } from "../mediaIdentity";

/** A reel as the feed serializer actually emits it. */
const REEL: MediaDescriptor = {
  id: 501,
  type: "video",
  mux_playback_id: "PLAYBACK501",
  playback_url: "https://stream.mux.com/PLAYBACK501.m3u8",
  media_url: "https://stream.mux.com/PLAYBACK501/high.mp4",
  poster_url: "https://image.mux.com/PLAYBACK501/thumbnail.jpg",
  thumbnail_url: "https://image.mux.com/PLAYBACK501/thumbnail.jpg?width=320"
};

const PHOTO: MediaDescriptor = {
  id: 502,
  type: "image",
  media_url: "https://cdn.pulsesoc.com/m/502.jpg",
  thumbnail_url: "https://cdn.pulsesoc.com/m/502_thumb.jpg"
};

/** Put a real, size-consistent file on disk for one rendition of one media. */
async function cacheRendition(media: MediaDescriptor, rendition: MediaRendition, bytes: number) {
  const url = renditionUrlFor(media, rendition);
  const key = mediaCacheKey({ mediaId: media.id, url, rendition });
  const uri = cacheFileUriFor(key, ".bin");
  mockFiles.set(uri, bytes);
  const entry = await commitCachedMedia({ key, fileUri: uri });
  expect(entry).not.toBeNull();
  return { key, uri };
}

function renditionUrlFor(media: MediaDescriptor, rendition: MediaRendition): string {
  switch (rendition) {
    case "poster":
      return String(media.poster_url || media.thumbnail_url || "");
    case "thumb":
      return String(media.thumbnail_url || media.poster_url || "");
    case "manifest":
      return String(media.playback_url || "");
    default:
      return String(media.media_url || "");
  }
}

beforeEach(async () => {
  mockFiles.clear();
  await AsyncStorage.clear();
  __resetMediaCacheMemory();
});

describe("nothing cached", () => {
  it("reports none, and none is not a visual", async () => {
    const report = await offlinePlaybackStateFor(REEL);
    expect(report.state).toBe("none");
    expect(canPlayOffline(report)).toBe(false);
    expect(hasOfflineVisual(report)).toBe(false);
    expect(report.fileUri).toBeNull();
  });

  it("reports none for media with no durable identity rather than inventing one", async () => {
    const report = await offlinePlaybackStateFor({ type: "video" });
    expect(report.state).toBe("none");
    expect(report.identity).toBeNull();
  });
});

describe("a thumbnail is not a cached video", () => {
  it("reports preview_only when only the poster is on disk", async () => {
    await cacheRendition(REEL, "poster", 24_000);

    const report = await offlinePlaybackStateFor(REEL);
    expect(report.state).toBe("preview_only");
    expect(report.previewRendition).toBe("poster");
    expect(report.playableRendition).toBeNull();
    expect(report.playableBytes).toBe(0);
  });

  it("refuses to call a poster playable — the §91 mutation", async () => {
    // The failure this pins: a reel showing its poster, announcing itself as
    // available offline, then spinning forever on a video nobody fetched. A
    // boolean "is it cached" gets this wrong; the state cannot.
    await cacheRendition(REEL, "poster", 24_000);
    await cacheRendition(REEL, "thumb", 8_000);

    const report = await offlinePlaybackStateFor(REEL);
    expect(canPlayOffline(report)).toBe(false);
    // ...but it IS worth rendering. A feed of posters beats a feed of grey boxes.
    expect(hasOfflineVisual(report)).toBe(true);
    expect(needsDownloadForPlayback(REEL, report)).toBe(true);
  });

  it("does not let a cached manifest masquerade as a cached video", async () => {
    // A manifest is a few kilobytes of text naming segments that are not on
    // disk. Caching it feels like caching the video and is worth nothing.
    await cacheRendition(REEL, "manifest", 1_200);

    const report = await offlinePlaybackStateFor(REEL);
    expect(report.state).toBe("none");
    expect(canPlayOffline(report)).toBe(false);
  });
});

describe("the whole file is here", () => {
  it("reports full_offline and hands back a local file, never a remote URL", async () => {
    const { uri } = await cacheRendition(REEL, "full", 20_600_000);

    const report = await offlinePlaybackStateFor(REEL);
    expect(report.state).toBe("full_offline");
    expect(canPlayOffline(report)).toBe(true);
    expect(report.playableRendition).toBe("full");
    expect(report.playableBytes).toBe(20_600_000);
    expect(report.fileUri).toBe(uri);
    expect(report.fileUri).not.toMatch(/^https?:/);
  });

  it("still reports the preview alongside, so a cell can paint before it plays", async () => {
    await cacheRendition(REEL, "full", 20_600_000);
    await cacheRendition(REEL, "poster", 24_000);

    const report = await offlinePlaybackStateFor(REEL);
    expect(report.state).toBe("full_offline");
    expect(report.previewRendition).toBe("poster");
  });

  it("treats a photo's own bytes as full offline, since an image is its own preview", async () => {
    await cacheRendition(PHOTO, "full", 180_000);

    const report = await offlinePlaybackStateFor(PHOTO);
    expect(report.state).toBe("full_offline");
    expect(needsDownloadForPlayback(PHOTO, report)).toBe(false);
  });
});

describe("the index is not evidence", () => {
  it("does not report a vanished file as cached", async () => {
    // "Cache marked complete with missing file" — §91. The index still has the
    // entry; the bytes are gone. Trusting the index here is how a player gets
    // pointed at a file that is not there.
    const { uri } = await cacheRendition(REEL, "full", 20_600_000);
    mockFiles.delete(uri);

    const report = await offlinePlaybackStateFor(REEL);
    expect(report.state).toBe("none");
    expect(canPlayOffline(report)).toBe(false);
  });

  it("does not report a truncated file as cached", async () => {
    // Killed mid-write, or the disk filled. The size no longer matches what was
    // recorded, so the file is not what the index claims it is.
    const { uri } = await cacheRendition(REEL, "full", 20_600_000);
    mockFiles.set(uri, 3_000_000);

    const report = await offlinePlaybackStateFor(REEL);
    expect(report.state).toBe("none");
  });

  it("falls back to the preview when only the video is corrupt", async () => {
    const { uri } = await cacheRendition(REEL, "full", 20_600_000);
    await cacheRendition(REEL, "poster", 24_000);
    mockFiles.delete(uri);

    const report = await offlinePlaybackStateFor(REEL);
    expect(report.state).toBe("preview_only");
    expect(canPlayOffline(report)).toBe(false);
  });
});

describe("what to fetch next", () => {
  it("names the playable rendition and its URL when the video is missing", async () => {
    await cacheRendition(REEL, "poster", 24_000);
    const report = await offlinePlaybackStateFor(REEL);

    const next = renditionToMakeOffline(REEL, report);
    expect(next?.rendition).toBe("full");
    expect(next?.url).toBe("https://stream.mux.com/PLAYBACK501/high.mp4");
  });

  it("asks for nothing once the media is already fully offline", async () => {
    await cacheRendition(REEL, "full", 20_600_000);
    const report = await offlinePlaybackStateFor(REEL);
    expect(renditionToMakeOffline(REEL, report)).toBeNull();
  });

  it("asks for nothing when the backend has no playable URL to give", async () => {
    // A video still transcoding has a row and a poster but no rendition. Asking
    // for it is a guaranteed 404, so the answer is "nothing to fetch", not a URL.
    const transcoding: MediaDescriptor = {
      id: 503,
      type: "video",
      poster_url: "https://image.mux.com/X/thumbnail.jpg",
      mux_processing: true
    };
    const report = await offlinePlaybackStateFor(transcoding);
    expect(renditionToMakeOffline(transcoding, report)).toBeNull();
  });
});

describe("probing does not disturb the cache", () => {
  it("does not keep an entry alive merely by asking about it", async () => {
    // A state report probes several renditions per asset. If the probe counted
    // as a use, inspecting a feed would pin everything it looked at and the LRU
    // would stop being an LRU.
    const { key } = await cacheRendition(REEL, "full", 1_000);

    // Observed with peek, not lookup: reading the timestamp with the read path
    // would bump the very value under test and the assertion would pass for the
    // wrong reason.
    const stamp = (await peekCachedMedia(key))!.lastAccessAt;

    await new Promise((resolve) => setTimeout(resolve, 5));
    await offlinePlaybackStateFor(REEL);
    expect((await peekCachedMedia(key))!.lastAccessAt).toBe(stamp);

    // The control: a real read does bump it, so the assertion above is measuring
    // a difference between the two paths rather than a clock that never moved.
    await new Promise((resolve) => setTimeout(resolve, 5));
    await lookupCachedMedia(key);
    expect((await peekCachedMedia(key))!.lastAccessAt).toBeGreaterThan(stamp);
  });
});
