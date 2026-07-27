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

const TRACKS = [
  { id: "t1", title: "Track One", artist: "Artist A", audioUrl: "https://example.com/1.mp3" },
  { id: "t2", title: "Track Two", artist: "Artist B", audioUrl: "https://example.com/2.mp3" },
  { id: "t3", title: "Track Three", artist: "Artist C", audioUrl: "https://example.com/3.mp3" }
];

function loadModule() {
  jest.resetModules();
  const radioApi = require("../../api/radio");
  radioApi.listPulseRadioTracks.mockResolvedValue(TRACKS.map((t) => ({ ...t })));
  const pulseRadio = require("../pulseRadio");
  const { Audio } = require("expo-av");
  return { pulseRadio, Audio, radioApi };
}

function lastStatusCallback(Audio: any) {
  const calls = Audio.Sound.createAsync.mock.calls;
  return calls[calls.length - 1][2];
}

describe("pulseRadio queue engine", () => {
  beforeEach(() => {
    jest.clearAllMocks();
    mockSoundInstance.unloadAsync.mockClear();
    mockSoundInstance.setPositionAsync.mockClear();
  });

  it("loads the queue and plays the first track", async () => {
    const { pulseRadio } = loadModule();
    await pulseRadio.playPulseRadio();
    const state = pulseRadio.getPulseRadioState();
    expect(state.status).toBe("playing");
    expect(state.queue).toHaveLength(3);
    expect(state.queueIndex).toBe(0);
    expect(state.track?.id).toBe("t1");
  });

  it("advances to the next track and wraps only when repeat queue is set", async () => {
    const { pulseRadio } = loadModule();
    await pulseRadio.playPulseRadio();
    await pulseRadio.playNextTrack();
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t2");
    await pulseRadio.playNextTrack();
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t3");

    // End of queue with repeat off: stop instead of wrapping.
    await pulseRadio.playNextTrack();
    const stopped = pulseRadio.getPulseRadioState();
    expect(stopped.status).toBe("paused");
    expect(stopped.userWantsPlayback).toBe(false);

    // Re-enable playback, enable repeat queue, and confirm it wraps to the start.
    await pulseRadio.playPulseRadio();
    await pulseRadio.playNextTrack();
    await pulseRadio.playNextTrack();
    pulseRadio.setPulseRadioRepeatMode("queue");
    await pulseRadio.playNextTrack();
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t1");
  });

  it("goes to the previous track, or restarts the current track once played past the threshold", async () => {
    const { pulseRadio, Audio } = loadModule();
    await pulseRadio.playPulseRadio();
    await pulseRadio.playNextTrack();
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t2");

    // Fresh track (position 0) -> previous should move back a track.
    await pulseRadio.playPreviousTrack();
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t1");

    // Simulate meaningful playback progress, then "previous" should restart instead of skipping back.
    await pulseRadio.playNextTrack();
    const onStatus = lastStatusCallback(Audio);
    onStatus({ isLoaded: true, isPlaying: true, isBuffering: false, positionMillis: 9000, durationMillis: 120000, didJustFinish: false });
    await pulseRadio.playPreviousTrack();
    expect(mockSoundInstance.setPositionAsync).toHaveBeenCalledWith(0);
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t2");
  });

  it("cycles repeat mode off -> queue -> one -> off", () => {
    const { pulseRadio } = loadModule();
    expect(pulseRadio.getPulseRadioState().repeatMode).toBe("off");
    pulseRadio.cyclePulseRadioRepeatMode();
    expect(pulseRadio.getPulseRadioState().repeatMode).toBe("queue");
    pulseRadio.cyclePulseRadioRepeatMode();
    expect(pulseRadio.getPulseRadioState().repeatMode).toBe("one");
    pulseRadio.cyclePulseRadioRepeatMode();
    expect(pulseRadio.getPulseRadioState().repeatMode).toBe("off");
  });

  it("repeats the same track indefinitely when repeat one is enabled", async () => {
    const { pulseRadio } = loadModule();
    await pulseRadio.playPulseRadio();
    pulseRadio.setPulseRadioRepeatMode("one");
    await pulseRadio.playNextTrack();
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t1");
    await pulseRadio.playPreviousTrack();
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t1");
  });

  it("shuffles without interrupting the currently playing track, and restores order when disabled", async () => {
    const { pulseRadio } = loadModule();
    await pulseRadio.playPulseRadio();
    await pulseRadio.playNextTrack();
    const before = pulseRadio.getPulseRadioState().track?.id;
    expect(before).toBe("t2");

    pulseRadio.setPulseRadioShuffle(true);
    expect(pulseRadio.getPulseRadioState().shuffle).toBe(true);
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t2");

    pulseRadio.setPulseRadioShuffle(false);
    expect(pulseRadio.getPulseRadioState().shuffle).toBe(false);
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t2");
  });

  it("moves a queue item and keeps queueIndex pointed at the currently playing track", async () => {
    const { pulseRadio } = loadModule();
    await pulseRadio.playPulseRadio();
    expect(pulseRadio.getPulseRadioState().queueIndex).toBe(0);

    // Move the currently playing track (index 0) down to index 2.
    await pulseRadio.moveQueueTrack(0, 2);
    const state = pulseRadio.getPulseRadioState();
    expect(state.queue.map((t: any) => t.id)).toEqual(["t2", "t3", "t1"]);
    expect(state.queueIndex).toBe(2);
    expect(state.track?.id).toBe("t1");
  });

  it("removes a non-playing track and remaps queueIndex", async () => {
    const { pulseRadio } = loadModule();
    await pulseRadio.playPulseRadio();
    await pulseRadio.playNextTrack();
    expect(pulseRadio.getPulseRadioState().queueIndex).toBe(1);

    await pulseRadio.removeQueueTrackAt(0);
    const state = pulseRadio.getPulseRadioState();
    expect(state.queue.map((t: any) => t.id)).toEqual(["t2", "t3"]);
    expect(state.queueIndex).toBe(0);
    expect(state.track?.id).toBe("t2");
  });

  it("removing the currently playing track advances playback to the next remaining track", async () => {
    const { pulseRadio } = loadModule();
    await pulseRadio.playPulseRadio();
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t1");

    await pulseRadio.removeQueueTrackAt(0);
    const state = pulseRadio.getPulseRadioState();
    expect(state.queue.map((t: any) => t.id)).toEqual(["t2", "t3"]);
    expect(state.track?.id).toBe("t2");
  });

  it("seeks within the current track, clamped to duration", async () => {
    const { pulseRadio, Audio } = loadModule();
    await pulseRadio.playPulseRadio();
    const onStatus = lastStatusCallback(Audio);
    onStatus({ isLoaded: true, isPlaying: true, isBuffering: false, positionMillis: 5000, durationMillis: 10000, didJustFinish: false });

    await pulseRadio.seekPulseRadioBy(15000);
    expect(mockSoundInstance.setPositionAsync).toHaveBeenCalledWith(10000);
    expect(pulseRadio.getPulseRadioState().positionMillis).toBe(10000);

    await pulseRadio.seekPulseRadioTo(-500);
    expect(mockSoundInstance.setPositionAsync).toHaveBeenCalledWith(0);
  });

  it("advances to the next track automatically when a track finishes", async () => {
    const { pulseRadio, Audio } = loadModule();
    await pulseRadio.playPulseRadio();
    const onStatus = lastStatusCallback(Audio);
    onStatus({ isLoaded: true, isPlaying: true, isBuffering: false, positionMillis: 10000, durationMillis: 10000, didJustFinish: true });
    for (let i = 0; i < 10; i += 1) await Promise.resolve();
    expect(pulseRadio.getPulseRadioState().track?.id).toBe("t2");
  });
});
