/**
 * HISTORICAL DEFECT GUARD - livestream duplicate audio track.
 *
 * Context: the legacy `ensureMicrophonePublished` helper enabled the mic, polled
 * for 150ms, and toggled the mic off/on if it had not yet observed a
 * publication. Against a publish slower than 150ms this produced the call
 * sequence [true, false, true] and left TWO audio publications for a single
 * speaker. Measured on 2026-08-01 against a 400ms publish: 2 publications.
 *
 * `publishLiveMicrophone` replaced the poll with LiveKit's own
 * `localTrackPublished` event plus an explicit reconciliation pass. This test
 * pins the guarantee that made the defect impossible, so a future change that
 * reintroduces poll-and-toggle publishing fails here.
 *
 * NOTE: this file lives in __proof__/ only because the authoring session lacked
 * permission to remove its scratch directory. It is safe to move to
 * src/live/__tests__/ alongside the rest of the livestream audio tests.
 */

import { publishLiveMicrophone } from "../src/live/liveAudioPublisher";

function slowPublishRoom(publishDelayMs: number) {
  const publications = new Map<string, any>();
  const listeners = new Map<string, Set<(payload: any) => void>>();
  let seq = 0;

  const add = () => {
    seq += 1;
    const sid = `audio-${seq}`;
    const publication = { kind: "audio", isSubscribed: true, track: { kind: "audio", sid } };
    publications.set(sid, publication);
    for (const listener of listeners.get("localTrackPublished") || []) listener(publication);
  };

  const room: any = {
    micCalls: [] as boolean[],
    on(event: string, listener: (payload: any) => void) {
      if (!listeners.has(event)) listeners.set(event, new Set());
      listeners.get(event)!.add(listener);
      return room;
    },
    off(event: string, listener: (payload: any) => void) {
      listeners.get(event)?.delete(listener);
      return room;
    },
    localParticipant: {
      audioTrackPublications: publications,
      async setMicrophoneEnabled(enabled: boolean) {
        room.micCalls.push(enabled);
        if (!enabled) {
          publications.clear();
          return;
        }
        setTimeout(add, publishDelayMs);
      },
      async unpublishTrack(track: any) {
        publications.delete(track.sid);
      }
    }
  };
  return room;
}

describe("historical defect: duplicate livestream audio track", () => {
  it("never toggles the microphone off during a slow publish", async () => {
    const room = slowPublishRoom(400);
    await publishLiveMicrophone(room);

    // The off/on toggle was the duplicate generator. It must never appear.
    expect(room.micCalls).toEqual([true]);
    expect(room.micCalls).not.toContain(false);
  });

  it("settles on exactly one audio publication even when publishing is slow", async () => {
    const room = slowPublishRoom(400);
    await publishLiveMicrophone(room);
    // Allow any straggler publish callbacks to land.
    await new Promise((resolve) => setTimeout(resolve, 600));

    expect(room.localParticipant.audioTrackPublications.size).toBe(1);
  });
});
