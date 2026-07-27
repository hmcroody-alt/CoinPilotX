type AppStateListener = (state: string) => void;

let appStateListener: AppStateListener | null = null;

jest.mock("react-native", () => ({
  AppState: {
    addEventListener: jest.fn((_event: string, listener: AppStateListener) => {
      appStateListener = listener;
      return { remove: jest.fn() };
    })
  }
}));

const coordinator = require("../mediaPlaybackCoordinator") as typeof import("../mediaPlaybackCoordinator");
const liveOwnership = require("../../live/livePlaybackOwnership") as typeof import("../../live/livePlaybackOwnership");

describe("mediaPlaybackCoordinator real-time background ownership", () => {
  afterEach(async () => {
    await coordinator.resetMediaPlayback();
    jest.clearAllMocks();
  });

  it("retains active call ownership when the app backgrounds", async () => {
    const pause = jest.fn();
    const stop = jest.fn();

    await coordinator.claimMediaPlayback({ id: "call:1", kind: "call", pause, stop });
    appStateListener?.("background");

    expect(coordinator.getActiveMediaPlayback()).toEqual({ id: "call:1", kind: "call" });
    expect(pause).not.toHaveBeenCalled();
    expect(stop).not.toHaveBeenCalled();
  });

  it("retains active Live ownership when the app backgrounds", async () => {
    const pause = jest.fn();
    const stop = jest.fn();

    await coordinator.claimMediaPlayback({ id: "live:1", kind: "live", pause, stop });
    appStateListener?.("inactive");

    expect(coordinator.getActiveMediaPlayback()).toEqual({ id: "live:1", kind: "live" });
    expect(pause).not.toHaveBeenCalled();
    expect(stop).not.toHaveBeenCalled();
  });

  it("still releases short-form playback when the app backgrounds", async () => {
    const pause = jest.fn();
    const stop = jest.fn();

    await coordinator.claimMediaPlayback({ id: "reel:1", kind: "reel", pause, stop });
    appStateListener?.("background");

    expect(coordinator.getActiveMediaPlayback()).toBeNull();
    expect(stop).toHaveBeenCalledTimes(1);
    expect(pause).not.toHaveBeenCalled();
  });

  it("lets a native Live preempt radio before the LiveKit room finishes connecting", async () => {
    const radioPause = jest.fn();
    const radioStop = jest.fn();

    await coordinator.claimMediaPlayback({ id: "pulse-radio", kind: "radio", pause: radioPause, stop: radioStop });
    await liveOwnership.claimLivePlaybackOwner("viewer", 99);

    expect(coordinator.getActiveMediaPlayback()).toEqual({ id: "live-viewer:99", kind: "live" });
    expect(radioPause).toHaveBeenCalledTimes(1);
    expect(radioStop).not.toHaveBeenCalled();
  });

  it("does not let reel playback steal ownership from an active native Live", async () => {
    const livePause = jest.fn();
    const reelPause = jest.fn();

    await liveOwnership.claimLivePlaybackOwner("feed", 41, livePause);
    const granted = await coordinator.claimMediaPlayback({ id: "reel:41", kind: "reel", pause: reelPause });

    expect(granted).toBe(false);
    expect(coordinator.getActiveMediaPlayback()).toEqual({ id: "live-feed:41", kind: "live" });
    expect(livePause).not.toHaveBeenCalled();
    expect(reelPause).not.toHaveBeenCalled();
  });

  it("documents which owner kinds are retained on background", async () => {
    expect(coordinator.shouldRetainMediaPlaybackOnBackground("call")).toBe(true);
    expect(coordinator.shouldRetainMediaPlaybackOnBackground("live")).toBe(true);
    expect(coordinator.shouldRetainMediaPlaybackOnBackground("recording")).toBe(true);
    expect(coordinator.shouldRetainMediaPlaybackOnBackground("radio")).toBe(true);
    expect(coordinator.shouldRetainMediaPlaybackOnBackground("viewer")).toBe(false);
    expect(coordinator.shouldRetainMediaPlaybackOnBackground("reel")).toBe(false);
  });
});
