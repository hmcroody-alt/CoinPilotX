/**
 * Canonical participant registry tests.
 *
 * Multi-guest invariant: the UI never infers identity on its own. Backend
 * `participants[]` (who) joins Agora `remoteUids[]` (whose media is live)
 * strictly by rtc_uid == user_id. These tests lock that join, the provisional
 * handling of media the backend hasn't confirmed yet, and the transient
 * speaking / remote-mute display substate.
 */
jest.mock("../callSessionStore", () => ({
  useCallSession: jest.fn(),
  getCallSession: jest.fn(),
  subscribeCallSession: jest.fn()
}));

import type { PulseCall } from "../../api/calls";
import {
  __resetParticipantRegistryForTests,
  buildCallParticipants,
  connectedParticipants,
  getParticipantMediaState,
  isTerminalParticipantStatus,
  resetParticipantMediaState,
  ringingParticipants,
  setRemoteAudioMuted,
  setRemoteVideoMuted,
  setSpeakingUids
} from "../callParticipants";

const LOCAL_UID = 11;

function groupCall(): PulseCall {
  return {
    call_id: "call-g1",
    call_scope: "group",
    participants: [
      { user_id: 11, role: "caller", status: "joined", display_name: "Ada" },
      { user_id: 22, role: "callee", status: "joined", display_name: "Grace" },
      { user_id: 33, role: "callee", status: "ringing", display_name: "Edsger" },
      { user_id: 44, role: "callee", status: "declined", display_name: "Alan" }
    ]
  };
}

describe("buildCallParticipants", () => {
  beforeEach(() => __resetParticipantRegistryForTests());

  it("joins backend participants to remote media strictly by rtc_uid == user_id", () => {
    const views = buildCallParticipants(groupCall(), [22], LOCAL_UID);
    const grace = views.find((v) => v.userId === 22);
    const edsger = views.find((v) => v.userId === 33);
    expect(grace?.rtcConnected).toBe(true);
    expect(grace?.displayName).toBe("Grace");
    expect(edsger?.rtcConnected).toBe(false);
    expect(edsger?.backendStatus).toBe("ringing");
  });

  it("marks the local participant connected without requiring a remote uid", () => {
    const views = buildCallParticipants(groupCall(), [], LOCAL_UID);
    const local = views.find((v) => v.userId === LOCAL_UID);
    expect(local?.isLocal).toBe(true);
    expect(local?.rtcConnected).toBe(true);
  });

  it("keeps media the backend has not confirmed yet as a provisional participant", () => {
    const views = buildCallParticipants(groupCall(), [22, 55], LOCAL_UID);
    const provisional = views.find((v) => v.userId === 55);
    expect(provisional?.rtcConnected).toBe(true);
    expect(provisional?.backendStatus).toBe("joined");
  });

  it("never duplicates a uid that is both in the backend list and the media room", () => {
    const views = buildCallParticipants(groupCall(), [22], LOCAL_UID);
    expect(views.filter((v) => v.userId === 22)).toHaveLength(1);
  });

  it("selectors: tiles only for connected non-terminal, chips only for ringing", () => {
    const views = buildCallParticipants(groupCall(), [22], LOCAL_UID);
    const tiles = connectedParticipants(views).map((v) => v.userId).sort();
    const chips = ringingParticipants(views).map((v) => v.userId);
    expect(tiles).toEqual([11, 22]);
    expect(chips).toEqual([33]);
    expect(isTerminalParticipantStatus("declined")).toBe(true);
  });
});

describe("participant media substate", () => {
  beforeEach(() => __resetParticipantRegistryForTests());

  it("reflects speaking and remote mute state per uid", () => {
    setSpeakingUids([22]);
    setRemoteAudioMuted(33, true);
    setRemoteVideoMuted(22, true);
    const views = buildCallParticipants(groupCall(), [22, 33], LOCAL_UID, getParticipantMediaState());
    expect(views.find((v) => v.userId === 22)?.speaking).toBe(true);
    expect(views.find((v) => v.userId === 33)?.audioMuted).toBe(true);
    expect(views.find((v) => v.userId === 22)?.videoMuted).toBe(true);
  });

  it("honors backend muted flags even without an engine event", () => {
    const call = groupCall();
    (call.participants || [])[1].muted_audio = true;
    const views = buildCallParticipants(call, [22], LOCAL_UID);
    expect(views.find((v) => v.userId === 22)?.audioMuted).toBe(true);
  });

  it("resets cleanly on session teardown", () => {
    setSpeakingUids([22, 33]);
    setRemoteAudioMuted(22, true);
    resetParticipantMediaState();
    expect(getParticipantMediaState()).toEqual({ speakingUids: [], audioMutedUids: [], videoMutedUids: [] });
  });

  it("unmute removes the uid instead of accumulating state forever", () => {
    setRemoteAudioMuted(22, true);
    setRemoteAudioMuted(22, false);
    expect(getParticipantMediaState().audioMutedUids).toEqual([]);
  });
});
