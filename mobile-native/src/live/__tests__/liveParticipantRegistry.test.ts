import type { LiveGuest } from "../liveSession";
import {
  buildStageParticipants,
  findByRtcUid,
  focusParticipant,
  liveRoleLabel,
  normalizeLiveRole,
  participantDisplayName,
  publishingRoster,
  shouldRenderVideoTile,
  sortStageParticipants,
  stagePhaseForStatus,
  stageRoster,
  type LiveRtcPresence,
  type LiveStageParticipant
} from "../liveParticipantRegistry";

function guest(overrides: Partial<LiveGuest> = {}): LiveGuest {
  return {
    guestId: 1,
    userId: 22,
    requestId: 0,
    displayName: "Ada",
    avatarUrl: "",
    role: "guest",
    roleLabel: "Guest",
    status: "active",
    audioMuted: false,
    videoEnabled: true,
    joinedAt: "",
    rtcUid: 22,
    layoutPosition: 1,
    ...overrides
  };
}

function presence(overrides: Partial<LiveRtcPresence> = {}): LiveRtcPresence {
  return { rtcUid: 22, hasVideo: true, hasAudio: true, ...overrides };
}

describe("role normalization", () => {
  it("collapses historical spellings onto the canonical vocabulary", () => {
    expect(normalizeLiveRole("co-host")).toBe("cohost");
    expect(normalizeLiveRole("CO_HOST")).toBe("cohost");
    expect(normalizeLiveRole("moderator")).toBe("cohost");
    expect(normalizeLiveRole("creator")).toBe("host");
    expect(normalizeLiveRole("panelist")).toBe("guest");
    expect(normalizeLiveRole("viewer")).toBe("audience");
  });

  it("degrades unknown roles to audience rather than granting publishing", () => {
    expect(normalizeLiveRole("superhost")).toBe("audience");
    expect(normalizeLiveRole(undefined)).toBe("audience");
    expect(normalizeLiveRole(null)).toBe("audience");
    expect(normalizeLiveRole(7)).toBe("audience");
  });

  it("labels every role", () => {
    expect(liveRoleLabel("host")).toBe("Host");
    expect(liveRoleLabel("cohost")).toBe("Co-host");
    expect(liveRoleLabel("guest")).toBe("Guest");
    expect(liveRoleLabel("viewer")).toBe("Viewer");
  });
});

describe("stage phases", () => {
  it("maps server guest statuses onto phases", () => {
    expect(stagePhaseForStatus("invited")).toBe("invited");
    expect(stagePhaseForStatus("accepted")).toBe("accepted");
    expect(stagePhaseForStatus("joining")).toBe("preparing");
    expect(stagePhaseForStatus("publishing")).toBe("joining");
    expect(stagePhaseForStatus("live")).toBe("live");
    expect(stagePhaseForStatus("removed")).toBe("left");
  });

  it("treats an unknown status as preparing, never as live", () => {
    // A spinner for someone who is publishing is a much smaller failure than a
    // black tile for someone who is not.
    expect(stagePhaseForStatus("banana")).toBe("preparing");
    expect(stagePhaseForStatus(undefined)).toBe("preparing");
  });
});

describe("buildStageParticipants", () => {
  const roster = { hostUserId: 11, hostDisplayName: "Grace", hostAvatarUrl: "g.png", guests: [] as LiveGuest[] };

  it("puts the host first because the server says so, not because they arrived first", () => {
    // The guest is listed before the host in both inputs and still sorts second.
    const built = buildStageParticipants(
      { ...roster, guests: [guest({ userId: 22, rtcUid: 22 })] },
      [presence({ rtcUid: 22 }), presence({ rtcUid: 11 })]
    );
    expect(built.map((p) => p.displayName)).toEqual(["Grace", "Ada"]);
    expect(built[0].isHost).toBe(true);
    expect(built[1].isHost).toBe(false);
  });

  it("keeps the host flagged as host even when they connect after a guest", () => {
    // The old positional guess made the guest "the host" in exactly this case,
    // and the audience screen then showed the wrong person's stream.
    const built = buildStageParticipants(
      { ...roster, guests: [guest({ userId: 22, rtcUid: 22 })] },
      [presence({ rtcUid: 22 })]
    );
    expect(built[0].displayName).toBe("Grace");
    expect(built[0].isHost).toBe(true);
    expect(built[0].phase).toBe("joining");
  });

  it("resolves remote identity by uid, never by arrival order", () => {
    const built = buildStageParticipants(
      {
        ...roster,
        guests: [
          guest({ guestId: 1, userId: 22, rtcUid: 22, displayName: "Ada", layoutPosition: 1 }),
          guest({ guestId: 2, userId: 33, rtcUid: 33, displayName: "Alan", layoutPosition: 2 })
        ]
      },
      [presence({ rtcUid: 33 }), presence({ rtcUid: 22 }), presence({ rtcUid: 11 })]
    );
    expect(findByRtcUid(built, 33)?.displayName).toBe("Alan");
    expect(findByRtcUid(built, 22)?.displayName).toBe("Ada");
    expect(findByRtcUid(built, "11")?.displayName).toBe("Grace");
  });

  it("never invents a name for a remote publisher", () => {
    const built = buildStageParticipants(roster, [presence({ rtcUid: 99 })]);
    const unknown = findByRtcUid(built, 99);
    expect(unknown?.unidentified).toBe(true);
    expect(unknown?.displayName).toBe("");
    expect(participantDisplayName(unknown)).toBe("");
  });

  it("still renders a publisher the roster has not caught up with", () => {
    // Dropping a real stream because the state poll is a beat behind would be
    // worse than showing a neutral placeholder.
    const built = buildStageParticipants(roster, [presence({ rtcUid: 99 })]);
    expect(built).toHaveLength(2);
    expect(built[built.length - 1].rtcUid).toBe(99);
  });

  it("lists an invited guest before they connect so the UI can show joining", () => {
    const built = buildStageParticipants(
      { ...roster, guests: [guest({ status: "accepted", rtcUid: 22 })] },
      []
    );
    expect(built[1].phase).toBe("accepted");
    expect(built[1].hasVideo).toBe(false);
  });

  it("downgrades a live roster entry with no media to joining", () => {
    const built = buildStageParticipants(
      { ...roster, guests: [guest({ status: "live", rtcUid: 22 })] },
      []
    );
    expect(built[1].phase).toBe("joining");
  });

  it("does not duplicate the host when they also appear as a guest row", () => {
    const built = buildStageParticipants(
      { ...roster, guests: [guest({ guestId: 5, userId: 11, rtcUid: 11, displayName: "Grace" })] },
      [presence({ rtcUid: 11 })]
    );
    expect(built).toHaveLength(1);
    expect(built[0].isHost).toBe(true);
  });

  it("marks the local seat without guessing", () => {
    const built = buildStageParticipants(
      { ...roster, guests: [guest({ rtcUid: 22 })] },
      [presence({ rtcUid: 22 }), presence({ rtcUid: 11 })],
      { rtcUid: 22 }
    );
    expect(findByRtcUid(built, 22)?.isLocal).toBe(true);
    expect(findByRtcUid(built, 11)?.isLocal).toBe(false);
  });

  it("lets a moderator mute outrank the transport view", () => {
    const built = buildStageParticipants(
      { ...roster, guests: [guest({ audioMuted: true, rtcUid: 22 })] },
      [presence({ rtcUid: 22, audioMuted: false, speaking: true })]
    );
    const ada = findByRtcUid(built, 22);
    expect(ada?.audioMuted).toBe(true);
    expect(ada?.speaking).toBe(false);
  });

  it("falls back to userId when an older backend omits rtc_uid", () => {
    const built = buildStageParticipants(
      { ...roster, guests: [guest({ userId: 44, rtcUid: 0, displayName: "Ada" })] },
      [presence({ rtcUid: 44 })]
    );
    expect(findByRtcUid(built, 44)?.displayName).toBe("Ada");
  });

  it("handles an empty session without throwing", () => {
    expect(buildStageParticipants({ hostUserId: 0, guests: [] }, [])).toEqual([]);
  });

  it("ignores non-positive uids from the transport", () => {
    const built = buildStageParticipants(roster, [presence({ rtcUid: 0 }), presence({ rtcUid: -3 })]);
    expect(built).toHaveLength(1);
    expect(built[0].isHost).toBe(true);
  });
});

describe("stage ordering", () => {
  function participant(overrides: Partial<LiveStageParticipant>): LiveStageParticipant {
    return {
      rtcUid: 1,
      userId: 1,
      guestId: 0,
      key: "uid-1",
      displayName: "",
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

  it("orders host, then co-hosts, then guests, then unidentified", () => {
    const sorted = sortStageParticipants([
      participant({ rtcUid: 4, role: "guest", unidentified: true, layoutPosition: Number.MAX_SAFE_INTEGER }),
      participant({ rtcUid: 3, role: "guest", layoutPosition: 2 }),
      participant({ rtcUid: 2, role: "cohost", layoutPosition: 1 }),
      participant({ rtcUid: 1, role: "host", isHost: true })
    ]);
    expect(sorted.map((p) => p.rtcUid)).toEqual([1, 2, 3, 4]);
  });

  it("does not reorder when someone starts or stops speaking", () => {
    // Tiles must not swap places under people mid-sentence. Active speaker is a
    // highlight on a tile, never a change to where the tile sits.
    const base = [
      participant({ rtcUid: 11, role: "host", isHost: true }),
      participant({ rtcUid: 22, layoutPosition: 1 }),
      participant({ rtcUid: 33, layoutPosition: 2 })
    ];
    const quiet = sortStageParticipants(base).map((p) => p.rtcUid);
    const talking = sortStageParticipants(
      base.map((p) => (p.rtcUid === 33 ? { ...p, speaking: true } : p))
    ).map((p) => p.rtcUid);
    expect(talking).toEqual(quiet);
  });

  it("does not reorder when a guest turns their camera off", () => {
    const base = [
      participant({ rtcUid: 11, role: "host", isHost: true }),
      participant({ rtcUid: 22, layoutPosition: 1 }),
      participant({ rtcUid: 33, layoutPosition: 2 })
    ];
    const before = sortStageParticipants(base).map((p) => p.rtcUid);
    const after = sortStageParticipants(
      base.map((p) => (p.rtcUid === 22 ? { ...p, hasVideo: false } : p))
    ).map((p) => p.rtcUid);
    expect(after).toEqual(before);
  });

  it("breaks layout ties deterministically by uid", () => {
    const sorted = sortStageParticipants([
      participant({ rtcUid: 55, layoutPosition: 0 }),
      participant({ rtcUid: 44, layoutPosition: 0 })
    ]);
    expect(sorted.map((p) => p.rtcUid)).toEqual([44, 55]);
  });

  it("does not mutate its input", () => {
    const input = [participant({ rtcUid: 9 }), participant({ rtcUid: 2, isHost: true, role: "host" })];
    const copy = [...input];
    sortStageParticipants(input);
    expect(input).toEqual(copy);
  });
});

describe("rendering rules", () => {
  it("only gives a video tile to someone live with a camera track", () => {
    const built = buildStageParticipants(
      { hostUserId: 11, hostDisplayName: "Grace", guests: [guest({ rtcUid: 22, status: "live" })] },
      [presence({ rtcUid: 11, hasVideo: true }), presence({ rtcUid: 22, hasVideo: false, hasAudio: true })]
    );
    expect(shouldRenderVideoTile(built[0])).toBe(true);
    // Audio-only guest: on stage, audible, but no black rectangle.
    expect(shouldRenderVideoTile(built[1])).toBe(false);
    expect(built[1].hasAudio).toBe(true);
  });

  it("excludes departed participants from the stage roster", () => {
    const built = buildStageParticipants(
      {
        hostUserId: 11,
        hostDisplayName: "Grace",
        guests: [guest({ guestId: 1, rtcUid: 22, status: "removed" }), guest({ guestId: 2, userId: 33, rtcUid: 33 })]
      },
      [presence({ rtcUid: 11 }), presence({ rtcUid: 33 })]
    );
    expect(stageRoster(built).map((p) => p.rtcUid)).toEqual([11, 33]);
  });

  it("counts everyone actually publishing", () => {
    const built = buildStageParticipants(
      {
        hostUserId: 11,
        hostDisplayName: "Grace",
        guests: [guest({ guestId: 1, rtcUid: 22 }), guest({ guestId: 2, userId: 33, rtcUid: 33, status: "accepted" })]
      },
      [presence({ rtcUid: 11 }), presence({ rtcUid: 22 })]
    );
    expect(publishingRoster(built).map((p) => p.rtcUid)).toEqual([11, 22]);
  });

  it("focuses the host when present and the first publisher otherwise", () => {
    const withHost = buildStageParticipants(
      { hostUserId: 11, hostDisplayName: "Grace", guests: [guest({ rtcUid: 22 })] },
      [presence({ rtcUid: 11 }), presence({ rtcUid: 22 })]
    );
    expect(focusParticipant(withHost)?.rtcUid).toBe(11);

    const hostAway = buildStageParticipants(
      { hostUserId: 11, hostDisplayName: "Grace", guests: [guest({ rtcUid: 22 })] },
      [presence({ rtcUid: 22 })]
    );
    expect(focusParticipant(hostAway)?.rtcUid).toBe(22);
  });

  it("returns no focus when nobody is publishing", () => {
    expect(focusParticipant([])).toBeNull();
  });

  it("names the local participant You only when the server gave no name", () => {
    const built = buildStageParticipants(
      { hostUserId: 11, hostDisplayName: "Grace", guests: [] },
      [presence({ rtcUid: 11 })],
      { rtcUid: 11 }
    );
    expect(participantDisplayName(built[0])).toBe("Grace");
    expect(participantDisplayName({ ...built[0], displayName: "" })).toBe("You");
    expect(participantDisplayName(null)).toBe("");
  });

  it("gives every participant a stable key that survives media changes", () => {
    const first = buildStageParticipants(
      { hostUserId: 11, hostDisplayName: "Grace", guests: [guest({ rtcUid: 22 })] },
      [presence({ rtcUid: 11 }), presence({ rtcUid: 22, hasVideo: true })]
    );
    const second = buildStageParticipants(
      { hostUserId: 11, hostDisplayName: "Grace", guests: [guest({ rtcUid: 22, audioMuted: true })] },
      [presence({ rtcUid: 11 }), presence({ rtcUid: 22, hasVideo: false })]
    );
    expect(second.map((p) => p.key)).toEqual(first.map((p) => p.key));
    expect(new Set(first.map((p) => p.key)).size).toBe(first.length);
  });
});
