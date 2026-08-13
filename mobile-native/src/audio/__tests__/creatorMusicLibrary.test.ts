/**
 * The picker's catalog adapter.
 *
 * The load-bearing claim under test is the mission constraint: this layer must
 * NOT be a second media catalog. Every lane has to resolve to an endpoint the
 * product already serves, and nothing local may ever be treated as authoritative
 * for what a creator is allowed to use. So the tests pin:
 *
 * 1. LANE → ENDPOINT. Radio hits the radio endpoint; every other lane hits
 *    search with the lane the server understands.
 * 2. A typed query beats the lane, including on Radio, whose endpoint takes no
 *    query and would otherwise ignore what was typed.
 * 3. UNUSABLE TRACKS are filtered out of every lane, including the local ones.
 * 4. OFFLINE degrades to the snapshot rather than to an error, and says so.
 * 5. RECENTLY USED is a usage log, capped, most-recent-first, de-duplicated,
 *    and self-healing when its blob is corrupt.
 */

jest.mock("@react-native-async-storage/async-storage", () =>
  require("@react-native-async-storage/async-storage/jest/async-storage-mock")
);

jest.mock("../../api/music", () => ({
  searchPulseMusic: jest.fn(),
  listPulseMusicRadioTracks: jest.fn(),
  loadCachedPulseMusicSnapshot: jest.fn(async () => [])
}));

import AsyncStorage from "@react-native-async-storage/async-storage";

import { listPulseMusicRadioTracks, loadCachedPulseMusicSnapshot, PulseMusicTrack, searchPulseMusic } from "../../api/music";
import {
  CREATOR_MUSIC_LANES,
  CREATOR_MUSIC_RECENT_LIMIT,
  clearRecentCreatorMusicTracks,
  creatorMusicGenresFrom,
  loadCreatorMusicLane,
  loadRecentCreatorMusicTracks,
  rememberCreatorMusicTrack
} from "../creatorMusicLibrary";

const searchMock = searchPulseMusic as jest.Mock;
const radioMock = listPulseMusicRadioTracks as jest.Mock;
const cacheMock = loadCachedPulseMusicSnapshot as jest.Mock;

function track(overrides: Partial<PulseMusicTrack> = {}): PulseMusicTrack {
  return {
    id: "8821",
    title: "Midnight Drive",
    artist: "Ava Lang",
    artistUserId: 5501,
    durationSeconds: 214,
    previewUrl: "https://cdn.pulsesoc.com/music/8821-preview.m4a",
    audioUrl: "https://cdn.pulsesoc.com/music/8821.m4a",
    coverArtUrl: "https://cdn.pulsesoc.com/music/8821.jpg",
    waveform: [0.2, 0.4],
    genre: "synthwave",
    language: "en",
    mood: "night",
    licenseLabel: "pulsesoc_commercial_v2",
    moderationStatus: "approved",
    approvedByAdmin: true,
    active: true,
    playCount: 12,
    usageCount: 4,
    trendScore: 91,
    saveCount: 3,
    shareCount: 1,
    ...overrides
  };
}

beforeEach(async () => {
  jest.clearAllMocks();
  await AsyncStorage.clear();
  searchMock.mockResolvedValue({ tracks: [track()], surfaces: [], provider: {} });
  radioMock.mockResolvedValue([track({ id: "9001", title: "Radio Cut" })]);
  cacheMock.mockResolvedValue([]);
});

describe("lanes resolve to the canonical catalog", () => {
  it("sends trending and new straight through as the server's own lane values", async () => {
    await loadCreatorMusicLane({ lane: "trending" });
    expect(searchMock).toHaveBeenCalledWith(expect.objectContaining({ lane: "trending" }));

    await loadCreatorMusicLane({ lane: "new" });
    expect(searchMock).toHaveBeenLastCalledWith(expect.objectContaining({ lane: "new" }));
  });

  it("uses the radio endpoint for the radio lane rather than a filtered search", async () => {
    const result = await loadCreatorMusicLane({ lane: "radio" });
    expect(radioMock).toHaveBeenCalledTimes(1);
    expect(searchMock).not.toHaveBeenCalled();
    expect(result.tracks.map((item) => item.id)).toEqual(["9001"]);
  });

  it("routes a typed query to search even on the radio lane, which cannot take one", async () => {
    // Otherwise the creator types a song name on Radio and gets a shuffle back,
    // which reads as the search box being broken.
    await loadCreatorMusicLane({ lane: "radio", query: "midnight" });
    expect(radioMock).not.toHaveBeenCalled();
    expect(searchMock).toHaveBeenCalledWith(expect.objectContaining({ query: "midnight" }));
  });

  it("routes a query on the recently-used lane to the catalog too", async () => {
    await rememberCreatorMusicTrack(track());
    await loadCreatorMusicLane({ lane: "recent", query: "anything" });
    expect(searchMock).toHaveBeenCalledTimes(1);
  });

  it("clamps the requested limit into the range the endpoint accepts", async () => {
    await loadCreatorMusicLane({ lane: "trending", limit: 5000 });
    expect(searchMock).toHaveBeenCalledWith(expect.objectContaining({ limit: 80 }));
  });

  it("offers every lane the picker renders", () => {
    expect(CREATOR_MUSIC_LANES.map((item) => item.key)).toEqual(["trending", "new", "radio", "recent"]);
  });
});

describe("unusable tracks never reach the picker", () => {
  it("drops inactive, unapproved and unplayable rows from a search lane", async () => {
    searchMock.mockResolvedValue({
      tracks: [
        track({ id: "1" }),
        track({ id: "2", active: false }),
        track({ id: "3", moderationStatus: "pending" }),
        track({ id: "4", audioUrl: "", previewUrl: "" })
      ],
      surfaces: [],
      provider: {}
    });
    const result = await loadCreatorMusicLane({ lane: "trending" });
    expect(result.tracks.map((item) => item.id)).toEqual(["1"]);
  });

  it("applies the same filter to radio", async () => {
    radioMock.mockResolvedValue([track({ id: "5", active: false }), track({ id: "6" })]);
    const result = await loadCreatorMusicLane({ lane: "radio" });
    expect(result.tracks.map((item) => item.id)).toEqual(["6"]);
  });

  it("applies the same filter to the local recently-used list", async () => {
    // A track can be pulled from the catalog between the day it was used and the
    // day it is offered again. The local log must not be the thing that
    // resurrects it.
    await AsyncStorage.setItem(
      "pulsesoc.native.creator.music.recent.v1",
      JSON.stringify([track({ id: "7", active: false }), track({ id: "8" })])
    );
    const result = await loadCreatorMusicLane({ lane: "recent" });
    expect(result.tracks.map((item) => item.id)).toEqual(["8"]);
  });
});

describe("network failure degrades instead of emptying the picker", () => {
  it("falls back to the offline snapshot and says it is offline", async () => {
    searchMock.mockRejectedValue(new Error("Network request failed"));
    cacheMock.mockResolvedValue([track({ id: "42" })]);
    const result = await loadCreatorMusicLane({ lane: "trending" });
    expect(result.tracks.map((item) => item.id)).toEqual(["42"]);
    expect(result.offline).toBe(true);
    expect(result.message).toContain("Offline");
  });

  it("filters the snapshot by the query and genre the creator had typed", async () => {
    searchMock.mockRejectedValue(new Error("offline"));
    cacheMock.mockResolvedValue([
      track({ id: "10", title: "Midnight Drive", genre: "synthwave" }),
      track({ id: "11", title: "Sunrise", genre: "ambient" })
    ]);
    const byQuery = await loadCreatorMusicLane({ lane: "trending", query: "sunrise" });
    expect(byQuery.tracks.map((item) => item.id)).toEqual(["11"]);

    const byGenre = await loadCreatorMusicLane({ lane: "trending", genre: "synthwave" });
    expect(byGenre.tracks.map((item) => item.id)).toEqual(["10"]);
  });

  it("surfaces the underlying error when there is no snapshot to fall back to", async () => {
    searchMock.mockRejectedValue(new Error("Login required."));
    const result = await loadCreatorMusicLane({ lane: "trending" });
    expect(result.tracks).toEqual([]);
    expect(result.offline).toBe(true);
    expect(result.message).toBe("Login required.");
  });

  it("survives a snapshot read that itself throws", async () => {
    searchMock.mockRejectedValue(new Error("offline"));
    cacheMock.mockRejectedValue(new Error("storage unavailable"));
    await expect(loadCreatorMusicLane({ lane: "trending" })).resolves.toMatchObject({ tracks: [] });
  });
});

describe("recently used is a usage log, not a catalog", () => {
  it("keeps the most recent first and de-duplicates repeats", async () => {
    await rememberCreatorMusicTrack(track({ id: "a" }));
    await rememberCreatorMusicTrack(track({ id: "b" }));
    await rememberCreatorMusicTrack(track({ id: "a" }));
    const recent = await loadRecentCreatorMusicTracks();
    expect(recent.map((item) => item.id)).toEqual(["a", "b"]);
  });

  it("caps the list so it cannot grow into a worse copy of the library", async () => {
    for (let index = 0; index < CREATOR_MUSIC_RECENT_LIMIT + 6; index += 1) {
      await rememberCreatorMusicTrack(track({ id: `t${index}` }));
    }
    const recent = await loadRecentCreatorMusicTracks();
    expect(recent).toHaveLength(CREATOR_MUSIC_RECENT_LIMIT);
    expect(recent[0].id).toBe(`t${CREATOR_MUSIC_RECENT_LIMIT + 5}`);
  });

  it("ignores a track with no id rather than writing an unusable row", async () => {
    await rememberCreatorMusicTrack(track({ id: "" }));
    expect(await loadRecentCreatorMusicTracks()).toEqual([]);
  });

  it("drops a corrupt blob instead of throwing at a creator standing at the camera", async () => {
    await AsyncStorage.setItem("pulsesoc.native.creator.music.recent.v1", "{not json");
    expect(await loadRecentCreatorMusicTracks()).toEqual([]);
    expect(await AsyncStorage.getItem("pulsesoc.native.creator.music.recent.v1")).toBeNull();
  });

  it("tolerates a blob that parses but is not a list", async () => {
    await AsyncStorage.setItem("pulsesoc.native.creator.music.recent.v1", JSON.stringify({ id: "x" }));
    expect(await loadRecentCreatorMusicTracks()).toEqual([]);
  });

  it("hits no network at all for the recently-used lane", async () => {
    await rememberCreatorMusicTrack(track({ id: "z" }));
    const result = await loadCreatorMusicLane({ lane: "recent" });
    expect(searchMock).not.toHaveBeenCalled();
    expect(radioMock).not.toHaveBeenCalled();
    expect(result.tracks.map((item) => item.id)).toEqual(["z"]);
  });

  it("explains the empty state rather than looking broken", async () => {
    const result = await loadCreatorMusicLane({ lane: "recent" });
    expect(result.tracks).toEqual([]);
    expect(result.message).toContain("Songs you use");
  });

  it("can be cleared", async () => {
    await rememberCreatorMusicTrack(track({ id: "q" }));
    await clearRecentCreatorMusicTracks();
    expect(await loadRecentCreatorMusicTracks()).toEqual([]);
  });
});

describe("genre chips are derived from results, so no chip returns nothing", () => {
  it("de-duplicates case-insensitively and preserves the first spelling seen", () => {
    const genres = creatorMusicGenresFrom([
      track({ id: "1", genre: "Synthwave" }),
      track({ id: "2", genre: "synthwave" }),
      track({ id: "3", genre: "Ambient" })
    ]);
    expect(genres).toEqual(["Synthwave", "Ambient"]);
  });

  it("skips the api layer's placeholder for an unset genre", () => {
    // `normalizeMusicTrack` substitutes the literal string "genre" when a track
    // has none; rendering that as a chip would filter the list down to nothing.
    expect(creatorMusicGenresFrom([track({ id: "1", genre: "genre" })])).toEqual([]);
  });

  it("bounds the chip row", () => {
    const many = Array.from({ length: 20 }, (_item, index) => track({ id: `g${index}`, genre: `genre-${index}` }));
    expect(creatorMusicGenresFrom(many)).toHaveLength(8);
  });
});
