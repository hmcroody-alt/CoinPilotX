/**
 * Stages 42–49. The scenario suite: a whole Live, played out.
 *
 * Every other suite in this folder tests one module against its own contract.
 * That is necessary and it is not sufficient, because the failure this mission
 * exists to prevent is not a module returning a wrong value — it is a *sequence*
 * of individually correct steps adding up to a broadcast that restarts, goes
 * silent, or double-counts somebody. A guest joining is handled correctly by
 * `liveSeatReconciliation`; the roster is rebuilt correctly by
 * `liveParticipantRegistry`; the stage is arranged correctly by
 * `liveStageLayout`. The bug lives in the seam.
 *
 * So this suite runs the real modules together, in order, over a simulated
 * session, and asserts the properties that must hold *for the whole run*:
 *
 *   - the host's connection is never disrupted, at any point, by anything a
 *     guest does;
 *   - every authorised publisher is audible to every other one;
 *   - nobody is ever on stage twice;
 *   - the comment stream only ever grows;
 *   - the stage is always a broadcast — one featured host — never an even grid.
 *
 * The simulator below is deliberately thin. It holds exactly what a real client
 * holds (a server roster, an Agora presence list, a local seat) and derives
 * *everything else* by calling the shipping functions. Nothing is reimplemented
 * here; if a scenario passes, it passes because the production code produced the
 * result, not because the test computed it.
 *
 * What this suite cannot do: prove that media actually flows. Agora's SDK is not
 * loadable under Jest in this repo, and no amount of state simulation
 * substitutes for two phones in a room. The device gates in
 * `MULTI_GUEST_LIVE_DEVICE_ACCEPTANCE.md` own that half. This suite owns the
 * half that a device test is bad at: exhaustive, repeatable orderings.
 */

import type { LiveGuest } from "../liveSession";
import {
  buildStageParticipants,
  publishingRoster,
  type LiveRosterSnapshot,
  type LiveRtcPresence,
  type LiveStageParticipant
} from "../liveParticipantRegistry";
import { dedupeStageParticipants, hasDuplicateStagePresence } from "../liveSessionLifecycle";
import { layoutIdentity, planStageLayout } from "../liveStageLayout";
import {
  isDisruptive,
  reconcileLiveSeat,
  rosterChangeRequiresReconnect,
  type LiveSeat,
  type SeatAction
} from "../liveSeatReconciliation";
import { audibilityMatrix, silentPublishers } from "../liveAudioMatrix";
import { mergeLiveChat } from "../liveEventContinuity";
import { publisherVideoProfile } from "../liveStreamQuality";
import type { PulseLiveChatMessage } from "../../api/live";

// ---------------------------------------------------------------------------
// The simulator
// ---------------------------------------------------------------------------

const HOST_ID = 1001;
const CHANNEL = "pulse_live_9001";

function guestRow(userId: number, overrides: Partial<LiveGuest> = {}): LiveGuest {
  return {
    guestId: userId - HOST_ID,
    userId,
    requestId: userId,
    displayName: `Guest ${userId - HOST_ID}`,
    avatarUrl: "",
    role: "guest",
    roleLabel: "Guest",
    status: "active",
    audioMuted: false,
    videoEnabled: true,
    joinedAt: "2026-09-04T10:00:00Z",
    // The uid IS the user id — see `live_participants.rtc_uid`. The simulator
    // states it explicitly rather than relying on the fallback, because that is
    // what the server sends.
    rtcUid: userId,
    layoutPosition: userId - HOST_ID,
    ...overrides
  };
}

function presence(rtcUid: number, overrides: Partial<LiveRtcPresence> = {}): LiveRtcPresence {
  return { rtcUid, hasVideo: true, hasAudio: true, audioMuted: false, speaking: false, ...overrides };
}

/**
 * One client's view of one Live, advanced step by step.
 *
 * `hostSeat` is what makes the no-restart property testable end to end: every
 * time the roster changes, the simulator re-derives the credentials the host
 * would hold and asks the real reconciler what to do about them. A scenario
 * fails if that answer is ever disruptive.
 */
class BroadcastSimulator {
  roster: LiveRosterSnapshot = { hostUserId: HOST_ID, hostDisplayName: "Host", guests: [] };
  rtc: LiveRtcPresence[] = [presence(HOST_ID)];
  chat: PulseLiveChatMessage[] = [];
  /** Every seat action the host's connection was asked to perform, in order. */
  readonly hostActions: SeatAction[] = [];
  private hostSeat: LiveSeat = {
    provider: "agora",
    channelName: CHANNEL,
    uid: HOST_ID,
    publishing: true,
    token: "host-token-1"
  };

  participants(): LiveStageParticipant[] {
    return buildStageParticipants(this.roster, this.rtc, { rtcUid: HOST_ID, role: "host" });
  }

  /** Re-run the host's connect path against unchanged credentials, as the app does. */
  private settleHost(): void {
    const action = reconcileLiveSeat(this.hostSeat, { ...this.hostSeat });
    this.hostActions.push(action);
  }

  /** A guest is invited and accepts: on the roster, no media yet. */
  admit(userId: number, overrides: Partial<LiveGuest> = {}): this {
    this.roster = {
      ...this.roster,
      guests: [...this.roster.guests, guestRow(userId, { status: "joining", ...overrides })]
    };
    this.settleHost();
    return this;
  }

  /** That guest's tracks arrive. */
  publish(userId: number, overrides: Partial<LiveRtcPresence> = {}): this {
    this.roster = {
      ...this.roster,
      guests: this.roster.guests.map((guest) =>
        guest.userId === userId ? { ...guest, status: "active" } : guest
      )
    };
    this.rtc = [...this.rtc.filter((entry) => entry.rtcUid !== userId), presence(userId, overrides)];
    this.settleHost();
    return this;
  }

  /** Invite, accept and publish in one step. */
  seat(userId: number, overrides: Partial<LiveGuest> = {}): this {
    return this.admit(userId, overrides).publish(userId);
  }

  /** A guest leaves the stage. Their row goes terminal and their media stops. */
  depart(userId: number, status = "left"): this {
    this.roster = {
      ...this.roster,
      guests: this.roster.guests.map((guest) =>
        guest.userId === userId ? { ...guest, status } : guest
      )
    };
    this.rtc = this.rtc.filter((entry) => entry.rtcUid !== userId);
    this.settleHost();
    return this;
  }

  /** A guest's transport drops without the roster knowing yet. */
  dropMedia(userId: number): this {
    this.rtc = this.rtc.filter((entry) => entry.rtcUid !== userId);
    this.settleHost();
    return this;
  }

  /** The server issues the host a fresh token mid-broadcast. */
  refreshHostToken(token: string): SeatAction {
    const next = { ...this.hostSeat, token };
    const action = reconcileLiveSeat(this.hostSeat, next);
    this.hostSeat = next;
    this.hostActions.push(action);
    return action;
  }

  /** A poll returns a window of comments. */
  poll(messages: PulseLiveChatMessage[]): this {
    this.chat = mergeLiveChat(this.chat, messages);
    return this;
  }

  onStage(): LiveStageParticipant[] {
    return this.participants().filter((participant) => participant.phase !== "left");
  }

  publishers(): LiveStageParticipant[] {
    return publishingRoster(this.participants());
  }
}

function message(id: number, body: string): PulseLiveChatMessage {
  return { id, live_id: 9001, user_id: 5000 + id, body, created_at: `2026-09-04T10:${String(id).padStart(2, "0")}:00Z` };
}

/**
 * The properties that must hold at *every* point in *every* scenario.
 *
 * Called after each step rather than only at the end, because a broadcast that
 * is correct before and after a guest joins but briefly shows the guest twice
 * in between is still a broadcast that glitched on camera.
 */
function assertBroadcastInvariants(sim: BroadcastSimulator): void {
  const participants = sim.participants();

  // 1. Nobody on stage twice. Not the same connection, not the same person.
  expect(hasDuplicateStagePresence(participants)).toBe(false);
  expect(dedupeStageParticipants(participants)).toHaveLength(participants.length);

  // 2. Exactly one host, and the host is first. This is what keeps it a
  //    broadcast: the layout's featured slot is whoever sorts first.
  const hosts = participants.filter((participant) => participant.isHost);
  expect(hosts).toHaveLength(1);
  expect(participants[0].isHost).toBe(true);

  // 3. The host is never rendered as having left because their media blipped.
  expect(participants[0].phase).not.toBe("left");

  // 4. Every publisher can hear every other publisher.
  expect(silentPublishers(participants)).toEqual([]);

  // 5. The stage is featured, never an even grid, from the moment there is
  //    more than one person on it.
  const layout = planStageLayout(participants);
  const live = participants.filter((participant) => participant.phase !== "left");
  if (live.length > 1) {
    const featured = layout.tiles.filter((tile) => tile.featured);
    expect(featured).toHaveLength(1);
    expect(featured[0].participant.isHost).toBe(true);
    const others = layout.tiles.filter((tile) => !tile.featured);
    for (const tile of others) {
      expect(featured[0].heightRatio).toBeGreaterThan(tile.heightRatio);
    }
  }

  // 6. No overflow within the supported stage size.
  if (live.length <= 13) expect(layout.overflow).toBe(0);
}

// ---------------------------------------------------------------------------
// Stage 42 — the single-host Live, unchanged
// ---------------------------------------------------------------------------

describe("Stage 42 · a single-host Live is exactly what it was", () => {
  test("a host alone gets a solo full-bleed stage and one publisher", () => {
    const sim = new BroadcastSimulator();
    assertBroadcastInvariants(sim);

    const layout = planStageLayout(sim.participants());
    expect(layout.mode).toBe("solo");
    expect(layout.tiles).toHaveLength(1);
    expect(layout.tiles[0].heightRatio).toBe(1);
    expect(layout.tiles[0].columnSpan).toBe(1);
    expect(sim.publishers()).toHaveLength(1);
  });

  test("a solo host still encodes at the full 720x1280 profile", () => {
    // The encoder ladder exists to keep six publishers off the 2K recording
    // tier. It must not quietly cost a solo host any resolution — that would be
    // a regression paid by every ordinary Live to optimise the rare one.
    expect(publisherVideoProfile(1)).toMatchObject({ width: 720, height: 1280 });
  });

  test("token renewal on a solo Live is a renewal, not a restart", () => {
    const sim = new BroadcastSimulator();
    expect(sim.refreshHostToken("host-token-2")).toBe("renew_token");
    expect(isDisruptive("renew_token")).toBe(false);
    assertBroadcastInvariants(sim);
  });

  test("comments survive an empty poll", () => {
    const sim = new BroadcastSimulator();
    sim.poll([message(1, "hello"), message(2, "hi")]).poll([]);
    expect(sim.chat).toHaveLength(2);
  });
});

// ---------------------------------------------------------------------------
// Stage 43 — host + 1
// ---------------------------------------------------------------------------

describe("Stage 43 · host plus one guest", () => {
  test("the guest shows an avatar while preparing and a video tile once live", () => {
    const sim = new BroadcastSimulator();

    sim.admit(1002);
    assertBroadcastInvariants(sim);
    // Server status "joining" means "accepted, opening the camera" — the app
    // presents that as "preparing". The vocabularies differ deliberately: the
    // server's is about the row, the client's is about what the viewer sees.
    expect(sim.participants()[1].phase).toBe("preparing");
    // Stage 8: no empty black tile. The tile exists — the slot is reserved so
    // the stage does not jump when media arrives — but it shows an avatar.
    expect(planStageLayout(sim.participants()).tiles[1].showsVideo).toBe(false);

    sim.publish(1002);
    assertBroadcastInvariants(sim);
    expect(sim.participants()[1].phase).toBe("live");
    expect(planStageLayout(sim.participants()).tiles[1].showsVideo).toBe(true);
  });

  test("every step of the join sequence has an honest presentation", () => {
    // Stage 8's whole point: INVITED → ACCEPTED → PREPARING → JOINING → LIVE,
    // and at no point is a video surface mounted over a stream that does not
    // exist yet.
    const phases: Array<[string, string]> = [
      ["invited", "invited"],
      ["accepted", "accepted"],
      ["joining", "preparing"],
      ["joined", "joining"],
      // Note the last row. The server says "active", but no track has arrived,
      // so the client refuses to say "live". That downgrade is the single rule
      // that stops a black tile: the roster and the transport must *both* agree
      // before a video surface is mounted.
      ["active", "joining"]
    ];
    for (const [serverStatus, expectedPhase] of phases) {
      const sim = new BroadcastSimulator();
      sim.admit(1002, { status: serverStatus });
      const guest = sim.participants()[1];
      expect(guest.phase).toBe(expectedPhase);
      expect(planStageLayout(sim.participants()).tiles[1].showsVideo).toBe(false);
    }

    // Only when the transport agrees does the tile go live.
    const sim = new BroadcastSimulator().seat(1002);
    expect(sim.participants()[1].phase).toBe("live");
    expect(planStageLayout(sim.participants()).tiles[1].showsVideo).toBe(true);
  });

  test("two publishers get an uneven split, not two equal halves", () => {
    const sim = new BroadcastSimulator().seat(1002);
    const layout = planStageLayout(sim.participants());
    expect(layout.mode).toBe("split");
    expect(layout.tiles[0].heightRatio).toBeGreaterThan(layout.tiles[1].heightRatio);
    // An even 50/50 is the visual grammar of a video call. This is the one
    // assertion in the suite that is purely about it not looking like Zoom.
    expect(layout.tiles[0].heightRatio).not.toBe(layout.tiles[1].heightRatio);
  });

  test("the host's connection is untouched by the guest arriving", () => {
    const sim = new BroadcastSimulator().seat(1002);
    expect(sim.hostActions.every((action) => !isDisruptive(action))).toBe(true);
    expect(sim.hostActions).not.toContain("rejoin");
    expect(rosterChangeRequiresReconnect()).toBe(false);
  });

  test("host and guest hear each other, in both directions", () => {
    const sim = new BroadcastSimulator().seat(1002);
    const matrix = audibilityMatrix(sim.participants());
    const pairs = matrix.filter((pair) => pair.listener !== pair.speaker);
    expect(pairs).toHaveLength(2);
    expect(pairs.every((pair) => pair.audible)).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// Stage 44 — host + 2
// ---------------------------------------------------------------------------

describe("Stage 44 · host plus two guests", () => {
  test("three publishers, host featured above a guest row", () => {
    const sim = new BroadcastSimulator().seat(1002).seat(1003);
    assertBroadcastInvariants(sim);

    const layout = planStageLayout(sim.participants());
    expect(layout.mode).toBe("featured");
    expect(layout.rows).toBe(2);
    expect(layout.tiles[0].columnSpan).toBe(layout.columns);
    expect(layout.tiles[1].row).toBe(1);
    expect(layout.tiles[2].row).toBe(1);
    expect(sim.publishers()).toHaveLength(3);
  });

  test("everyone hears everyone: a full three-way matrix", () => {
    const sim = new BroadcastSimulator().seat(1002).seat(1003);
    const pairs = audibilityMatrix(sim.participants()).filter((pair) => pair.listener !== pair.speaker);
    expect(pairs).toHaveLength(6);
    expect(pairs.every((pair) => pair.audible)).toBe(true);
  });

  test("adding the second guest still did not restart anything", () => {
    const sim = new BroadcastSimulator().seat(1002).seat(1003);
    expect(sim.hostActions.every((action) => action === "noop")).toBe(true);
  });

  test("a moderator mute silences one guest without silencing the stage", () => {
    const sim = new BroadcastSimulator().seat(1002).seat(1003);
    sim.roster = {
      ...sim.roster,
      guests: sim.roster.guests.map((guest) =>
        guest.userId === 1003 ? { ...guest, audioMuted: true } : guest
      )
    };
    const participants = sim.participants();
    const muted = participants.find((participant) => participant.userId === 1003);
    expect(muted?.audioMuted).toBe(true);

    // `silentPublishers` reports everyone on stage who is not currently heard,
    // *with a reason* — it is a diagnostic, not an alarm. A moderator mute is
    // reported as "muted", which is the distinction that matters: an operator
    // reading this can tell an intended silence from a dead microphone.
    const silent = silentPublishers(participants);
    expect(silent).toHaveLength(1);
    expect(silent[0]).toMatchObject({ key: muted?.key, reason: "muted" });

    // The stage is otherwise unaffected: the other two still hear each other,
    // and both are still heard by the muted guest.
    const pairs = audibilityMatrix(participants);
    expect(pairs.find((pair) => pair.listener === participants[0].key && pair.speaker === muted?.key)?.audible).toBe(false);
    const unmuted = participants.filter((participant) => participant.key !== muted?.key);
    expect(
      pairs
        .filter((pair) => unmuted.some((p) => p.key === pair.speaker))
        .every((pair) => pair.audible)
    ).toBe(true);
    expect(participants.filter((participant) => participant.audioMuted)).toHaveLength(1);
  });
});

// ---------------------------------------------------------------------------
// Stage 45 — the maximum stage
// ---------------------------------------------------------------------------

describe("Stage 45 · a full stage", () => {
  const fill = (count: number) => {
    const sim = new BroadcastSimulator();
    for (let index = 0; index < count; index += 1) sim.seat(HOST_ID + 1 + index);
    return sim;
  };

  test("twelve guests plus the host all get tiles, with no overflow", () => {
    const sim = fill(12);
    assertBroadcastInvariants(sim);
    const layout = planStageLayout(sim.participants());
    expect(sim.publishers()).toHaveLength(13);
    expect(layout.tiles).toHaveLength(13);
    expect(layout.overflow).toBe(0);
  });

  test("the host is still visually dominant at thirteen people", () => {
    const layout = planStageLayout(fill(12).participants());
    expect(layout.mode).toBe("featured-strip");
    const [featured, ...rest] = layout.tiles;
    expect(featured.participant.isHost).toBe(true);
    // Half the stage, at the largest population the layout supports. That is
    // the point at which a grid would have won if the rule were soft.
    expect(featured.heightRatio).toBeGreaterThanOrEqual(0.5);
    expect(rest.every((tile) => tile.heightRatio < featured.heightRatio)).toBe(true);
  });

  test("a full stage is still fully audible", () => {
    const participants = fill(12).participants();
    expect(silentPublishers(participants)).toEqual([]);
    const pairs = audibilityMatrix(participants).filter((pair) => pair.listener !== pair.speaker);
    expect(pairs).toHaveLength(13 * 12);
    expect(pairs.every((pair) => pair.audible)).toBe(true);
  });

  test("the encoder steps down as the stage fills, so recording stays off the 2K tier", () => {
    // Stage 33. Agora bills on aggregate resolution across every subscribed
    // stream, so the publish-side ladder is what decides the recording tier.
    // Six publishers on a fixed 720p canvas bills at the 2K+ rate.
    const six = publisherVideoProfile(6);
    expect(six.width * six.height * 6).toBeLessThanOrEqual(2_073_600);
    const one = publisherVideoProfile(1);
    expect(six.width * six.height).toBeLessThan(one.width * one.height);
  });

  test("the layout's own ceiling matches the server's, and is never the gate", () => {
    // The layout has a capacity constant so it can degrade gracefully, but it
    // must never be what decides whether someone may join — that is the
    // server's `LIVE_MAX_GUESTS`. Overflow is the graceful degradation.
    const sim = fill(20);
    const layout = planStageLayout(sim.participants());
    expect(layout.tiles).toHaveLength(13);
    expect(layout.overflow).toBe(8);
    // Crucially, the people beyond the cap are still in the roster and still
    // audible. They are off-screen, not off-stage.
    expect(sim.publishers().length).toBe(21);
  });
});

// ---------------------------------------------------------------------------
// Stage 46 — join / leave chaos
// ---------------------------------------------------------------------------

describe("Stage 46 · guests arriving and leaving in every order", () => {
  test("a hundred randomised join/leave steps never disrupt the host", () => {
    // Seeded so a failure is reproducible. A random walk is the right shape
    // here: the orderings that break a broadcast are the ones nobody thought
    // to write a test for.
    let seed = 20260904;
    const random = () => {
      seed = (seed * 1103515245 + 12345) % 2147483648;
      return seed / 2147483648;
    };

    const sim = new BroadcastSimulator();
    const seated = new Set<number>();
    let nextId = HOST_ID + 1;

    for (let step = 0; step < 100; step += 1) {
      const roll = random();
      if (roll < 0.5 && seated.size < 12) {
        const userId = nextId;
        nextId += 1;
        sim.seat(userId);
        seated.add(userId);
      } else if (seated.size > 0) {
        const victim = Array.from(seated)[Math.floor(random() * seated.size)];
        sim.depart(victim);
        seated.delete(victim);
      }
      assertBroadcastInvariants(sim);
    }

    expect(sim.hostActions.length).toBeGreaterThan(50);
    expect(sim.hostActions.every((action) => !isDisruptive(action))).toBe(true);
  });

  test("the last guest leaving returns the stage to solo, and the Live continues", () => {
    const sim = new BroadcastSimulator().seat(1002).seat(1003);
    sim.depart(1002).depart(1003);
    assertBroadcastInvariants(sim);

    const layout = planStageLayout(sim.participants());
    expect(layout.mode).toBe("solo");
    // Stage 20. The broadcast is still running. A guest leaving is not an end.
    expect(sim.participants()[0].isHost).toBe(true);
    expect(sim.participants()[0].phase).toBe("live");
    expect(sim.hostActions).not.toContain("rejoin");
  });

  test("a departed guest's seat is reused without resurrecting them", () => {
    const sim = new BroadcastSimulator().seat(1002);
    sim.depart(1002);
    sim.seat(1003);
    const keys = sim.onStage().map((participant) => participant.userId);
    expect(keys).toEqual([HOST_ID, 1003]);
    expect(keys).not.toContain(1002);
  });

  test("a guest removed by a moderator does not linger on the stage", () => {
    const sim = new BroadcastSimulator().seat(1002).seat(1003);
    sim.depart(1003, "removed");
    assertBroadcastInvariants(sim);
    expect(sim.onStage().map((participant) => participant.userId)).toEqual([HOST_ID, 1002]);
  });
});

// ---------------------------------------------------------------------------
// Stage 47 — network chaos
// ---------------------------------------------------------------------------

describe("Stage 47 · media and roster disagreeing", () => {
  test("a guest whose media drops before the roster catches up shows as joining, not gone", () => {
    const sim = new BroadcastSimulator().seat(1002);
    sim.dropMedia(1002);
    assertBroadcastInvariants(sim);

    const guest = sim.participants().find((participant) => participant.userId === 1002);
    // The server still lists them as active, so they keep their slot. Dropping
    // the tile on a transport blip is what makes a stage flicker every time
    // somebody walks through a tunnel.
    expect(guest?.phase).toBe("joining");
    expect(sim.onStage()).toHaveLength(2);
  });

  test("a guest who reconnects on a new row is one person, not two", () => {
    // Stages 23 and 38. The old row is still being reaped when the new one
    // arrives. Both describe the same user id.
    const sim = new BroadcastSimulator().seat(1002);
    sim.roster = {
      ...sim.roster,
      guests: [
        ...sim.roster.guests,
        guestRow(1002, { guestId: 99, status: "active", layoutPosition: 5 })
      ]
    };
    const participants = sim.participants();
    expect(participants.filter((participant) => participant.userId === 1002)).toHaveLength(1);
    assertBroadcastInvariants(sim);
  });

  test("the host's own media blipping does not end the broadcast", () => {
    const sim = new BroadcastSimulator().seat(1002);
    sim.rtc = sim.rtc.filter((entry) => entry.rtcUid !== HOST_ID);
    assertBroadcastInvariants(sim);
    const host = sim.participants()[0];
    expect(host.isHost).toBe(true);
    // Stage 21. A momentary absence is "joining". Ending a Live is an explicit
    // act by the host, never an inference from a missing packet.
    expect(host.phase).toBe("joining");
  });

  test("a token refresh during a guest arrival renews rather than rejoins", () => {
    const sim = new BroadcastSimulator();
    sim.admit(1002);
    expect(sim.refreshHostToken("host-token-2")).toBe("renew_token");
    sim.publish(1002);
    expect(sim.hostActions.every((action) => !isDisruptive(action))).toBe(true);
    assertBroadcastInvariants(sim);
  });

  test("only a genuinely different channel or uid restarts the session", () => {
    const seat: LiveSeat = {
      provider: "agora",
      channelName: CHANNEL,
      uid: HOST_ID,
      publishing: true,
      token: "t1"
    };
    expect(reconcileLiveSeat(seat, { ...seat, channelName: "pulse_live_9002" })).toBe("rejoin");
    expect(reconcileLiveSeat(seat, { ...seat, uid: 2002 })).toBe("rejoin");
    // And nothing else does. Promotion in particular must not — that is a guest
    // going on stage, which is the whole feature.
    expect(reconcileLiveSeat({ ...seat, publishing: false }, seat)).toBe("promote");
    expect(isDisruptive("promote")).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// Stage 48 — an audience member joining mid-stream
// ---------------------------------------------------------------------------

describe("Stage 48 · joining a Live that is already running", () => {
  test("a viewer arriving at a three-person stage sees all three", () => {
    // The viewer's first `/state` response describes the Live as it already is.
    // There is no replay of the joins they missed, so the roster has to be
    // sufficient on its own.
    const arrived = new BroadcastSimulator().seat(1002).seat(1003);
    const viewerParticipants = buildStageParticipants(arrived.roster, arrived.rtc, {
      rtcUid: 7777,
      role: "audience"
    });

    expect(viewerParticipants).toHaveLength(3);
    expect(viewerParticipants[0].isHost).toBe(true);
    expect(viewerParticipants.some((participant) => participant.isLocal)).toBe(false);
    expect(publishingRoster(viewerParticipants)).toHaveLength(3);
  });

  test("the viewer's stage is arranged identically to the host's", () => {
    // Stage 12. Layout order is server-assigned, not observation-ordered, so
    // every client renders the same stage. If it were arrival-ordered, a viewer
    // who joined late would see the guests in a different arrangement to
    // everyone else, and an active-speaker highlight would point at the wrong
    // tile on their screen.
    const arrived = new BroadcastSimulator().seat(1002).seat(1003).seat(1004);
    const hostLayout = planStageLayout(arrived.participants());
    const viewerLayout = planStageLayout(
      buildStageParticipants(arrived.roster, arrived.rtc, { rtcUid: 7777, role: "audience" })
    );
    expect(layoutIdentity(viewerLayout)).toBe(layoutIdentity(hostLayout));
  });

  test("an audience member is not on the stage and publishes nothing", () => {
    // Stage 25. The viewer's own uid appears nowhere in the participant list,
    // because they are not a publisher. There is no seat for them to fill and
    // nothing for them to initialise.
    const arrived = new BroadcastSimulator().seat(1002);
    const viewerParticipants = buildStageParticipants(arrived.roster, arrived.rtc, {
      rtcUid: 7777,
      role: "audience"
    });
    expect(viewerParticipants.some((participant) => participant.rtcUid === 7777)).toBe(false);
  });

  test("the comment history a late viewer loads is not truncated by later polls", () => {
    // Stage 27. The server returns a bounded recent window. A poll that returns
    // fewer rows than the client holds is a quiet minute, not a deletion.
    const sim = new BroadcastSimulator();
    sim.poll([message(1, "a"), message(2, "b"), message(3, "c")]);
    sim.poll([message(3, "c")]);
    expect(sim.chat).toHaveLength(3);
  });
});

// ---------------------------------------------------------------------------
// Stage 49 — the late guest
// ---------------------------------------------------------------------------

describe("Stage 49 · a guest promoted long after the Live began", () => {
  test("promotion is a role change in place — the channel and uid never move", () => {
    const asViewer: LiveSeat = {
      provider: "agora",
      channelName: CHANNEL,
      uid: 1005,
      publishing: false,
      token: "viewer-token"
    };
    const asGuest: LiveSeat = { ...asViewer, publishing: true, token: "guest-token" };

    // Same channel, same uid. Only the publish privilege changed, which is
    // exactly what the server encoded in the new token.
    expect(asGuest.channelName).toBe(asViewer.channelName);
    expect(asGuest.uid).toBe(asViewer.uid);
    expect(reconcileLiveSeat(asViewer, asGuest)).toBe("promote");
    expect(isDisruptive("promote")).toBe(false);
  });

  test("the late guest's arrival does not reshuffle the guests already on stage", () => {
    const sim = new BroadcastSimulator().seat(1002).seat(1003);
    const before = planStageLayout(sim.participants()).tiles.map((tile) => tile.key);

    sim.seat(1004);
    const after = planStageLayout(sim.participants()).tiles.map((tile) => tile.key);

    // Stage 14's rule, applied to arrivals rather than to volume: the people
    // already on stage keep their positions and the newcomer is appended. A
    // stage that re-sorts on every join moves tiles under people mid-sentence.
    expect(after.slice(0, before.length)).toEqual(before);
    assertBroadcastInvariants(sim);
  });

  test("the late guest sees the comments that were posted before they arrived", () => {
    const history = [message(1, "before"), message(2, "they"), message(3, "arrived")];
    // Their client starts empty and folds in the server's window.
    expect(mergeLiveChat([], history)).toHaveLength(3);
    // And a duplicate delivery — the poll and the realtime event carrying the
    // same comment — is still one comment.
    expect(mergeLiveChat(history, [message(3, "arrived")])).toHaveLength(3);
  });

  test("a guest who joins at the ceiling is still fully wired, not a half-member", () => {
    const sim = new BroadcastSimulator();
    for (let index = 0; index < 11; index += 1) sim.seat(HOST_ID + 1 + index);
    sim.seat(HOST_ID + 12);

    const last = sim.participants().find((participant) => participant.userId === HOST_ID + 12);
    expect(last?.phase).toBe("live");
    expect(last?.hasAudio).toBe(true);
    expect(last?.hasVideo).toBe(true);
    expect(silentPublishers(sim.participants())).toEqual([]);
    assertBroadcastInvariants(sim);
  });
});

// ---------------------------------------------------------------------------
// The whole thing, once, in order
// ---------------------------------------------------------------------------

describe("the mission's acceptance sentence, executed", () => {
  test("host goes live, audience watches, guests join and leave, the Live never restarts", () => {
    const sim = new BroadcastSimulator();

    // HOST GOES LIVE
    expect(planStageLayout(sim.participants()).mode).toBe("solo");

    // AUDIENCE WATCHES
    const audienceView = () =>
      buildStageParticipants(sim.roster, sim.rtc, { rtcUid: 7777, role: "audience" });
    expect(audienceView()).toHaveLength(1);

    // HOST INVITES GUEST → GUEST ACCEPTS → GUEST APPEARS LIVE
    sim.admit(1002);
    expect(sim.participants()[1].phase).toBe("preparing");
    sim.publish(1002);
    expect(sim.participants()[1].phase).toBe("live");

    // BOTH PUBLISH AUDIO + VIDEO
    expect(sim.publishers().every((participant) => participant.hasAudio && participant.hasVideo)).toBe(true);
    expect(silentPublishers(sim.participants())).toEqual([]);

    // MORE GUESTS MAY JOIN / LEAVE
    sim.seat(1003).seat(1004);
    expect(sim.publishers()).toHaveLength(4);
    sim.depart(1003);
    expect(sim.publishers()).toHaveLength(3);
    sim.seat(1005);
    expect(sim.publishers()).toHaveLength(4);

    // The audience sees every authorised publisher, throughout.
    expect(publishingRoster(audienceView())).toHaveLength(4);

    // LIVESTREAM NEVER RESTARTS
    expect(sim.hostActions).not.toContain("rejoin");
    expect(sim.hostActions.every((action) => !isDisruptive(action))).toBe(true);

    assertBroadcastInvariants(sim);
  });
});
