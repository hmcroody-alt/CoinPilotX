/**
 * The canonical runtime registry of who is on a Live stage.
 *
 * A multi-guest Live is a broadcast: one Agora channel, a bounded set of
 * publishers on stage, and an unbounded audience subscribing to them. Agora
 * tells a client only that "uid 4242 joined and is now sending video". It does
 * not know that 4242 is Ada, that she is a co-host, or that she should sit
 * second on the stage. Only the PulseSoc server knows that, and it says so in
 * the guest roster returned by `/api/pulse/live/<id>/state`.
 *
 * Before this module the app filled the gap by guessing. Every remote publisher
 * was named "Live participant", and the host was whoever happened to arrive
 * first (`isHost: !publish && participants.length === 0`). With exactly one
 * publisher that guess is invisible. With two it is wrong roughly half the
 * time, and if the host's connection blips and rejoins after a guest, the guest
 * silently becomes "the host" — which matters, because the audience screen
 * sorts by that flag and shows only the top-ranked stream.
 *
 * So: this module joins the transport-level facts (uid, has video, has audio,
 * currently speaking) to the server-level facts (user, name, avatar, role,
 * stage order) and produces one ordered list that UI components read. A
 * component must never infer identity from arrival order, array position, or
 * anything else it can see locally. It looks the uid up here.
 *
 * Pure and synchronous by design — no hooks, no engine handles, no I/O — so the
 * ordering and identity rules can be tested without a device.
 */

import type { LiveGuest } from "./liveSession";
// Value import, but not a cycle: `liveSessionLifecycle` imports only *types*
// from this module, so nothing is evaluated in the other direction at runtime.
import { dedupeStageParticipants } from "./liveSessionLifecycle";

/** Canonical role vocabulary, matching `services/live_participants.py`. */
export type LiveRole = "host" | "cohost" | "guest" | "audience";

/**
 * Where a stage participant is in the join sequence.
 *
 * The intermediate states exist so a guest never appears as an empty black tile.
 * A tile is only rendered once its owner reaches "live"; before that the UI
 * shows the person's avatar and a status, which is honest about what is
 * happening rather than presenting a broken video surface.
 */
export type LiveStagePhase = "invited" | "accepted" | "preparing" | "joining" | "live" | "left";

export type LiveStageParticipant = {
  /** Agora numeric uid. The join key between transport and roster. */
  rtcUid: number;
  /** PulseSoc user id. 0 when the server has not identified this uid. */
  userId: number;
  /** Guest row id, or 0 for the host and for unmatched publishers. */
  guestId: number;
  /** Stable key for React lists. Survives mute, video toggles and reconnects. */
  key: string;
  displayName: string;
  avatarUrl: string;
  role: LiveRole;
  roleLabel: string;
  phase: LiveStagePhase;
  isLocal: boolean;
  isHost: boolean;
  /** Publishing a camera track right now. */
  hasVideo: boolean;
  /** Publishing a microphone track right now. */
  hasAudio: boolean;
  /** Muted by themselves or by a moderator. */
  audioMuted: boolean;
  /** Currently the loudest speaker, from Agora's volume indication. */
  speaking: boolean;
  /** Server-assigned stage order. Lower sorts earlier. */
  layoutPosition: number;
  /**
   * True when Agora reports a uid the server roster does not describe. Such a
   * participant is still rendered — dropping a real publisher because the
   * roster is a poll interval behind would be worse — but it is flagged so the
   * UI can show a neutral placeholder rather than invent a name.
   */
  unidentified: boolean;
};

/** What the transport layer knows about a uid, independent of who they are. */
export type LiveRtcPresence = {
  rtcUid: number;
  isLocal?: boolean;
  hasVideo?: boolean;
  hasAudio?: boolean;
  audioMuted?: boolean;
  speaking?: boolean;
  videoTrack?: any | null;
  audioTrack?: any | null;
};

/** What the server knows about the session, from `/state`. */
export type LiveRosterSnapshot = {
  hostUserId: number;
  hostDisplayName?: string;
  hostAvatarUrl?: string;
  guests: LiveGuest[];
};

/** The viewing client's own seat, so it can find itself without guessing. */
export type LiveLocalSeat = {
  rtcUid: number;
  userId?: number;
  displayName?: string;
  avatarUrl?: string;
  role?: string;
};

const ROLE_ORDER: Record<LiveRole, number> = { host: 0, cohost: 1, guest: 2, audience: 3 };

const ROLE_LABELS: Record<LiveRole, string> = {
  host: "Host",
  cohost: "Co-host",
  guest: "Guest",
  audience: "Viewer"
};

const ROLE_ALIASES: Record<string, LiveRole> = {
  host: "host",
  creator: "host",
  publisher: "host",
  owner: "host",
  cohost: "cohost",
  "co-host": "cohost",
  co_host: "cohost",
  moderator: "cohost",
  guest: "guest",
  speaker: "guest",
  panelist: "guest",
  audience: "audience",
  viewer: "audience",
  watcher: "audience",
  subscriber: "audience"
};

/**
 * Guest statuses, mapped to the phase the UI should present.
 *
 * These strings are the server's, from `pulse_live_guests.status`. An unknown
 * status resolves to "preparing" rather than "live", because showing a spinner
 * for someone who is actually publishing is a far smaller failure than showing
 * a black tile for someone who is not.
 */
const STATUS_PHASES: Record<string, LiveStagePhase> = {
  invited: "invited",
  pending: "invited",
  requested: "invited",
  accepted: "accepted",
  joining: "preparing",
  joined: "joining",
  publishing: "joining",
  active: "live",
  live: "live",
  left: "left",
  removed: "left",
  declined: "left",
  expired: "left",
  rejected: "left"
};

export function normalizeLiveRole(role: unknown): LiveRole {
  const key = String(role ?? "").trim().toLowerCase();
  return ROLE_ALIASES[key] || "audience";
}

export function liveRoleLabel(role: unknown): string {
  return ROLE_LABELS[normalizeLiveRole(role)];
}

/** Map a server guest status onto a stage phase. */
export function stagePhaseForStatus(status: unknown): LiveStagePhase {
  const key = String(status ?? "").trim().toLowerCase();
  return STATUS_PHASES[key] || "preparing";
}

/**
 * Whether this participant should be given a video tile yet.
 *
 * Requires both that the server considers them live and that a camera track is
 * actually arriving. Requiring both is what prevents the empty black tile: a
 * guest whose row says "live" but whose camera has not yet produced a frame is
 * still shown as an avatar.
 */
export function shouldRenderVideoTile(participant: LiveStageParticipant): boolean {
  return participant.phase === "live" && participant.hasVideo;
}

/** Participants who occupy a stage slot, in server order. */
export function stageRoster(participants: LiveStageParticipant[]): LiveStageParticipant[] {
  return participants.filter((participant) => participant.phase !== "left");
}

/** Participants currently publishing media. */
export function publishingRoster(participants: LiveStageParticipant[]): LiveStageParticipant[] {
  return participants.filter(
    (participant) => participant.phase === "live" && (participant.hasVideo || participant.hasAudio)
  );
}

function toInt(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? Math.trunc(parsed) : 0;
}

function participantKey(rtcUid: number, userId: number, guestId: number): string {
  // uid first because it is the only identifier guaranteed present for every
  // publisher, including one the roster has not caught up with yet. Falling
  // back through userId and guestId keeps the key stable for a roster entry
  // that has not connected to RTC yet.
  if (rtcUid > 0) return `uid-${rtcUid}`;
  if (userId > 0) return `user-${userId}`;
  if (guestId > 0) return `guest-${guestId}`;
  return "unknown";
}

/**
 * Build the ordered stage roster.
 *
 * The server roster is authoritative for identity, role and order. The RTC
 * presence map is authoritative for whether media is actually flowing. Neither
 * alone is sufficient: the roster does not know a camera has frozen, and the
 * transport does not know who anyone is.
 *
 * A participant appears if the server lists them (even before they connect, so
 * the UI can show "joining") or if Agora reports them (even before the roster
 * catches up, so a real publisher is never dropped).
 */
export function buildStageParticipants(
  roster: LiveRosterSnapshot,
  presence: LiveRtcPresence[],
  localSeat?: LiveLocalSeat | null
): LiveStageParticipant[] {
  const presenceByUid = new Map<number, LiveRtcPresence>();
  for (const entry of presence || []) {
    const uid = toInt(entry?.rtcUid);
    if (uid > 0) presenceByUid.set(uid, entry);
  }

  const localUid = toInt(localSeat?.rtcUid);
  const hostUserId = toInt(roster?.hostUserId);
  const built: LiveStageParticipant[] = [];
  const claimedUids = new Set<number>();

  const claim = (uid: number) => {
    if (uid > 0) claimedUids.add(uid);
    return presenceByUid.get(uid);
  };

  // The host always holds the first stage slot. This is a server fact — the
  // session's owner — not an observation about who connected first.
  if (hostUserId > 0) {
    const uid = hostUserId; // rtc uid is the user id; see live_participants.rtc_uid
    const media = claim(uid);
    built.push({
      rtcUid: uid,
      userId: hostUserId,
      guestId: 0,
      key: participantKey(uid, hostUserId, 0),
      displayName: String(roster?.hostDisplayName || "Host"),
      avatarUrl: String(roster?.hostAvatarUrl || ""),
      role: "host",
      roleLabel: ROLE_LABELS.host,
      // A host with no RTC presence yet is "joining", not "left". Ending a Live
      // is a separate, explicit act; a momentary absence must never be rendered
      // as the broadcast being over.
      phase: media ? "live" : "joining",
      isLocal: uid === localUid,
      isHost: true,
      hasVideo: Boolean(media?.hasVideo),
      hasAudio: Boolean(media?.hasAudio),
      audioMuted: Boolean(media?.audioMuted),
      speaking: Boolean(media?.speaking),
      layoutPosition: 0,
      unidentified: false
    });
  }

  for (const guest of roster?.guests || []) {
    const userId = toInt(guest?.userId);
    // The server tells us the uid. We do not derive it, because the derivation
    // is a backend protocol detail that this layer should not depend on.
    const uid = toInt(guest?.rtcUid) || userId;
    if (uid > 0 && claimedUids.has(uid)) continue; // host also listed as a guest row
    const media = claim(uid);
    const role = normalizeLiveRole(guest?.role);
    const rosterPhase = stagePhaseForStatus(guest?.status);
    built.push({
      rtcUid: uid,
      userId,
      guestId: toInt(guest?.guestId),
      key: participantKey(uid, userId, toInt(guest?.guestId)),
      displayName: String(guest?.displayName || "Guest"),
      avatarUrl: String(guest?.avatarUrl || ""),
      role: role === "audience" ? "guest" : role,
      roleLabel: String(guest?.roleLabel || ROLE_LABELS[role === "audience" ? "guest" : role]),
      // The server says they are on stage; the transport says whether their
      // media has arrived. Both must agree before a tile goes live.
      phase: rosterPhase === "live" && !media ? "joining" : rosterPhase,
      isLocal: uid > 0 && uid === localUid,
      isHost: false,
      hasVideo: Boolean(media?.hasVideo),
      hasAudio: Boolean(media?.hasAudio),
      // A moderator mute is a server fact and outranks the transport's view.
      audioMuted: Boolean(guest?.audioMuted) || Boolean(media?.audioMuted),
      speaking: Boolean(media?.speaking) && !guest?.audioMuted,
      layoutPosition: toInt(guest?.layoutPosition),
      unidentified: false
    });
  }

  // Publishers Agora reports that the roster has not described yet. Rendering
  // them with a neutral placeholder is better than dropping a real stream
  // because the state poll is a beat behind — but they are never given an
  // invented name, and they sort last.
  for (const entry of presence || []) {
    const uid = toInt(entry?.rtcUid);
    if (uid <= 0 || claimedUids.has(uid)) continue;
    claimedUids.add(uid);
    built.push({
      rtcUid: uid,
      userId: 0,
      guestId: 0,
      key: participantKey(uid, 0, 0),
      displayName: "",
      avatarUrl: "",
      role: "guest",
      roleLabel: ROLE_LABELS.guest,
      phase: "live",
      isLocal: uid === localUid,
      isHost: false,
      hasVideo: Boolean(entry?.hasVideo),
      hasAudio: Boolean(entry?.hasAudio),
      audioMuted: Boolean(entry?.audioMuted),
      speaking: Boolean(entry?.speaking),
      layoutPosition: Number.MAX_SAFE_INTEGER,
      unidentified: true
    });
  }

  // Stage 23/38. The uid claim above stops the same *connection* appearing
  // twice; this stops the same *person* appearing twice. They are different
  // failures: a guest who reconnects gets a new roster row while the old one is
  // still being reaped, so the stage shows one person in two tiles and counts
  // them twice against the guest limit — a panel of four presenting as full.
  // The rule lives in `liveSessionLifecycle` so that the reconnect path and the
  // multi-device path cannot end up with two different answers.
  return sortStageParticipants(dedupeStageParticipants(built));
}

/**
 * Stable stage order: host, then co-hosts, then guests, then anyone the roster
 * has not identified — each group in the server's layout order, with uid as the
 * final tiebreak.
 *
 * Determinism matters more than cleverness here. Sorting by anything volatile —
 * who is speaking, who has video — would make tiles swap places under people
 * mid-sentence. Active speaker is surfaced as a highlight on a tile, never as a
 * change to where that tile sits.
 */
export function sortStageParticipants(participants: LiveStageParticipant[]): LiveStageParticipant[] {
  return [...participants].sort((a, b) => {
    if (a.isHost !== b.isHost) return a.isHost ? -1 : 1;
    const roleDelta = ROLE_ORDER[a.role] - ROLE_ORDER[b.role];
    if (roleDelta !== 0) return roleDelta;
    if (a.layoutPosition !== b.layoutPosition) return a.layoutPosition - b.layoutPosition;
    return a.rtcUid - b.rtcUid;
  });
}

/** Look up who a remote uid is. Components must use this, never array order. */
export function findByRtcUid(
  participants: LiveStageParticipant[],
  rtcUid: unknown
): LiveStageParticipant | null {
  const needle = toInt(rtcUid);
  if (needle <= 0) return null;
  return participants.find((participant) => participant.rtcUid === needle) || null;
}

/**
 * Display name for a participant, without ever inventing one.
 *
 * An unidentified uid gets a neutral placeholder. It does not get a plausible
 * name, because a wrong name attached to a real face is worse than no name.
 */
export function participantDisplayName(participant: LiveStageParticipant | null): string {
  if (!participant) return "";
  if (participant.isLocal) return participant.displayName || "You";
  return participant.displayName || "";
}

/** The participant a spotlight layout should focus on. */
export function focusParticipant(participants: LiveStageParticipant[]): LiveStageParticipant | null {
  const publishing = publishingRoster(participants);
  return publishing.find((participant) => participant.isHost) || publishing[0] || null;
}
