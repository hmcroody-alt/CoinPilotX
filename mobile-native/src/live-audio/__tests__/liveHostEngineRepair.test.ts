import { NativeModules, Platform } from "react-native";

import { stabilizeLiveAudioEngine } from "../liveAudioEngine";
import { isStaleRecordingWithoutEngine } from "../liveAudioNative";

/**
 * The silent-host incident measured on iPhone P3r7or (2026-08-07).
 *
 * The broadcast looked healthy from every angle the app could see: the mic track
 * was created and published (`audioTrackCount: 1`), the AVAudioSession was
 * active and record-capable, the camera was publishing, and the audience saw
 * video. There was simply no sound.
 *
 * The ADM reported an impossible-looking pair - `engineRunning=false` with
 * `inputEnabled=true` AND `inputRunning=true` - which is what these tests pin
 * down. AVAudioEngine was stopped, so nothing was captured no matter what the
 * input flags claimed, and the guard's two defects below meant three full
 * recovery passes performed no repair at all.
 *
 * Unlike `src/live/__tests__/liveAudioDegrade.test.ts`, which deliberately runs
 * with NO native bridge, these tests mock the patched bridge in - the failure
 * only exists on a binary that HAS it, because the bridge's reading is what the
 * stale-recorder gate consults.
 */

const modules = NativeModules as Record<string, unknown>;
const originalWebRTCModule = modules.WebRTCModule;

/** The exact engine state read off the device during the silent broadcast. */
function silentHostNativeState() {
  return {
    outputEnabled: false,
    outputRunning: false,
    inputEnabled: true,
    inputRunning: true,
    inputMuted: false,
    muteMode: 0,
    playoutInitialized: false,
    recordingInitialized: true,
    playing: false,
    recording: true,
    engineRunning: false,
    microphoneMuted: false,
    recordingAlwaysPrepared: true,
    manualRendering: false,
    inputAvailable: true,
    outputAvailable: true
  };
}

function installBridge(
  state: (() => Record<string, unknown>) | null,
  initPlayout?: () => number
) {
  if (!state) {
    modules.WebRTCModule = {};
    return;
  }
  const bridge: Record<string, unknown> = { audioDeviceModuleGetEngineState: state };
  // Only attached when a test opts in, so the suites above keep exercising the
  // binary shape that shipped BEFORE the bridge existed.
  if (initPlayout) bridge.audioDeviceModuleInitPlayout = initPlayout;
  modules.WebRTCModule = bridge;
}

/**
 * A host ADM wedged in the captured state, with a mutable native reading so a
 * successful repair is visible to the next pass rather than looping forever.
 */
function silentHostModule(options: { repairWorks: boolean }) {
  const native = silentHostNativeState();
  const calls: string[] = [];
  installBridge(() => native);
  return {
    calls,
    native,
    module: {
      isEngineRunning: () => native.engineRunning,
      isRecording: () => native.recording,
      isPlaying: () => native.playing,
      isRecordingAlwaysPreparedMode: () => native.recordingAlwaysPrepared,
      setRecordingAlwaysPreparedMode: (value: boolean) => {
        calls.push(`alwaysPrepared:${value}`);
        native.recordingAlwaysPrepared = value;
        return Promise.resolve();
      },
      stopRecording: () => {
        calls.push("stopRecording");
        native.recording = false;
        native.inputEnabled = false;
        native.inputRunning = false;
        return Promise.resolve();
      },
      startLocalRecording: () => {
        calls.push("startLocalRecording");
        if (options.repairWorks) {
          native.recording = true;
          native.inputEnabled = true;
          native.inputRunning = true;
          native.engineRunning = true;
        }
        return Promise.resolve();
      },
      startRecording: () => {
        calls.push("startRecording");
        return Promise.resolve();
      },
      startPlayout: () => {
        calls.push("startPlayout");
        return Promise.resolve();
      },
      stopPlayout: () => {
        calls.push("stopPlayout");
        return Promise.resolve();
      }
    }
  };
}

const HOST_OPTIONS = {
  role: "HOST_AUDIO_VIDEO" as const,
  playout: true,
  recording: true,
  requirePlayout: false,
  settleMs: 0,
  stage: "app_foreground" as const
};

afterEach(() => {
  modules.WebRTCModule = originalWebRTCModule;
  Platform.OS = "ios";
});

describe("isStaleRecordingWithoutEngine", () => {
  // The regression. This predicate used to also require `inputRunning === false`,
  // so it answered FALSE for the one state it was written to catch, and the only
  // host repair in the guard became unreachable.
  it("detects the silent host: capture flags set over a stopped engine", () => {
    expect(isStaleRecordingWithoutEngine(silentHostNativeState())).toBe(true);
  });

  it("does not fire while the engine is actually running", () => {
    expect(isStaleRecordingWithoutEngine({ ...silentHostNativeState(), engineRunning: true })).toBe(false);
  });

  // A stopped engine that was never asked to capture is a different situation
  // and must not be routed into a recorder tear-down.
  it("does not fire when no capture path was ever set up", () => {
    expect(
      isStaleRecordingWithoutEngine({
        ...silentHostNativeState(),
        inputEnabled: false,
        inputRunning: false,
        recordingInitialized: false
      })
    ).toBe(false);
  });
});

describe("live host engine repair with the native bridge present", () => {
  it("repairs the silent host instead of declining every branch", async () => {
    const { module, calls } = silentHostModule({ repairWorks: true });

    const status = await stabilizeLiveAudioEngine(module as never, HOST_OPTIONS);

    // Before the fix this list was `["startPlayout"]` and nothing else, across
    // all three passes.
    expect(calls).toContain("stopRecording");
    expect(calls).toContain("startLocalRecording");
    expect(status.engineRunning).toBe(true);
    expect(status.recordingRunning).toBe(true);
  });

  // Always-prepared mode is what makes `stopRecording` a no-op, so it has to be
  // lowered for the repair and put back exactly as it was found.
  it("cycles always-prepared mode around the repair and restores it", async () => {
    const { module, calls, native } = silentHostModule({ repairWorks: true });

    await stabilizeLiveAudioEngine(module as never, HOST_OPTIONS);

    expect(calls.indexOf("alwaysPrepared:false")).toBeLessThan(calls.indexOf("stopRecording"));
    expect(calls).toContain("alwaysPrepared:true");
    expect(native.recordingAlwaysPrepared).toBe(true);
  });

  it("still fails when the repair does not take, so a silent host is never called healthy", async () => {
    const { module, calls } = silentHostModule({ repairWorks: false });

    await expect(stabilizeLiveAudioEngine(module as never, HOST_OPTIONS)).rejects.toMatchObject({
      code: "LIVE_AUDIO_ENGINE_INACTIVE"
    });

    expect(calls).toContain("startLocalRecording");
  });

  // The second defect. `startPlayout` asks for outputRunning without
  // outputEnabled, which ModifyEngineState rejects outright ("Output must be
  // enabled if running"). A Live host subscribes to nobody, so output stays
  // disabled for the whole broadcast and this call can never do anything but
  // log an error.
  it("does not ask a host with no output path to start playout", async () => {
    const { module, calls } = silentHostModule({ repairWorks: true });

    await stabilizeLiveAudioEngine(module as never, HOST_OPTIONS);

    expect(calls).not.toContain("startPlayout");
  });

  it("still starts playout once an output path actually exists", async () => {
    const { module, calls, native } = silentHostModule({ repairWorks: true });
    native.outputEnabled = true;

    await stabilizeLiveAudioEngine(module as never, HOST_OPTIONS);

    expect(calls).toContain("startPlayout");
  });

  // Without the patched bridge there is no reading to gate on, and suppressing
  // the call there would remove the only playout repair an unpatched binary has.
  it("attempts playout when the bridge cannot answer", async () => {
    const { module, calls } = silentHostModule({ repairWorks: true });
    installBridge(null);

    await stabilizeLiveAudioEngine(module as never, HOST_OPTIONS);

    expect(calls).toContain("startPlayout");
  });
});

/**
 * The root cause, on a binary that can actually fix it.
 *
 * The suites above pin the two guard defects, but neither could make the host
 * audible: with output disabled AVAudioEngine will not run at all, and a stopped
 * engine captures nothing however the input flags read. `initPlayout` is the
 * only call that enables output, and it reaches JS only through the patched
 * bridge mocked in here.
 */
describe("live host output-path enable", () => {
  /** As above, plus the initPlayout bridge and a mutable output reading. */
  function hostWithInitPlayout(options: { initResult: number }) {
    const native = silentHostNativeState();
    const calls: string[] = [];
    installBridge(
      () => native,
      () => {
        calls.push("initPlayout");
        if (options.initResult === 0) {
          native.outputEnabled = true;
          native.playoutInitialized = true;
        }
        return options.initResult;
      }
    );
    return {
      calls,
      native,
      module: {
        isEngineRunning: () => native.engineRunning,
        isRecording: () => native.recording,
        isPlaying: () => native.playing,
        isRecordingAlwaysPreparedMode: () => native.recordingAlwaysPrepared,
        setRecordingAlwaysPreparedMode: (value: boolean) => {
          native.recordingAlwaysPrepared = value;
          return Promise.resolve();
        },
        stopRecording: () => {
          calls.push("stopRecording");
          native.recording = false;
          native.inputEnabled = false;
          native.inputRunning = false;
          return Promise.resolve();
        },
        startLocalRecording: () => {
          calls.push("startLocalRecording");
          native.recording = true;
          native.inputEnabled = true;
          native.inputRunning = true;
          native.engineRunning = true;
          return Promise.resolve();
        },
        startRecording: () => Promise.resolve(),
        startPlayout: () => {
          calls.push("startPlayout");
          return Promise.resolve();
        },
        stopPlayout: () => Promise.resolve()
      }
    };
  }

  it("enables output on a host whose output was never enabled", async () => {
    const { module, calls, native } = hostWithInitPlayout({ initResult: 0 });

    await stabilizeLiveAudioEngine(module as never, HOST_OPTIONS);

    expect(calls).toContain("initPlayout");
    expect(native.outputEnabled).toBe(true);
  });

  // Ordering is the fix, not just the call. Restarting capture into an engine
  // that cannot run is precisely what every earlier pass did, and it is why they
  // all reported the state they started from.
  it("enables output BEFORE repairing the recorder", async () => {
    const { module, calls } = hostWithInitPlayout({ initResult: 0 });

    await stabilizeLiveAudioEngine(module as never, HOST_OPTIONS);

    // Assert presence before order: `indexOf` answers -1 for a call that never
    // happened, and -1 is less than every real index - so an ordering check
    // alone passes vacuously on the exact regression it is meant to catch.
    expect(calls).toContain("initPlayout");
    expect(calls.indexOf("initPlayout")).toBeLessThan(calls.indexOf("stopRecording"));
    expect(calls.indexOf("initPlayout")).toBeLessThan(calls.indexOf("startLocalRecording"));
  });

  // ModifyEngineState rejects outputRunning-without-outputEnabled, so the start
  // is only legal after init. Asking in the other order is the no-op that spent
  // every pass's single native call.
  it("starts playout only after init has enabled output", async () => {
    const { module, calls } = hostWithInitPlayout({ initResult: 0 });

    await stabilizeLiveAudioEngine(module as never, HOST_OPTIONS);

    expect(calls).toContain("startPlayout");
    expect(calls.indexOf("initPlayout")).toBeLessThan(calls.indexOf("startPlayout"));
  });

  // A failed init leaves output disabled, so the pair stays illegal and the
  // start must not be attempted anyway.
  it("does not chase playout when init fails", async () => {
    const { module, calls, native } = hostWithInitPlayout({ initResult: -1 });

    await stabilizeLiveAudioEngine(module as never, HOST_OPTIONS);

    expect(calls).toContain("initPlayout");
    expect(native.outputEnabled).toBe(false);
    expect(calls).not.toContain("startPlayout");
  });
});
