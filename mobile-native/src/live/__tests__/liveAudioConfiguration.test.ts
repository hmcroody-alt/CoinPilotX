import {
  describeLiveAudioFailure,
  initializeLivePublisherMedia,
  resolveLiveAudioConfiguration,
  shouldForceLiveSpeakerRoute,
  stabilizeLivePublisherAudio,
  stabilizeLiveRemotePlayback,
  stabilizeLiveViewerAudio
} from "../useLiveBroadcastRoom";
import { isLiveAudioV2EnabledForSession, resolveLiveAudioPathForSession } from "../liveAudioFlags";

describe("emergency publisher V2 rollback", () => {
  it("holds publishers on the stable path when the general V2 flag is on", () => {
    const flags = { audioV2Enabled: true };
    expect(isLiveAudioV2EnabledForSession(flags, true)).toBe(false);
    expect(resolveLiveAudioPathForSession(flags, true)).toBe("v1_legacy");
  });

  it("requires an additional explicit server opt-in for publisher V2", () => {
    const flags = { audioV2Enabled: true, publisherAudioV2Enabled: true };
    expect(isLiveAudioV2EnabledForSession(flags, true)).toBe(true);
    expect(resolveLiveAudioPathForSession(flags, true)).toBe("v2_isolated");
  });

  it("does not change the listen-only viewer rollout", () => {
    expect(isLiveAudioV2EnabledForSession({ audioV2Enabled: true }, false)).toBe(true);
  });
});

/**
 * Regression guard for the production livestream-audio P0: native calls already
 * have working bidirectional audio, so Live must use the same call-grade iOS
 * session instead of a media-playback-only profile that can be interrupted by
 * Reels/Radio/media playback while LiveKit is rendering video.
 *
 * The contract these tests lock in:
 *   - host/co-host records, so it MUST use `playAndRecord`.
 *   - listen-only viewer uses the call-grade communication output route but
 *     never publishes microphone ownership.
 */
describe("resolveLiveAudioConfiguration", () => {
  it("gives a publisher a record-capable communication session", () => {
    const config = resolveLiveAudioConfiguration(true);
    expect(config.audioCategory).toBe("playAndRecord");
    expect(config.audioMode).toBe("videoChat");
    // defaultToSpeaker is only valid alongside playAndRecord.
    expect(config.audioCategoryOptions).toContain("defaultToSpeaker");
  });

  it("gives a listen-only viewer the call-grade output route without publisher ownership", () => {
    const config = resolveLiveAudioConfiguration(false);
    expect(config.audioCategory).toBe("playAndRecord");
    expect(config.audioMode).toBe("videoChat");
    expect(config.audioCategoryOptions).toContain("defaultToSpeaker");
  });

  it("routes both roles to Bluetooth/AirPlay outputs", () => {
    for (const publish of [true, false]) {
      const config = resolveLiveAudioConfiguration(publish);
      expect(config.audioCategoryOptions).toEqual(
        expect.arrayContaining(["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay"])
      );
    }
  });

  it("keeps every Live role on an audible speaker route", () => {
    expect(shouldForceLiveSpeakerRoute()).toBe(true);
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
    const audioSession = {
      selectAudioOutput: jest.fn().mockResolvedValue(undefined),
      setAppleAudioConfiguration: jest.fn().mockResolvedValue(undefined)
    };

    const result = await stabilizeLiveViewerAudio(audioDevice.module, audioSession, { settleMs: 0 });

    expect(result.playoutRunning).toBe(true);
    expect(audioDevice.module.startPlayout).toHaveBeenCalledTimes(1);
    expect(audioDevice.module.startRecording).not.toHaveBeenCalled();
    expect(audioSession.setAppleAudioConfiguration).toHaveBeenCalledWith(expect.objectContaining({
      audioCategory: "playAndRecord",
      audioMode: "videoChat"
    }));
    expect(audioSession.selectAudioOutput).toHaveBeenCalledWith("force_speaker");
  });

  it("uses the call-grade viewer path to enable remote Live audio and restart playout", async () => {
    const audioDevice = audioDeviceModule();
    const track = { kind: "audio", setEnabled: jest.fn().mockResolvedValue(undefined) };
    const room = {
      remoteParticipants: new Map([
        ["host", { audioTrackPublications: new Map([["mic", { track }]]) }]
      ])
    };
    const audioSession = {
      selectAudioOutput: jest.fn().mockResolvedValue(undefined),
      setAppleAudioConfiguration: jest.fn().mockResolvedValue(undefined)
    };

    const result = await stabilizeLiveRemotePlayback(room, audioDevice.module, audioSession, true, { settleMs: 0 });

    expect(result.remoteAudioTrackCount).toBe(1);
    expect(track.setEnabled).toHaveBeenCalledWith(true);
    expect(audioDevice.values()).toEqual({ engine: true, playing: true, recording: false });
    expect(audioSession.setAppleAudioConfiguration).toHaveBeenCalledWith(expect.objectContaining({
      audioCategory: "playAndRecord",
      audioMode: "videoChat"
    }));
    expect(audioSession.selectAudioOutput).toHaveBeenCalledWith("force_speaker");
  });

  it("does not run the fail-closed engine guard until after camera publication", async () => {
    const events: string[] = [];

    await initializeLivePublisherMedia({
      useV2: true,
      publishMicrophone: async () => {
        events.push("microphone");
        return 1;
      },
      enableCamera: async () => {
        events.push("camera");
      },
      stabilizeAudio: async () => {
        events.push("guard");
        return 1;
      },
    });

    expect(events).toEqual(["microphone", "camera", "microphone", "guard"]);
  });

  it("runs legacy publishers through the same call-grade post-camera stabilizer", async () => {
    const events: string[] = [];

    await initializeLivePublisherMedia({
      useV2: false,
      publishMicrophone: async () => {
        events.push("microphone");
        return 1;
      },
      enableCamera: async () => {
        events.push("camera");
      },
      stabilizeAudio: async () => {
        events.push("guard");
        return 1;
      }
    });

    expect(events).toEqual(["microphone", "camera", "microphone", "guard"]);
  });

  it("uses the video-call single stabilization boundary", async () => {
    const events: string[] = [];
    let guardAttempts = 0;
    await expect(initializeLivePublisherMedia({
      useV2: true,
      publishMicrophone: async () => 1,
      enableCamera: async () => undefined,
      stabilizeAudio: async () => {
        guardAttempts += 1;
        throw new Error("The native real-time audio engine did not remain active.");
      },
      trace: (event) => events.push(event)
    })).rejects.toThrow("did not remain active");
    expect(guardAttempts).toBe(1);
    expect(events).toEqual([
      "microphone_track_create_started",
      "microphone_publish_started",
      "microphone_track_created",
      "microphone_published",
      "camera_initialization_started",
      "camera_initialized",
      "live_audio_active_verification_started",
      "live_audio_active_verification_failed"
    ]);
  });

});

/**
 * The copy shown when the audio guard fails.
 *
 * It was one hardcoded sentence blaming camera startup, shown for every stage.
 * A host whose audio never came up at connect was told the camera was at fault,
 * which sends the user to the wrong workaround and the engineer to the wrong
 * part of the log.
 */
describe("live audio failure copy", () => {
  it("names the stage that actually failed", () => {
    expect(describeLiveAudioFailure("camera_start")).toContain("camera started");
    expect(describeLiveAudioFailure("session_start")).toContain("could not start on this device");
    expect(describeLiveAudioFailure("room_connected")).toContain("after connecting");
    expect(describeLiveAudioFailure("app_foreground")).toContain("returned to the foreground");
    expect(describeLiveAudioFailure("route_change")).toContain("audio output changed");
    expect(describeLiveAudioFailure("track_subscribed")).toContain("joining the stream");
  });

  // The specific bug: a non-camera failure must not claim the camera.
  it("does not blame the camera for a failure that happened elsewhere", () => {
    for (const stage of ["session_start", "room_connected", "app_foreground", "route_change", "unspecified"]) {
      expect(describeLiveAudioFailure(stage)).not.toContain("camera");
    }
  });

  // A stage added later but not yet given copy must degrade to a vague-but-true
  // sentence, never to a specific-but-wrong one and never to an empty string.
  it("falls back to a true general sentence for an unknown or missing stage", () => {
    for (const stage of ["unspecified", "something_new", undefined, null]) {
      const message = describeLiveAudioFailure(stage as any);
      expect(message).toBe("Broadcast audio could not stay active. Please try going live again.");
    }
  });

  // Engine internals belong in telemetry. A user cannot act on an error code.
  it("never surfaces engine internals, and always offers the one available action", () => {
    for (const stage of ["camera_start", "session_start", "room_connected", "unspecified"]) {
      const message = describeLiveAudioFailure(stage);
      expect(message).not.toMatch(/REALTIME_AUDIO|AVAudioEngine|ADM|engine=|native/i);
      expect(message).toContain("Please try going live again.");
    }
  });
});
