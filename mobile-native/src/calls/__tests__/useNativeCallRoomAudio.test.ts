import {
  applyCallRemoteAudioEnabled,
  countPublishedAudioTracks,
  countSubscribedRemoteAudioTracks,
  ensureCallMicrophonePublished
} from "../useNativeCallRoom";

function sdkTrack() {
  const calls: boolean[] = [];
  return {
    kind: "audio",
    setEnabledCalls: calls,
    setEnabled(value: boolean) {
      calls.push(value);
      return Promise.resolve();
    }
  };
}

function rawTrack() {
  return { kind: "audio", mediaStreamTrack: { enabled: true } };
}

function participantWithAudioPublications(publications: any[]) {
  return {
    audioTrackPublications: new Map(publications.map((publication, index) => [String(index), publication]))
  };
}

function roomWithRemoteTracks(tracks: any[]) {
  const remote = participantWithAudioPublications(tracks.map((track) => ({ track })));
  return { remoteParticipants: new Map([["remote-1", remote]]) };
}

describe("useNativeCallRoom audio publication helpers", () => {
  it("counts only subscribed local audio publications with real tracks", () => {
    const participant = participantWithAudioPublications([
      { track: sdkTrack(), isSubscribed: true },
      { track: sdkTrack() },
      { track: null },
      { track: sdkTrack(), isSubscribed: false }
    ]);

    expect(countPublishedAudioTracks(participant)).toBe(2);
  });

  it("counts subscribed remote audio across every participant", () => {
    const room = {
      remoteParticipants: new Map([
        ["a", participantWithAudioPublications([{ track: sdkTrack() }, { track: null }])],
        ["b", participantWithAudioPublications([{ track: sdkTrack() }, { track: sdkTrack(), isSubscribed: false }])]
      ])
    };

    expect(countSubscribedRemoteAudioTracks(room)).toBe(2);
  });

  it("drives every subscribed remote audio track on so video calls cannot stay visually connected but silent", async () => {
    const a = sdkTrack();
    const b = sdkTrack();
    const touched = await applyCallRemoteAudioEnabled(roomWithRemoteTracks([a, b]), true);

    expect(touched).toBe(2);
    expect(a.setEnabledCalls).toEqual([true]);
    expect(b.setEnabledCalls).toEqual([true]);
  });

  it("falls back to mediaStreamTrack.enabled when the SDK track has no setEnabled", async () => {
    const raw = rawTrack();
    const touched = await applyCallRemoteAudioEnabled(roomWithRemoteTracks([raw]), false);

    expect(touched).toBe(1);
    expect(raw.mediaStreamTrack.enabled).toBe(false);
  });

  it("retries local microphone publication when the first enable returns before the publication exists", async () => {
    let enabledCalls = 0;
    const participant = {
      audioTrackPublications: new Map(),
      setMicrophoneEnabled: jest.fn(async (enabled: boolean) => {
        if (enabled) {
          enabledCalls += 1;
          if (enabledCalls >= 2) participant.audioTrackPublications.set("mic", { track: sdkTrack() });
        }
      })
    };

    const count = await ensureCallMicrophonePublished({ localParticipant: participant });

    expect(count).toBe(1);
    expect(participant.setMicrophoneEnabled).toHaveBeenCalledWith(true);
    expect(participant.setMicrophoneEnabled).toHaveBeenCalledWith(false);
  });

  it("returns zero when no local microphone publication can be verified", async () => {
    const participant = {
      audioTrackPublications: new Map(),
      setMicrophoneEnabled: jest.fn().mockResolvedValue(undefined)
    };

    await expect(ensureCallMicrophonePublished({ localParticipant: participant })).resolves.toBe(0);
  });
});
