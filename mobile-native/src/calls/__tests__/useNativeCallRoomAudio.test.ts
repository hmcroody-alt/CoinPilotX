import {
  applyCallRemoteAudioEnabled,
  countPublishedAudioTracks,
  countSubscribedRemoteAudioTracks,
  ensureCallMicrophonePublished,
  initializeCallLocalMedia
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

  it("waits for the LiveKit publication event without toggling the microphone off", async () => {
    const listeners = new Set<(publication: any) => void>();
    const participant = {
      audioTrackPublications: new Map(),
      setMicrophoneEnabled: jest.fn(async (enabled: boolean) => {
        if (enabled) {
          const publication = { kind: "audio", track: sdkTrack() };
          participant.audioTrackPublications.set("mic", publication);
          listeners.forEach((listener) => listener(publication));
        }
      })
    };
    const room = {
      localParticipant: participant,
      on: (_event: string, listener: (publication: any) => void) => listeners.add(listener),
      off: (_event: string, listener: (publication: any) => void) => listeners.delete(listener)
    };

    const count = await ensureCallMicrophonePublished(room, { timeoutMs: 50 });

    expect(count).toBe(1);
    expect(participant.setMicrophoneEnabled.mock.calls).toEqual([[true]]);
  });

  it("returns zero when no local microphone publication can be verified", async () => {
    const participant = {
      audioTrackPublications: new Map(),
      setMicrophoneEnabled: jest.fn().mockResolvedValue(undefined)
    };

    await expect(ensureCallMicrophonePublished({ localParticipant: participant }, { timeoutMs: 10 })).resolves.toBe(0);
  });

  it("starts video media from the working microphone path and reasserts audio after camera startup", async () => {
    const order: string[] = [];
    const participant = {
      audioTrackPublications: new Map(),
      setMicrophoneEnabled: jest.fn(async (enabled: boolean) => {
        order.push(`microphone:${enabled}`);
        if (enabled && participant.audioTrackPublications.size === 0) {
          participant.audioTrackPublications.set("mic", { kind: "audio", track: sdkTrack() });
        }
      }),
      setCameraEnabled: jest.fn(async (enabled: boolean) => {
        order.push(`camera:${enabled}`);
      })
    };

    const count = await initializeCallLocalMedia(
      { localParticipant: participant },
      { video: true, useV2: false, fallbackEnabled: true }
    );

    expect(count).toBe(1);
    expect(order).toEqual(["microphone:true", "camera:true", "microphone:true"]);
  });

  it("does not touch camera startup for the protected audio-call path", async () => {
    const participant = {
      audioTrackPublications: new Map(),
      setMicrophoneEnabled: jest.fn(async (enabled: boolean) => {
        if (enabled) participant.audioTrackPublications.set("mic", { kind: "audio", track: sdkTrack() });
      }),
      setCameraEnabled: jest.fn().mockResolvedValue(undefined)
    };

    await expect(
      initializeCallLocalMedia(
        { localParticipant: participant },
        { video: false, useV2: false, fallbackEnabled: true }
      )
    ).resolves.toBe(1);

    expect(participant.setMicrophoneEnabled).toHaveBeenCalledTimes(1);
    expect(participant.setCameraEnabled).not.toHaveBeenCalled();
  });

  it("recovers the microphone when camera startup removes its publication", async () => {
    const participant = {
      audioTrackPublications: new Map<string, any>(),
      setMicrophoneEnabled: jest.fn(async (enabled: boolean) => {
        if (enabled) participant.audioTrackPublications.set("mic", { kind: "audio", track: sdkTrack() });
      }),
      setCameraEnabled: jest.fn(async () => {
        participant.audioTrackPublications.clear();
      })
    };

    await expect(initializeCallLocalMedia(
      { localParticipant: participant },
      { video: true, useV2: false, fallbackEnabled: true }
    )).resolves.toBe(1);

    expect(participant.setMicrophoneEnabled.mock.calls).toEqual([[true], [true]]);
    expect(participant.audioTrackPublications.size).toBe(1);
  });
});
