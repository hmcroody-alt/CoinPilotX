import { applyRemoteAudioEnabled, ensureLiveMicrophonePublished } from "../useLiveBroadcastRoom";

/** A fake LiveKit audio track that records setEnabled calls (the SDK path). */
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

/** A fake track with no setEnabled — exercises the mediaStreamTrack fallback. */
function rawTrack() {
  return { kind: "audio", mediaStreamTrack: { enabled: true } };
}

function room(tracks: any[]) {
  const remote = {
    audioTrackPublications: new Map(tracks.map((track, index) => [String(index), { track }]))
  };
  return { remoteParticipants: new Map([["remote-1", remote]]) };
}

describe("applyRemoteAudioEnabled (Issue 5: viewer mute must stick across new tracks)", () => {
  it("drives setEnabled on every subscribed remote audio track", async () => {
    const a = sdkTrack();
    const b = sdkTrack();
    const touched = await applyRemoteAudioEnabled(room([a, b]), false);
    expect(touched).toBe(2);
    expect(a.setEnabledCalls).toEqual([false]);
    expect(b.setEnabledCalls).toEqual([false]);
  });

  it("re-enables tracks when the viewer turns sound back on", async () => {
    const a = sdkTrack();
    await applyRemoteAudioEnabled(room([a]), false);
    await applyRemoteAudioEnabled(room([a]), true);
    expect(a.setEnabledCalls).toEqual([false, true]);
  });

  it("falls back to mediaStreamTrack.enabled when the track has no setEnabled", async () => {
    const raw = rawTrack();
    const touched = await applyRemoteAudioEnabled(room([raw]), false);
    expect(touched).toBe(1);
    expect(raw.mediaStreamTrack.enabled).toBe(false);
  });

  it("is a safe no-op for an empty or null room", async () => {
    expect(await applyRemoteAudioEnabled(room([]), false)).toBe(0);
    expect(await applyRemoteAudioEnabled(null, false)).toBe(0);
    expect(await applyRemoteAudioEnabled({}, false)).toBe(0);
  });

  it("skips publications that have no track yet (not subscribed)", async () => {
    const roomWithEmptyPub = { remoteParticipants: new Map([["r", { audioTrackPublications: new Map([["0", { track: null }]]) }]]) };
    expect(await applyRemoteAudioEnabled(roomWithEmptyPub, false)).toBe(0);
  });

  it("skips explicitly unsubscribed audio publications", async () => {
    const a = sdkTrack();
    const roomWithUnsubscribedPub = { remoteParticipants: new Map([["r", { audioTrackPublications: new Map([["0", { track: a, isSubscribed: false }]]) }]]) };
    expect(await applyRemoteAudioEnabled(roomWithUnsubscribedPub, true)).toBe(0);
    expect(a.setEnabledCalls).toEqual([]);
  });
});

describe("ensureLiveMicrophonePublished", () => {
  it("enables and verifies the local microphone publication", async () => {
    const participant = {
      audioTrackPublications: new Map(),
      setMicrophoneEnabled: jest.fn(async (enabled: boolean) => {
        if (enabled) participant.audioTrackPublications.set("mic", { track: sdkTrack(), isSubscribed: true });
      })
    };

    await expect(ensureLiveMicrophonePublished({ localParticipant: participant })).resolves.toBe(1);
    expect(participant.setMicrophoneEnabled).toHaveBeenCalledWith(true);
  });

  it("retries once when the first microphone enable does not publish a track", async () => {
    let enableCount = 0;
    const participant = {
      audioTrackPublications: new Map(),
      setMicrophoneEnabled: jest.fn(async (enabled: boolean) => {
        if (enabled) {
          enableCount += 1;
          if (enableCount >= 2) participant.audioTrackPublications.set("mic", { track: sdkTrack(), isSubscribed: true });
        }
      })
    };

    await expect(ensureLiveMicrophonePublished({ localParticipant: participant })).resolves.toBe(1);
    expect(participant.setMicrophoneEnabled).toHaveBeenCalledWith(false);
    expect(participant.setMicrophoneEnabled).toHaveBeenLastCalledWith(true);
  });

  it("returns zero when no local participant exists", async () => {
    await expect(ensureLiveMicrophonePublished({})).resolves.toBe(0);
  });
});
