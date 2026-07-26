jest.mock("@react-native-async-storage/async-storage", () => ({
  getItem: jest.fn(),
  setItem: jest.fn(),
  removeItem: jest.fn()
}));

const mockPulseApi = jest.fn();

jest.mock("../pulseApi", () => ({
  pulseApi: (...args: unknown[]) => mockPulseApi(...args)
}));

import {
  confirmHostLivePublish,
  confirmGuestPublishComplete,
  getLiveJoinStatus,
  livePlaybackUrl,
  liveSupportsNativePlayback,
  liveSupportsNativeWebRtc,
  normalizeLiveItem,
  normalizeLiveState,
  requestToJoinLive
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

  it("normalizes LiveKit-direct active state as native WebRTC even while HLS is absent", () => {
    const live = normalizeLiveState(
      {
        live_id: 43,
        status: "live",
        publish_state: "browser_live_livekit_direct",
        playback: {
          status: "live",
          preferred_transport: "webrtc",
          supports_webrtc: true,
          webrtc_room_id: "pulse-live-43"
        },
        livekit: { room: "pulse-live-43" }
      },
      43
    );

    expect(live.status).toBe("live");
    expect(live.playback?.preferred_transport).toBe("webrtc");
    expect(livePlaybackUrl(live)).toBe("");
    expect(liveSupportsNativePlayback(live)).toBe(false);
    expect(liveSupportsNativeWebRtc(live)).toBe(true);
  });
});

describe("native Live guest publishing API", () => {
  beforeEach(() => {
    mockPulseApi.mockReset();
  });

  it("confirms native host published audio and video through the existing live publish route", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      status: "live",
      publish_path: "livekit_direct",
      audio_tracks: 1,
      video_tracks: 1,
      playback: {
        status: "live",
        supports_webrtc: true,
        preferred_transport: "webrtc",
        webrtc_room_id: "pulse-live-45"
      }
    });

    const result = await confirmHostLivePublish(45, { audioTracks: 1, videoTracks: 1, traceId: "native-host" });

    expect(result.ok).toBe(true);
    expect(result.publishPath).toBe("livekit_direct");
    expect(result.playback.status).toBe("live");
    expect(result.playback.supports_webrtc).toBe(true);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/live/45/native-publish", {
      method: "POST",
      body: JSON.stringify({
        source: "native",
        trace_id: "native-host",
        audio_tracks: 1,
        video_tracks: 1
      })
    });
  });

  it("requests a co-host seat through the production join-request route", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      status: "pending",
      request_id: 12,
      message: "Request sent. Waiting for host approval."
    });

    const result = await requestToJoinLive(44, { cameraReady: true, micReady: true, networkQuality: "good" });

    expect(result.request_id).toBe(12);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/live/44/join-request", {
      method: "POST",
      body: JSON.stringify({
        requested_role: "cohost",
        camera_ready: true,
        mic_ready: true,
        network_quality: "good"
      })
    });
  });

  it("normalizes join status with accepted guest data", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      status: "accepted",
      step: "host_accepted",
      can_publish: true,
      livekit_configured: true,
      token_url: "/api/pulse/live/44/livekit/token",
      request: { id: 12, live_id: 44, user_id: 8, status: "accepted" },
      guest: { id: 91, live_id: 44, user_id: 8, request_id: 12, status: "accepted", role: "cohost" }
    });

    const status = await getLiveJoinStatus(44);

    expect(status.canPublish).toBe(true);
    expect(status.guest?.guestId).toBe(91);
    expect(status.guest?.requestId).toBe(12);
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/live/44/join-status");
  });

  it("confirms published guest tracks through publish-complete", async () => {
    mockPulseApi.mockResolvedValueOnce({
      ok: true,
      status: "live",
      state: "live",
      step: "cohost_live",
      trace_id: "trace-44",
      guest: { id: 91, live_id: 44, user_id: 8, request_id: 12, status: "live", role: "cohost" }
    });

    const result = await confirmGuestPublishComplete(44, 91, {
      traceId: "trace-44",
      participantIdentity: "pulse-live-guest-8",
      audioPublicationSid: "audio-sid",
      videoPublicationSid: "video-sid"
    });

    expect(result.state).toBe("live");
    expect(result.guest?.status).toBe("live");
    expect(mockPulseApi).toHaveBeenCalledWith("/api/pulse/live/44/guests/91/publish-complete", {
      method: "POST",
      body: JSON.stringify({
        trace_id: "trace-44",
        participant_identity: "pulse-live-guest-8",
        room_connected: true,
        video_publication_sid: "video-sid",
        audio_publication_sid: "audio-sid"
      })
    });
  });
});
