import {
  DEFAULT_LIVE_MUSIC_MIXING_STATE,
  clampLiveMixLevel,
  liveMixLevelToAgoraVolume,
  musicRestorationAfterAudioChange,
  musicRestorationIsRequired,
  normalizeLiveMusicTrack,
  type LiveMusicMixingState,
  type LiveMusicMixingStatus
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

/**
 * Stage 35. The music mix has to survive a guest arriving, and the moment a
 * guest arrives is exactly when the audio scenario moves — so the restoration
 * plan below is what stands between "host brings someone up" and "host's music
 * goes silent for no visible reason".
 */
describe("preserving the music mix across an audio-module change", () => {
  const withStatus = (status: LiveMusicMixingStatus): LiveMusicMixingState => ({
    ...DEFAULT_LIVE_MUSIC_MIXING_STATE,
    status,
    track: status === "idle" ? null : { id: "t", title: "T", artist: "A", audioUrl: "https://x/a.m4a" }
  });

  it("restores everything for music that was playing", () => {
    expect(musicRestorationAfterAudioChange(withStatus("playing"))).toEqual({
      reapplyVolumes: true,
      reapplyMicVolume: true,
      resumePlayback: true
    });
  });

  it("restores the levels of paused music without un-pausing it", () => {
    // A host who paused the music meant to. Resuming it because a guest joined
    // would be the same bug in the other direction.
    const plan = musicRestorationAfterAudioChange(withStatus("paused"));
    expect(plan.reapplyVolumes).toBe(true);
    expect(plan.resumePlayback).toBe(false);
  });

  it("touches nothing when no music is mixing", () => {
    for (const status of ["idle", "loading", "error"] as LiveMusicMixingStatus[]) {
      expect(musicRestorationIsRequired(withStatus(status))).toBe(false);
    }
  });

  it("never resumes playback it was not asked to resume", () => {
    for (const status of ["idle", "loading", "error", "paused"] as LiveMusicMixingStatus[]) {
      expect(musicRestorationAfterAudioChange(withStatus(status)).resumePlayback).toBe(false);
    }
  });

  it("survives a malformed state without deciding to touch the engine", () => {
    expect(musicRestorationIsRequired({} as LiveMusicMixingState)).toBe(false);
  });
});
