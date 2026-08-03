import {
  activateRealtimeAudioSession,
  applyRemoteAudioEnabled,
  getActiveRealtimeAudioOwner,
  getActiveRealtimeMicrophoneOwner,
  PULSE_LIVE_VIDEO_CAPTURE_OPTIONS,
  PULSE_LIVE_VIDEO_PUBLISH_OPTIONS,
  reapplyRealtimeAudioConfiguration,
  reassertRealtimeMicrophone,
  releaseRealtimeAudioSession,
  resetRealtimeAudioOwnership,
  resolveRealtimeAudioConfiguration,
  selectRealtimeAudioOutput,
  stabilizeRealtimeAudioEngine
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
  afterEach(async () => {
    await resetRealtimeAudioOwnership();
  });

  it("uses the call-grade iOS audio profile for calls and Live publishers", () => {
    for (const mode of ["audio_call", "video_call", "live_host", "live_guest"] as const) {
      const config = resolveRealtimeAudioConfiguration(mode);
      expect(config.audioCategory).toBe("playAndRecord");
      expect(config.audioMode).toBe("videoChat");
      expect(config.audioCategoryOptions).toEqual(
        expect.arrayContaining(["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay", "defaultToSpeaker"])
      );
    }
  });

  it("uses playback configuration for a listen-only Live viewer", () => {
    const config = resolveRealtimeAudioConfiguration("live_viewer");
    expect(config.audioCategory).toBe("playback");
    expect(config.audioMode).toBe("default");
    expect(config.audioCategoryOptions).not.toContain("defaultToSpeaker");
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

  it("rejects stale cleanup after a same-room owner rotates to a newer lease", async () => {
    const audioSession = {
      startAudioSession: jest.fn().mockResolvedValue(undefined),
      stopAudioSession: jest.fn().mockResolvedValue(undefined)
    };

    const first = await activateRealtimeAudioSession(audioSession, "audio_call", "call:room-1");
    const second = await activateRealtimeAudioSession(audioSession, "audio_call", "call:room-1");

    expect(second.leaseId).toBeGreaterThan(first.leaseId);
    await expect(releaseRealtimeAudioSession(audioSession, first)).resolves.toBe(false);
    expect(getActiveRealtimeAudioOwner()?.leaseId).toBe(second.leaseId);
    expect(audioSession.stopAudioSession).not.toHaveBeenCalled();

    await expect(releaseRealtimeAudioSession(audioSession, second)).resolves.toBe(true);
    expect(audioSession.stopAudioSession).toHaveBeenCalledTimes(1);
  });

  it("keeps the current displacement handler when stale cleanup is rejected", async () => {
    const audioSession = { startAudioSession: jest.fn().mockResolvedValue(undefined) };
    const currentDisplacement = jest.fn();
    const stale = await activateRealtimeAudioSession(audioSession, "live_host", "live:room-1");
    await activateRealtimeAudioSession(audioSession, "live_host", "live:room-1", { onDisplaced: currentDisplacement });

    await expect(releaseRealtimeAudioSession(audioSession, stale)).resolves.toBe(false);
    await activateRealtimeAudioSession(audioSession, "audio_call", "call:room-2");

    expect(currentDisplacement).toHaveBeenCalledTimes(1);
  });

  it("distinguishes viewer playback ownership from microphone ownership", async () => {
    const audioSession = { startAudioSession: jest.fn().mockResolvedValue(undefined) };
    await activateRealtimeAudioSession(audioSession, "live_viewer", "live:viewer:room-1");

    expect(getActiveRealtimeAudioOwner()?.mode).toBe("live_viewer");
    expect(getActiveRealtimeMicrophoneOwner()).toBeNull();
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

  it("restarts required playout and recording after camera startup stops the native engine", async () => {
    let engineRunning = false;
    let playoutRunning = false;
    let recordingRunning = false;
    const audioDeviceModule = {
      isEngineRunning: jest.fn(() => engineRunning),
      isPlaying: jest.fn(() => playoutRunning),
      isRecording: jest.fn(() => recordingRunning),
      startPlayout: jest.fn(async () => {
        engineRunning = true;
        playoutRunning = true;
      }),
      startRecording: jest.fn(async () => {
        engineRunning = true;
        recordingRunning = true;
      })
    };

    const status = await stabilizeRealtimeAudioEngine(audioDeviceModule, {
      playout: true,
      recording: true,
      settleMs: 0
    });

    expect(status).toEqual({ engineRunning: true, playoutRunning: true, recordingRunning: true });
    expect(audioDeviceModule.startPlayout).toHaveBeenCalledTimes(1);
    expect(audioDeviceModule.startRecording).toHaveBeenCalledTimes(1);
  });

  it("fails verification when the native engine remains stopped after repair", async () => {
    const audioDeviceModule = {
      isEngineRunning: jest.fn(() => false),
      isPlaying: jest.fn(() => false),
      isRecording: jest.fn(() => false),
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startRecording: jest.fn().mockResolvedValue(undefined)
    };

    await expect(stabilizeRealtimeAudioEngine(audioDeviceModule, {
      playout: true,
      recording: true,
      settleMs: 0
    })).rejects.toMatchObject({ code: "REALTIME_AUDIO_ENGINE_INACTIVE" });
  });

  it("reinitializes local recording when camera startup tears down the audio engine", async () => {
    let engineRunning = false;
    let playoutRunning = false;
    let recordingRunning = false;
    const audioDeviceModule = {
      isEngineRunning: jest.fn(() => engineRunning),
      isPlaying: jest.fn(() => playoutRunning),
      isRecording: jest.fn(() => recordingRunning),
      startPlayout: jest.fn(async () => {
        if (!engineRunning) throw new Error("playout requires an initialized engine");
        playoutRunning = true;
      }),
      startRecording: jest.fn(async () => {
        throw new Error("recording is no longer initialized");
      }),
      startLocalRecording: jest.fn(async () => {
        engineRunning = true;
        recordingRunning = true;
      })
    };

    await expect(stabilizeRealtimeAudioEngine(audioDeviceModule, {
      playout: true,
      recording: true,
      settleMs: 0
    })).resolves.toEqual({
      engineRunning: true,
      playoutRunning: true,
      recordingRunning: true
    });

    expect(audioDeviceModule.startLocalRecording).toHaveBeenCalledTimes(1);
    expect(audioDeviceModule.startRecording).not.toHaveBeenCalled();
    expect(audioDeviceModule.startPlayout).toHaveBeenCalledTimes(1);
  });

  it("re-establishes the audio session before restarting the ADM when the engine is stopped", async () => {
    const order: string[] = [];
    let engineRunning = false;
    const audioDeviceModule = {
      isEngineRunning: () => engineRunning,
      isPlaying: () => engineRunning,
      isRecording: () => engineRunning,
      startPlayout: jest.fn(async () => { order.push("startPlayout"); engineRunning = true; }),
      startRecording: jest.fn(async () => { order.push("startRecording"); engineRunning = true; })
    };
    const reactivateSession = jest.fn(async () => { order.push("reactivate"); });

    const status = await stabilizeRealtimeAudioEngine(audioDeviceModule, {
      playout: true,
      recording: true,
      settleMs: 0,
      reactivateSession
    });

    expect(status.engineRunning).toBe(true);
    expect(reactivateSession).toHaveBeenCalledTimes(1);
    // The record-capable session must be restored BEFORE the recorder restart,
    // otherwise startRecording runs against the camera-reconfigured session.
    expect(order[0]).toBe("reactivate");
    expect(order.indexOf("reactivate")).toBeLessThan(order.indexOf("startRecording"));
  });

  it("does not disturb a healthy running engine with a session re-activation", async () => {
    const audioDeviceModule = {
      isEngineRunning: () => true,
      isPlaying: () => true,
      isRecording: () => true,
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startRecording: jest.fn().mockResolvedValue(undefined)
    };
    const reactivateSession = jest.fn().mockResolvedValue(undefined);

    await stabilizeRealtimeAudioEngine(audioDeviceModule, {
      playout: true,
      recording: true,
      settleMs: 0,
      reactivateSession
    });

    expect(reactivateSession).not.toHaveBeenCalled();
    expect(audioDeviceModule.startRecording).not.toHaveBeenCalled();
  });

  it("reapplyRealtimeAudioConfiguration restores a record-capable publisher session", async () => {
    const audioSession = { setAppleAudioConfiguration: jest.fn().mockResolvedValue(undefined) };
    await reapplyRealtimeAudioConfiguration(audioSession, "live_host");
    expect(audioSession.setAppleAudioConfiguration).toHaveBeenCalledWith(
      expect.objectContaining({ audioCategory: "playAndRecord", audioMode: "videoChat" })
    );
  });

  it("reasserts an existing microphone without creating a second publication", async () => {
    const track = sdkTrack();
    const participant = {
      audioTrackPublications: new Map([["mic", { kind: "audio", track }]]),
      setMicrophoneEnabled: jest.fn().mockResolvedValue(undefined)
    };

    await expect(reassertRealtimeMicrophone({ localParticipant: participant })).resolves.toBe(1);

    expect(participant.setMicrophoneEnabled).toHaveBeenCalledWith(true);
    expect(track.setEnabledCalls).toEqual([true]);
    expect(participant.audioTrackPublications.size).toBe(1);
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
