/**
 * Stages 20-23, 38-39 — who ends a Live, who merely leaves it, what happens
 * while the host is missing, what a co-host may actually do, and what to do
 * when the same person turns up twice.
 *
 * These five questions look unrelated. They are the same question asked five
 * ways: *when the roster changes, what is still true about the broadcast?* The
 * answer that must never change is that the broadcast survives. A guest walking
 * off stage, a host's phone dropping to 1 bar, a reconnect that arrives before
 * the old connection has been reaped, a second device signing in — none of
 * those are grounds for ending a Live that an audience is watching.
 *
 * The failure mode each function here exists to prevent, stated plainly because
 * each one is a production incident rather than a hypothetical:
 *
 *   Stage 20  A guest taps the X in the corner and 40,000 viewers see the
 *             stream end. The button was wired to `end`, because the host's
 *             screen and the guest's screen are the same screen.
 *   Stage 21  The host goes through a tunnel. The Live is torn down four
 *             seconds later and cannot be resumed, because "host disconnected"
 *             was treated as "host ended".
 *   Stage 22  A co-host is granted the host's role object for convenience and
 *             can now end someone else's broadcast.
 *   Stage 23  A reconnect produces a second tile for a person who is already on
 *             stage, and the stage looks full at four people.
 *   Stage 39  A user opens the Live on their tablet and their phone's publish
 *             is silently stolen, or worse, both publish and echo.
 *
 * Pure: no network, no Agora, no React, no clock reads. Time is passed in so
 * the grace period is testable without waiting for it.
 */

import type { LiveRole, LiveStageParticipant } from "./liveParticipantRegistry";

// ---------------------------------------------------------------------------
// Stage 20 — leaving the stage is not ending the broadcast
// ---------------------------------------------------------------------------

export type LiveExitIntent =
  /** Close the whole broadcast for everyone. Host only. */
  | { action: "end"; endsBroadcastForEveryone: true; confirm: true; labelKey: string }
  /** Step off the stage. The Live continues without this person. */
  | { action: "leave"; endsBroadcastForEveryone: false; confirm: true; labelKey: string }
  /** Stop watching. Nothing on the server changes for anyone else. */
  | { action: "stopWatching"; endsBroadcastForEveryone: false; confirm: false; labelKey: string };

/**
 * What the exit control means for the person pressing it.
 *
 * Deliberately keyed on the actor rather than on the screen. The host screen
 * and the guest screen share a component tree, and a component that decides
 * "this is the host view, so exit means end" is one prop away from ending a
 * broadcast on a guest's behalf. Asking the person's role instead makes the
 * dangerous branch unreachable for anyone who is not the host.
 *
 * `endsBroadcastForEveryone` is spelled out rather than left implicit in the
 * action string so that a call site can guard on it without having to know that
 * "end" is the dangerous one.
 */
export function resolveExitIntent(participant: {
  role: LiveRole;
  isHost: boolean;
}): LiveExitIntent {
  if (participant.isHost) {
    return {
      action: "end",
      endsBroadcastForEveryone: true,
      confirm: true,
      labelKey: "extended:live.exit.endLive"
    };
  }
  if (participant.role === "cohost" || participant.role === "guest") {
    // A co-host is on stage, not in charge. Their exit is a leave.
    return {
      action: "leave",
      endsBroadcastForEveryone: false,
      confirm: true,
      labelKey: "extended:live.exit.leaveStage"
    };
  }
  return {
    action: "stopWatching",
    endsBroadcastForEveryone: false,
    confirm: false,
    labelKey: "extended:live.exit.stopWatching"
  };
}

/**
 * Whether a departure should end the broadcast.
 *
 * The single sentence Stage 20 is defending, written so a regression fails
 * loudly. Called by tests and review rather than by product code: the
 * production path uses `resolveExitIntent`, and this asserts that path can
 * never answer "yes" for anyone but the host.
 */
export function departureEndsBroadcast(participant: { role: LiveRole; isHost: boolean }): boolean {
  return resolveExitIntent(participant).endsBroadcastForEveryone;
}

// ---------------------------------------------------------------------------
// Stage 21 — the host is missing, not gone
// ---------------------------------------------------------------------------

/**
 * How long a broadcast waits for a host who has dropped off the network.
 *
 * Ninety seconds is a judgement, and it is worth writing down why rather than
 * leaving a bare constant. Below roughly thirty seconds, ordinary mobile events
 * — a lift, a tunnel, a handover between cells, an incoming phone call — end
 * broadcasts that would have recovered on their own. Above a few minutes, an
 * audience is left watching a frozen stage with no explanation and leaves
 * anyway, so the extra patience buys nothing and costs the host their viewers.
 *
 * The server owns the authoritative deadline; this constant exists so the
 * client's countdown agrees with it rather than inventing a second answer.
 */
export const LIVE_HOST_GRACE_SECONDS = 90;

export type HostAbsenceDecision = {
  /** What the audience and the remaining stage should be told. */
  status: "present" | "waiting" | "expired";
  /** Whether the Live should be torn down now. */
  endBroadcast: boolean;
  /** Whether guests already publishing should keep publishing. */
  guestsKeepPublishing: boolean;
  /** Seconds left before the grace period expires. Zero once it has. */
  secondsRemaining: number;
  /** i18n key for the banner, or empty when there is nothing to say. */
  noticeKey: string;
};

/**
 * What to do about a host who is not currently connected.
 *
 * Two decisions are kept apart here on purpose. "Should the audience see a
 * notice" and "should the broadcast end" are different questions with different
 * deadlines, and collapsing them is what produces a Live that dies during a
 * lift ride. Throughout the waiting window the guests keep publishing: a panel
 * that goes silent because the host walked into a car park is a worse outcome
 * than a panel that carries on without them for a minute.
 *
 * `disconnectedForSeconds` is supplied by the caller rather than derived from a
 * clock inside this function, so the expiry boundary can be tested exactly.
 */
export function resolveHostAbsence(input: {
  hostConnected: boolean;
  disconnectedForSeconds: number;
  graceSeconds?: number;
  /** True once the host has explicitly ended; short-circuits the grace period. */
  hostEndedExplicitly?: boolean;
}): HostAbsenceDecision {
  const grace = Math.max(0, Math.floor(Number(input.graceSeconds ?? LIVE_HOST_GRACE_SECONDS) || 0));

  if (input.hostEndedExplicitly) {
    // An explicit end is a decision, not an absence. It does not wait.
    return {
      status: "expired",
      endBroadcast: true,
      guestsKeepPublishing: false,
      secondsRemaining: 0,
      noticeKey: ""
    };
  }

  if (input.hostConnected) {
    return {
      status: "present",
      endBroadcast: false,
      guestsKeepPublishing: true,
      secondsRemaining: grace,
      noticeKey: ""
    };
  }

  const elapsed = Math.max(0, Math.floor(Number(input.disconnectedForSeconds) || 0));
  const remaining = Math.max(0, grace - elapsed);

  if (remaining <= 0) {
    return {
      status: "expired",
      endBroadcast: true,
      guestsKeepPublishing: false,
      secondsRemaining: 0,
      noticeKey: "extended:live.host.absenceEnded"
    };
  }

  return {
    status: "waiting",
    endBroadcast: false,
    guestsKeepPublishing: true,
    secondsRemaining: remaining,
    noticeKey: "extended:live.host.reconnecting"
  };
}

// ---------------------------------------------------------------------------
// Stage 22 — a co-host is not a host
// ---------------------------------------------------------------------------

export type LivePermission =
  /** Publish audio and video to the stage. */
  | "publish"
  /** Mute or remove other guests. */
  | "moderateGuests"
  /** Invite someone onto the stage. */
  | "inviteGuests"
  /** Approve or decline an audience request to join. */
  | "approveRequests"
  /** Change someone's role — promote a guest, demote a co-host. */
  | "assignRoles"
  /** End the broadcast for everyone. */
  | "endBroadcast"
  /** Start or stop the recording. */
  | "controlRecording"
  /** Delete or pin chat messages. */
  | "moderateChat";

/**
 * The permissions each role carries.
 *
 * Written as an explicit table rather than as a hierarchy with overrides,
 * because a hierarchy is what produces "co-host is host minus a couple of
 * things" and then quietly grants a new capability to co-hosts the day someone
 * adds it to the host. Every cell here is a decision that had to be typed.
 *
 * The three the co-host does NOT have are the three that would let them take
 * the broadcast from the person whose broadcast it is: they cannot end it, they
 * cannot change who is on stage by role, and they cannot control the recording.
 * They can run the room; they cannot own it.
 *
 * This mirrors, and must not exceed, what the server enforces. The client copy
 * exists to avoid offering buttons the server will refuse — never to decide.
 */
const ROLE_PERMISSIONS: Readonly<Record<LiveRole, readonly LivePermission[]>> = {
  host: [
    "publish",
    "moderateGuests",
    "inviteGuests",
    "approveRequests",
    "assignRoles",
    "endBroadcast",
    "controlRecording",
    "moderateChat"
  ],
  cohost: ["publish", "moderateGuests", "inviteGuests", "approveRequests", "moderateChat"],
  guest: ["publish"],
  audience: []
};

export function permissionsForRole(role: LiveRole): readonly LivePermission[] {
  return ROLE_PERMISSIONS[role] ?? ROLE_PERMISSIONS.audience;
}

export function hasLivePermission(role: LiveRole, permission: LivePermission): boolean {
  return permissionsForRole(role).includes(permission);
}

/**
 * Whether a role is the host's role in all but name.
 *
 * Stage 22 in one assertion. If a future edit hands co-hosts `endBroadcast` or
 * `assignRoles`, this starts returning true and the test that calls it fails,
 * which is the point: the mistake is silent otherwise, and only shows up when a
 * co-host ends somebody else's Live.
 */
export function isHostEquivalent(role: LiveRole): boolean {
  const host = permissionsForRole("host");
  const other = permissionsForRole(role);
  return host.every((permission) => other.includes(permission));
}

// ---------------------------------------------------------------------------
// Stages 23, 38, 39 — the same person, twice
// ---------------------------------------------------------------------------

export type ParticipantConnection = {
  /** Stable PulseSoc identity. The RTC uid equals this by contract. */
  userId: number;
  /** Which physical device this connection came from. */
  deviceId: string;
  /** The RTC uid Agora reported for this connection. */
  rtcUid: number;
  /** When the connection joined, as epoch milliseconds. */
  joinedAtMs: number;
  /** Whether this connection is publishing media. */
  publishing: boolean;
};

export type DuplicateResolution = {
  /** The connection that keeps the stage slot. */
  keep: ParticipantConnection;
  /** Connections that must stop publishing and become viewers. */
  demote: ParticipantConnection[];
  /** Why, in a form safe to log and to render. */
  reason:
    | "single_connection"
    | "reconnect_supersedes_stale"
    | "newest_device_wins";
};

/**
 * Decide which of a user's connections owns their place on stage.
 *
 * Stage 23 and Stage 39 are the same code path seen from two angles. A
 * reconnect and a second device both present as "this user is here twice"; the
 * difference is only whether the earlier connection is genuinely gone. Handling
 * them together means there is one rule, and the rule cannot disagree with
 * itself depending on which entry point noticed the duplicate.
 *
 * The policy is **newest connection wins**, and the reason is that the
 * alternative is worse in the case that actually happens. If the oldest wins, a
 * user whose app was killed cannot get back on stage until a server timeout
 * expires, and to them the product is simply broken. Newest-wins costs a user
 * who deliberately joins from two devices their older session — which is both
 * recoverable and what they would expect.
 *
 * Losing connections are demoted to viewers, never disconnected outright: the
 * person keeps watching, and a second device becomes a second screen rather
 * than an error. Demoting also stops the echo that two publishing devices in
 * one room produce, which is the audible half of this bug.
 */
export function resolveDuplicateConnections(connections: ParticipantConnection[]): DuplicateResolution | null {
  const valid = connections.filter((connection) => Number.isFinite(connection.joinedAtMs));
  if (valid.length === 0) return null;
  if (valid.length === 1) {
    return { keep: valid[0], demote: [], reason: "single_connection" };
  }

  // Newest first. Ties broken by rtcUid so the result is deterministic — two
  // connections stamped in the same millisecond must not resolve differently on
  // two clients, or each will demote the other's keeper and nobody publishes.
  const ordered = [...valid].sort((a, b) =>
    b.joinedAtMs !== a.joinedAtMs ? b.joinedAtMs - a.joinedAtMs : b.rtcUid - a.rtcUid
  );
  const [keep, ...rest] = ordered;
  const sameDevice = rest.every((connection) => connection.deviceId === keep.deviceId);

  return {
    keep,
    demote: rest,
    reason: sameDevice ? "reconnect_supersedes_stale" : "newest_device_wins"
  };
}

/**
 * Collapse a roster so that each user appears at most once.
 *
 * The registry builds tiles from what Agora reports, and Agora will report both
 * uids across a reconnect for as long as it takes the old one to time out. A
 * stage that renders both shows one person twice and counts them twice against
 * the guest limit, so a panel of four can present as full.
 *
 * Local participants are protected: whatever the remote view says, the device
 * knows which tile is its own camera, and dropping it would black out the
 * user's own preview.
 */
export function dedupeStageParticipants(participants: LiveStageParticipant[]): LiveStageParticipant[] {
  const bestByUser = new Map<number, LiveStageParticipant>();
  const passthrough: LiveStageParticipant[] = [];

  for (const participant of participants) {
    // An unidentified tile has no user to deduplicate against; two of them are
    // two different people until the roster says otherwise.
    if (!participant.userId || participant.unidentified) {
      passthrough.push(participant);
      continue;
    }
    const existing = bestByUser.get(participant.userId);
    if (!existing) {
      bestByUser.set(participant.userId, participant);
      continue;
    }
    bestByUser.set(participant.userId, preferConnection(existing, participant));
  }

  // Preserve the incoming order: the layout depends on it, and reordering here
  // would move tiles around for reasons the layout module never asked for.
  const kept = new Set<LiveStageParticipant>([...bestByUser.values(), ...passthrough]);
  return participants.filter((participant) => kept.has(participant));
}

/**
 * Which of two tiles for the same user is the real one.
 *
 * Order of preference, and why each beats the next: the local tile is the
 * device's own camera and is never wrong; a live phase beats a joining one
 * because the stale connection is the one still claiming to arrive; publishing
 * media beats not, because a tile with a stream is the one the audience can
 * see. Only then does the reconnect ordering matter.
 */
function preferConnection(a: LiveStageParticipant, b: LiveStageParticipant): LiveStageParticipant {
  if (a.isLocal !== b.isLocal) return a.isLocal ? a : b;
  if (a.isHost !== b.isHost) return a.isHost ? a : b;

  const aLive = a.phase === "live";
  const bLive = b.phase === "live";
  if (aLive !== bLive) return aLive ? a : b;

  const aMedia = Number(a.hasVideo) + Number(a.hasAudio);
  const bMedia = Number(b.hasVideo) + Number(b.hasAudio);
  if (aMedia !== bMedia) return aMedia > bMedia ? a : b;

  // Nothing distinguishes them but the connection. The higher rtcUid is the
  // later join in Agora's allocation, which makes this consistent with
  // `resolveDuplicateConnections` rather than a second, quieter opinion.
  return b.rtcUid > a.rtcUid ? b : a;
}

/**
 * Whether a roster still contains the same person twice.
 *
 * The invariant Stages 23 and 38 exist to hold, exposed so it can be asserted
 * after any roster change rather than only where duplicates were expected.
 */
export function hasDuplicateStagePresence(participants: LiveStageParticipant[]): boolean {
  const seen = new Set<number>();
  for (const participant of participants) {
    if (!participant.userId || participant.unidentified) continue;
    if (seen.has(participant.userId)) return true;
    seen.add(participant.userId);
  }
  return false;
}
