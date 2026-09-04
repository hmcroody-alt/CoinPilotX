import type { LiveStageParticipant } from "../liveParticipantRegistry";
import {
  DEFAULT_ACTIVE_SPEAKER_CONFIG,
  INITIAL_ACTIVE_SPEAKER_STATE,
  STAGE_LAYOUT_CAPACITY,
  activeSpeakerParticipant,
  applyActiveSpeaker,
  layoutIdentity,
  planStageLayout,
  reduceActiveSpeaker,
  type ActiveSpeakerState
} from "../liveStageLayout";

function person(overrides: Partial<LiveStageParticipant> = {}): LiveStageParticipant {
  const rtcUid = overrides.rtcUid ?? 1;
  return {
    rtcUid,
    userId: rtcUid,
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

function stage(count: number): LiveStageParticipant[] {
  return Array.from({ length: count }, (_, index) =>
    person({ rtcUid: index + 1, isHost: index === 0, role: index === 0 ? "host" : "guest", layoutPosition: index })
  );
}

describe("stage layout", () => {
  it("gives a lone host the whole stage", () => {
    const layout = planStageLayout(stage(1));
    expect(layout.mode).toBe("solo");
    expect(layout.tiles).toHaveLength(1);
    expect(layout.tiles[0].heightRatio).toBe(1);
  });

  it("splits unevenly for two, so it does not read as a video call", () => {
    const layout = planStageLayout(stage(2));
    expect(layout.mode).toBe("split");
    expect(layout.tiles[0].heightRatio).toBeGreaterThan(layout.tiles[1].heightRatio);
  });

  it("keeps the host the largest tile at every population", () => {
    for (let count = 1; count <= STAGE_LAYOUT_CAPACITY; count += 1) {
      const layout = planStageLayout(stage(count));
      const [first, ...others] = layout.tiles;
      expect(first.participant.isHost).toBe(true);
      expect(first.featured).toBe(true);
      for (const other of others) {
        // Same row would mean the host is merely one cell among equals.
        expect(other.row).toBeGreaterThan(0);
        expect(first.heightRatio).toBeGreaterThanOrEqual(other.heightRatio);
        expect(first.columnSpan).toBeGreaterThanOrEqual(other.columnSpan);
      }
    }
  });

  it("widens rather than shrinking the host as guests arrive", () => {
    expect(planStageLayout(stage(4)).columns).toBe(2);
    expect(planStageLayout(stage(8)).columns).toBe(3);
    expect(planStageLayout(stage(4)).tiles[0].columnSpan).toBe(2);
    expect(planStageLayout(stage(8)).tiles[0].columnSpan).toBe(3);
  });

  it("places every publisher exactly once, with no gaps and no collisions", () => {
    for (let count = 1; count <= STAGE_LAYOUT_CAPACITY; count += 1) {
      const layout = planStageLayout(stage(count));
      expect(layout.tiles).toHaveLength(count);
      expect(new Set(layout.tiles.map((tile) => tile.key)).size).toBe(count);
      expect(new Set(layout.tiles.map((tile) => `${tile.row}:${tile.column}`)).size).toBe(count);
      expect(layout.overflow).toBe(0);
    }
  });

  it("reserves a slot for a guest who is still joining, so the stage does not jump", () => {
    // The guest has no media yet. They hold their place and show an avatar
    // rather than appearing suddenly and pushing everyone sideways.
    const roster = [...stage(2)];
    roster[1] = person({ ...roster[1], phase: "joining", hasVideo: false });
    const layout = planStageLayout(roster);
    expect(layout.tiles).toHaveLength(2);
    expect(layout.tiles[1].showsVideo).toBe(false);
  });

  it("does not mount a video surface for a live participant with no camera track", () => {
    const roster = stage(2);
    roster[1] = person({ ...roster[1], hasVideo: false, hasAudio: true });
    expect(planStageLayout(roster).tiles[1].showsVideo).toBe(false);
  });

  it("drops people who have left", () => {
    const roster = [...stage(3)];
    roster[2] = person({ ...roster[2], phase: "left" });
    expect(planStageLayout(roster).tiles).toHaveLength(2);
  });

  it("reports overflow rather than silently truncating beyond capacity", () => {
    const layout = planStageLayout(stage(STAGE_LAYOUT_CAPACITY + 3));
    expect(layout.tiles).toHaveLength(STAGE_LAYOUT_CAPACITY);
    expect(layout.overflow).toBe(3);
  });

  it("survives an empty stage", () => {
    expect(planStageLayout([]).tiles).toHaveLength(0);
  });

  it("treats a volume change as the same layout, so video surfaces are not remounted", () => {
    const quiet = stage(4);
    const loud = quiet.map((participant, index) => ({ ...participant, speaking: index === 2 }));
    expect(layoutIdentity(planStageLayout(loud))).toBe(layoutIdentity(planStageLayout(quiet)));
  });

  it("treats an arrival as a different layout", () => {
    expect(layoutIdentity(planStageLayout(stage(3)))).not.toBe(layoutIdentity(planStageLayout(stage(4))));
  });
});

describe("active speaker", () => {
  const config = DEFAULT_ACTIVE_SPEAKER_CONFIG;
  const loud = (rtcUid: number, volume = 90) => [{ rtcUid, volume }];

  function run(
    steps: Array<{ volumes: Array<{ rtcUid: number; volume: number }>; at: number }>,
    from: ActiveSpeakerState = INITIAL_ACTIVE_SPEAKER_STATE
  ): ActiveSpeakerState {
    return steps.reduce((state, step) => reduceActiveSpeaker(state, step.volumes, step.at, config), from);
  }

  it("highlights the first person to speak on a silent stage", () => {
    expect(run([{ volumes: loud(5), at: 1000 }]).activeUid).toBe(5);
  });

  it("ignores noise below the speaking threshold", () => {
    expect(run([{ volumes: [{ rtcUid: 5, volume: config.threshold - 1 }], at: 1000 }]).activeUid).toBe(0);
  });

  it("discards uid 0, rather than highlighting a phantom participant", () => {
    expect(run([{ volumes: [{ rtcUid: 0, volume: 200 }], at: 1000 }]).activeUid).toBe(0);
  });

  it("does not hand the highlight over on a single louder report", () => {
    // Backchannel — a laugh or an "mm-hm" — must not steal the highlight.
    const state = run([
      { volumes: loud(5), at: 0 },
      { volumes: loud(6, 200), at: 5000 }
    ]);
    expect(state.activeUid).toBe(5);
    expect(state.challengerUid).toBe(6);
  });

  it("hands over once a challenger sustains the lead", () => {
    const state = run([
      { volumes: loud(5), at: 0 },
      { volumes: loud(6, 200), at: 5000 },
      { volumes: loud(6, 200), at: 5200 }
    ]);
    expect(state.activeUid).toBe(6);
  });

  it("refuses to hand over before the incumbent's hold has elapsed", () => {
    // Two people talking over each other must not make the ring strobe.
    const state = run([
      { volumes: loud(5), at: 0 },
      { volumes: loud(6, 200), at: 100 },
      { volumes: loud(6, 200), at: 200 },
      { volumes: loud(6, 200), at: 300 }
    ]);
    expect(state.activeUid).toBe(5);
  });

  it("refuses to hand over to someone only marginally louder", () => {
    const state = run([
      { volumes: loud(5, 100), at: 0 },
      { volumes: [{ rtcUid: 5, volume: 100 }, { rtcUid: 6, volume: 100 + config.margin - 1 }], at: 5000 },
      { volumes: [{ rtcUid: 5, volume: 100 }, { rtcUid: 6, volume: 100 + config.margin - 1 }], at: 5200 }
    ]);
    expect(state.activeUid).toBe(5);
  });

  it("resets a challenger's case when they stop leading", () => {
    const state = run([
      { volumes: loud(5), at: 0 },
      { volumes: loud(6, 200), at: 5000 },
      { volumes: loud(5, 200), at: 5100 },
      { volumes: loud(6, 200), at: 5200 }
    ]);
    expect(state.activeUid).toBe(5);
  });

  it("holds the highlight through the pauses in ordinary speech", () => {
    const state = run([
      { volumes: loud(5), at: 0 },
      { volumes: [], at: 500 },
      { volumes: [], at: 1200 }
    ]);
    expect(state.activeUid).toBe(5);
  });

  it("clears the highlight once the silence is longer than a breath", () => {
    const state = run([
      { volumes: loud(5), at: 0 },
      { volumes: [], at: config.silenceMs + 100 }
    ]);
    expect(state.activeUid).toBe(0);
  });

  it("does not reorder the stage when the speaker changes", () => {
    const roster = stage(4);
    const before = planStageLayout(roster).tiles.map((tile) => tile.key);
    const highlighted = applyActiveSpeaker(roster, { ...INITIAL_ACTIVE_SPEAKER_STATE, activeUid: 4 });
    expect(planStageLayout(highlighted).tiles.map((tile) => tile.key)).toEqual(before);
  });

  it("highlights exactly one participant", () => {
    const marked = applyActiveSpeaker(stage(4), { ...INITIAL_ACTIVE_SPEAKER_STATE, activeUid: 3 });
    expect(marked.filter((participant) => participant.speaking).map((p) => p.rtcUid)).toEqual([3]);
  });

  it("never highlights a muted participant", () => {
    const roster = stage(3).map((participant) => ({ ...participant, audioMuted: true }));
    const marked = applyActiveSpeaker(roster, { ...INITIAL_ACTIVE_SPEAKER_STATE, activeUid: 2 });
    expect(marked.some((participant) => participant.speaking)).toBe(false);
    expect(activeSpeakerParticipant(roster, { ...INITIAL_ACTIVE_SPEAKER_STATE, activeUid: 2 })).toBeNull();
  });

  it("preserves object identity for tiles it did not change", () => {
    // A new object for every participant on every volume report would rerender
    // the whole stage several times a second.
    const roster = stage(4);
    const marked = applyActiveSpeaker(roster, { ...INITIAL_ACTIVE_SPEAKER_STATE, activeUid: 3 });
    expect(marked[0]).toBe(roster[0]);
    expect(marked[2]).not.toBe(roster[2]);
  });

  it("returns nobody once the highlighted speaker has left", () => {
    expect(activeSpeakerParticipant(stage(2), { ...INITIAL_ACTIVE_SPEAKER_STATE, activeUid: 99 })).toBeNull();
  });
});
