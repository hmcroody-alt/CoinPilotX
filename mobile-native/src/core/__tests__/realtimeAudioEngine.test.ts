import { NativeModules } from "react-native";

import { setRealtimeAudioTelemetrySink } from "../realtimeAudioTelemetry";
import {
  activateRealtimeAudioSession,
  applyRemoteAudioEnabled,
  enableRealtimeRecordingAlwaysPrepared,
  getActiveRealtimeAudioOwner,
  getActiveRealtimeMicrophoneOwner,
  PULSE_LIVE_VIDEO_CAPTURE_OPTIONS,
  PULSE_LIVE_VIDEO_PUBLISH_OPTIONS,
  reapplyRealtimeAudioConfiguration,
  reassertRealtimeMicrophone,
  REALTIME_AUDIO_HEALTH_PROFILES,
  realtimeAudioProfileFor,
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

  it("uses the call-grade iOS audio profile for calls and LiveKit real-time media", () => {
    for (const mode of ["audio_call", "video_call", "live_host", "live_guest", "live_viewer"] as const) {
      const config = resolveRealtimeAudioConfiguration(mode);
      expect(config.audioCategory).toBe("playAndRecord");
      expect(config.audioMode).toBe("videoChat");
      expect(config.audioCategoryOptions).toEqual(
        expect.arrayContaining(["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay", "defaultToSpeaker"])
      );
    }
  });

  it("does not treat a listen-only Live viewer as a microphone owner", () => {
    const config = resolveRealtimeAudioConfiguration("live_viewer");
    expect(config.audioCategory).toBe("playAndRecord");
    expect(config.audioMode).toBe("videoChat");
    expect(config.audioCategoryOptions).toContain("defaultToSpeaker");
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

  // Measured on iPhone P3r7or, 2026-08-05: three consecutive Live broadcasts
  // reported engine=true;recording=true;playout=false and were killed by this
  // guard even though the microphone was live. A Live host publishes to an
  // empty room, so iOS has no remote audio to render and AURemoteIO's output
  // side is legitimately not running. Requiring playout there was a red light
  // no healthy host could ever turn green.
  it("does not fail a Live host whose playout is down because no remote audio exists yet", async () => {
    const audioDeviceModule = {
      isEngineRunning: jest.fn(() => true),
      isPlaying: jest.fn(() => false),
      isRecording: jest.fn(() => true),
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startRecording: jest.fn().mockResolvedValue(undefined)
    };

    await expect(stabilizeRealtimeAudioEngine(audioDeviceModule, {
      playout: true,
      recording: true,
      requirePlayout: false,
      settleMs: 0
    })).resolves.toEqual({
      engineRunning: true,
      playoutRunning: false,
      recordingRunning: true
    });

    // Playout is still ATTEMPTED - a co-host can arrive at any moment and the
    // host must hear them. It is only the failure that is waived.
    expect(audioDeviceModule.startPlayout).toHaveBeenCalled();
  });

  it("still fails a Live host whose recording is down, so a silent broadcast cannot report healthy", async () => {
    const audioDeviceModule = {
      isEngineRunning: jest.fn(() => true),
      isPlaying: jest.fn(() => false),
      isRecording: jest.fn(() => false),
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startRecording: jest.fn().mockResolvedValue(undefined)
    };

    await expect(stabilizeRealtimeAudioEngine(audioDeviceModule, {
      playout: true,
      recording: true,
      requirePlayout: false,
      settleMs: 0
    })).rejects.toMatchObject({ code: "REALTIME_AUDIO_ENGINE_INACTIVE" });
  });

  it("keeps calls fail-closed on playout by default, because a caller who hears nothing has a broken call", async () => {
    const audioDeviceModule = {
      isEngineRunning: jest.fn(() => true),
      isPlaying: jest.fn(() => false),
      isRecording: jest.fn(() => true),
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startRecording: jest.fn().mockResolvedValue(undefined)
    };

    // No requirePlayout supplied: every existing call site must behave exactly
    // as it did before the option existed.
    await expect(stabilizeRealtimeAudioEngine(audioDeviceModule, {
      playout: true,
      recording: true,
      settleMs: 0
    })).rejects.toMatchObject({ code: "REALTIME_AUDIO_ENGINE_INACTIVE" });
  });

  // Measured on iPhone P3r7or, 2026-08-05, 17:49 and 17:50: two consecutive
  // Live broadcasts reported `engine=false;playout=false;recording=true`.
  // `recordingRunning` is an ADM-level flag that outlives the AVAudioEngine
  // dying underneath it, so that combination is a host publishing SILENCE, not
  // a healthy host. It must never pass, for any role, under any option.
  it("never lets recording=true mask engine=false for a Live host", async () => {
    const audioDeviceModule = {
      isEngineRunning: jest.fn(() => false),
      isPlaying: jest.fn(() => false),
      isRecording: jest.fn(() => true),
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startRecording: jest.fn().mockResolvedValue(undefined),
      startLocalRecording: jest.fn().mockResolvedValue(undefined)
    };

    await expect(stabilizeRealtimeAudioEngine(audioDeviceModule, {
      playout: true,
      recording: true,
      // The exact publisher options used by the Live host path. Relaxing
      // playout must not drag the engine requirement down with it.
      requirePlayout: false,
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

  // Formerly two tests against `recoverRealtimeRecordingEngine`, the second
  // recovery path that ran immediately before the guard with the same telemetry
  // context. It was deleted because it duplicated every guard line in the device
  // log while gating its repair on a condition the real failure never met. Its
  // behaviour is now the bounded loop inside the guard, so its coverage moves
  // here rather than disappearing with it.
  it("re-inits a torn-down recorder through the SDK init-and-start path, after re-activating the session", async () => {
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

    const order: string[] = [];
    audioDeviceModule.startLocalRecording.mockImplementation(async () => {
      order.push("startLocalRecording");
      engineRunning = true;
      recordingRunning = true;
    });
    const reactivateSession = jest.fn(async () => { order.push("reactivate"); });

    const status = await stabilizeRealtimeAudioEngine(audioDeviceModule, {
      playout: true,
      recording: true,
      settleMs: 0,
      reactivateSession
    });

    expect(status).toEqual({ engineRunning: true, playoutRunning: true, recordingRunning: true });
    expect(audioDeviceModule.startLocalRecording).toHaveBeenCalled();
    expect(audioDeviceModule.startPlayout).toHaveBeenCalled();
    // The inactive session must be re-activated BEFORE the recorder restart,
    // otherwise startLocalRecording runs against an inactive session and no-ops.
    expect(reactivateSession).toHaveBeenCalled();
    expect(order.indexOf("reactivate")).toBeLessThan(order.indexOf("startLocalRecording"));
  });

  it("leaves a healthy engine untouched and never reaches for the init-and-start path", async () => {
    const audioDeviceModule = {
      isEngineRunning: () => true,
      isPlaying: () => true,
      isRecording: () => true,
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startRecording: jest.fn().mockResolvedValue(undefined),
      startLocalRecording: jest.fn().mockResolvedValue(undefined)
    };

    await expect(
      stabilizeRealtimeAudioEngine(audioDeviceModule, { playout: true, recording: true, settleMs: 0 })
    ).resolves.toEqual({
      engineRunning: true,
      playoutRunning: true,
      recordingRunning: true
    });
    expect(audioDeviceModule.startLocalRecording).not.toHaveBeenCalled();
    expect(audioDeviceModule.startPlayout).not.toHaveBeenCalled();
    expect(audioDeviceModule.startRecording).not.toHaveBeenCalled();
  });

  it("enableRealtimeRecordingAlwaysPrepared toggles the ADM lever when present", async () => {
    const setRecordingAlwaysPreparedMode = jest.fn().mockResolvedValue(undefined);
    await expect(enableRealtimeRecordingAlwaysPrepared({ setRecordingAlwaysPreparedMode })).resolves.toBe(true);
    expect(setRecordingAlwaysPreparedMode).toHaveBeenCalledWith(true);

    // Missing native method or absent module must be a no-op, never a throw.
    await expect(enableRealtimeRecordingAlwaysPrepared({})).resolves.toBe(false);
    await expect(enableRealtimeRecordingAlwaysPrepared(null)).resolves.toBe(false);
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

/**
 * The bounded recovery loop that replaced the two-function `legacy_recover`.
 *
 * Every test here pins one of the four properties the incident turned on: the
 * guard emits ONE start and ONE terminal event (the duplicate-log symptom), it
 * cannot be talked into more than three passes (the "hide it with retries"
 * prohibition), it repairs `recording=true` over `engine=false` (the state that
 * previously matched no repair branch at all), and it never restarts a recorder
 * that is genuinely capturing (the regression that repair could introduce).
 */
describe("realtimeAudioEngine bounded recovery", () => {
  const events: any[] = [];
  const modules = NativeModules as Record<string, unknown>;
  const originalWebRTCModule = modules.WebRTCModule;

  beforeEach(() => {
    events.length = 0;
    setRealtimeAudioTelemetrySink((event) => events.push(event));
  });

  afterEach(() => {
    setRealtimeAudioTelemetrySink(null);
    modules.WebRTCModule = originalWebRTCModule;
  });

  /** Install the patched-bridge reading the guard uses to tell a corpse from a recorder. */
  function installNativeState(state: Record<string, unknown> | null) {
    modules.WebRTCModule = { audioDeviceModuleGetEngineState: () => state };
  }

  const named = (name: string) => events.filter((event) => event.name === name);
  const terminal = () =>
    events.filter((event) => event.name === "audio_engine_guard_completed" || event.name === "audio_engine_guard_failed");

  function healthyModule() {
    return {
      isEngineRunning: () => true,
      isPlaying: () => true,
      isRecording: () => true,
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startRecording: jest.fn().mockResolvedValue(undefined),
      startLocalRecording: jest.fn().mockResolvedValue(undefined),
      stopRecording: jest.fn().mockResolvedValue(undefined)
    };
  }

  /** An ADM that never comes back, however many times it is asked. */
  function deadModule() {
    return {
      isEngineRunning: () => false,
      isPlaying: () => false,
      isRecording: () => false,
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startRecording: jest.fn().mockResolvedValue(undefined),
      startLocalRecording: jest.fn().mockResolvedValue(undefined),
      stopRecording: jest.fn().mockResolvedValue(undefined)
    };
  }

  // The reported symptom was every guard line appearing twice, caused by a
  // second recovery function running ahead of the guard with an identical
  // telemetry context. One invocation must now produce one start and one verdict.
  it("emits exactly one guard start and one terminal event per invocation", async () => {
    await stabilizeRealtimeAudioEngine(healthyModule(), { playout: true, recording: true, settleMs: 0 });

    expect(named("audio_engine_guard_started")).toHaveLength(1);
    expect(terminal()).toHaveLength(1);
    expect(terminal()[0].name).toBe("audio_engine_guard_completed");
  });

  // A healthy engine must not burn the whole budget: the loop breaks on the
  // first satisfied pass, so a green start costs one attempt, not three.
  it("stops at the first satisfied pass instead of spending its whole budget", async () => {
    await stabilizeRealtimeAudioEngine(healthyModule(), { playout: true, recording: true, settleMs: 0 });

    expect(named("audio_engine_recovery_attempt")).toHaveLength(1);
    expect(named("audio_engine_recovery_attempt")[0].recoveryAttempt).toBe(1);
  });

  it("keeps working across the passes when the engine only recovers on a later one", async () => {
    let passes = 0;
    let engineRunning = false;
    const audioDeviceModule = {
      isEngineRunning: () => engineRunning,
      isPlaying: () => engineRunning,
      isRecording: () => engineRunning,
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startRecording: jest.fn(async () => {
        passes += 1;
        // Camera startup stops RemoteIO asynchronously, so the first attempt can
        // legitimately land inside the window and do nothing.
        if (passes >= 2) engineRunning = true;
      })
    };

    const status = await stabilizeRealtimeAudioEngine(audioDeviceModule, {
      playout: true,
      recording: true,
      settleMs: 0
    });

    expect(status.engineRunning).toBe(true);
    expect(named("audio_engine_recovery_attempt")).toHaveLength(2);
    expect(terminal()[0].name).toBe("audio_engine_guard_completed");
  });

  // The prohibition this pins: "Do not hide the defect with longer retries."
  // `settleMs` spaces the passes and nothing else - no caller can buy a fourth.
  it.each([0, 1, 40])("never exceeds three passes, at settleMs=%i", async (settleMs) => {
    const audioDeviceModule = deadModule();

    await expect(
      stabilizeRealtimeAudioEngine(audioDeviceModule, { playout: true, recording: true, settleMs })
    ).rejects.toMatchObject({ code: "REALTIME_AUDIO_ENGINE_INACTIVE" });

    expect(named("audio_engine_recovery_attempt")).toHaveLength(3);
    expect(named("audio_engine_recovery_attempt").map((event) => event.recoveryAttempt)).toEqual([1, 2, 3]);
    expect(named("audio_engine_guard_started")).toHaveLength(1);
    expect(terminal()).toHaveLength(1);
    expect(terminal()[0].name).toBe("audio_engine_guard_failed");
    expect(terminal()[0].failureCategory).toBe("native_engine_not_running");
  });

  // THE INCIDENT STATE. `recording=true` over `engine=false` matched neither
  // repair branch before, so the six observed passes did no work whatsoever.
  // `startRecording` cannot fix it either - the ADM short-circuits on a module
  // that already answers "recording". Only an explicit stop clears the stale
  // enable so init-and-start can rebuild the engine.
  it("repairs a stale recorder with an explicit stop before the init-and-start", async () => {
    installNativeState({
      engineRunning: false,
      inputEnabled: true,
      inputRunning: false,
      recordingInitialized: true,
      recording: true
    });

    const order: string[] = [];
    let engineRunning = false;
    let recording = true;
    const audioDeviceModule = {
      isEngineRunning: () => engineRunning,
      isPlaying: () => engineRunning,
      isRecording: () => recording,
      startPlayout: jest.fn(async () => { order.push("startPlayout"); }),
      startRecording: jest.fn(async () => { order.push("startRecording"); }),
      startLocalRecording: jest.fn(async () => {
        order.push("startLocalRecording");
        engineRunning = true;
        recording = true;
      }),
      stopRecording: jest.fn(async () => {
        order.push("stopRecording");
        recording = false;
      })
    };

    const status = await stabilizeRealtimeAudioEngine(audioDeviceModule, {
      playout: true,
      recording: true,
      requirePlayout: false,
      settleMs: 0
    });

    expect(status.engineRunning).toBe(true);
    expect(order.indexOf("stopRecording")).toBeGreaterThanOrEqual(0);
    expect(order.indexOf("stopRecording")).toBeLessThan(order.indexOf("startLocalRecording"));
    // `startRecording` would have short-circuited on the stale flag, which is
    // exactly why it must not be the path taken here.
    expect(audioDeviceModule.startRecording).not.toHaveBeenCalled();
  });

  // The reason the stop above was not enough on a real device.
  //
  // Always-prepared mode exists to keep the record path INITIALIZED across a
  // stop. With it on, `stopRecording()` returns without clearing the stale
  // enable, the restart short-circuits on an ADM that still answers "already
  // recording", and the engine is never rebuilt - the exact wedge this incident
  // is. The lever therefore has to come down before the stop.
  it("lowers always-prepared mode before the stop, or the stop cannot clear the stale enable", async () => {
    installNativeState({
      engineRunning: false,
      inputEnabled: true,
      inputRunning: false,
      recordingInitialized: true,
      recording: true,
      recordingAlwaysPrepared: true
    });

    const order: string[] = [];
    let engineRunning = false;
    const audioDeviceModule = {
      isEngineRunning: () => engineRunning,
      isPlaying: () => engineRunning,
      isRecording: () => true,
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startRecording: jest.fn().mockResolvedValue(undefined),
      startLocalRecording: jest.fn(async () => { order.push("startLocalRecording"); engineRunning = true; }),
      stopRecording: jest.fn(async () => { order.push("stopRecording"); }),
      setRecordingAlwaysPreparedMode: jest.fn(async (enabled: boolean) => {
        order.push(`alwaysPrepared:${enabled}`);
      })
    };

    await stabilizeRealtimeAudioEngine(audioDeviceModule, {
      playout: true,
      recording: true,
      requirePlayout: false,
      settleMs: 0
    });

    expect(order).toEqual(["alwaysPrepared:false", "stopRecording", "startLocalRecording", "alwaysPrepared:true"]);
  });

  // The lever is a mitigation, not a defect. Leaving it down after the repair
  // would trade this wedge for the silence it was added to prevent, so the
  // guard must hand it back exactly as it found it.
  it("restores always-prepared mode after the repair rather than leaving it down", async () => {
    installNativeState({
      engineRunning: false,
      inputEnabled: true,
      inputRunning: false,
      recordingInitialized: true,
      recording: true,
      recordingAlwaysPrepared: true
    });

    let engineRunning = false;
    const setRecordingAlwaysPreparedMode = jest.fn().mockResolvedValue(undefined);
    await stabilizeRealtimeAudioEngine({
      isEngineRunning: () => engineRunning,
      isPlaying: () => engineRunning,
      isRecording: () => true,
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startLocalRecording: jest.fn(async () => { engineRunning = true; }),
      stopRecording: jest.fn().mockResolvedValue(undefined),
      setRecordingAlwaysPreparedMode
    }, { playout: true, recording: true, requirePlayout: false, settleMs: 0 });

    expect(setRecordingAlwaysPreparedMode.mock.calls.map((call) => call[0])).toEqual([false, true]);
  });

  // A lever that is already down has nothing to restore, and moving it would be
  // a state change the guard was not asked to make.
  it("never touches the lever when always-prepared mode is already off", async () => {
    installNativeState({
      engineRunning: false,
      inputEnabled: true,
      inputRunning: false,
      recordingInitialized: true,
      recording: true,
      recordingAlwaysPrepared: false
    });

    let engineRunning = false;
    const setRecordingAlwaysPreparedMode = jest.fn().mockResolvedValue(undefined);
    const stopRecording = jest.fn().mockResolvedValue(undefined);
    await stabilizeRealtimeAudioEngine({
      isEngineRunning: () => engineRunning,
      isPlaying: () => engineRunning,
      isRecording: () => true,
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startLocalRecording: jest.fn(async () => { engineRunning = true; }),
      stopRecording,
      setRecordingAlwaysPreparedMode
    }, { playout: true, recording: true, requirePlayout: false, settleMs: 0 });

    expect(setRecordingAlwaysPreparedMode).not.toHaveBeenCalled();
    // The repair itself still runs - the lever is not what triggers it.
    expect(stopRecording).toHaveBeenCalled();
  });

  // A build without the lever must still be repaired. Gating the stop-and-start
  // on the lever would skip the repair precisely where the plain stop works.
  it("still repairs a stale recorder on a build that has no always-prepared lever", async () => {
    installNativeState({
      engineRunning: false,
      inputEnabled: true,
      inputRunning: false,
      recordingInitialized: true,
      recording: true
    });

    let engineRunning = false;
    const stopRecording = jest.fn().mockResolvedValue(undefined);
    const startLocalRecording = jest.fn(async () => { engineRunning = true; });
    const status = await stabilizeRealtimeAudioEngine({
      isEngineRunning: () => engineRunning,
      isPlaying: () => engineRunning,
      isRecording: () => true,
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startLocalRecording,
      stopRecording
    }, { playout: true, recording: true, requirePlayout: false, settleMs: 0 });

    expect(stopRecording).toHaveBeenCalled();
    expect(startLocalRecording).toHaveBeenCalled();
    expect(status.engineRunning).toBe(true);
  });

  // The tear-down that must NOT happen. `inputRunning === true` means the ADM is
  // genuinely delivering buffers; stopping it to "repair" the engine would cut
  // live audio that was working. The native reading is the only thing that can
  // tell this apart from the corpse above - both report `isRecording() === true`.
  it("never stops a recorder that the native bridge reports as genuinely capturing", async () => {
    installNativeState({
      engineRunning: false,
      inputEnabled: true,
      inputRunning: true,
      recordingInitialized: true,
      recording: true
    });

    const audioDeviceModule = {
      isEngineRunning: () => true,
      isPlaying: () => true,
      isRecording: () => true,
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startRecording: jest.fn().mockResolvedValue(undefined),
      startLocalRecording: jest.fn().mockResolvedValue(undefined),
      stopRecording: jest.fn().mockResolvedValue(undefined)
    };

    await stabilizeRealtimeAudioEngine(audioDeviceModule, { playout: true, recording: true, settleMs: 0 });

    expect(audioDeviceModule.stopRecording).not.toHaveBeenCalled();
    expect(audioDeviceModule.startLocalRecording).not.toHaveBeenCalled();
  });

  // Without a stage tag the camera-start guard and the room-connected guard
  // produced byte-identical lines, so two real events read as one logged twice.
  it("stamps the stage on every event the invocation emits", async () => {
    await stabilizeRealtimeAudioEngine(healthyModule(), {
      playout: true,
      recording: true,
      settleMs: 0,
      stage: "camera_start"
    });

    expect(events).not.toHaveLength(0);
    for (const event of events) expect(event.failureStage).toBe("camera_start");
  });

  it("falls back to an explicit unspecified stage rather than an absent field", async () => {
    await stabilizeRealtimeAudioEngine(healthyModule(), { playout: true, recording: true, settleMs: 0 });
    for (const event of events) expect(event.failureStage).toBe("unspecified");
  });

  // A role replaces the caller's triple outright. Merging them would let a call
  // site keep a private definition of "healthy" while claiming a role - which is
  // the drift the profiles exist to eliminate.
  it("lets the role override a contradictory hand-assembled requirement", async () => {
    const audioDeviceModule = {
      isEngineRunning: () => true,
      isPlaying: () => false,
      isRecording: () => true,
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startRecording: jest.fn().mockResolvedValue(undefined)
    };

    // The caller asks to fail closed on playout. HOST_AUDIO_VIDEO does not, and
    // the role wins, so a host publishing into an empty room is not killed.
    await expect(
      stabilizeRealtimeAudioEngine(audioDeviceModule, {
        role: "HOST_AUDIO_VIDEO",
        playout: true,
        recording: true,
        requirePlayout: true,
        settleMs: 0
      })
    ).resolves.toMatchObject({ engineRunning: true });

    // And the reverse: a caller that tries to relax a role that fails closed.
    await expect(
      stabilizeRealtimeAudioEngine(audioDeviceModule, {
        role: "CALL_PARTICIPANT",
        playout: true,
        recording: true,
        requirePlayout: false,
        settleMs: 0
      })
    ).rejects.toMatchObject({ code: "REALTIME_AUDIO_ENGINE_INACTIVE" });
  });

  // The one requirement that is not a profile field. Making it configurable is
  // how `recording=true` would be allowed to mask `engine=false`.
  it("requires a running engine for every role, including the relaxed host ones", async () => {
    for (const role of ["HOST_AUDIO_ONLY", "HOST_AUDIO_VIDEO", "AUDIENCE", "CALL_PARTICIPANT"] as const) {
      events.length = 0;
      const audioDeviceModule = {
        isEngineRunning: () => false,
        isPlaying: () => true,
        isRecording: () => true,
        startPlayout: jest.fn().mockResolvedValue(undefined),
        startRecording: jest.fn().mockResolvedValue(undefined)
      };
      await expect(
        stabilizeRealtimeAudioEngine(audioDeviceModule, { role, playout: true, recording: true, settleMs: 0 })
      ).rejects.toMatchObject({ code: "REALTIME_AUDIO_ENGINE_INACTIVE", role });
    }
  });

  // A viewer that records would open a second capture path and steal the session
  // from the host - the exact class of defect the audio policy forbids.
  it("never asks an AUDIENCE role to record", async () => {
    const audioDeviceModule = {
      isEngineRunning: () => true,
      isPlaying: () => true,
      isRecording: () => false,
      startPlayout: jest.fn().mockResolvedValue(undefined),
      startRecording: jest.fn().mockResolvedValue(undefined),
      startLocalRecording: jest.fn().mockResolvedValue(undefined),
      stopRecording: jest.fn().mockResolvedValue(undefined)
    };

    await stabilizeRealtimeAudioEngine(audioDeviceModule, {
      role: "AUDIENCE",
      playout: true,
      recording: true,
      settleMs: 0
    });

    expect(audioDeviceModule.startRecording).not.toHaveBeenCalled();
    expect(audioDeviceModule.startLocalRecording).not.toHaveBeenCalled();
    expect(audioDeviceModule.stopRecording).not.toHaveBeenCalled();
  });

  // The error has to say WHERE, or the screen goes back to guessing "the camera".
  it("carries the failing stage and role on the thrown error", async () => {
    await expect(
      stabilizeRealtimeAudioEngine(deadModule(), {
        role: "HOST_AUDIO_VIDEO",
        playout: true,
        recording: true,
        settleMs: 0,
        stage: "room_connected"
      })
    ).rejects.toMatchObject({ stage: "room_connected", role: "HOST_AUDIO_VIDEO" });
  });

  // A failure line without the native reading is what left `engine=false`
  // unexplained for the whole incident.
  it("carries the native engine reading on the failure verdict", async () => {
    installNativeState({ engineRunning: false, inputEnabled: true, inputRunning: false, recordingInitialized: true });

    await expect(
      stabilizeRealtimeAudioEngine(deadModule(), { playout: true, recording: true, settleMs: 0 })
    ).rejects.toMatchObject({ code: "REALTIME_AUDIO_ENGINE_INACTIVE" });

    expect(terminal()[0].engineState).toContain("nativeIn=");
  });
});

describe("realtimeAudioEngine role health profiles", () => {
  const roles = ["HOST_AUDIO_ONLY", "HOST_AUDIO_VIDEO", "AUDIENCE", "CALL_PARTICIPANT"] as const;

  it("defines exactly the four roles the product has, and no others", () => {
    expect(Object.keys(REALTIME_AUDIO_HEALTH_PROFILES).sort()).toEqual([...roles].sort());
  });

  it("states a rationale for every profile, so a future relaxation has to argue with it", () => {
    for (const role of roles) {
      const profile = realtimeAudioProfileFor(role);
      expect(profile.role).toBe(role);
      expect(profile.rationale.length).toBeGreaterThan(40);
    }
  });

  // These four assertions ARE the policy. A change to any of them is a change to
  // what "healthy audio" means for that role, and should have to be argued for in
  // a diff to this test - not made quietly at whichever call site is failing.
  it("pins each role's requirements", () => {
    expect(realtimeAudioProfileFor("HOST_AUDIO_ONLY")).toMatchObject({
      playout: true,
      recording: true,
      requirePlayout: false
    });
    expect(realtimeAudioProfileFor("HOST_AUDIO_VIDEO")).toMatchObject({
      playout: true,
      recording: true,
      requirePlayout: false
    });
    expect(realtimeAudioProfileFor("AUDIENCE")).toMatchObject({
      playout: true,
      recording: false,
      requirePlayout: true
    });
    expect(realtimeAudioProfileFor("CALL_PARTICIPANT")).toMatchObject({
      playout: true,
      recording: true,
      requirePlayout: true
    });
  });

  // A viewer that records is a second capture path. There is no circumstance in
  // which that is correct, so it is asserted separately from the table above.
  it("never grants recording to the audience role", () => {
    expect(realtimeAudioProfileFor("AUDIENCE").recording).toBe(false);
  });

  it("is frozen, so a profile cannot be reshaped at runtime by a screen", () => {
    expect(Object.isFrozen(REALTIME_AUDIO_HEALTH_PROFILES)).toBe(true);
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
