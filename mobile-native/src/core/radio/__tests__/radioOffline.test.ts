const mockPeek = jest.fn();
const mockDrop = jest.fn();
const mockDownload = jest.fn();

jest.mock("../../../media/mediaCache", () => ({
  ...jest.requireActual("../../../media/mediaCache"),
  peekCachedMedia: (...args: unknown[]) => mockPeek(...args),
  dropCachedMedia: (...args: unknown[]) => mockDrop(...args)
}));
jest.mock("../../../media/mediaDownloader", () => ({
  downloadMedia: (...args: unknown[]) => mockDownload(...args)
}));

import AsyncStorage from "@react-native-async-storage/async-storage";
import { resetConnectivityForTests } from "../../connectivity";
import { resetJsonCacheMemory } from "../../cache";
import {
  cacheRadioQueue,
  discardCachedRadioTrack,
  loadCachedRadioQueue,
  nextTrackToWarm,
  radioTrackCacheKey,
  RADIO_QUEUE_CACHE_KEY,
  resolveRadioSource,
  warmRadioTrack
} from "../radioOffline";

function track(id: string, audioUrl = `https://cdn.pulsesoc.com/audio/${id}.mp3`) {
  return { id, title: `Track ${id}`, artist: "Artist", audioUrl };
}

beforeEach(async () => {
  jest.clearAllMocks();
  resetJsonCacheMemory();
  await AsyncStorage.clear();
  mockPeek.mockResolvedValue(null);
  mockDrop.mockResolvedValue(true);
  mockDownload.mockResolvedValue({ key: "k", fileUri: "file:///cache/k.mp3", bytes: 10 });
  resetConnectivityForTests({ state: "online" });
});

describe("radio track identity", () => {
  it("survives the delivery URL being re-signed", () => {
    // §38. Keying on the URL as handed to us means every signature rotation
    // orphans the file we just downloaded: the cache grows and is never hit,
    // which reads as "offline playback doesn't work" long after the cause.
    const first = radioTrackCacheKey(track("t1", "https://cdn.pulsesoc.com/audio/t1.mp3?sig=AAA&exp=1"));
    const second = radioTrackCacheKey(track("t1", "https://cdn.pulsesoc.com/audio/t1.mp3?sig=BBB&exp=2"));
    expect(first).toBeTruthy();
    expect(second).toBe(first);
  });

  it("keeps two different tracks apart", () => {
    expect(radioTrackCacheKey(track("t1"))).not.toBe(radioTrackCacheKey(track("t2")));
  });

  it("reports no key rather than inventing one", () => {
    // A synthesised key would be unique per call: it would consume the storage
    // budget and never once be hit.
    expect(radioTrackCacheKey(null)).toBe("");
    expect(radioTrackCacheKey({ id: "", title: "", artist: "", audioUrl: "" })).toBe("");
  });
});

describe("radio queue durability", () => {
  it("round-trips the queue with an age attached", async () => {
    await cacheRadioQueue([track("t1"), track("t2")]);
    const snapshot = await loadCachedRadioQueue();
    expect(snapshot.tracks.map((t) => t.id)).toEqual(["t1", "t2"]);
    expect(snapshot.ageMs).not.toBeNull();
    expect(snapshot.ageMs).toBeLessThan(5_000);
  });

  it("refuses to replace a usable queue with an empty one", async () => {
    // One failed request must not turn into a permanently empty radio.
    await cacheRadioQueue([track("t1")]);
    await cacheRadioQueue([]);
    expect((await loadCachedRadioQueue()).tracks).toHaveLength(1);
  });

  it("drops entries that could never be played", async () => {
    await cacheRadioQueue([track("t1"), { id: "t2", title: "x", artist: "y", audioUrl: "" } as never]);
    expect((await loadCachedRadioQueue()).tracks.map((t) => t.id)).toEqual(["t1"]);
  });

  it("says nothing rather than guessing when the entry predates timestamps", async () => {
    // An entry written by an earlier build has no age, and "just now" over a
    // queue that may be weeks old is a freshness claim never observed.
    await AsyncStorage.setItem(RADIO_QUEUE_CACHE_KEY, JSON.stringify([track("t1")]));
    resetJsonCacheMemory();
    const snapshot = await loadCachedRadioQueue();
    expect(snapshot.tracks).toHaveLength(1);
    expect(snapshot.storedAt).toBeNull();
    expect(snapshot.ageMs).toBeNull();
  });
});

describe("resolveRadioSource", () => {
  it("prefers a verified file on disk over the stream", async () => {
    mockPeek.mockResolvedValue({ fileUri: "file:///cache/t1.mp3", bytes: 2048 });
    const source = await resolveRadioSource(track("t1"));
    expect(source).toEqual({ uri: "file:///cache/t1.mp3", offline: true });
  });

  it("falls back to the stream and says so", async () => {
    const source = await resolveRadioSource(track("t1"));
    expect(source?.offline).toBe(false);
    expect(source?.uri).toContain("https://");
  });

  it("returns nothing for a track with no source at all", async () => {
    expect(await resolveRadioSource({ id: "t9", title: "", artist: "", audioUrl: "" })).toBeNull();
  });
});

describe("warming the next track", () => {
  it("fetches one track ahead, never the whole queue", () => {
    const queue = [track("t1"), track("t2"), track("t3")];
    const order = [0, 1, 2];
    expect(nextTrackToWarm(queue, order, 0)?.id).toBe("t2");
    expect(nextTrackToWarm(queue, order, 1)?.id).toBe("t3");
    // Nothing after the last one — asking would fetch the first track again.
    expect(nextTrackToWarm(queue, order, 2)).toBeNull();
  });

  it("follows the shuffled order, not the queue order", () => {
    const queue = [track("t1"), track("t2"), track("t3")];
    expect(nextTrackToWarm(queue, [2, 0, 1], 0)?.id).toBe("t1");
  });

  it("does not compete with the audio that is currently playing", async () => {
    // DEGRADED is precisely the state where a speculative download steals
    // bandwidth from the track the listener can hear.
    resetConnectivityForTests({ state: "degraded" });
    expect(await warmRadioTrack(track("t2"))).toBe(false);
    resetConnectivityForTests({ state: "offline" });
    expect(await warmRadioTrack(track("t2"))).toBe(false);
    expect(mockDownload).not.toHaveBeenCalled();
  });

  it("downloads under the track's own id, not its signed URL", async () => {
    await warmRadioTrack(track("t2", "https://cdn.pulsesoc.com/audio/t2.mp3?sig=AAA"));
    expect(mockDownload).toHaveBeenCalledWith(expect.objectContaining({ mediaId: "t2", rendition: "full", kind: "audio" }));
  });

  it("skips the download when the file is already held", async () => {
    mockPeek.mockResolvedValue({ fileUri: "file:///cache/t2.mp3", bytes: 2048 });
    expect(await warmRadioTrack(track("t2"))).toBe(true);
    expect(mockDownload).not.toHaveBeenCalled();
  });

  it("reports a failed warm instead of throwing it at the player", async () => {
    // Warming is an optimisation. A failure to warm must never surface as a
    // playback error for a track that is playing perfectly well.
    mockDownload.mockRejectedValue(new Error("network"));
    await expect(warmRadioTrack(track("t2"))).resolves.toBe(false);
  });
});

describe("discardCachedRadioTrack", () => {
  it("forgets the copy the player proved it could not open", async () => {
    await discardCachedRadioTrack(track("t1"));
    expect(mockDrop).toHaveBeenCalledWith(radioTrackCacheKey(track("t1")));
  });

  it("has nothing to forget for an unkeyable track", async () => {
    expect(await discardCachedRadioTrack(null)).toBe(false);
    expect(mockDrop).not.toHaveBeenCalled();
  });
});
