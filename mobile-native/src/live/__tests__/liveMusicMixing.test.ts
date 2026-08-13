import {
  clampLiveMixLevel,
  liveMixLevelToAgoraVolume,
  normalizeLiveMusicTrack
} from "../liveMusicMixing";

describe("live music mixing", () => {
  it("normalizes a playable PulseSoc Music track for the native Live mixer", () => {
    expect(
      normalizeLiveMusicTrack({
        id: "track-7",
        title: "Orbit Signal",
        artist: "PulseSoc Music",
        audioUrl: "https://cdn.example.com/orbit.m4a",
        coverArtUrl: "https://cdn.example.com/orbit.jpg"
      })
    ).toEqual({
      id: "track-7",
      title: "Orbit Signal",
      artist: "PulseSoc Music",
      audioUrl: "https://cdn.example.com/orbit.m4a",
      coverArtUrl: "https://cdn.example.com/orbit.jpg"
    });
  });

  it("rejects tracks that cannot be mixed into the broadcast", () => {
    expect(normalizeLiveMusicTrack({ id: "track-7", title: "No URL", artist: "PulseSoc Music", audioUrl: "" })).toBeNull();
    expect(normalizeLiveMusicTrack({ id: "", title: "No ID", artist: "PulseSoc Music", audioUrl: "https://cdn.example.com/a.mp3" })).toBeNull();
  });

  it("maps UI levels to Agora volume safely", () => {
    expect(clampLiveMixLevel(-1)).toBe(0);
    expect(clampLiveMixLevel(0.42)).toBe(0.42);
    expect(clampLiveMixLevel(5)).toBe(1);
    expect(liveMixLevelToAgoraVolume(0.42)).toBe(42);
    expect(liveMixLevelToAgoraVolume(1.3, 100)).toBe(100);
  });
});
