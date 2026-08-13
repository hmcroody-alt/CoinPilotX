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
      coverArtUrl: "https://cdn.example.com/orbit.jpg",
      artistUserId: 0,
      durationSeconds: 0
    });
  });

  it("carries the rights reference and duration into the Live mixer", () => {
    // A Live take has to be creditable from the same data a recorded one is.
    // Dropping these here would leave the broadcast holding a caption — a title
    // and an artist name — with nothing to re-resolve the licence from, and it
    // would leave the start-point control with no track length to scrub within.
    const track = normalizeLiveMusicTrack({
      id: "track-7",
      title: "Orbit Signal",
      artist: "Ava Lang",
      audioUrl: "https://cdn.example.com/orbit.m4a",
      durationSeconds: 214,
      licenseLabel: "pulsesoc_commercial_v2",
      artistUserId: 5501
    });
    expect(track).toMatchObject({ durationSeconds: 214, licenseLabel: "pulsesoc_commercial_v2", artistUserId: 5501 });
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
