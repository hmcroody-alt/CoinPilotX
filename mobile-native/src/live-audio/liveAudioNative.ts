import { NativeModules, Platform } from "react-native";

/**
 * Native audio-engine diagnostics.
 *
 * The three booleans the LiveKit JS wrapper exposes (`isPlaying`, `isRecording`,
 * `isEngineRunning`) collapse two distinct native concepts - ENABLED and RUNNING
 * - into one bit each. That is why a Live host can report `recording=true` while
 * `engine=false`: the ADM's record path is *enabled* (and, with always-prepared
 * mode on, stays enabled) while the underlying `AVAudioEngine` is not *running*.
 * A broadcast in that state publishes silence.
 *
 * `RTCAudioDeviceModule.engineState` carries both halves, plus the init-level
 * flags, and WebRTC already logs the exact `AVAudioEngine` start failure. Neither
 * reaches JS in the stock package, so both are bridged by
 * `patches/@livekit+react-native-webrtc+144.1.1.patch`.
 *
 * Every function here is read-only with respect to audio state and returns a
 * neutral value when the bridge is missing (Android, Expo Go, an older native
 * binary that predates the patch). A diagnostic must never change or block the
 * outcome it is describing.
 */

export type NativeAudioEngineState = {
  /** Render (playout) path is configured on the engine. */
  outputEnabled: boolean;
  /** Render path is actually pulling buffers. */
  outputRunning: boolean;
  /** Capture (record) path is configured on the engine. */
  inputEnabled: boolean;
  /** Capture path is actually delivering buffers. */
  inputRunning: boolean;
  inputMuted: boolean;
  muteMode: number;
  playoutInitialized: boolean;
  recordingInitialized: boolean;
  playing: boolean;
  recording: boolean;
  engineRunning: boolean;
  microphoneMuted: boolean;
  /** `setRecordingAlwaysPreparedMode(true)` keeps the record path enabled across engine stops. */
  recordingAlwaysPrepared: boolean;
  manualRendering: boolean;
  inputAvailable: boolean;
  outputAvailable: boolean;
};

export type NativeAudioLogEntry = {
  /** RTCLoggingSeverity: 0 verbose, 1 info, 2 warning, 3 error. */
  severity: number;
  message: string;
};

/**
 * Substrings a WebRTC log line must contain to be retained.
 *
 * Taken from the literals present in the shipped WebRTC binary. The list is
 * deliberately narrow: the native side captures nothing that does not match, so
 * widening this is the only way to retain more log traffic, and every entry here
 * is an audio-engine lifecycle or failure string that carries no user content.
 */
export const NATIVE_AUDIO_ENGINE_LOG_FILTERS = [
  "Failed to start engine",
  "No input path.",
  "SetEngineAvailability",
  "ReconfigureEngine",
  "engine state for interruption",
  "AudioEngineDevice",
  "AVAudioEngine",
  "InitAndStartRecording",
  "InitRecording",
  "InitPlayout"
] as const;

const LOG_CAPTURE_CAPACITY = 200;

type WebRTCEngineBridge = {
  audioDeviceModuleGetEngineState?: () => Record<string, unknown> | null;
  audioDeviceModuleStartEngineLogCapture?: (filters: string[], capacity: number) => boolean;
  audioDeviceModuleDrainEngineLogCapture?: () => { entries?: unknown; dropped?: unknown } | null;
  audioDeviceModuleStopEngineLogCapture?: () => boolean;
};

function bridge(): WebRTCEngineBridge | null {
  if (Platform.OS !== "ios") return null;
  const module = (NativeModules as Record<string, unknown>)?.WebRTCModule;
  return (module as WebRTCEngineBridge) ?? null;
}

function readBoolean(source: Record<string, unknown>, key: string): boolean {
  return source[key] === true || source[key] === 1;
}

/**
 * True when the patched native bridge is present in the running binary.
 *
 * A JS bundle can be newer than the native binary it is loaded into (that is the
 * normal state during an OTA update), so callers must not assume the bridge
 * exists just because this file does.
 */
export function hasNativeAudioEngineDiagnostics(): boolean {
  return typeof bridge()?.audioDeviceModuleGetEngineState === "function";
}

export function readNativeAudioEngineState(): NativeAudioEngineState | null {
  const reader = bridge()?.audioDeviceModuleGetEngineState;
  if (typeof reader !== "function") return null;
  let raw: Record<string, unknown> | null = null;
  try {
    raw = reader();
  } catch {
    return null;
  }
  if (!raw || typeof raw !== "object") return null;
  return {
    outputEnabled: readBoolean(raw, "outputEnabled"),
    outputRunning: readBoolean(raw, "outputRunning"),
    inputEnabled: readBoolean(raw, "inputEnabled"),
    inputRunning: readBoolean(raw, "inputRunning"),
    inputMuted: readBoolean(raw, "inputMuted"),
    muteMode: Number(raw.muteMode ?? -1),
    playoutInitialized: readBoolean(raw, "playoutInitialized"),
    recordingInitialized: readBoolean(raw, "recordingInitialized"),
    playing: readBoolean(raw, "playing"),
    recording: readBoolean(raw, "recording"),
    engineRunning: readBoolean(raw, "engineRunning"),
    microphoneMuted: readBoolean(raw, "microphoneMuted"),
    recordingAlwaysPrepared: readBoolean(raw, "recordingAlwaysPrepared"),
    manualRendering: readBoolean(raw, "manualRendering"),
    inputAvailable: readBoolean(raw, "inputAvailable"),
    outputAvailable: readBoolean(raw, "outputAvailable")
  };
}

export function startNativeAudioEngineLogCapture(): boolean {
  const start = bridge()?.audioDeviceModuleStartEngineLogCapture;
  if (typeof start !== "function") return false;
  try {
    return start([...NATIVE_AUDIO_ENGINE_LOG_FILTERS], LOG_CAPTURE_CAPACITY) === true;
  } catch {
    return false;
  }
}

/** Reads and clears the native buffer, so the same line is never reported twice. */
export function drainNativeAudioEngineLogs(): { entries: NativeAudioLogEntry[]; dropped: number } {
  const drain = bridge()?.audioDeviceModuleDrainEngineLogCapture;
  if (typeof drain !== "function") return { entries: [], dropped: 0 };
  try {
    const result = drain();
    const rawEntries = Array.isArray(result?.entries) ? result.entries : [];
    const entries = rawEntries
      .map((entry) => {
        const record = entry as Record<string, unknown> | null;
        const message = typeof record?.message === "string" ? record.message : "";
        return { severity: Number(record?.severity ?? 1), message };
      })
      .filter((entry) => entry.message.length > 0);
    return { entries, dropped: Number(result?.dropped ?? 0) };
  } catch {
    return { entries: [], dropped: 0 };
  }
}

export function stopNativeAudioEngineLogCapture(): boolean {
  const stop = bridge()?.audioDeviceModuleStopEngineLogCapture;
  if (typeof stop !== "function") return false;
  try {
    return stop() === true;
  } catch {
    return false;
  }
}

/**
 * Flatten the native state into the telemetry line.
 *
 * `input`/`output` are rendered as `enabled/running` pairs on purpose - that
 * distinction is the whole reason this bridge exists, and collapsing it back to
 * a single boolean here would defeat it.
 */
export function describeNativeAudioEngineState(state: NativeAudioEngineState | null): string {
  if (!state) return "native=unavailable";
  return (
    `nativeIn=${state.inputEnabled ? "en" : "dis"}/${state.inputRunning ? "run" : "stop"};` +
    `nativeOut=${state.outputEnabled ? "en" : "dis"}/${state.outputRunning ? "run" : "stop"};` +
    `nativeInit=play:${state.playoutInitialized},rec:${state.recordingInitialized};` +
    `alwaysPrepared=${state.recordingAlwaysPrepared};muteMode=${state.muteMode};` +
    `inputMuted=${state.inputMuted};manualRender=${state.manualRendering}`
  );
}

/**
 * Highest-severity native log lines, joined for a single telemetry field.
 *
 * Capped at `limit` entries because telemetry lines are truncated downstream and
 * a truncated diagnostic that drops the error line is worse than none.
 */
export function summarizeNativeAudioEngineLogs(
  logs: { entries: NativeAudioLogEntry[]; dropped: number },
  limit = 3
): string {
  if (logs.entries.length === 0) {
    return logs.dropped > 0 ? `nativeLogs=none(+${logs.dropped} dropped)` : "nativeLogs=none";
  }
  const ranked = [...logs.entries].sort((a, b) => b.severity - a.severity).slice(0, Math.max(1, limit));
  const rendered = ranked.map((entry) => `[${entry.severity}]${entry.message}`).join(" | ");
  const suffix = logs.dropped > 0 ? ` (+${logs.dropped} dropped)` : "";
  return `nativeLogs=${rendered}${suffix}`;
}

/**
 * The specific state this incident is about, named once so tests and recovery
 * code cannot drift apart on what "the engine died under a live recorder" means.
 *
 * Capture is enabled (or even initialized) while the engine is not running. The
 * ADM will answer `isRecording === true` here, which is exactly the reading that
 * must never be accepted as a healthy host.
 */
export function isStaleRecordingWithoutEngine(state: NativeAudioEngineState | null): boolean {
  if (!state) return false;
  return !state.engineRunning && (state.inputEnabled || state.recordingInitialized) && !state.inputRunning;
}
