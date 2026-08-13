jest.mock("expo-av", () => ({
  Audio: { setAudioModeAsync: jest.fn(), Sound: { createAsync: jest.fn() } },
  InterruptionModeIOS: { MixWithOthers: 0 }
}));
jest.mock("pulse-video-mixer", () => ({ isPulseVideoMixerSupported: true, mixVideoWithMusic: jest.fn() }));

import {
  DEFAULT_VIDEO_MIX_SETTINGS,
  safeMonitorVolume,
  videoMusicAttribution,
  videoMusicSourceFromRadio,
  videoMusicSourceFromTrack
} from "../videoMusicMix";

describe("video music mix contract", () => {
  it("captures the current Pulse Radio digital asset and live playhead", () => {
    const source = videoMusicSourceFromRadio({
      status: "playing", message: "", userWantsPlayback: true, interruptedBy: null,
      queue: [], queueIndex: 0, shuffle: false, repeatMode: "off",
      positionMillis: 34_500, durationMillis: 180_000,
      track: { id: "9", title: "Pulse", artist: "Nova", audioUrl: "https://cdn/pulse.m4a" }
    });
    expect(source).toMatchObject({ kind: "pulse_radio", trackId: "9", startOffsetSeconds: 34.5, audioUrl: "https://cdn/pulse.m4a" });
  });

  it("preserves canonical attribution instead of display text alone", () => {
    const source = videoMusicSourceFromTrack({
      id: "12", title: "Signal", artist: "Roody", artistUserId: 3, durationSeconds: 90,
      previewUrl: "", audioUrl: "https://cdn/signal.m4a", coverArtUrl: "", waveform: [],
      genre: "pop", language: "en", mood: "upbeat", licenseLabel: "video eligible",
      moderationStatus: "approved", approvedByAdmin: true, active: true, playCount: 0,
      usageCount: 0, trendScore: 0, saveCount: 0, shareCount: 0
    }, 12);
    expect(videoMusicAttribution(source, DEFAULT_VIDEO_MIX_SETTINGS, 18)).toMatchObject({
      track_id: "12", catalog_source: "catalog", start_offset_seconds: 12, duration_used_seconds: 18
    });
  });

  it("maps user-facing maximum monitoring level below unity for headroom", () => {
    expect(safeMonitorVolume(1)).toBe(0.72);
    expect(safeMonitorVolume(5)).toBe(0.72);
    expect(safeMonitorVolume(-1)).toBe(0);
  });
});
