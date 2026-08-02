import { publishedLiveAudioTrackCount, publishLiveMicrophone } from "../liveAudioPublisher";

/**
 * Fake LiveKit room whose publish latency is controllable, so we can reproduce
 * the exact race that produced duplicate audio tracks in production: a publish
 * that completes AFTER the old code's 150ms poll gave up.
 */
function fakeRoom(options: { publishDelayMs?: number; autoPublish?: boolean } = {}) {
  const publishDelayMs = options.publishDelayMs ?? 0;
  const autoPublish = options.autoPublish !== false;
  const listeners = new Map<string, Set<(payload: any) => void>>();
  const publications = new Map<string, any>();
  let publishSeq = 0;

  const emit = (event: string, payload: any) => {
    for (const listener of listeners.get(event) || []) listener(payload);
  };

  const addPublication = () => {
    publishSeq += 1;
    const sid = `audio-${publishSeq}`;
    const publication = { kind: "audio", isSubscribed: true, track: { kind: "audio", sid } };
    publications.set(sid, publication);
    emit("localTrackPublished", publication);
  };

  const room = {
    setMicrophoneEnabledCalls: [] as boolean[],
    unpublished: [] as any[],
    on(event: string, listener: (payload: any) => void) {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event)!.add(listener);
      return room;
    },
    off(event: string, listener: (payload: any) => void) {
      listeners.get(event)?.delete(listener);
      return room;
    },
    forcePublish: addPublication,
    localParticipant: {
      audioTrackPublications: publications,
      async setMicrophoneEnabled(enabled: boolean) {
        room.setMicrophoneEnabledCalls.push(enabled);
        if (!enabled) {
          publications.clear();
          return;
        }
        if (!autoPublish) return;
        if (publishDelayMs > 0) {
          await new Promise((resolve) => setTimeout(resolve, publishDelayMs));
        }
        addPublication();
      },
      async unpublishTrack(track: any) {
        room.unpublished.push(track);
        publications.delete(track.sid);
      }
    }
  };
  return room;
}

describe("deterministic livestream microphone publishing", () => {
  it("publishes exactly one audio track on a fast publish", async () => {
    const room = fakeRoom();
    const result = await publishLiveMicrophone(room);

    expect(result.outcome).toBe("published");
    expect(result.audioTrackCount).toBe(1);
    expect(room.setMicrophoneEnabledCalls).toEqual([true]);
  });

  it("REGRESSION: a slow publish still yields one track, never a duplicate", async () => {
    // 400ms comfortably exceeds the old 150ms poll window that caused the
    // off/on toggle and therefore the duplicate publication.
    const room = fakeRoom({ publishDelayMs: 400 });
    const result = await publishLiveMicrophone(room);

    expect(result.outcome).toBe("published");
    expect(result.audioTrackCount).toBe(1);
    expect(result.duplicatesRemoved).toBe(0);
    // The mic is never toggled off - that toggle was the duplicate generator.
    expect(room.setMicrophoneEnabledCalls).toEqual([true]);
    expect(room.setMicrophoneEnabledCalls).not.toContain(false);
  });

  it("REGRESSION: two back-to-back publish calls share one operation", async () => {
    const room = fakeRoom({ publishDelayMs: 100 });
    const [first, second] = await Promise.all([publishLiveMicrophone(room), publishLiveMicrophone(room)]);

    expect(room.setMicrophoneEnabledCalls).toEqual([true]);
    expect(first.audioTrackCount).toBe(1);
    expect(second.audioTrackCount).toBe(1);
    expect(publishedLiveAudioTrackCount(room)).toBe(1);
  });

  it("is free when the room is already publishing", async () => {
    const room = fakeRoom();
    await publishLiveMicrophone(room);
    const again = await publishLiveMicrophone(room);

    expect(again.outcome).toBe("already_published");
    expect(room.setMicrophoneEnabledCalls).toEqual([true]);
  });

  it("reconciles pre-existing duplicate publications down to one", async () => {
    const room = fakeRoom();
    room.forcePublish();
    room.forcePublish();
    room.forcePublish();
    expect(publishedLiveAudioTrackCount(room)).toBe(3);

    const result = await publishLiveMicrophone(room);

    expect(result.duplicatesRemoved).toBe(2);
    expect(publishedLiveAudioTrackCount(room)).toBe(1);
  });

  it("reports a timeout instead of hanging when the track never publishes", async () => {
    const room = fakeRoom({ autoPublish: false });
    const result = await publishLiveMicrophone(room, { timeoutMs: 50 });

    expect(result.outcome).toBe("timeout");
    expect(result.audioTrackCount).toBe(0);
  });

  it("detaches its listener so repeated publishes do not leak handlers", async () => {
    const room = fakeRoom({ publishDelayMs: 10 });
    await publishLiveMicrophone(room);
    await publishLiveMicrophone(room);
    await publishLiveMicrophone(room);

    // Emitting after completion must not throw or double-resolve.
    expect(() => room.forcePublish()).not.toThrow();
  });

  it("handles a missing room and a missing participant without throwing", async () => {
    await expect(publishLiveMicrophone(null)).resolves.toEqual(
      expect.objectContaining({ outcome: "no_participant" })
    );
    await expect(publishLiveMicrophone({ localParticipant: null })).resolves.toEqual(
      expect.objectContaining({ outcome: "no_participant" })
    );
  });

  it("refuses microphone publication for a viewer even when client code calls the publisher", async () => {
    const room = fakeRoom();
    const result = await publishLiveMicrophone(room, {
      context: { participantRole: "viewer", roomType: "livestream", canPublishMicrophone: false }
    });

    expect(result.outcome).toBe("forbidden");
    expect(result.audioTrackCount).toBe(0);
    expect(room.setMicrophoneEnabledCalls).toEqual([]);
  });
});
