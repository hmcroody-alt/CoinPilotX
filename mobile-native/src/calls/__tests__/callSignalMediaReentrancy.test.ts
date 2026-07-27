// Guards the single-owner tone controller against the "orphaned second loop" bug.
// Audio.Sound.createAsync is async, so a stopCallTone (or a newer startCallTone)
// landing while an older start is still loading must discard the older sound —
// otherwise a second looping ringtone/ringback plays with no owner able to stop it.

const flush = () => new Promise((resolve) => setImmediate(resolve));

/** A fake expo-av whose Sound loads can be resolved individually, in any order. */
function makeDeferredAudioMock() {
  const sounds: any[] = [];
  const resolvers: Array<(v: any) => void> = [];

  const createAsync = jest.fn((_source: any, _opts: any) => {
    const sound = {
      stopAsync: jest.fn().mockResolvedValue(undefined),
      unloadAsync: jest.fn().mockResolvedValue(undefined),
      setOnPlaybackStatusUpdate: jest.fn()
    };
    sounds.push(sound);
    return new Promise((resolve) => {
      resolvers.push(() => resolve({ sound, status: {} }));
    });
  });

  return {
    sounds,
    // Wait until at least `n` loads are in-flight (the code has reached createAsync).
    waitForPending: async (n: number) => {
      for (let i = 0; i < 50 && resolvers.length < n; i += 1) await flush();
    },
    // Resolve the load at index `i` (0 = oldest).
    settle: (i: number) => resolvers[i]?.(undefined),
    module: {
      Audio: {
        setAudioModeAsync: jest.fn().mockResolvedValue(undefined),
        Sound: { createAsync }
      },
      InterruptionModeAndroid: { DoNotMix: 1 },
      InterruptionModeIOS: { DoNotMix: 1 }
    }
  };
}

function loadModule(audioModule: any) {
  jest.resetModules();
  jest.doMock("expo-av", () => audioModule);
  jest.doMock("expo-haptics", () => ({
    notificationAsync: jest.fn().mockResolvedValue(undefined),
    impactAsync: jest.fn().mockResolvedValue(undefined),
    NotificationFeedbackType: { Warning: 1 },
    ImpactFeedbackStyle: { Light: 1, Medium: 2, Rigid: 3 }
  }));
  // "web" short-circuits the vibration setInterval so no timer leaks past the test.
  jest.doMock("react-native", () => ({ Platform: { OS: "web" } }));
  return require("../callSignalMedia");
}

describe("callSignalMedia reentrancy guard (Issue 6: no orphaned second loop)", () => {
  afterEach(() => jest.resetModules());

  it("discards a start whose load finishes AFTER a stop superseded it", async () => {
    const audio = makeDeferredAudioMock();
    const media = loadModule(audio.module);

    const starting = media.startCallTone("ringtone"); // load goes in-flight
    await audio.waitForPending(1);
    await media.stopCallTone(); // supersede the in-flight load
    audio.settle(0); // the older load finally resolves
    await starting;

    // The superseded sound must have been stopped + unloaded, never left looping.
    expect(audio.sounds).toHaveLength(1);
    expect(audio.sounds[0].stopAsync).toHaveBeenCalled();
    expect(audio.sounds[0].unloadAsync).toHaveBeenCalled();
  });

  it("keeps only the newest loop when two starts race", async () => {
    const audio = makeDeferredAudioMock();
    const media = loadModule(audio.module);

    const first = media.startCallTone("ringback"); // in-flight load #0
    await audio.waitForPending(1);
    const second = media.startCallTone("ringtone"); // supersedes; in-flight load #1
    await audio.waitForPending(2);

    audio.settle(1); // newest load resolves and becomes the owner
    await second;
    audio.settle(0); // older load resolves late and must self-discard
    await first;

    expect(audio.sounds).toHaveLength(2);
    expect(audio.sounds[0].unloadAsync).toHaveBeenCalled(); // superseded, torn down
    expect(audio.sounds[1].unloadAsync).not.toHaveBeenCalled(); // survivor still playing
  });

  it("is a no-op when the same tone is already looping", async () => {
    const audio = makeDeferredAudioMock();
    const media = loadModule(audio.module);

    const start = media.startCallTone("ringtone");
    await audio.waitForPending(1);
    audio.settle(0);
    await start;
    const before = audio.module.Audio.Sound.createAsync.mock.calls.length;

    await media.startCallTone("ringtone"); // same tone already active
    expect(audio.module.Audio.Sound.createAsync.mock.calls.length).toBe(before);
  });
});
