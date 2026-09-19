/**
 * The lock screen is an input surface, and it had no test.
 *
 * `MPRemoteCommandCenter` gives eight buttons to the lock screen, Control
 * Centre, AirPods and CarPlay. Every one of them lands in
 * `pulseRadio.handleRemoteCommand` by way of `onRemoteCommand(...)` at module
 * scope. Both suites that load the engine — `pulseRadio.test.ts` and
 * `pulseRadioOffline.test.ts` — mock `onRemoteCommand` as
 * `jest.fn(() => () => undefined)`, so the listener is registered and thrown
 * away. Their 22 tests cover the in-app controls and none of them cover the
 * buttons a user presses with the phone in their pocket.
 *
 * Two things follow from that, and this file exists for the second.
 *
 * The first is ordinary coverage: a `case` dropped from that switch, or a
 * renamed engine export, is invisible on device until someone locks their
 * phone and presses a button that does nothing.
 *
 * The second is the property that makes this whole capability safe under the
 * real-time audio locks. The native module never touches `AVAudioSession`; it
 * forwards a string to JS. Arbitration happens here, in
 * `claimMediaPlayback({ kind: "radio" })`, and `radio` sits at priority 20
 * against `call: 100`, `recording: 90` and `live: 70`. So a lock-screen play
 * press during a call *cannot* start audio — not because the button is hidden
 * or disabled, but because the claim is refused. That is a safety claim about
 * a protected subsystem, and it was resting entirely on reading the code.
 *
 * These tests drive the captured listener directly, which is exactly what the
 * native module does with `sendEvent("onRemoteCommand", …)`.
 */
const mockSoundInstance = {
  unloadAsync: jest.fn().mockResolvedValue(undefined),
  setPositionAsync: jest.fn().mockResolvedValue(undefined)
};

jest.mock("expo-av", () => ({
  Audio: {
    setAudioModeAsync: jest.fn().mockResolvedValue(undefined),
    Sound: {
      createAsync: jest.fn(() => Promise.resolve({ sound: mockSoundInstance, status: {} }))
    }
  },
  InterruptionModeAndroid: { DoNotMix: 1 },
  InterruptionModeIOS: { DoNotMix: 1 }
}));

jest.mock("../../api/radio", () => ({
  listPulseRadioTracks: jest.fn(),
  recordPulseRadioPlay: jest.fn().mockResolvedValue(undefined)
}));

jest.mock("../mediaPlaybackCoordinator", () => ({
  claimMediaPlayback: jest.fn().mockResolvedValue(true),
  releaseMediaPlayback: jest.fn().mockResolvedValue(undefined),
  subscribeMediaPlayback: jest.fn(() => () => undefined)
}));

// The listener the engine registers at module scope. Captured rather than
// discarded — that single difference is what this file is.
let mockRemoteListener: ((event: Record<string, unknown>) => void) | null = null;

jest.mock("../../native/nowPlayingBridge", () => ({
  pushNowPlayingInfo: jest.fn(),
  pushNowPlayingProgress: jest.fn(),
  clearNowPlaying: jest.fn(),
  onRemoteCommand: jest.fn((listener: (event: Record<string, unknown>) => void) => {
    mockRemoteListener = listener;
    return () => undefined;
  })
}));

const TRACKS = [
  { id: "t1", title: "Track One", artist: "Artist A", audioUrl: "https://example.com/1.mp3" },
  { id: "t2", title: "Track Two", artist: "Artist B", audioUrl: "https://example.com/2.mp3" },
  { id: "t3", title: "Track Three", artist: "Artist C", audioUrl: "https://example.com/3.mp3" }
];

function loadModule() {
  jest.resetModules();
  mockRemoteListener = null;
  const radioApi = require("../../api/radio");
  radioApi.listPulseRadioTracks.mockResolvedValue(TRACKS.map((t) => ({ ...t })));
  const pulseRadio = require("../pulseRadio");
  const { Audio } = require("expo-av");
  const coordinator = require("../mediaPlaybackCoordinator");
  return { pulseRadio, Audio, coordinator };
}

/**
 * `handleRemoteCommand` is deliberately synchronous — it fires a promise and
 * returns `.success` to the OS immediately rather than making the system wait
 * on a network round trip. So a test has to drain the microtask queue itself.
 * Several turns, because a play travels through claim → fill queue → create
 * sound, each its own await.
 */
async function settle(turns = 6) {
  for (let i = 0; i < turns; i += 1) await Promise.resolve();
  await new Promise((resolve) => setImmediate(resolve));
}

function press(command: string, extra: Record<string, unknown> = {}) {
  if (!mockRemoteListener) throw new Error("the engine never registered a remote-command listener");
  mockRemoteListener({ command, ...extra });
}

function playingStatus(positionMillis = 0, durationMillis = 120000) {
  return { isLoaded: true, isPlaying: true, isBuffering: false, positionMillis, durationMillis, didJustFinish: false };
}

describe("Pulse Radio lock-screen remote commands", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockSoundInstance.unloadAsync.mockClear();
    mockSoundInstance.setPositionAsync.mockClear();
  });

  it("registers a listener when the engine loads", () => {
    loadModule();
    const { onRemoteCommand } = require("../../native/nowPlayingBridge");
    expect(onRemoteCommand).toHaveBeenCalledTimes(1);
    expect(typeof mockRemoteListener).toBe("function");
  });

  it("play and pause reach the engine", async () => {
    const { pulseRadio } = loadModule();

    press("play");
    await settle();
    expect(pulseRadio.getPulseRadioState().status).toBe("playing");

    press("pause");
    await settle();
    const paused = pulseRadio.getPulseRadioState();
    expect(paused.status).toBe("paused");
    expect(paused.userWantsPlayback).toBe(false);
  });

  it("toggle plays when stopped and pauses when playing", async () => {
    const { pulseRadio } = loadModule();

    press("toggle");
    await settle();
    expect(pulseRadio.getPulseRadioState().status).toBe("playing");

    press("toggle");
    await settle();
    expect(pulseRadio.getPulseRadioState().status).toBe("paused");
  });

  it("next and previous move through the queue", async () => {
    const { pulseRadio } = loadModule();

    press("play");
    await settle();
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t1");

    press("next");
    await settle();
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t2");

    press("previous");
    await settle();
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t1");
  });

  it("scrubbing on the lock screen seeks to the absolute position it reports", async () => {
    const { pulseRadio, Audio } = loadModule();
    press("play");
    await settle();

    const onStatus = Audio.Sound.createAsync.mock.calls.at(-1)[2];
    onStatus(playingStatus(1000));

    // MPChangePlaybackPositionCommandEvent reports seconds.
    press("seek", { positionSeconds: 42 });
    await settle();
    expect(mockSoundInstance.setPositionAsync).toHaveBeenCalledWith(42000);
    expect(pulseRadio.getPulseRadioState().positionMillis).toBe(42000);
  });

  it("skip forward and back use the interval the system sends, not a hardcoded one", async () => {
    const { pulseRadio, Audio } = loadModule();
    press("play");
    await settle();

    const onStatus = Audio.Sound.createAsync.mock.calls.at(-1)[2];
    onStatus(playingStatus(30000));

    // The native module advertises `preferredIntervals = [15]`, but CarPlay and
    // some accessories send their own. Honour what arrives.
    press("skipForward", { intervalSeconds: 30 });
    await settle();
    expect(mockSoundInstance.setPositionAsync).toHaveBeenLastCalledWith(60000);

    press("skipBackward", { intervalSeconds: 30 });
    await settle();
    expect(mockSoundInstance.setPositionAsync).toHaveBeenLastCalledWith(30000);
  });

  it("falls back to 15 seconds when no interval is sent", async () => {
    const { Audio } = loadModule();
    press("play");
    await settle();

    const onStatus = Audio.Sound.createAsync.mock.calls.at(-1)[2];
    onStatus(playingStatus(30000));

    press("skipForward");
    await settle();
    expect(mockSoundInstance.setPositionAsync).toHaveBeenLastCalledWith(45000);
  });

  it("ignores a command it does not implement instead of throwing into the native bridge", async () => {
    const { pulseRadio } = loadModule();
    expect(() => press("bookmark")).not.toThrow();
    await settle();
    expect(pulseRadio.getPulseRadioState().status).toBe("paused");
  });

  // ---------------------------------------------------------------------
  // The property this file exists for.
  // ---------------------------------------------------------------------

  it("a lock-screen play press cannot start audio while a higher-priority owner holds playback", async () => {
    const { pulseRadio, Audio, coordinator } = loadModule();
    // What a call, a recording or a live stream looks like from here: the
    // coordinator refuses the claim because `radio` is priority 20.
    coordinator.claimMediaPlayback.mockResolvedValue(false);

    press("play");
    await settle();

    const state = pulseRadio.getPulseRadioState();
    expect(state.status).toBe("paused");
    expect(state.interruptedBy).toBeTruthy();
    // The load-bearing assertion: no sound was ever created, so nothing
    // competed for the audio session.
    expect(Audio.Sound.createAsync).not.toHaveBeenCalled();
    expect(Audio.setAudioModeAsync).not.toHaveBeenCalled();
  });

  it("neither does next, previous, or toggle", async () => {
    const { pulseRadio, Audio, coordinator } = loadModule();
    coordinator.claimMediaPlayback.mockResolvedValue(false);

    for (const command of ["next", "previous", "toggle"]) {
      press(command);
      await settle();
    }

    expect(Audio.Sound.createAsync).not.toHaveBeenCalled();
    expect(Audio.setAudioModeAsync).not.toHaveBeenCalled();
    expect(pulseRadio.getPulseRadioState().status).toBe("paused");
  });

  it("claims as `radio` — the kind the priority table ranks lowest", async () => {
    const { coordinator } = loadModule();
    press("play");
    await settle();

    // If this ever claims as anything else, the refusal above stops being a
    // guarantee and becomes a coincidence of the priority table.
    for (const call of coordinator.claimMediaPlayback.mock.calls) {
      expect(call[0]).toMatchObject({ id: "pulse-radio", kind: "radio" });
    }
    expect(coordinator.claimMediaPlayback).toHaveBeenCalled();
  });

  it("pause still works while a higher-priority owner holds playback", async () => {
    // The asymmetry is deliberate: stopping is always allowed. A listener who
    // presses pause on the lock screen must not be told no.
    const { pulseRadio, coordinator } = loadModule();
    press("play");
    await settle();
    expect(pulseRadio.getPulseRadioState().status).toBe("playing");

    coordinator.claimMediaPlayback.mockResolvedValue(false);
    press("pause");
    await settle();
    expect(pulseRadio.getPulseRadioState().status).toBe("paused");
  });
});
