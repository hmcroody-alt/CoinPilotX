jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

import {
  livePlaybackUrl,
  liveSupportsNativePlayback,
  liveSupportsNativeWebRtc,
  normalizeLiveItem,
  normalizeLiveState
} from "../live";

describe("native Live playback capability mapping", () => {
  it("keeps HLS lives on the native video path", () => {
    const live = normalizeLiveItem({
      id: 41,
      live_id: 41,
      playback: {
        hls_url: "https://stream.example/live.m3u8",
        preferred_transport: "hls"
      }
    });

    expect(livePlaybackUrl(live)).toBe("https://stream.example/live.m3u8");
    expect(liveSupportsNativePlayback(live)).toBe(true);
    expect(liveSupportsNativeWebRtc(live)).toBe(false);
  });

  it("treats WebRTC-only LiveKit rooms as native-capable without an HLS URL", () => {
    const live = normalizeLiveState(
      {
        live_id: 42,
        playback: {
          webrtc_room_id: "pulse-live-42",
          preferred_transport: "webrtc"
        },
        livekit: {
          room: "pulse-live-42"
        }
      },
      42
    );

    expect(livePlaybackUrl(live)).toBe("");
    expect(liveSupportsNativePlayback(live)).toBe(false);
    expect(liveSupportsNativeWebRtc(live)).toBe(true);
  });
});
