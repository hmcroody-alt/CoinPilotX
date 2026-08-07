import { stabilizeLiveAudioEngine } from "../../live-audio/liveAudioEngine";

/**
 * The wedge state this whole file is about.
 *
 * The engine is dead, but the ADM still answers `isRecording === true` because
 * always-prepared mode keeps the record path ENABLED across an engine stop. A
 * host in this state publishes silence.
 *
 * Neither of the guard's original repair branches could see it. The
 * stale-recorder branch reads the patched native bridge, which returns null on
 * any binary built without the WebRTC patch, and `isStaleRecordingWithoutEngine`
 * answers false for null by design - with no native reading there is no honest
 * way to call a recorder stale. The other branch requires `isRecording === false`,
 * and this state reports true. So the guard did nothing at all and then threw,
 * and the host was told the broadcast could not start.
 *
 * These tests drive the guard through the module's public surface, with no
 * native bridge mocked in - which is exactly the binary the failure was reported
 * on. `NativeModules.WebRTCModule` is absent under jest, so
 * `readNativeAudioEngineState()` returns null here for the same reason it does
 * on an unpatched device.
 */
function wedgedModule(options: { repairWorks: boolean }) {
  // `engineRunning` false and `isRecording` true: the wedge. `startLocalRecording`
  // is the only ADM call that both initialises and starts, so a repair that works
  // is one that reaches it.
  const state = { engine: false, recording: true, playing: false };
  const calls: string[] = [];
  return {
    calls,
    module: {
      isEngineRunning: () => state.engine,
      isRecording: () => state.recording,
      isPlaying: () => state.playing,
      isRecordingAlwaysPreparedMode: () => true,
      setRecordingAlwaysPreparedMode: (value: boolean) => {
        calls.push(`alwaysPrepared:${value}`);
        return Promise.resolve();
      },
      startPlayout: () => {
        calls.push("startPlayout");
        state.playing = true;
        return Promise.resolve();
      },
      stopRecording: () => {
        calls.push("stopRecording");
        state.recording = false;
        return Promise.resolve();
      },
      startLocalRecording: () => {
        calls.push("startLocalRecording");
        if (options.repairWorks) {
          state.recording = true;
          state.engine = true;
        }
        return Promise.resolve();
      },
      startRecording: () => {
        calls.push("startRecording");
        return Promise.resolve();
      }
    }
  };
}

describe("host guard repair without the native diagnostics bridge", () => {
  it("restarts a recorder wedged over a dead engine and lets the host go live", async () => {
    const { module, calls } = wedgedModule({ repairWorks: true });

    const status = await stabilizeLiveAudioEngine(module as any, {
      role: "HOST_AUDIO_VIDEO",
      playout: true,
      recording: true,
      requirePlayout: false,
      settleMs: 0,
      stage: "camera_start"
    });

    // The repair has to actually run. Before the fix the guard threw here having
    // called nothing but startPlayout, which no-ops on an uninitialised ADM.
    expect(calls).toContain("stopRecording");
    expect(calls).toContain("startLocalRecording");
    expect(status.engineRunning).toBe(true);
    expect(status.recordingRunning).toBe(true);
  });

  it("still fails when the repair does not take, so a silent host is never called healthy", async () => {
    const { module, calls } = wedgedModule({ repairWorks: false });

    await expect(
      stabilizeLiveAudioEngine(module as any, {
        role: "HOST_AUDIO_VIDEO",
        playout: true,
        recording: true,
        requirePlayout: false,
        settleMs: 0,
        stage: "camera_start"
      })
    ).rejects.toMatchObject({ code: "LIVE_AUDIO_ENGINE_INACTIVE", stage: "camera_start" });

    // Attempted, not skipped. The distinction matters: the old failure was the
    // guard declining every repair and reporting the untouched state as final.
    expect(calls).toContain("startLocalRecording");
  });

  it("never tears down a recorder that is genuinely capturing", async () => {
    // Engine running and recording running: healthy. The blind fallback is gated
    // on the engine being stopped precisely so it cannot reach this state - a
    // stopRecording here would create the outage it is supposed to repair.
    const calls: string[] = [];
    const healthy = {
      isEngineRunning: () => true,
      isRecording: () => true,
      isPlaying: () => true,
      stopRecording: () => {
        calls.push("stopRecording");
        return Promise.resolve();
      },
      startLocalRecording: () => {
        calls.push("startLocalRecording");
        return Promise.resolve();
      },
      startPlayout: () => Promise.resolve(),
      startRecording: () => Promise.resolve()
    };

    await stabilizeLiveAudioEngine(healthy as any, {
      role: "HOST_AUDIO_VIDEO",
      playout: true,
      recording: true,
      requirePlayout: false,
      settleMs: 0,
      stage: "camera_start"
    });

    expect(calls).not.toContain("stopRecording");
  });

  it("leaves the audience path alone - it must not start a microphone", async () => {
    // The blind fallback is guarded on `wantRecording`. A viewer that acquired a
    // capture path here would be a second live microphone in the room, which the
    // one-publisher invariant forbids outright.
    const calls: string[] = [];
    const viewer = {
      isEngineRunning: () => true,
      isRecording: () => false,
      isPlaying: () => true,
      stopRecording: () => {
        calls.push("stopRecording");
        return Promise.resolve();
      },
      startLocalRecording: () => {
        calls.push("startLocalRecording");
        return Promise.resolve();
      },
      startRecording: () => {
        calls.push("startRecording");
        return Promise.resolve();
      },
      startPlayout: () => Promise.resolve()
    };

    await stabilizeLiveAudioEngine(viewer as any, {
      role: "AUDIENCE",
      playout: true,
      recording: false,
      requirePlayout: true,
      settleMs: 0,
      stage: "track_subscribed"
    });

    expect(calls).toEqual([]);
  });
});
