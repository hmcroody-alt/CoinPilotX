import {
  applyRemoteAudioEnabled,
  countSubscribedRemoteAudioTracks,
  synchronizeRealtimeRemoteAudio
} from "../realtimeRemoteAudioController";

function track() {
  return { setEnabled: jest.fn().mockResolvedValue(undefined) };
}

describe("canonical remote realtime audio controller", () => {
  it("drives host and approved-guest tracks through one multi-speaker path", async () => {
    const hostTrack = track();
    const guestTrack = track();
    const pending = { isSubscribed: false, setSubscribed: jest.fn().mockResolvedValue(undefined) };
    const room = {
      remoteParticipants: new Map([
        ["host", { identity: "host", audioTrackPublications: new Map([["h", { trackSid: "h", track: hostTrack }]]) }],
        ["guest", { identity: "guest", audioTrackPublications: new Map<string, any>([["g", { trackSid: "g", track: guestTrack }], ["p", pending]]) }]
      ])
    };

    const result = await synchronizeRealtimeRemoteAudio(room, true, {
      sessionId: "live-42",
      roomType: "livestream",
      participantRole: "viewer",
      canSubscribe: true
    });

    expect(result).toEqual({ discovered: 3, subscriptionRequests: 1, subscribed: 2, enabled: 2 });
    expect(hostTrack.setEnabled).toHaveBeenCalledWith(true);
    expect(guestTrack.setEnabled).toHaveBeenCalledWith(true);
    expect(pending.setSubscribed).toHaveBeenCalledWith(true);
    expect(countSubscribedRemoteAudioTracks(room)).toBe(2);
  });

  it("does not subscribe when server authorization forbids it", async () => {
    const pending = { isSubscribed: false, setSubscribed: jest.fn().mockResolvedValue(undefined) };
    const room = {
      remoteParticipants: new Map([["remote", { audioTrackPublications: new Map([["p", pending]]) }]])
    };

    await expect(synchronizeRealtimeRemoteAudio(room, true, { canSubscribe: false })).resolves.toEqual({
      discovered: 0,
      subscriptionRequests: 0,
      subscribed: 0,
      enabled: 0
    });
    expect(pending.setSubscribed).not.toHaveBeenCalled();
  });

  it("keeps the existing boolean adapter for protected call behavior", async () => {
    const remoteTrack = track();
    const room = {
      remoteParticipants: new Map([["remote", { audioTrackPublications: new Map([["a", { track: remoteTrack }]]) }]])
    };

    await expect(applyRemoteAudioEnabled(room, false)).resolves.toBe(1);
    expect(remoteTrack.setEnabled).toHaveBeenCalledWith(false);
  });
});
