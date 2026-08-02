/**
 * CALL AUDIO REGRESSION NET
 *
 * Written BEFORE the livestream audio-ownership hardening so it captures the
 * pre-existing, known-good call behaviour. Every assertion here must pass both
 * before and after the livestream work. If one of these fails, the livestream
 * change has regressed the protected call path and must be reverted.
 *
 * Protected contract:
 *   1. Calls use the playAndRecord/videoChat profile.
 *   2. Activating a call claims ownership and starts exactly one audio session.
 *   3. A foreign ownerId can never stop a call's audio session.
 *   4. The owning call always releases its own session.
 *   5. A call is never denied the audio session by any other feature.
 */

import {
  activateRealtimeAudioSession,
  getActiveRealtimeAudioOwner,
  releaseRealtimeAudioSession,
  resolveRealtimeAudioConfiguration
} from "../../core/realtimeAudioEngine";

function audioSessionDouble() {
  return {
    setAppleAudioConfiguration: jest.fn().mockResolvedValue(undefined),
    configureAudio: jest.fn().mockResolvedValue(undefined),
    startAudioSession: jest.fn().mockResolvedValue(undefined),
    stopAudioSession: jest.fn().mockResolvedValue(undefined),
    selectAudioOutput: jest.fn().mockResolvedValue(undefined)
  };
}

async function resetOwnership() {
  const owner = getActiveRealtimeAudioOwner();
  if (owner) await releaseRealtimeAudioSession(audioSessionDouble(), owner.ownerId).catch(() => undefined);
}

describe("PROTECTED: call audio profile", () => {
  beforeEach(resetOwnership);

  it("keeps the call-grade playAndRecord/videoChat profile for audio and video calls", () => {
    for (const mode of ["audio_call", "video_call"] as const) {
      const config = resolveRealtimeAudioConfiguration(mode);
      expect(config.audioCategory).toBe("playAndRecord");
      expect(config.audioMode).toBe("videoChat");
      expect(config.audioCategoryOptions).toEqual(
        expect.arrayContaining(["allowBluetooth", "allowBluetoothA2DP", "allowAirPlay", "defaultToSpeaker"])
      );
    }
  });
});

describe("PROTECTED: call audio session lifecycle", () => {
  beforeEach(resetOwnership);

  it("claims ownership and starts exactly one audio session when a call connects", async () => {
    const audioSession = audioSessionDouble();
    await activateRealtimeAudioSession(audioSession, "audio_call", "call:regression-1");

    expect(getActiveRealtimeAudioOwner()).toEqual(
      expect.objectContaining({ mode: "audio_call", ownerId: "call:regression-1" })
    );
    expect(audioSession.startAudioSession).toHaveBeenCalledTimes(1);
  });

  it("never lets a foreign owner stop an active call's audio session", async () => {
    const audioSession = audioSessionDouble();
    await activateRealtimeAudioSession(audioSession, "video_call", "call:regression-2");

    await expect(releaseRealtimeAudioSession(audioSession, "live:some-broadcast")).resolves.toBe(false);
    expect(audioSession.stopAudioSession).not.toHaveBeenCalled();
    expect(getActiveRealtimeAudioOwner()).toEqual(expect.objectContaining({ ownerId: "call:regression-2" }));
  });

  it("releases the call's own audio session exactly once and clears ownership", async () => {
    const audioSession = audioSessionDouble();
    await activateRealtimeAudioSession(audioSession, "audio_call", "call:regression-3");

    await expect(releaseRealtimeAudioSession(audioSession, "call:regression-3")).resolves.toBe(true);
    expect(audioSession.stopAudioSession).toHaveBeenCalledTimes(1);
    expect(getActiveRealtimeAudioOwner()).toBeNull();
  });

  it("is idempotent - a repeated release by the same call does not stop the session twice", async () => {
    const audioSession = audioSessionDouble();
    await activateRealtimeAudioSession(audioSession, "audio_call", "call:regression-4");

    await releaseRealtimeAudioSession(audioSession, "call:regression-4");
    await expect(releaseRealtimeAudioSession(audioSession, "call:regression-4")).resolves.toBe(false);
    expect(audioSession.stopAudioSession).toHaveBeenCalledTimes(1);
  });
});

describe("PROTECTED: calls always win the audio session", () => {
  beforeEach(resetOwnership);

  it("lets an incoming call take ownership while a livestream viewer holds it", async () => {
    const liveSession = audioSessionDouble();
    const callSession = audioSessionDouble();

    await activateRealtimeAudioSession(liveSession, "live_viewer", "live:viewer-99");
    await activateRealtimeAudioSession(callSession, "audio_call", "call:regression-5");

    expect(getActiveRealtimeAudioOwner()).toEqual(
      expect.objectContaining({ mode: "audio_call", ownerId: "call:regression-5" })
    );
    expect(callSession.startAudioSession).toHaveBeenCalledTimes(1);
  });

  it("lets an incoming call take ownership while a livestream host holds it", async () => {
    const liveSession = audioSessionDouble();
    const callSession = audioSessionDouble();

    await activateRealtimeAudioSession(liveSession, "live_host", "live:host-99");
    await activateRealtimeAudioSession(callSession, "video_call", "call:regression-6");

    expect(getActiveRealtimeAudioOwner()).toEqual(
      expect.objectContaining({ mode: "video_call", ownerId: "call:regression-6" })
    );
  });

  it("still lets the call release cleanly after displacing a livestream", async () => {
    const liveSession = audioSessionDouble();
    const callSession = audioSessionDouble();

    await activateRealtimeAudioSession(liveSession, "live_host", "live:host-98");
    await activateRealtimeAudioSession(callSession, "audio_call", "call:regression-7");
    await expect(releaseRealtimeAudioSession(callSession, "call:regression-7")).resolves.toBe(true);

    expect(getActiveRealtimeAudioOwner()).toBeNull();
  });
});
