import {
  resolveLiveAudioConfiguration,
  stabilizeLivePublisherAudio,
  stabilizeLiveViewerAudio
} from "../useLiveBroadcastRoom";

/**
 * Regression guard for the production livestream-audio P0: native calls already
 * have working bidirectional audio, so Live must use the same call-grade iOS
 * session instead of a media-playback-only profile that can be interrupted by
 * Reels/Radio/media playback while LiveKit is rendering video.
 *
 * The contract these tests lock in:
 *   - host/co-host records, so it MUST use `playAndRecord`.
 *   - listen-only viewer uses playback and never needs microphone ownership.
 */
describe("resolveLiveAudioConfiguration", () => {
  it("gives a publisher a record-capable communication session", () => {
    const config = resolveLiveAudioConfiguration(true);
    expect(config.audioCategory).toBe("playAndRecord");
    expect(config.audioMode).toBe("videoChat");
    // defaultToSpeaker is only valid alongside playAndRecord.
    expect(config.audioCategoryOptions).toContain("defaultToSpeaker");
  });

  it("gives a listen-only viewer a playback-only output route", () => {
    const config = resolveLiveAudioConfiguration(false);
    expect(config.audioCategory).toBe("playback");
    expect(config.audioMode).toBe("default");
    expect(config.audioCategoryOptions).not.toContain("defaultToSpeaker");
  });

  it("routes both roles to Bluetooth/AirPlay outputs", () => {
    for (const publish of [true, false]) {
      const config = resolveLiveAudioConfiguration(publish);
      expect(config.audioCategoryOptions).toEqual(
        expect.arrayContaining(["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay"])
      );
    }
  });
});

describe("post-camera Live audio stabilization", () => {
  function audioDeviceModule() {
    let engine = false;
    let playing = false;
    let recording = false;
    return {
      module: {
        isEngineRunning: jest.fn(() => engine),
        isPlaying: jest.fn(() => playing),
        isRecording: jest.fn(() => recording),
        startPlayout: jest.fn(async () => { engine = true; playing = true; }),
        startRecording: jest.fn(async () => { engine = true; recording = true; })
      },
      values: () => ({ engine, playing, recording })
    };
  }

  it("reasserts one host microphone and restores both recording and playout", async () => {
    const track = { kind: "audio", setEnabled: jest.fn().mockResolvedValue(undefined) };
    const participant = {
      audioTrackPublications: new Map([["mic", { kind: "audio", track }]]),
      setMicrophoneEnabled: jest.fn().mockResolvedValue(undefined)
    };
    const audioDevice = audioDeviceModule();
    const audioSession = { selectAudioOutput: jest.fn().mockResolvedValue(undefined) };

    const result = await stabilizeLivePublisherAudio(
      { localParticipant: participant },
      audioDevice.module,
      audioSession,
      { settleMs: 0 }
    );

    expect(result.audioTrackCount).toBe(1);
    expect(participant.setMicrophoneEnabled).toHaveBeenCalledWith(true);
    expect(track.setEnabled).toHaveBeenCalledWith(true);
    expect(audioDevice.values()).toEqual({ engine: true, playing: true, recording: true });
    expect(audioSession.selectAudioOutput).toHaveBeenCalledWith("force_speaker");
  });

  it("restores viewer playout without ever starting microphone recording", async () => {
    const audioDevice = audioDeviceModule();
    const audioSession = { selectAudioOutput: jest.fn().mockResolvedValue(undefined) };

    const result = await stabilizeLiveViewerAudio(audioDevice.module, audioSession, { settleMs: 0 });

    expect(result.playoutRunning).toBe(true);
    expect(audioDevice.module.startPlayout).toHaveBeenCalledTimes(1);
    expect(audioDevice.module.startRecording).not.toHaveBeenCalled();
    expect(audioSession.selectAudioOutput).toHaveBeenCalledWith("force_speaker");
  });

});
