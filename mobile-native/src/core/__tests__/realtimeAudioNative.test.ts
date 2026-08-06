import { NativeModules, Platform } from "react-native";

import {
  NATIVE_AUDIO_ENGINE_LOG_FILTERS,
  describeNativeAudioEngineState,
  drainNativeAudioEngineLogs,
  hasNativeAudioEngineDiagnostics,
  isStaleRecordingWithoutEngine,
  readNativeAudioEngineState,
  startNativeAudioEngineLogCapture,
  stopNativeAudioEngineLogCapture,
  summarizeNativeAudioEngineLogs
} from "../realtimeAudioNative";

const modules = NativeModules as Record<string, unknown>;
const originalWebRTCModule = modules.WebRTCModule;

function installBridge(bridge: Record<string, unknown> | undefined) {
  modules.WebRTCModule = bridge;
}

function healthyNativeState() {
  return {
    outputEnabled: true,
    outputRunning: true,
    inputEnabled: true,
    inputRunning: true,
    inputMuted: false,
    muteMode: 0,
    playoutInitialized: true,
    recordingInitialized: true,
    playing: true,
    recording: true,
    engineRunning: true,
    microphoneMuted: false,
    recordingAlwaysPrepared: false,
    manualRendering: false,
    inputAvailable: true,
    outputAvailable: true
  };
}

afterEach(() => {
  installBridge(originalWebRTCModule as Record<string, unknown> | undefined);
  Platform.OS = "ios";
});

describe("native audio engine bridge availability", () => {
  // A JS bundle can be newer than the native binary it runs in (that is the
  // normal state during an OTA update). Every reader must degrade to a neutral
  // value there rather than throw, or a missing DIAGNOSTIC would break the
  // BROADCAST it was added to explain.
  it("reports unavailable and stays silent when the patched bridge is missing", () => {
    installBridge({});
    expect(hasNativeAudioEngineDiagnostics()).toBe(false);
    expect(readNativeAudioEngineState()).toBeNull();
    expect(startNativeAudioEngineLogCapture()).toBe(false);
    expect(stopNativeAudioEngineLogCapture()).toBe(false);
    expect(drainNativeAudioEngineLogs()).toEqual({ entries: [], dropped: 0 });
  });

  it("never touches the bridge on Android", () => {
    const getEngineState = jest.fn(() => healthyNativeState());
    installBridge({ audioDeviceModuleGetEngineState: getEngineState });
    Platform.OS = "android";
    expect(hasNativeAudioEngineDiagnostics()).toBe(false);
    expect(readNativeAudioEngineState()).toBeNull();
    expect(getEngineState).not.toHaveBeenCalled();
  });

  it("swallows a throwing native reader instead of failing the caller", () => {
    installBridge({
      audioDeviceModuleGetEngineState: () => {
        throw new Error("bridge exploded");
      }
    });
    expect(hasNativeAudioEngineDiagnostics()).toBe(true);
    expect(readNativeAudioEngineState()).toBeNull();
  });
});

describe("reading native engine state", () => {
  it("maps every field of the native dictionary", () => {
    installBridge({ audioDeviceModuleGetEngineState: () => healthyNativeState() });
    expect(readNativeAudioEngineState()).toEqual(healthyNativeState());
  });

  // The bridge marshals BOOLs; a value that arrives as 0/1 must not read as a
  // truthy object or a silently-false boolean.
  it("accepts numeric booleans from the bridge", () => {
    installBridge({
      audioDeviceModuleGetEngineState: () => ({ ...healthyNativeState(), inputRunning: 0, engineRunning: 1 })
    });
    const state = readNativeAudioEngineState();
    expect(state?.inputRunning).toBe(false);
    expect(state?.engineRunning).toBe(true);
  });
});

describe("isStaleRecordingWithoutEngine", () => {
  // This is the exact state captured on iPhone P3r7or: the ADM answers
  // `isRecording === true` because the capture path is still ENABLED, while the
  // AVAudioEngine underneath it is not RUNNING. A broadcast in this state
  // publishes silence, so naming it here keeps the guard, the recovery path and
  // the tests from drifting apart on what the failure is.
  it("detects a capture path left enabled over a dead engine", () => {
    expect(
      isStaleRecordingWithoutEngine({
        ...healthyNativeState(),
        engineRunning: false,
        inputRunning: false,
        outputRunning: false
      })
    ).toBe(true);
  });

  it("detects a persistently initialized recorder over a dead engine", () => {
    expect(
      isStaleRecordingWithoutEngine({
        ...healthyNativeState(),
        engineRunning: false,
        inputEnabled: false,
        inputRunning: false,
        recordingInitialized: true,
        recordingAlwaysPrepared: true
      })
    ).toBe(true);
  });

  it("does not fire for a healthy running engine", () => {
    expect(isStaleRecordingWithoutEngine(healthyNativeState())).toBe(false);
  });

  // An engine that is running with input live is the success case, and an
  // engine that was never set up at all is a different failure (no capture was
  // ever requested) that must not be routed into the stale-recorder repair.
  it("does not fire for an engine that was never initialized", () => {
    expect(
      isStaleRecordingWithoutEngine({
        ...healthyNativeState(),
        engineRunning: false,
        inputEnabled: false,
        inputRunning: false,
        recordingInitialized: false,
        playoutInitialized: false
      })
    ).toBe(false);
  });

  it("returns false rather than guessing when the bridge is unavailable", () => {
    expect(isStaleRecordingWithoutEngine(null)).toBe(false);
  });
});

describe("describeNativeAudioEngineState", () => {
  // Collapsing enabled and running back into one token here would destroy the
  // only distinction this bridge exists to expose.
  it("renders enabled and running separately for input and output", () => {
    const described = describeNativeAudioEngineState({
      ...healthyNativeState(),
      engineRunning: false,
      inputRunning: false,
      outputEnabled: false,
      outputRunning: false,
      recordingAlwaysPrepared: true
    });
    expect(described).toContain("nativeIn=en/stop");
    expect(described).toContain("nativeOut=dis/stop");
    expect(described).toContain("alwaysPrepared=true");
  });

  it("says so explicitly when there is no native reading", () => {
    expect(describeNativeAudioEngineState(null)).toBe("native=unavailable");
  });
});

describe("native engine log capture", () => {
  it("passes the audio-only filter list and a bounded capacity", () => {
    const start = jest.fn(() => true);
    installBridge({ audioDeviceModuleStartEngineLogCapture: start });
    expect(startNativeAudioEngineLogCapture()).toBe(true);
    const [filters, capacity] = start.mock.calls[0] as unknown as [string[], number];
    expect(filters).toEqual([...NATIVE_AUDIO_ENGINE_LOG_FILTERS]);
    expect(filters).toContain("Failed to start engine");
    expect(capacity).toBeGreaterThan(0);
    expect(capacity).toBeLessThanOrEqual(1000);
  });

  it("shapes drained entries and drops malformed ones", () => {
    installBridge({
      audioDeviceModuleDrainEngineLogCapture: () => ({
        entries: [
          { severity: 3, message: "Failed to start engine: -10875" },
          { severity: 1, message: "" },
          { severity: 2 },
          null
        ],
        dropped: 4
      })
    });
    expect(drainNativeAudioEngineLogs()).toEqual({
      entries: [{ severity: 3, message: "Failed to start engine: -10875" }],
      dropped: 4
    });
  });
});

describe("summarizeNativeAudioEngineLogs", () => {
  // Telemetry lines are truncated downstream, so a summary that leads with
  // chatty info lines and pushes the error off the end is worse than none.
  it("puts the highest severity first and honours the limit", () => {
    const summary = summarizeNativeAudioEngineLogs(
      {
        entries: [
          { severity: 1, message: "Starting AVAudioEngine" },
          { severity: 3, message: "Failed to start engine: -10875" },
          { severity: 2, message: "No input path." }
        ],
        dropped: 0
      },
      2
    );
    expect(summary).toBe("nativeLogs=[3]Failed to start engine: -10875 | [2]No input path.");
  });

  it("reports how many lines the ring buffer discarded", () => {
    expect(summarizeNativeAudioEngineLogs({ entries: [], dropped: 12 })).toBe("nativeLogs=none(+12 dropped)");
    expect(summarizeNativeAudioEngineLogs({ entries: [], dropped: 0 })).toBe("nativeLogs=none");
  });
});
