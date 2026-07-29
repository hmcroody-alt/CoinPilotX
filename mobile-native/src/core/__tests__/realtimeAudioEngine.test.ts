import {
  activateRealtimeAudioSession,
  applyRemoteAudioEnabled,
  getActiveRealtimeAudioOwner,
  PULSE_LIVE_VIDEO_CAPTURE_OPTIONS,
  PULSE_LIVE_VIDEO_PUBLISH_OPTIONS,
  releaseRealtimeAudioSession,
  resolveRealtimeAudioConfiguration,
  selectRealtimeAudioOutput
} from "../realtimeAudioEngine";

function sdkTrack() {
  const calls: boolean[] = [];
  return {
    kind: "audio",
    setEnabledCalls: calls,
    setEnabled(value: boolean) {
      calls.push(value);
      return Promise.resolve();
    }
  };
}

describe("realtimeAudioEngine canonical audio ownership", () => {
  it("uses the call-grade iOS audio profile for calls, live host, live guest, and live viewer", () => {
    for (const mode of ["audio_call", "video_call", "live_host", "live_guest", "live_viewer"] as const) {
      const config = resolveRealtimeAudioConfiguration(mode);
      expect(config.audioCategory).toBe("playAndRecord");
      expect(config.audioMode).toBe("videoChat");
      expect(config.audioCategoryOptions).toEqual(
        expect.arrayContaining(["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay", "defaultToSpeaker"])
      );
    }
  });

  it("tracks one active realtime audio owner and only releases that owner", async () => {
    const audioSession = {
      setAppleAudioConfiguration: jest.fn().mockResolvedValue(undefined),
      configureAudio: jest.fn().mockResolvedValue(undefined),
      startAudioSession: jest.fn().mockResolvedValue(undefined),
      stopAudioSession: jest.fn().mockResolvedValue(undefined),
      selectAudioOutput: jest.fn().mockResolvedValue(undefined)
    };

    await activateRealtimeAudioSession(audioSession, "live_host", "live:1");
    expect(getActiveRealtimeAudioOwner()).toEqual(expect.objectContaining({ mode: "live_host", ownerId: "live:1" }));

    await expect(releaseRealtimeAudioSession(audioSession, "call:other")).resolves.toBe(false);
    expect(audioSession.stopAudioSession).not.toHaveBeenCalled();

    await expect(releaseRealtimeAudioSession(audioSession, "live:1")).resolves.toBe(true);
    expect(audioSession.stopAudioSession).toHaveBeenCalledTimes(1);
    expect(getActiveRealtimeAudioOwner()).toBeNull();
  });

  it("selects the native speaker route through the shared helper", async () => {
    const audioSession = { selectAudioOutput: jest.fn().mockResolvedValue(undefined) };
    await selectRealtimeAudioOutput(audioSession, true);
    expect(audioSession.selectAudioOutput).toHaveBeenCalledWith("force_speaker");
  });

  it("keeps viewer remote-audio state authoritative across every subscribed track", async () => {
    const a = sdkTrack();
    const b = sdkTrack();
    const room = {
      remoteParticipants: new Map([
        ["remote", { audioTrackPublications: new Map([["a", { track: a }], ["b", { track: b }]]) }]
      ])
    };

    await expect(applyRemoteAudioEnabled(room, false)).resolves.toBe(2);
    expect(a.setEnabledCalls).toEqual([false]);
    expect(b.setEnabledCalls).toEqual([false]);
  });
});

describe("realtimeAudioEngine Live camera defaults", () => {
  it("uses portrait front-camera capture so native Live does not start from landscape WebRTC defaults", () => {
    expect(PULSE_LIVE_VIDEO_CAPTURE_OPTIONS.facingMode).toBe("user");
    expect(PULSE_LIVE_VIDEO_CAPTURE_OPTIONS.frameRate).toBe(30);
    expect(PULSE_LIVE_VIDEO_CAPTURE_OPTIONS.resolution).toEqual(
      expect.objectContaining({
        width: 720,
        height: 1280,
        aspectRatio: 9 / 16
      })
    );
  });

  it("publishes a bounded premium Live video encoding instead of the uncapped default", () => {
    expect(PULSE_LIVE_VIDEO_PUBLISH_OPTIONS.simulcast).toBe(true);
    expect(PULSE_LIVE_VIDEO_PUBLISH_OPTIONS.videoEncoding).toEqual(
      expect.objectContaining({
        maxBitrate: 2_300_000,
        maxFramerate: 30
      })
    );
  });
});
