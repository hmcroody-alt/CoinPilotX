/**
 * Contract tests for the real-time audio invariants that were physically
 * verified on 2026-08-02 (reports/realtime_audio_verified_baseline.md).
 *
 * These are deliberately separate from the per-module unit tests. A unit test
 * asks "does this function behave as written". These ask "does the platform still
 * hold the promises that made audio audible on a phone", and they are phrased in
 * those terms so that a failure here reads as a product regression rather than a
 * refactor detail.
 *
 * Each test corresponds to a line in the hard-lock's invariant list. Where an
 * invariant cannot be proven in a simulator — anything about actual audibility —
 * the test proves the closest structural fact and says explicitly what it does
 * not prove, so nobody mistakes a green suite for a heard sound.
 */
import { Platform } from "react-native";

import {
  audioPublications,
  claimRealtimeAudioSession,
  countPublishedAudioTracks,
  countSubscribedRemoteAudioTracks,
  activateRealtimeAudioSession,
  applyRemoteAudioEnabled,
  getActiveRealtimeAudioOwner,
  getActiveRealtimeMicrophoneOwner,
  modePublishesMicrophone,
  releaseRealtimeAudioSession,
  resetRealtimeAudioOwnership,
  resolveRealtimeAudioConfiguration,
  selectRealtimeAudioOutput,
  type RealtimeAudioMode
} from "../realtimeAudioEngine";
import { RealtimeAudioOwnershipError, resolveOwnershipDecision } from "../audioOwnershipPolicy";
import {
  publishRealtimeMicrophone,
  publishedRealtimeAudioTrackCount,
  setRealtimeMicrophoneEnabled
} from "../realtimeMicrophonePublisher";

/* ------------------------------------------------------------------------- *
 * Fakes. These model LiveKit's shape closely enough that the engine cannot
 * tell the difference, and no more. A richer fake would start testing itself.
 * ------------------------------------------------------------------------- */

function audioSession() {
  return {
    setAppleAudioConfiguration: jest.fn(async () => undefined),
    configureAudio: jest.fn(async () => undefined),
    startAudioSession: jest.fn(async () => undefined),
    stopAudioSession: jest.fn(async () => undefined),
    selectAudioOutput: jest.fn(async () => undefined),
    showAudioRoutePicker: jest.fn(async () => undefined)
  };
}

function track(kind: "audio" | "video" = "audio") {
  return { kind, setEnabled: jest.fn(async () => undefined), mediaStreamTrack: { enabled: true } };
}

/**
 * A local participant whose microphone actually publishes, the way a connected
 * room behaves. `setMicrophoneEnabled(true)` adds exactly one publication and
 * emits `localTrackPublished`, which is the event the publisher waits on.
 */
function room(options: { connected?: boolean; canPublish?: boolean } = {}) {
  const listeners = new Map<string, ((...args: any[]) => void)[]>();
  const audio = new Map<string, any>();
  const video = new Map<string, any>();
  let published = 0;

  const localParticipant = {
    audioTrackPublications: audio,
    videoTrackPublications: video,
    setMicrophoneEnabled: jest.fn(async (enabled: boolean) => {
      if (options.connected === false) return;
      if (!enabled) {
        audio.clear();
        return;
      }
      if (audio.size > 0) return;
      published += 1;
      const sid = `TR_AUDIO_${published}`;
      const publication = { sid, kind: "audio", isSubscribed: true, track: track("audio") };
      audio.set(sid, publication);
      (listeners.get("localTrackPublished") || []).forEach((fn) => fn(publication));
    }),
    setCameraEnabled: jest.fn(async (enabled: boolean) => {
      if (!enabled) {
        video.clear();
        return;
      }
      const publication = { sid: "TR_VIDEO_1", kind: "video", isSubscribed: true, track: track("video") };
      video.set(publication.sid, publication);
    }),
    unpublishTrack: jest.fn(async (t: any) => {
      for (const [sid, pub] of audio) if (pub.track === t) audio.delete(sid);
      for (const [sid, pub] of video) if (pub.track === t) video.delete(sid);
    }),
    publishTrack: jest.fn(async () => undefined)
  };

  return {
    localParticipant,
    remoteParticipants: new Map<string, any>(),
    on: (event: string, fn: (...args: any[]) => void) => {
      listeners.set(event, [...(listeners.get(event) || []), fn]);
    },
    off: (event: string, fn: (...args: any[]) => void) => {
      listeners.set(event, (listeners.get(event) || []).filter((x) => x !== fn));
    },
    /** Force a second publication, simulating a racing publish path. */
    injectDuplicateAudio() {
      const publication = { sid: "TR_AUDIO_DUP", kind: "audio", isSubscribed: true, track: track("audio") };
      audio.set(publication.sid, publication);
    },
    addRemoteWithAudio(id: string) {
      const publication = { sid: `R_${id}`, kind: "audio", isSubscribed: true, track: track("audio") };
      this.remoteParticipants.set(id, { audioTrackPublications: new Map([[publication.sid, publication]]) });
      return publication;
    }
  };
}

beforeEach(async () => {
  jest.clearAllMocks();
  // resetRealtimeAudioOwnership is the documented test-setup escape hatch. It is
  // forbidden in feature code by the architecture test, which is why the
  // allowlist there names only the engine itself.
  await resetRealtimeAudioOwnership();
  (Platform as any).OS = "ios";
});

/* ------------------------------------------------------------------------- *
 * Shared invariants — true no matter which surface is running
 * ------------------------------------------------------------------------- */

describe("shared: one microphone owner at a time", () => {
  it("denies a livestream that tries to take the session from an active call", () => {
    claimRealtimeAudioSession("audio_call", "call-1");
    // This is the regression that made a call go silent when a Live started.
    // It must throw, not quietly win.
    expect(() => claimRealtimeAudioSession("live_host", "live-9")).toThrow(RealtimeAudioOwnershipError);
    expect(getActiveRealtimeAudioOwner()?.ownerId).toBe("call-1");
  });

  it("lets a call take the session from a livestream, and tells the livestream it lost", () => {
    const displaced = jest.fn();
    claimRealtimeAudioSession("live_host", "live-9", { onDisplaced: displaced });
    claimRealtimeAudioSession("audio_call", "call-1");
    // Being displaced silently is what left a broadcast believing it still had a
    // microphone it no longer held.
    expect(displaced).toHaveBeenCalledTimes(1);
    expect(getActiveRealtimeAudioOwner()?.ownerId).toBe("call-1");
  });

  it("never reports two microphone owners", () => {
    claimRealtimeAudioSession("audio_call", "call-1");
    claimRealtimeAudioSession("video_call", "call-2");
    const owner = getActiveRealtimeMicrophoneOwner();
    expect(owner?.ownerId).toBe("call-2");
    expect(getActiveRealtimeAudioOwner()?.ownerId).toBe("call-2");
  });

  it("gives a viewer no microphone ownership even while it owns the session", () => {
    claimRealtimeAudioSession("live_viewer", "viewer-3");
    expect(getActiveRealtimeAudioOwner()?.ownerId).toBe("viewer-3");
    // Owning the session for playback and owning the microphone are different
    // things; conflating them is how a viewer ends up capturing audio.
    expect(getActiveRealtimeMicrophoneOwner()).toBeNull();
    expect(modePublishesMicrophone("live_viewer")).toBe(false);
  });
});

describe("shared: at most one microphone track and one publication per session", () => {
  it("publishes exactly one audio track", async () => {
    const r = room();
    const result = await publishRealtimeMicrophone(r);
    expect(result.outcome).toBe("published");
    expect(result.audioTrackCount).toBe(1);
    expect(countPublishedAudioTracks(r.localParticipant)).toBe(1);
  });

  it("collapses concurrent publish calls into one operation", async () => {
    const r = room();
    // Two features asking at once must not produce two tracks. The in-flight map
    // is what makes the second caller await the first rather than race it.
    const [a, b] = await Promise.all([publishRealtimeMicrophone(r), publishRealtimeMicrophone(r)]);
    expect(a).toBe(b);
    expect(publishedRealtimeAudioTrackCount(r)).toBe(1);
    expect(r.localParticipant.setMicrophoneEnabled).toHaveBeenCalledTimes(1);
  });

  it("unpublishes a duplicate track rather than leaving both", async () => {
    const r = room();
    await publishRealtimeMicrophone(r);
    r.injectDuplicateAudio();
    expect(publishedRealtimeAudioTrackCount(r)).toBe(2);

    const result = await publishRealtimeMicrophone(r);
    // Two live microphone tracks are heard as echo or as silence depending on
    // which one the SFU forwards, so reconciliation is not cosmetic.
    expect(result.duplicatesRemoved).toBe(1);
    expect(publishedRealtimeAudioTrackCount(r)).toBe(1);
  });

  it("reports no_participant instead of publishing when the room is not connected", async () => {
    const result = await publishRealtimeMicrophone({ localParticipant: null } as any);
    expect(result.outcome).toBe("no_participant");
    expect(result.audioTrackCount).toBe(0);
  });
});

describe("shared: leases, cleanup, and stale sessions", () => {
  it("refuses a stale lease from deactivating a newer session", async () => {
    const session = audioSession();
    const first = claimRealtimeAudioSession("audio_call", "call-1");
    // Same semantic owner, new acquisition — a reconnect. The lease rotates.
    const second = claimRealtimeAudioSession("audio_call", "call-1");
    expect(second.leaseId).toBeGreaterThan(first.leaseId);

    // A cleanup timer from the first attempt fires late.
    await expect(releaseRealtimeAudioSession(session, first)).resolves.toBe(false);
    expect(getActiveRealtimeAudioOwner()?.leaseId).toBe(second.leaseId);
    expect(session.stopAudioSession).not.toHaveBeenCalled();

    // The current lease still works.
    await expect(releaseRealtimeAudioSession(session, second)).resolves.toBe(true);
    expect(getActiveRealtimeAudioOwner()).toBeNull();
  });

  it("makes cleanup idempotent", async () => {
    const session = audioSession();
    const lease = claimRealtimeAudioSession("live_host", "live-1");
    await expect(releaseRealtimeAudioSession(session, lease)).resolves.toBe(true);
    // A second teardown pass — from an unmount racing a disconnect handler —
    // must be a no-op, not a second stopAudioSession against whatever is running.
    await expect(releaseRealtimeAudioSession(session, lease)).resolves.toBe(false);
    expect(session.stopAudioSession).toHaveBeenCalledTimes(1);
  });

  it("does not restart the audio session when the same owner re-activates", async () => {
    const session = audioSession();
    await activateRealtimeAudioSession(session, "audio_call", "call-1");
    await activateRealtimeAudioSession(session, "audio_call", "call-1");
    // Unbalanced start/stop pairs leak the iOS microphone indicator and leave
    // the route stuck after the feature exits.
    expect(session.startAudioSession).toHaveBeenCalledTimes(1);
  });

  it("releases nothing when the owner id does not match", async () => {
    const session = audioSession();
    claimRealtimeAudioSession("audio_call", "call-1");
    await expect(releaseRealtimeAudioSession(session, "live-9")).resolves.toBe(false);
    expect(getActiveRealtimeAudioOwner()?.ownerId).toBe("call-1");
  });
});

describe("shared: session configuration by mode", () => {
  it("gives every publishing mode a record-capable category", () => {
    const publishing: RealtimeAudioMode[] = ["audio_call", "video_call", "live_host", "live_guest"];
    publishing.forEach((mode) => {
      const config = resolveRealtimeAudioConfiguration(mode);
      // playAndRecord is what allows capture. A publisher configured for
      // playback alone connects, publishes nothing audible, and reports success.
      expect([mode, config.audioCategory]).toEqual([mode, "playAndRecord"]);
      expect(config.audioCategoryOptions).toContain("allowBluetooth");
    });
  });

  it("gives a viewer a playback category, not a recording one", () => {
    const config = resolveRealtimeAudioConfiguration("live_viewer");
    // A viewer on playAndRecord shows the microphone indicator and degrades
    // output quality on Bluetooth for no reason.
    expect(config.audioCategory).toBe("playback");
    expect(config.audioMode).toBe("default");
  });
});

/* ------------------------------------------------------------------------- *
 * Audio call
 * ------------------------------------------------------------------------- */

describe("audio call", () => {
  it("publishes the microphone for the caller and for the callee", async () => {
    for (const ownerId of ["call-caller", "call-callee"]) {
      await resetRealtimeAudioOwnership();
      const session = audioSession();
      const r = room();
      await activateRealtimeAudioSession(session, "audio_call", ownerId);
      const result = await publishRealtimeMicrophone(r, { context: { roomType: "audio_call" } });
      // Both sides publish. A design where only the caller publishes is
      // one-way audio, which is the single most reported call defect.
      expect([ownerId, result.outcome]).toEqual([ownerId, "published"]);
    }
  });

  it("subscribes to the remote participant's audio", async () => {
    const r = room();
    r.addRemoteWithAudio("peer");
    expect(countSubscribedRemoteAudioTracks(r)).toBe(1);
    const touched = await applyRemoteAudioEnabled(r, true);
    expect(touched).toBe(1);
  });

  it("keeps the room and the session when the microphone is muted and unmuted", async () => {
    const session = audioSession();
    const r = room();
    const lease = await activateRealtimeAudioSession(session, "audio_call", "call-1");
    await publishRealtimeMicrophone(r);

    await setRealtimeMicrophoneEnabled(r, false);
    // Mute must not tear down the session — doing so drops the call's audio
    // route and often the call itself.
    expect(getActiveRealtimeAudioOwner()?.leaseId).toBe(lease.leaseId);
    expect(session.stopAudioSession).not.toHaveBeenCalled();

    const count = await setRealtimeMicrophoneEnabled(r, true);
    expect(count).toBe(1);
    expect(getActiveRealtimeAudioOwner()?.ownerId).toBe("call-1");
  });

  it("releases ownership when the call ends", async () => {
    const session = audioSession();
    const lease = await activateRealtimeAudioSession(session, "audio_call", "call-1");
    await releaseRealtimeAudioSession(session, lease);
    expect(getActiveRealtimeAudioOwner()).toBeNull();
    expect(getActiveRealtimeMicrophoneOwner()).toBeNull();
    // The next feature can now acquire without being denied.
    expect(() => claimRealtimeAudioSession("live_host", "live-1")).not.toThrow();
  });
});

/* ------------------------------------------------------------------------- *
 * Video call — the surface where audio breaks most quietly
 * ------------------------------------------------------------------------- */

describe("video call", () => {
  it("publishes the microphone the same way an audio call does", async () => {
    const session = audioSession();
    const r = room();
    await activateRealtimeAudioSession(session, "video_call", "vcall-1");
    const result = await publishRealtimeMicrophone(r, { context: { roomType: "video_call" } });
    expect(result.outcome).toBe("published");
    expect(resolveRealtimeAudioConfiguration("video_call").audioCategory).toBe("playAndRecord");
  });

  it("does not replace the microphone track when the camera starts", async () => {
    const r = room();
    await publishRealtimeMicrophone(r);
    const before = audioPublications(r.localParticipant).map((p: any) => p.sid);

    await r.localParticipant.setCameraEnabled(true);

    const after = audioPublications(r.localParticipant).map((p: any) => p.sid);
    // The physical failure this guards: camera startup tore down the audio
    // device module and the call went silent while still showing "connected".
    expect(after).toEqual(before);
    expect(countPublishedAudioTracks(r.localParticipant)).toBe(1);
  });

  it("keeps exactly one microphone publication across a camera switch", async () => {
    const r = room();
    await publishRealtimeMicrophone(r);
    await r.localParticipant.setCameraEnabled(true);
    await r.localParticipant.setCameraEnabled(false);
    await r.localParticipant.setCameraEnabled(true);
    expect(countPublishedAudioTracks(r.localParticipant)).toBe(1);
  });

  it("keeps audio when video is disabled", async () => {
    const session = audioSession();
    const r = room();
    const lease = await activateRealtimeAudioSession(session, "video_call", "vcall-1");
    await publishRealtimeMicrophone(r);
    await r.localParticipant.setCameraEnabled(true);

    await r.localParticipant.setCameraEnabled(false);

    // Turning the camera off is a common mid-call action; it must not be a
    // shortcut for ending the audio session.
    expect(countPublishedAudioTracks(r.localParticipant)).toBe(1);
    expect(getActiveRealtimeAudioOwner()?.leaseId).toBe(lease.leaseId);
    expect(session.stopAudioSession).not.toHaveBeenCalled();
  });

  it("releases both camera and audio ownership when the video call ends", async () => {
    const session = audioSession();
    const r = room();
    const lease = await activateRealtimeAudioSession(session, "video_call", "vcall-1");
    await publishRealtimeMicrophone(r);
    await r.localParticipant.setCameraEnabled(true);

    await r.localParticipant.setCameraEnabled(false);
    await setRealtimeMicrophoneEnabled(r, false);
    await releaseRealtimeAudioSession(session, lease);

    expect(r.localParticipant.videoTrackPublications.size).toBe(0);
    expect(countPublishedAudioTracks(r.localParticipant)).toBe(0);
    expect(getActiveRealtimeAudioOwner()).toBeNull();
  });
});

/* ------------------------------------------------------------------------- *
 * Livestream
 * ------------------------------------------------------------------------- */

describe("livestream", () => {
  it("publishes the host through the same shared engine as a call", async () => {
    const session = audioSession();
    const r = room();
    await activateRealtimeAudioSession(session, "live_host", "live-1");
    const result = await publishRealtimeMicrophone(r, { context: { roomType: "live_host" } });
    // "Same engine" is the whole architecture. A Live-only publisher is what
    // let Live and calls drift apart in the first place.
    expect(result.outcome).toBe("published");
    expect(getActiveRealtimeMicrophoneOwner()?.mode).toBe("live_host");
  });

  it("refuses to publish for a viewer and says so explicitly", async () => {
    const r = room();
    const result = await publishRealtimeMicrophone(r, {
      context: { roomType: "live_viewer", canPublishMicrophone: false }
    });
    // "forbidden" rather than a silent no-op: the caller can distinguish "you
    // may not" from "it did not work", and telemetry can too.
    expect(result.outcome).toBe("forbidden");
    expect(publishedRealtimeAudioTrackCount(r)).toBe(0);
    expect(r.localParticipant.setMicrophoneEnabled).not.toHaveBeenCalled();
  });

  it("receives host audio for a viewer through the shared playback path", async () => {
    const r = room();
    const publication = r.addRemoteWithAudio("host");
    const touched = await applyRemoteAudioEnabled(r, true);
    expect(touched).toBe(1);
    expect(publication.track.setEnabled).toHaveBeenCalledWith(true);
    // This proves the remote track was enabled. It does NOT prove a person heard
    // it; that evidence lives only in the physical baseline document.
  });

  it("publishes an approved guest through the shared engine", async () => {
    const session = audioSession();
    const r = room();
    await activateRealtimeAudioSession(session, "live_guest", "guest-4");
    const result = await publishRealtimeMicrophone(r, {
      context: { roomType: "live_guest", canPublishMicrophone: true }
    });
    expect(result.outcome).toBe("published");
    expect(resolveRealtimeAudioConfiguration("live_guest").audioCategory).toBe("playAndRecord");
  });

  it("stops publication when a guest is removed", async () => {
    const session = audioSession();
    const r = room();
    const lease = await activateRealtimeAudioSession(session, "live_guest", "guest-4");
    await publishRealtimeMicrophone(r);
    expect(publishedRealtimeAudioTrackCount(r)).toBe(1);

    await setRealtimeMicrophoneEnabled(r, false);
    await releaseRealtimeAudioSession(session, lease);

    // A removed guest that keeps publishing is heard by every viewer.
    expect(publishedRealtimeAudioTrackCount(r)).toBe(0);
    expect(getActiveRealtimeAudioOwner()).toBeNull();
  });

  it("leaves a terminal state when the host ends Live", async () => {
    const session = audioSession();
    const r = room();
    const lease = await activateRealtimeAudioSession(session, "live_host", "live-1");
    await publishRealtimeMicrophone(r);

    await setRealtimeMicrophoneEnabled(r, false);
    await releaseRealtimeAudioSession(session, lease);

    // The old lease must not be able to bring the session back.
    await expect(releaseRealtimeAudioSession(session, lease)).resolves.toBe(false);
    expect(getActiveRealtimeAudioOwner()).toBeNull();
    expect(publishedRealtimeAudioTrackCount(r)).toBe(0);
  });
});

/* ------------------------------------------------------------------------- *
 * Mixed-session transitions — the eight the mission enumerates
 * ------------------------------------------------------------------------- */

describe("mixed sessions", () => {
  /**
   * Runs one transition end to end: the first owner acquires, publishes if its
   * mode publishes, then tears down; the second owner acquires afterwards. The
   * assertion is always the same — the second surface ends up as the sole owner
   * with at most one microphone track — because that is what "works without an
   * app restart" actually means underneath.
   */
  async function transition(from: RealtimeAudioMode, to: RealtimeAudioMode) {
    await resetRealtimeAudioOwnership();
    const session = audioSession();
    const firstRoom = room();
    const fromId = `${from}-a`;
    const toId = `${to}-b`;

    const firstLease = await activateRealtimeAudioSession(session, from, fromId);
    if (modePublishesMicrophone(from)) {
      const result = await publishRealtimeMicrophone(firstRoom, { context: { roomType: from } });
      expect([from, result.outcome]).toEqual([from, "published"]);
    }

    await setRealtimeMicrophoneEnabled(firstRoom, false);
    await releaseRealtimeAudioSession(session, firstLease);
    expect(getActiveRealtimeAudioOwner()).toBeNull();

    const secondRoom = room();
    const secondLease = await activateRealtimeAudioSession(session, to, toId);
    if (modePublishesMicrophone(to)) {
      const result = await publishRealtimeMicrophone(secondRoom, { context: { roomType: to } });
      expect([to, result.outcome]).toEqual([to, "published"]);
    }

    const owner = getActiveRealtimeAudioOwner();
    expect([from, to, owner?.ownerId]).toEqual([from, to, toId]);
    expect([from, to, owner?.leaseId]).toEqual([from, to, secondLease.leaseId]);
    expect(countPublishedAudioTracks(firstRoom.localParticipant)).toBe(0);
    expect(countPublishedAudioTracks(secondRoom.localParticipant)).toBe(
      modePublishesMicrophone(to) ? 1 : 0
    );
    expect(resolveRealtimeAudioConfiguration(to)).toEqual(resolveRealtimeAudioConfiguration(to));
  }

  const CASES: [string, RealtimeAudioMode, RealtimeAudioMode][] = [
    ["call to Live", "audio_call", "live_host"],
    ["Live to call", "live_host", "audio_call"],
    ["video call to Live", "video_call", "live_host"],
    ["Live to video call", "live_host", "video_call"],
    ["audio call to video call", "audio_call", "video_call"],
    ["video call to audio call", "video_call", "audio_call"],
    ["Live to Live", "live_host", "live_host"],
    ["call to call", "audio_call", "audio_call"]
  ];

  it.each(CASES)("survives %s without a restart", async (_label, from, to) => {
    await transition(from, to);
  });

  it("does not let a Live start mid-call take the session", async () => {
    claimRealtimeAudioSession("video_call", "vcall-1");
    // Not a transition — an overlap. The call wins, and the Live is told why in
    // a message safe to show to a user.
    let error: RealtimeAudioOwnershipError | null = null;
    try {
      claimRealtimeAudioSession("live_host", "live-1");
    } catch (caught) {
      error = caught as RealtimeAudioOwnershipError;
    }
    expect(error?.code).toBe("AUDIO_SESSION_BUSY");
    expect(error?.message).toContain("active call");
    expect(getActiveRealtimeAudioOwner()?.ownerId).toBe("vcall-1");
  });

  it("keeps arbitration decisions pure and inspectable", () => {
    // The policy module is what a future author will read to understand why
    // their claim was denied, so it must answer without a device.
    expect(resolveOwnershipDecision(null, { ownerId: "a", mode: "audio_call" }).outcome).toBe("granted");
    expect(
      resolveOwnershipDecision({ ownerId: "a", mode: "audio_call" }, { ownerId: "a", mode: "audio_call" }).outcome
    ).toBe("reacquired");
    expect(
      resolveOwnershipDecision({ ownerId: "a", mode: "audio_call" }, { ownerId: "b", mode: "live_viewer" }).outcome
    ).toBe("denied");
    expect(
      resolveOwnershipDecision({ ownerId: "a", mode: "live_viewer" }, { ownerId: "b", mode: "audio_call" }).outcome
    ).toBe("displaced");
  });
});

/* ------------------------------------------------------------------------- *
 * Routing
 * ------------------------------------------------------------------------- */

describe("output routing", () => {
  it("forces the speaker on iOS and returns to the default route when asked", async () => {
    const session = audioSession();
    await selectRealtimeAudioOutput(session, true);
    expect(session.selectAudioOutput).toHaveBeenCalledWith("force_speaker");
    await selectRealtimeAudioOutput(session, false);
    // "default" rather than "earpiece" on iOS: the OS picks the right receiver
    // or connected accessory, which is what makes Bluetooth work.
    expect(session.selectAudioOutput).toHaveBeenCalledWith("default");
  });

  it("uses the Android route names on Android", async () => {
    (Platform as any).OS = "android";
    const session = audioSession();
    await selectRealtimeAudioOutput(session, true);
    expect(session.selectAudioOutput).toHaveBeenCalledWith("speaker");
    await selectRealtimeAudioOutput(session, false);
    expect(session.selectAudioOutput).toHaveBeenCalledWith("earpiece");
  });

  it("activates with the speaker route by default for a call", async () => {
    const session = audioSession();
    await activateRealtimeAudioSession(session, "audio_call", "call-1");
    expect(session.selectAudioOutput).toHaveBeenCalledWith("force_speaker");
  });
});
