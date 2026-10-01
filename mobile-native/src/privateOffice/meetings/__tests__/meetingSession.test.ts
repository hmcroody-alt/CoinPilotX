/**
 * Meeting session store — the §61 native matrix, session layer.
 *
 * What these tests defend:
 *  - The waiting room parks WITHOUT media: no `beginCallSession` until the
 *    projection says admitted (§14: the waiting room is a missing row, not a
 *    hidden button).
 *  - Admission hands media to the CANONICAL call store (§3/§59: zero new
 *    Agora owners; this store never touches an engine).
 *  - Leave is me-only and NEVER calls the engine's end path (§42):
 *    `endMeetingForEveryone` must not fire, `hangupCallSession` is not even
 *    imported.
 *  - External teardown (CallKit end, terminal 410) records the meeting-side
 *    leave so the roster never shows a ghost (§43).
 *  - Terminal projection refusals (meeting over, removed) end the session
 *    locally instead of retrying forever.
 */

import { MeetingRefusal, PrivateMeeting } from "../types";

jest.mock("../../../calls/callSessionStore", () => {
  let listener: (() => void) | null = null;
  const state = {
    sessionActive: true,
    callId: "call_1",
    connected: true,
    reconnecting: false
  };
  return {
    beginCallSession: jest.fn(),
    clearCallSession: jest.fn(),
    finalizeCallSession: jest.fn(),
    getCallSession: jest.fn(() => state),
    subscribeCallSession: jest.fn((fn: () => void) => {
      listener = fn;
      return () => {
        listener = null;
      };
    }),
    __setCallState: (patch: Partial<typeof state>) => Object.assign(state, patch),
    __fireCallListener: () => listener && listener()
  };
});

jest.mock("../api", () => ({
  getMeeting: jest.fn(),
  joinMeeting: jest.fn(),
  leaveMeeting: jest.fn(() => Promise.resolve()),
  endMeetingForEveryone: jest.fn(() => Promise.resolve()),
  preflightMeetingMedia: jest.fn(() => Promise.resolve()),
  reportReconnecting: jest.fn(() => Promise.resolve())
}));

// eslint-disable-next-line @typescript-eslint/no-var-requires
const callStore = require("../../../calls/callSessionStore");
// eslint-disable-next-line @typescript-eslint/no-var-requires
const api = require("../api");
import {
  enterMeeting,
  getMeetingSession,
  leaveCurrentMeeting,
  endCurrentMeetingForEveryone,
  refreshMeetingProjection,
  resetMeetingSession
} from "../meetingSession";

function projection(overrides: Partial<PrivateMeeting> = {}): PrivateMeeting {
  return {
    public_id: "mtg_1",
    title: "Standup",
    status: "LIVE",
    waiting_room_enabled: true,
    locked: false,
    scheduled_start_at: "",
    duration_minutes: 0,
    started_at: "2026-09-05T10:00:00+00:00",
    ended_at: "",
    end_reason: "",
    owner_user_id: 1,
    me: {
      user_id: 2,
      role: "PARTICIPANT",
      state: "ADMITTED",
      rtc_uid: 2,
      raised_hand: false,
      raised_hand_at: "",
      joined_at: "",
      left_at: ""
    },
    participants: [],
    recording_active: false,
    capabilities: {
      screen_share: { available: false, reason: "not_implemented" },
      captions: { available: false, reason: "provider_required" },
      recording: { available: false, reason: "flag_disabled" }
    },
    created_at: "",
    call_public_id: "call_1",
    channel_name: "room_1",
    ...overrides
  };
}

beforeEach(() => {
  jest.clearAllMocks();
  callStore.__setCallState({
    sessionActive: true,
    callId: "call_1",
    connected: true,
    reconnecting: false
  });
  resetMeetingSession();
});

afterEach(() => {
  resetMeetingSession();
});

describe("waiting room (§14)", () => {
  it("parks without media when the server answers WAITING_ROOM", async () => {
    api.joinMeeting.mockResolvedValue(
      projection({
        me: { ...projection().me!, state: "WAITING_ROOM" },
        call_public_id: undefined,
        channel_name: undefined
      })
    );
    await enterMeeting("mtg_1");
    expect(getMeetingSession().phase).toBe("waiting_room");
    expect(callStore.beginCallSession).not.toHaveBeenCalled();
  });

  it("connects media through the canonical store once admitted (§3/§59)", async () => {
    api.joinMeeting.mockResolvedValue(projection());
    await enterMeeting("mtg_1");
    expect(api.preflightMeetingMedia).toHaveBeenCalledWith("mtg_1");
    expect(callStore.beginCallSession).toHaveBeenCalledWith(
      expect.objectContaining({ callId: "call_1", callType: "video" })
    );
    expect(getMeetingSession().phase).toBe("in_meeting");
  });

  it("surfaces the server's refusal code and stays idle", async () => {
    api.joinMeeting.mockRejectedValue(
      new MeetingRefusal("meeting_locked", 423, "locked")
    );
    await expect(enterMeeting("mtg_1")).rejects.toBeInstanceOf(MeetingRefusal);
    expect(getMeetingSession().phase).toBe("idle");
    expect(getMeetingSession().refusalCode).toBe("meeting_locked");
    expect(callStore.beginCallSession).not.toHaveBeenCalled();
  });
});

describe("leave vs end-for-everyone (§42)", () => {
  it("leave is me-only: records the leave, never calls the end route", async () => {
    api.joinMeeting.mockResolvedValue(projection());
    await enterMeeting("mtg_1");
    await leaveCurrentMeeting();
    expect(api.leaveMeeting).toHaveBeenCalledWith("mtg_1");
    expect(api.endMeetingForEveryone).not.toHaveBeenCalled();
    expect(callStore.finalizeCallSession).toHaveBeenCalledWith("meeting_left");
    const session = getMeetingSession();
    expect(session.phase).toBe("ended");
    expect(session.endedReason).toBe("left");
  });

  it("end-for-everyone goes through the meeting route, not the engine", async () => {
    api.joinMeeting.mockResolvedValue(projection());
    await enterMeeting("mtg_1");
    await endCurrentMeetingForEveryone();
    expect(api.endMeetingForEveryone).toHaveBeenCalledWith("mtg_1");
    expect(callStore.finalizeCallSession).toHaveBeenCalledWith("meeting_ended");
    expect(getMeetingSession().endedReason).toBe("ended_by_host");
  });
});

describe("external teardown + terminal refusals (§43)", () => {
  it("records the meeting-side leave when someone else tears media down", async () => {
    api.joinMeeting.mockResolvedValue(projection());
    await enterMeeting("mtg_1");
    callStore.__setCallState({ sessionActive: false });
    callStore.__fireCallListener();
    expect(api.leaveMeeting).toHaveBeenCalledWith("mtg_1");
    const session = getMeetingSession();
    expect(session.phase).toBe("ended");
    expect(session.endedReason).toBe("call_ended");
  });

  it("a 410 projection ends the session locally as 'ended'", async () => {
    api.joinMeeting.mockResolvedValue(projection());
    await enterMeeting("mtg_1");
    api.getMeeting.mockRejectedValue(
      new MeetingRefusal("meeting_over", 410, "over")
    );
    await refreshMeetingProjection();
    const session = getMeetingSession();
    expect(session.phase).toBe("ended");
    expect(session.endedReason).toBe("ended");
  });

  it("a 404 after being in the meeting ends as 'removed'", async () => {
    api.joinMeeting.mockResolvedValue(projection());
    await enterMeeting("mtg_1");
    api.getMeeting.mockRejectedValue(
      new MeetingRefusal("not_found", 404, "gone")
    );
    await refreshMeetingProjection();
    expect(getMeetingSession().endedReason).toBe("removed");
  });

  it("transient failures keep the session alive", async () => {
    api.joinMeeting.mockResolvedValue(projection());
    await enterMeeting("mtg_1");
    api.getMeeting.mockRejectedValue(new Error("network"));
    await refreshMeetingProjection();
    expect(getMeetingSession().phase).toBe("in_meeting");
  });
});
