/**
 * Stages 20-23, 38-39.
 *
 * Every test below corresponds to a way a working broadcast gets destroyed by
 * something that should have been survivable. The assertions are written
 * against the outcome the audience experiences — "the Live is still running" —
 * rather than against internal state, because that is the property the mission
 * actually requires.
 */
import type { LiveStageParticipant } from "../liveParticipantRegistry";
import {
  LIVE_HOST_GRACE_SECONDS,
  dedupeStageParticipants,
  departureEndsBroadcast,
  hasDuplicateStagePresence,
  hasLivePermission,
  isHostEquivalent,
  permissionsForRole,
  resolveDuplicateConnections,
  resolveExitIntent,
  resolveHostAbsence,
  type ParticipantConnection
} from "../liveSessionLifecycle";

function person(overrides: Partial<LiveStageParticipant> = {}): LiveStageParticipant {
  const rtcUid = overrides.rtcUid ?? 2;
  return {
    rtcUid,
    userId: overrides.userId ?? rtcUid,
    guestId: 0,
    key: `uid-${rtcUid}`,
    displayName: `User ${rtcUid}`,
    avatarUrl: "",
    role: "guest",
    roleLabel: "Guest",
    phase: "live",
    isLocal: false,
    isHost: false,
    hasVideo: true,
    hasAudio: true,
    audioMuted: false,
    speaking: false,
    layoutPosition: 0,
    unidentified: false,
    ...overrides
  };
}

function connection(overrides: Partial<ParticipantConnection> = {}): ParticipantConnection {
  return { userId: 5, deviceId: "phone", rtcUid: 5, joinedAtMs: 1_000, publishing: true, ...overrides };
}

// ---------------------------------------------------------------------------

describe("Stage 20 — leaving the stage is not ending the broadcast", () => {
  it("ends the broadcast only for the host", () => {
    expect(resolveExitIntent({ role: "host", isHost: true }).action).toBe("end");
    expect(departureEndsBroadcast({ role: "host", isHost: true })).toBe(true);
  });

  it("never ends the broadcast for anyone else, whatever their role", () => {
    // The one assertion this whole stage exists for.
    for (const role of ["cohost", "guest", "audience"] as const) {
      expect(departureEndsBroadcast({ role, isHost: false })).toBe(false);
    }
  });

  it("gives a co-host a leave, not an end — they are on stage, not in charge", () => {
    const intent = resolveExitIntent({ role: "cohost", isHost: false });
    expect(intent.action).toBe("leave");
    expect(intent.confirm).toBe(true);
  });

  it("lets a viewer stop watching without a confirmation dialog", () => {
    const intent = resolveExitIntent({ role: "audience", isHost: false });
    expect(intent.action).toBe("stopWatching");
    expect(intent.confirm).toBe(false);
    expect(intent.endsBroadcastForEveryone).toBe(false);
  });

  it("confirms before any exit that changes what other people see", () => {
    for (const role of ["host", "cohost", "guest"] as const) {
      expect(resolveExitIntent({ role, isHost: role === "host" }).confirm).toBe(true);
    }
  });

  it("carries an i18n key for every branch, so the copy cannot be built in a component", () => {
    for (const role of ["host", "cohost", "guest", "audience"] as const) {
      expect(resolveExitIntent({ role, isHost: role === "host" }).labelKey).toMatch(/^extended:live\.exit\./);
    }
  });
});

// ---------------------------------------------------------------------------

describe("Stage 21 — a host who is missing is not a host who has left", () => {
  it("does nothing while the host is connected", () => {
    const decision = resolveHostAbsence({ hostConnected: true, disconnectedForSeconds: 0 });
    expect(decision.status).toBe("present");
    expect(decision.endBroadcast).toBe(false);
    expect(decision.noticeKey).toBe("");
  });

  it("waits, rather than ending, through an ordinary mobile blip", () => {
    // A lift, a tunnel, a cell handover. All of these recover.
    for (const seconds of [1, 5, 20, 45, 89]) {
      const decision = resolveHostAbsence({ hostConnected: false, disconnectedForSeconds: seconds });
      expect(decision.status).toBe("waiting");
      expect(decision.endBroadcast).toBe(false);
    }
  });

  it("keeps the guests publishing for the whole waiting window", () => {
    // A panel must not fall silent because the host walked into a car park.
    for (const seconds of [0, 30, 89]) {
      expect(resolveHostAbsence({ hostConnected: false, disconnectedForSeconds: seconds }).guestsKeepPublishing).toBe(true);
    }
  });

  it("counts down toward the deadline so the banner can be honest", () => {
    expect(resolveHostAbsence({ hostConnected: false, disconnectedForSeconds: 0 }).secondsRemaining).toBe(LIVE_HOST_GRACE_SECONDS);
    expect(resolveHostAbsence({ hostConnected: false, disconnectedForSeconds: 60 }).secondsRemaining).toBe(LIVE_HOST_GRACE_SECONDS - 60);
  });

  it("ends the broadcast exactly at the deadline, not before it", () => {
    const before = resolveHostAbsence({ hostConnected: false, disconnectedForSeconds: LIVE_HOST_GRACE_SECONDS - 1 });
    const at = resolveHostAbsence({ hostConnected: false, disconnectedForSeconds: LIVE_HOST_GRACE_SECONDS });
    expect(before.endBroadcast).toBe(false);
    expect(at.endBroadcast).toBe(true);
    expect(at.status).toBe("expired");
  });

  it("recovers cleanly when the host comes back inside the window", () => {
    expect(resolveHostAbsence({ hostConnected: false, disconnectedForSeconds: 80 }).endBroadcast).toBe(false);
    expect(resolveHostAbsence({ hostConnected: true, disconnectedForSeconds: 0 }).status).toBe("present");
  });

  it("does not wait when the host actually ended the Live", () => {
    // An explicit end is a decision, not an absence.
    const decision = resolveHostAbsence({ hostConnected: false, disconnectedForSeconds: 0, hostEndedExplicitly: true });
    expect(decision.endBroadcast).toBe(true);
    expect(decision.guestsKeepPublishing).toBe(false);
  });

  it("survives a nonsense elapsed time without ending a healthy broadcast", () => {
    for (const seconds of [Number.NaN, -30, Number.POSITIVE_INFINITY]) {
      expect(() => resolveHostAbsence({ hostConnected: true, disconnectedForSeconds: seconds })).not.toThrow();
      expect(resolveHostAbsence({ hostConnected: true, disconnectedForSeconds: seconds }).endBroadcast).toBe(false);
    }
    // Negative and NaN both mean "no measured absence", which must not expire.
    expect(resolveHostAbsence({ hostConnected: false, disconnectedForSeconds: Number.NaN }).endBroadcast).toBe(false);
    expect(resolveHostAbsence({ hostConnected: false, disconnectedForSeconds: -30 }).endBroadcast).toBe(false);
  });
});

// ---------------------------------------------------------------------------

describe("Stage 22 — a co-host is not an alias for the host", () => {
  it("lets a co-host run the room", () => {
    for (const permission of ["publish", "moderateGuests", "inviteGuests", "approveRequests", "moderateChat"] as const) {
      expect(hasLivePermission("cohost", permission)).toBe(true);
    }
  });

  it("does not let a co-host end, reassign, or record someone else's broadcast", () => {
    // These three are what would let a co-host take the Live from its owner.
    expect(hasLivePermission("cohost", "endBroadcast")).toBe(false);
    expect(hasLivePermission("cohost", "assignRoles")).toBe(false);
    expect(hasLivePermission("cohost", "controlRecording")).toBe(false);
  });

  it("fails loudly if a co-host ever becomes host-equivalent", () => {
    expect(isHostEquivalent("host")).toBe(true);
    expect(isHostEquivalent("cohost")).toBe(false);
  });

  it("gives a guest publishing and nothing else", () => {
    expect(permissionsForRole("guest")).toEqual(["publish"]);
  });

  it("gives the audience nothing — an audience member cannot self-promote", () => {
    expect(permissionsForRole("audience")).toEqual([]);
    expect(hasLivePermission("audience", "publish")).toBe(false);
  });

  it("never grants a permission the host does not have", () => {
    const host = permissionsForRole("host");
    for (const role of ["cohost", "guest", "audience"] as const) {
      for (const permission of permissionsForRole(role)) {
        expect(host).toContain(permission);
      }
    }
  });
});

// ---------------------------------------------------------------------------

describe("Stages 23 and 39 — the same person, twice", () => {
  it("leaves a single connection alone", () => {
    const resolution = resolveDuplicateConnections([connection()]);
    expect(resolution?.demote).toEqual([]);
    expect(resolution?.reason).toBe("single_connection");
  });

  it("keeps the reconnect and demotes the stale connection", () => {
    const stale = connection({ rtcUid: 5, joinedAtMs: 1_000 });
    const fresh = connection({ rtcUid: 5, joinedAtMs: 9_000 });
    const resolution = resolveDuplicateConnections([stale, fresh]);
    expect(resolution?.keep).toBe(fresh);
    expect(resolution?.demote).toEqual([stale]);
    expect(resolution?.reason).toBe("reconnect_supersedes_stale");
  });

  it("gives the stage to the newest device when a user joins from two", () => {
    const phone = connection({ deviceId: "phone", joinedAtMs: 1_000 });
    const tablet = connection({ deviceId: "tablet", joinedAtMs: 5_000 });
    const resolution = resolveDuplicateConnections([phone, tablet]);
    expect(resolution?.keep).toBe(tablet);
    expect(resolution?.reason).toBe("newest_device_wins");
  });

  it("demotes rather than disconnects, so the losing device keeps watching", () => {
    const resolution = resolveDuplicateConnections([
      connection({ deviceId: "phone", joinedAtMs: 1_000 }),
      connection({ deviceId: "tablet", joinedAtMs: 5_000 })
    ]);
    expect(resolution?.demote).toHaveLength(1);
    // Exactly one connection publishes: two in one room is the echo bug.
    expect([resolution?.keep, ...(resolution?.demote ?? [])]).toHaveLength(2);
  });

  it("resolves a same-millisecond tie the same way every time", () => {
    // Two clients that disagree here each demote the other's keeper and nobody
    // ends up publishing.
    const a = connection({ rtcUid: 5, joinedAtMs: 4_000, deviceId: "a" });
    const b = connection({ rtcUid: 6, joinedAtMs: 4_000, deviceId: "b" });
    expect(resolveDuplicateConnections([a, b])?.keep).toBe(b);
    expect(resolveDuplicateConnections([b, a])?.keep).toBe(b);
  });

  it("returns null rather than guessing when there is nothing to resolve", () => {
    expect(resolveDuplicateConnections([])).toBeNull();
    expect(resolveDuplicateConnections([connection({ joinedAtMs: Number.NaN })])).toBeNull();
  });
});

// ---------------------------------------------------------------------------

describe("Stage 38 — a roster that shows one person once", () => {
  it("leaves a clean roster untouched", () => {
    const roster = [person({ rtcUid: 1, isHost: true, role: "host" }), person({ rtcUid: 2 }), person({ rtcUid: 3 })];
    expect(dedupeStageParticipants(roster)).toEqual(roster);
    expect(hasDuplicateStagePresence(roster)).toBe(false);
  });

  it("collapses a reconnect that has not yet timed out", () => {
    // The failure: a panel of four presents as full because one guest is
    // counted twice across a reconnect.
    const roster = [
      person({ rtcUid: 1, userId: 1, isHost: true, role: "host" }),
      person({ rtcUid: 2, userId: 2, phase: "joining", hasVideo: false, hasAudio: false }),
      person({ rtcUid: 12, userId: 2, phase: "live" })
    ];
    const deduped = dedupeStageParticipants(roster);
    expect(deduped).toHaveLength(2);
    expect(deduped.map((entry) => entry.rtcUid)).toEqual([1, 12]);
    expect(hasDuplicateStagePresence(deduped)).toBe(false);
  });

  it("never drops the local tile, whatever the remote view claims", () => {
    // Dropping it blacks out the user's own preview.
    const roster = [
      person({ rtcUid: 7, userId: 7, isLocal: true, phase: "joining", hasVideo: false, hasAudio: false }),
      person({ rtcUid: 17, userId: 7, phase: "live" })
    ];
    const deduped = dedupeStageParticipants(roster);
    expect(deduped).toHaveLength(1);
    expect(deduped[0].isLocal).toBe(true);
  });

  it("never drops the host tile", () => {
    const roster = [
      person({ rtcUid: 1, userId: 1, isHost: true, role: "host" }),
      person({ rtcUid: 11, userId: 1, role: "guest" })
    ];
    expect(dedupeStageParticipants(roster)[0].isHost).toBe(true);
  });

  it("prefers the connection that actually has media", () => {
    const roster = [
      person({ rtcUid: 4, userId: 4, hasVideo: false, hasAudio: false }),
      person({ rtcUid: 14, userId: 4, hasVideo: true, hasAudio: true })
    ];
    expect(dedupeStageParticipants(roster)[0].rtcUid).toBe(14);
  });

  it("keeps two unidentified tiles apart — they are two people until proven otherwise", () => {
    const roster = [
      person({ rtcUid: 30, userId: 0, unidentified: true }),
      person({ rtcUid: 31, userId: 0, unidentified: true })
    ];
    expect(dedupeStageParticipants(roster)).toHaveLength(2);
    expect(hasDuplicateStagePresence(roster)).toBe(false);
  });

  it("preserves stage order, because the layout depends on it", () => {
    const roster = [
      person({ rtcUid: 1, userId: 1, isHost: true, role: "host" }),
      person({ rtcUid: 2, userId: 2 }),
      person({ rtcUid: 3, userId: 3 }),
      person({ rtcUid: 13, userId: 3, phase: "joining", hasVideo: false, hasAudio: false })
    ];
    expect(dedupeStageParticipants(roster).map((entry) => entry.userId)).toEqual([1, 2, 3]);
  });

  it("survives an empty roster", () => {
    expect(dedupeStageParticipants([])).toEqual([]);
    expect(hasDuplicateStagePresence([])).toBe(false);
  });
});
