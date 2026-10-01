/**
 * Tile derivation — the §61 grid/identity slice that is pure enough to lock
 * down without a device:
 *  - the projection is the identity authority; an Agora uid with no admitted
 *    participant renders nothing (media cannot introduce a person);
 *  - one row per user forever → keying by user_id makes duplicate tiles after
 *    reconnect impossible;
 *  - waiting-room occupants are never tiles;
 *  - active speaker rides the uid==user_id identity, not a parallel registry.
 */

import { buildMeetingTiles, meetingGridColumns, raisedHands, waitingRoomOccupants } from "../meetingTiles";
import { MeetingParticipant, PrivateMeeting } from "../types";

function participant(overrides: Partial<MeetingParticipant>): MeetingParticipant {
  return {
    user_id: 0,
    role: "PARTICIPANT",
    state: "JOINED",
    rtc_uid: 0,
    raised_hand: false,
    raised_hand_at: "",
    joined_at: "",
    left_at: "",
    ...overrides
  };
}

function meeting(participants: MeetingParticipant[], overrides: Partial<PrivateMeeting> = {}): PrivateMeeting {
  return {
    public_id: "pm_test",
    title: "Standup",
    status: "LIVE",
    waiting_room_enabled: true,
    locked: false,
    scheduled_start_at: "",
    duration_minutes: 0,
    started_at: "2026-09-05T10:00:00+00:00",
    ended_at: "",
    end_reason: "",
    owner_user_id: 101,
    me: participants.find((p) => p.user_id === 101) || null,
    participants,
    recording_active: false,
    capabilities: {
      screen_share: { available: false, reason: "not_implemented" },
      captions: { available: false, reason: "provider_required" },
      recording: { available: false, reason: "flag_disabled" }
    },
    created_at: "2026-09-05T09:00:00+00:00",
    ...overrides
  };
}

describe("buildMeetingTiles", () => {
  test("projection is the identity authority — unknown Agora uid renders nothing", () => {
    const m = meeting([
      participant({ user_id: 101, role: "HOST" }),
      participant({ user_id: 202 })
    ]);
    const tiles = buildMeetingTiles({ meeting: m, remoteUids: [202, 999], localUid: 101 });
    expect(tiles.map((t) => t.userId)).toEqual([101, 202]);
    expect(tiles.find((t) => t.userId === 999)).toBeUndefined();
  });

  test("waiting-room occupants and departed participants are never tiles", () => {
    const m = meeting([
      participant({ user_id: 101, role: "HOST" }),
      participant({ user_id: 202, state: "WAITING_ROOM" }),
      participant({ user_id: 303, state: "LEFT" }),
      participant({ user_id: 404, state: "REMOVED" })
    ]);
    const tiles = buildMeetingTiles({ meeting: m, remoteUids: [202, 303, 404], localUid: 101 });
    expect(tiles.map((t) => t.userId)).toEqual([101]);
  });

  test("reconnect cannot duplicate a tile — keyed by user id, RECONNECTING still present", () => {
    const m = meeting([
      participant({ user_id: 101, role: "HOST" }),
      participant({ user_id: 202, state: "RECONNECTING" })
    ]);
    // Media layer briefly reports the uid while presence says RECONNECTING.
    const tiles = buildMeetingTiles({ meeting: m, remoteUids: [202, 202], localUid: 101 });
    expect(tiles.filter((t) => t.userId === 202)).toHaveLength(1);
    expect(tiles.find((t) => t.userId === 202)?.mediaLive).toBe(true);
  });

  test("self is always mediaLive; absent remote uid means tile without media", () => {
    const m = meeting([
      participant({ user_id: 101, role: "HOST" }),
      participant({ user_id: 202, state: "ADMITTED" })
    ]);
    const tiles = buildMeetingTiles({ meeting: m, remoteUids: [], localUid: 101 });
    expect(tiles.find((t) => t.userId === 101)?.mediaLive).toBe(true);
    expect(tiles.find((t) => t.userId === 202)?.mediaLive).toBe(false);
  });

  test("active speaker maps through uid == user id", () => {
    const m = meeting([
      participant({ user_id: 101, role: "HOST" }),
      participant({ user_id: 202 }),
      participant({ user_id: 303 })
    ]);
    const tiles = buildMeetingTiles({
      meeting: m,
      remoteUids: [202, 303],
      localUid: 101,
      speakingUids: [303]
    });
    expect(tiles.find((t) => t.userId === 303)?.isActiveSpeaker).toBe(true);
    expect(tiles.find((t) => t.userId === 202)?.isActiveSpeaker).toBe(false);
  });

  test("ordering: self, then moderators, then join order — stable across polls", () => {
    const m = meeting([
      participant({ user_id: 505 }),
      participant({ user_id: 101, role: "HOST" }),
      participant({ user_id: 303, role: "CO_HOST" }),
      participant({ user_id: 202 })
    ]);
    const tiles = buildMeetingTiles({ meeting: m, remoteUids: [505, 101, 303], localUid: 202 });
    expect(tiles.map((t) => t.userId)).toEqual([202, 101, 303, 505]);
  });
});

describe("helpers", () => {
  test("grid columns follow the proven call layout family", () => {
    expect(meetingGridColumns(1)).toBe(1);
    expect(meetingGridColumns(2)).toBe(2);
    expect(meetingGridColumns(4)).toBe(2);
    expect(meetingGridColumns(9)).toBe(2);
  });

  test("waiting room and raised hands derive from the projection only", () => {
    const m = meeting([
      participant({ user_id: 101, role: "HOST" }),
      participant({ user_id: 202, state: "WAITING_ROOM" }),
      participant({ user_id: 303, raised_hand: true, raised_hand_at: "2026-09-05T10:02:00+00:00" }),
      participant({ user_id: 404, raised_hand: true, raised_hand_at: "2026-09-05T10:01:00+00:00" })
    ]);
    expect(waitingRoomOccupants(m).map((p) => p.user_id)).toEqual([202]);
    expect(raisedHands(m).map((p) => p.user_id)).toEqual([404, 303]);
    expect(raisedHands(null)).toEqual([]);
  });
});
