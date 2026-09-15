/**
 * Pulse Radio end to end, from the player's side.
 *
 * `radio/radioOffline` and `radio/radioRecovery` are unit-tested on their own.
 * What those tests cannot show is whether the player actually *asks* them — a
 * correct recovery plan that nothing consults is worth nothing, and that is the
 * shape most of these regressions take. So this file mocks the offline layer and
 * asserts on what reaches `Audio.Sound.createAsync`: the URI it was handed, and
 * the position it was told to start from.
 *
 * Connectivity is the real module, driven through `reportReachability`, because
 * the resume path depends on a real transition being emitted to a real
 * subscriber. Two ordering rules follow from that, and breaking either one makes
 * this file assert against a player that was never connected to anything:
 *
 *   1. `resetConnectivityForTests` clears the listener set, so it must run
 *      BEFORE `require("../pulseRadio")` — a reset afterwards silently
 *      unsubscribes the player.
 *   2. Connectivity must be taken from the harness, never from a top-level
 *      import. `jest.resetModules()` gives `pulseRadio` a FRESH connectivity
 *      instance; a module-scope import stays bound to the discarded one, whose
 *      state the player cannot see. That version of this file reported
 *      `buffering` where the player was genuinely correct to report `offline`,
 *      because the "outage" had been declared on an orphaned module.
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

jest.mock("../../native/nowPlayingBridge", () => ({
  pushNowPlayingInfo: jest.fn(),
  pushNowPlayingProgress: jest.fn(),
  clearNowPlaying: jest.fn(),
  onRemoteCommand: jest.fn(() => () => undefined)
}));

jest.mock("../radio/radioOffline", () => ({
  loadCachedRadioQueue: jest.fn(),
  cacheRadioQueue: jest.fn().mockResolvedValue(undefined),
  resolveRadioSource: jest.fn(),
  warmRadioTrack: jest.fn().mockResolvedValue(true),
  discardCachedRadioTrack: jest.fn().mockResolvedValue(true),
  nextTrackToWarm: jest.requireActual("../radio/radioOffline").nextTrackToWarm
}));

const TRACKS = [
  { id: "t1", title: "Track One", artist: "Artist A", audioUrl: "https://cdn.pulsesoc.com/1.mp3" },
  { id: "t2", title: "Track Two", artist: "Artist B", audioUrl: "https://cdn.pulsesoc.com/2.mp3" },
  { id: "t3", title: "Track Three", artist: "Artist C", audioUrl: "https://cdn.pulsesoc.com/3.mp3" }
];

type Harness = ReturnType<typeof loadModule>;

/**
 * A fresh player against a stated network.
 *
 * The connectivity reset has to happen between `resetModules` and the require:
 * the player subscribes at import time, and the reset empties the listener set.
 */
function loadModule(connectivity: "online" | "offline" | "degraded" = "online") {
  jest.resetModules();
  // Taken after the reset, so this is the same instance the player will import.
  const net = require("../connectivity");
  net.resetConnectivityForTests({ state: connectivity });
  const radioApi = require("../../api/radio");
  const offline = require("../radio/radioOffline");
  const pulseRadio = require("../pulseRadio");
  const { Audio } = require("expo-av");
  return { pulseRadio, Audio, radioApi, offline, net };
}

/** Drop the network the way the app learns about it: failed requests. */
function loseTheNetwork({ net }: Harness) {
  net.reportReachability("unreachable");
  net.reportReachability("unreachable");
  expect(net.connectivityState()).toBe("offline");
}

/** The real recovery path: a round trip proves a path, the orchestrator confirms. */
async function regainTheNetwork({ net }: Harness) {
  net.reportReachability("round_trip");
  net.markRecoveryComplete();
  await new Promise((resolve) => setTimeout(resolve, 0));
}

/** What the player last asked expo-av to play, and from where. */
function lastCreateCall(Audio: any) {
  const calls = Audio.Sound.createAsync.mock.calls;
  return { uri: calls[calls.length - 1][0].uri, options: calls[calls.length - 1][1] };
}

function lastStatusCallback(Audio: any) {
  const calls = Audio.Sound.createAsync.mock.calls;
  return calls[calls.length - 1][2];
}

/** Every track streams; nothing is on disk. */
function streamEverything({ offline }: Harness) {
  offline.resolveRadioSource.mockImplementation(async (track: any) =>
    track?.audioUrl ? { uri: track.audioUrl, offline: false } : null
  );
}

beforeEach(() => {
  jest.clearAllMocks();
  jest.useRealTimers();
});

describe("cold start with no network", () => {
  it("plays the cached queue instead of reporting an empty radio", async () => {
    // The pre-migration failure: `listPulseRadioTracks()` was the only source of
    // a queue, so a cold start in a tunnel rejected and the radio said "Pulse
    // Radio has no playable tracks right now." — a claim about the catalogue
    // made on the evidence of a failed request.
    const harness = loadModule("offline");
    streamEverything(harness);
    harness.offline.loadCachedRadioQueue.mockResolvedValue({
      tracks: TRACKS.map((t) => ({ ...t })),
      storedAt: Date.now() - 60_000,
      ageMs: 60_000
    });
    harness.radioApi.listPulseRadioTracks.mockRejectedValue(new Error("unreachable"));

    await harness.pulseRadio.playPulseRadio();

    const state = harness.pulseRadio.getPulseRadioState();
    expect(state.status).toBe("playing");
    expect(state.queue).toHaveLength(3);
    expect(state.track?.id).toBe("t1");
  });

  it("still says so when there is genuinely nothing cached", async () => {
    // The empty state has to stay truthful in the other direction too: no cache
    // AND no network is a real "nothing to play", and dressing it as offline
    // would send the listener to look for a signal that would not help.
    const harness = loadModule("offline");
    streamEverything(harness);
    harness.offline.loadCachedRadioQueue.mockResolvedValue({ tracks: [], storedAt: null, ageMs: null });
    harness.radioApi.listPulseRadioTracks.mockRejectedValue(new Error("unreachable"));

    await harness.pulseRadio.playPulseRadio();

    expect(harness.pulseRadio.getPulseRadioState().status).toBe("offline");
  });

  it("does not leave the radio permanently empty after a superseded first load", async () => {
    // `tracksLoaded` used to be set beside the request rather than after a queue
    // existed. A load that lost its generation race therefore left the flag true
    // and the queue empty, and every later play short-circuited to "no playable
    // tracks" until the app was restarted.
    const harness = loadModule("online");
    streamEverything(harness);
    harness.offline.loadCachedRadioQueue.mockResolvedValue({ tracks: [], storedAt: null, ageMs: null });
    harness.radioApi.listPulseRadioTracks.mockRejectedValueOnce(new Error("boom"));

    await harness.pulseRadio.playPulseRadio();
    expect(harness.pulseRadio.getPulseRadioState().status).toBe("error");

    harness.radioApi.listPulseRadioTracks.mockResolvedValue(TRACKS.map((t) => ({ ...t })));
    await harness.pulseRadio.playPulseRadio();

    expect(harness.pulseRadio.getPulseRadioState().status).toBe("playing");
    expect(harness.pulseRadio.getPulseRadioState().track?.id).toBe("t1");
  });
});

describe("cached bytes", () => {
  it("hands the player the local file rather than the stream", async () => {
    const harness = loadModule("online");
    harness.offline.loadCachedRadioQueue.mockResolvedValue({ tracks: [], storedAt: null, ageMs: null });
    harness.radioApi.listPulseRadioTracks.mockResolvedValue(TRACKS.map((t) => ({ ...t })));
    harness.offline.resolveRadioSource.mockResolvedValue({ uri: "file:///cache/t1.mp3", offline: true });

    await harness.pulseRadio.playPulseRadio();

    expect(lastCreateCall(harness.Audio).uri).toBe("file:///cache/t1.mp3");
  });

  it("warms exactly one track ahead, not the queue", async () => {
    // §13 at a smaller file size. Three tracks in the queue, one warm call.
    const harness = loadModule("online");
    streamEverything(harness);
    harness.offline.loadCachedRadioQueue.mockResolvedValue({ tracks: [], storedAt: null, ageMs: null });
    harness.radioApi.listPulseRadioTracks.mockResolvedValue(TRACKS.map((t) => ({ ...t })));

    await harness.pulseRadio.playPulseRadio();

    expect(harness.offline.warmRadioTrack).toHaveBeenCalledTimes(1);
    expect(harness.offline.warmRadioTrack).toHaveBeenCalledWith(expect.objectContaining({ id: "t2" }));
  });

  it("caches the queue it just fetched so the next cold start has one", async () => {
    const harness = loadModule("online");
    streamEverything(harness);
    harness.offline.loadCachedRadioQueue.mockResolvedValue({ tracks: [], storedAt: null, ageMs: null });
    harness.radioApi.listPulseRadioTracks.mockResolvedValue(TRACKS.map((t) => ({ ...t })));

    await harness.pulseRadio.playPulseRadio();

    expect(harness.offline.cacheRadioQueue).toHaveBeenCalledWith(
      expect.arrayContaining([expect.objectContaining({ id: "t1" })])
    );
  });
});

describe("losing the network mid-track", () => {
  async function playingAt(positionMillis: number) {
    const harness = loadModule("online");
    streamEverything(harness);
    harness.offline.loadCachedRadioQueue.mockResolvedValue({ tracks: [], storedAt: null, ageMs: null });
    harness.radioApi.listPulseRadioTracks.mockResolvedValue(TRACKS.map((t) => ({ ...t })));
    await harness.pulseRadio.playPulseRadio();

    // Let the player observe real progress, so the position it carries into
    // recovery is one it actually reached rather than one the test injected.
    lastStatusCallback(harness.Audio)({
      isLoaded: true,
      isPlaying: true,
      isBuffering: false,
      positionMillis,
      durationMillis: 240_000,
      didJustFinish: false
    });
    expect(harness.pulseRadio.getPulseRadioState().positionMillis).toBe(positionMillis);
    return harness;
  }

  it("waits for the network and keeps the listener's intent to play", async () => {
    const harness = await playingAt(92_000);

    loseTheNetwork(harness);
    lastStatusCallback(harness.Audio)({
      isLoaded: true,
      isPlaying: false,
      isBuffering: true,
      positionMillis: 92_000,
      durationMillis: 240_000,
      didJustFinish: false
    });

    const state = harness.pulseRadio.getPulseRadioState();
    expect(state.status).toBe("offline");
    // The intent survives the outage. Without it the reconnect has nothing to
    // resume, and the listener has to press play again for a song they never
    // stopped.
    expect(state.userWantsPlayback).toBe(true);
  });

  it("resumes at the position it stopped at, not at zero", async () => {
    // §84 in one assertion. A three-second tunnel cost the whole song because
    // every recovery path restarted the track from the beginning.
    const harness = await playingAt(92_000);

    loseTheNetwork(harness);
    lastStatusCallback(harness.Audio)({
      isLoaded: true,
      isPlaying: false,
      isBuffering: true,
      positionMillis: 92_000,
      durationMillis: 240_000,
      didJustFinish: false
    });
    expect(harness.pulseRadio.getPulseRadioState().status).toBe("offline");

    const createCallsBefore = harness.Audio.Sound.createAsync.mock.calls.length;
    await regainTheNetwork(harness);

    expect(harness.Audio.Sound.createAsync.mock.calls.length).toBeGreaterThan(createCallsBefore);
    expect(lastCreateCall(harness.Audio).options.positionMillis).toBe(92_000);
  });

  it("does not start music for someone who pressed pause during the outage", async () => {
    const harness = await playingAt(92_000);

    loseTheNetwork(harness);
    lastStatusCallback(harness.Audio)({
      isLoaded: true,
      isPlaying: false,
      isBuffering: true,
      positionMillis: 92_000,
      durationMillis: 240_000,
      didJustFinish: false
    });

    await harness.pulseRadio.pausePulseRadio();
    const createCallsBefore = harness.Audio.Sound.createAsync.mock.calls.length;
    await regainTheNetwork(harness);

    expect(harness.Audio.Sound.createAsync.mock.calls.length).toBe(createCallsBefore);
  });
});

describe("a cached file that will not open", () => {
  it("discards the copy rather than blaming the network", async () => {
    // The bytes are the right size and the wrong content. Waiting for
    // connectivity would replay them forever; the track would be permanently
    // broken for that account with no way for the listener to clear it.
    const harness = loadModule("offline");
    harness.offline.loadCachedRadioQueue.mockResolvedValue({
      tracks: TRACKS.map((t) => ({ ...t })),
      storedAt: Date.now(),
      ageMs: 0
    });
    harness.radioApi.listPulseRadioTracks.mockRejectedValue(new Error("unreachable"));
    harness.offline.resolveRadioSource.mockResolvedValue({ uri: "file:///cache/t1.mp3", offline: true });

    await harness.pulseRadio.playPulseRadio();
    expect(harness.pulseRadio.getPulseRadioState().status).toBe("playing");

    lastStatusCallback(harness.Audio)({ isLoaded: false, error: "AVFoundation: cannot decode" });

    expect(harness.offline.discardCachedRadioTrack).toHaveBeenCalledWith(expect.objectContaining({ id: "t1" }));
    expect(harness.pulseRadio.getPulseRadioState().status).toBe("error");
  });

  it("does not discard a streamed track when the network is what failed", async () => {
    const harness = loadModule("online");
    streamEverything(harness);
    harness.offline.loadCachedRadioQueue.mockResolvedValue({ tracks: [], storedAt: null, ageMs: null });
    harness.radioApi.listPulseRadioTracks.mockResolvedValue(TRACKS.map((t) => ({ ...t })));

    await harness.pulseRadio.playPulseRadio();
    lastStatusCallback(harness.Audio)({ isLoaded: false, error: "The network connection was lost" });

    expect(harness.offline.discardCachedRadioTrack).not.toHaveBeenCalled();
  });
});
