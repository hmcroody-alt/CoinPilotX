/**
 * Private Meetings — tile derivation (pure).
 *
 * The video grid is derived from TWO sources that must never disagree about
 * identity (mission §5: rtc_uid is never identity authority):
 *
 *  - the meeting projection (who is admitted, their role, hand, presence
 *    state) — the AUTHORITY on who a tile is;
 *  - the canonical call snapshot (which Agora uids currently publish media)
 *    — a HINT about which tiles have live video/audio right now.
 *
 * The engine mints tokens with uid == PulseSoc user id, so the merge is a
 * lookup, not a guess. An Agora uid with no matching admitted participant is
 * dropped, not rendered: media cannot introduce a person the meeting layer
 * never admitted. And because one logical participant is one row forever
 * (§37), keying tiles by user_id makes a duplicate tile after reconnect a
 * structural impossibility rather than a bug to chase.
 */

import { MeetingParticipant, PrivateMeeting, PRESENT_STATES } from "./types";

export type MeetingTile = {
  /** Stable key: the PulseSoc user id. */
  userId: number;
  participant: MeetingParticipant;
  /** True when the Agora room currently carries this uid (or it is me). */
  mediaLive: boolean;
  isSelf: boolean;
  isActiveSpeaker: boolean;
};

export type MeetingTileInput = {
  meeting: PrivateMeeting | null;
  /** From the canonical call snapshot. */
  remoteUids: number[];
  localUid: number;
  /** Speaking uids from the call layer's active-speaker events. */
  speakingUids?: number[];
};

export function buildMeetingTiles(input: MeetingTileInput): MeetingTile[] {
  const meeting = input.meeting;
  if (!meeting) return [];
  const remote = new Set((input.remoteUids || []).map((uid) => Number(uid) || 0));
  const speaking = new Set((input.speakingUids || []).map((uid) => Number(uid) || 0));
  const localUid = Number(input.localUid) || meeting.me?.user_id || 0;

  const tiles = new Map<number, MeetingTile>();
  for (const participant of meeting.participants || []) {
    if (!PRESENT_STATES.has(participant.state)) continue;
    const userId = Number(participant.user_id) || 0;
    if (userId <= 0 || tiles.has(userId)) continue;
    const isSelf = userId === localUid;
    tiles.set(userId, {
      userId,
      participant,
      mediaLive: isSelf || remote.has(userId),
      isSelf,
      isActiveSpeaker: speaking.has(userId)
    });
  }
  // Self first, then hosts, then join order (stable for reconnects).
  return Array.from(tiles.values()).sort((a, b) => {
    if (a.isSelf !== b.isSelf) return a.isSelf ? -1 : 1;
    const aMod = a.participant.role !== "PARTICIPANT" ? 0 : 1;
    const bMod = b.participant.role !== "PARTICIPANT" ? 0 : 1;
    if (aMod !== bMod) return aMod - bMod;
    return a.userId - b.userId;
  });
}

/** Grid shape for N tiles — matches the call screen's proven layout family. */
export function meetingGridColumns(tileCount: number): number {
  if (tileCount <= 1) return 1;
  if (tileCount <= 4) return 2;
  return 2; // 2×N scroll beyond 4; rows grow, columns stay readable.
}

export function waitingRoomOccupants(meeting: PrivateMeeting | null): MeetingParticipant[] {
  if (!meeting) return [];
  return (meeting.participants || []).filter((p) => p.state === "WAITING_ROOM");
}

export function raisedHands(meeting: PrivateMeeting | null): MeetingParticipant[] {
  if (!meeting) return [];
  return (meeting.participants || [])
    .filter((p) => PRESENT_STATES.has(p.state) && p.raised_hand)
    .sort((a, b) => a.raised_hand_at.localeCompare(b.raised_hand_at));
}
