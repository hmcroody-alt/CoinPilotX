import {
  audioOwnerPriority,
  isCallOwner,
  isLivestreamOwner,
  ownershipDenialMessage,
  RealtimeAudioOwnershipError,
  resolveOwnershipDecision
} from "../audioOwnershipPolicy";
import {
  activateRealtimeAudioSession,
  getActiveRealtimeAudioOwner,
  getLastOwnershipDecision,
  releaseRealtimeAudioSession,
  resetRealtimeAudioOwnership
} from "../realtimeAudioEngine";

function audioSessionDouble() {
  return {
    setAppleAudioConfiguration: jest.fn().mockResolvedValue(undefined),
    configureAudio: jest.fn().mockResolvedValue(undefined),
    startAudioSession: jest.fn().mockResolvedValue(undefined),
    stopAudioSession: jest.fn().mockResolvedValue(undefined),
    selectAudioOutput: jest.fn().mockResolvedValue(undefined)
  };
}

beforeEach(async () => {
  await resetRealtimeAudioOwnership();
});

describe("audio ownership arbitration", () => {
  it("grants the session when nobody holds it", () => {
    expect(resolveOwnershipDecision(null, { ownerId: "live:1", mode: "live_host" })).toEqual({
      outcome: "granted",
      displaces: null
    });
  });

  it("treats a repeat claim by the same owner as a re-acquire, not a new session", () => {
    const current = { ownerId: "live:1", mode: "live_host" as const };
    expect(resolveOwnershipDecision(current, { ownerId: "live:1", mode: "live_host" })).toEqual({
      outcome: "reacquired",
      displaces: null
    });
  });

  it("denies a livestream that tries to take the session from an active call", () => {
    const call = { ownerId: "call:7", mode: "audio_call" as const };
    const decision = resolveOwnershipDecision(call, { ownerId: "live:1", mode: "live_host" });
    expect(decision).toEqual({ outcome: "denied", blockedBy: "call:7", blockedByMode: "audio_call" });
  });

  it("denies a live viewer that tries to take the session from a live host", () => {
    const host = { ownerId: "live:host", mode: "live_host" as const };
    const decision = resolveOwnershipDecision(host, { ownerId: "live:viewer", mode: "live_viewer" });
    expect(decision.outcome).toBe("denied");
  });

  it("lets a call displace a livestream", () => {
    const live = { ownerId: "live:1", mode: "live_host" as const };
    expect(resolveOwnershipDecision(live, { ownerId: "call:7", mode: "audio_call" })).toEqual({
      outcome: "displaced",
      displaces: "live:1"
    });
  });

  it("ranks calls above voice messages above live publishing above live viewing above playback", () => {
    expect(audioOwnerPriority("audio_call")).toBeGreaterThan(audioOwnerPriority("voice_message"));
    expect(audioOwnerPriority("voice_message")).toBeGreaterThan(audioOwnerPriority("live_host"));
    expect(audioOwnerPriority("live_host")).toBeGreaterThan(audioOwnerPriority("live_viewer"));
    expect(audioOwnerPriority("live_viewer")).toBeGreaterThan(audioOwnerPriority("music_playback"));
  });

  it("classifies owner families correctly", () => {
    expect(isCallOwner("video_call")).toBe(true);
    expect(isCallOwner("live_host")).toBe(false);
    expect(isLivestreamOwner("live_guest")).toBe(true);
    expect(isLivestreamOwner("audio_call")).toBe(false);
  });

  it("produces a denial message that names no identifiers", () => {
    const message = ownershipDenialMessage("audio_call");
    expect(message).toContain("call");
    expect(message).not.toMatch(/call:|live:|user-\d/);
  });
});

describe("audio ownership enforcement in the engine", () => {
  it("refuses to start a livestream while a call owns the session, and leaves the call untouched", async () => {
    const callSession = audioSessionDouble();
    const liveSession = audioSessionDouble();

    await activateRealtimeAudioSession(callSession, "audio_call", "call:active");

    await expect(
      activateRealtimeAudioSession(liveSession, "live_host", "live:intruder")
    ).rejects.toBeInstanceOf(RealtimeAudioOwnershipError);

    // The call must still own the session and must not have been stopped.
    expect(getActiveRealtimeAudioOwner()).toEqual(expect.objectContaining({ ownerId: "call:active" }));
    expect(callSession.stopAudioSession).not.toHaveBeenCalled();
    expect(liveSession.startAudioSession).not.toHaveBeenCalled();
  });

  it("tells the displaced livestream it lost the session so it can tear its own media down", async () => {
    const liveSession = audioSessionDouble();
    const callSession = audioSessionDouble();
    const onDisplaced = jest.fn();

    await activateRealtimeAudioSession(liveSession, "live_host", "live:host-1", { onDisplaced });
    expect(onDisplaced).not.toHaveBeenCalled();

    await activateRealtimeAudioSession(callSession, "audio_call", "call:incoming");
    expect(onDisplaced).toHaveBeenCalledTimes(1);
  });

  it("does not start a second audio session when the same owner re-activates", async () => {
    const audioSession = audioSessionDouble();

    await activateRealtimeAudioSession(audioSession, "live_host", "live:host-2");
    await activateRealtimeAudioSession(audioSession, "live_host", "live:host-2");

    expect(getLastOwnershipDecision()?.outcome).toBe("reacquired");
    expect(audioSession.startAudioSession).toHaveBeenCalledTimes(1);
  });

  it("preserves the original session start time across a re-acquire", async () => {
    const audioSession = audioSessionDouble();

    const first = await activateRealtimeAudioSession(audioSession, "live_host", "live:host-3");
    await new Promise((resolve) => setTimeout(resolve, 5));
    const second = await activateRealtimeAudioSession(audioSession, "live_host", "live:host-3");

    expect(second.startedAt).toBe(first.startedAt);
  });

  it("stops notifying a displaced owner once it has released cleanly", async () => {
    const audioSession = audioSessionDouble();
    const onDisplaced = jest.fn();

    await activateRealtimeAudioSession(audioSession, "live_viewer", "live:viewer-1", { onDisplaced });
    await releaseRealtimeAudioSession(audioSession, "live:viewer-1");
    await activateRealtimeAudioSession(audioSession, "audio_call", "call:later");

    expect(onDisplaced).not.toHaveBeenCalled();
  });

  it("clears every owner and handler on a hard reset", async () => {
    const audioSession = audioSessionDouble();
    const onDisplaced = jest.fn();

    await activateRealtimeAudioSession(audioSession, "live_host", "live:host-4", { onDisplaced });
    await resetRealtimeAudioOwnership(audioSession);

    expect(onDisplaced).toHaveBeenCalledTimes(1);
    expect(getActiveRealtimeAudioOwner()).toBeNull();
  });
});
