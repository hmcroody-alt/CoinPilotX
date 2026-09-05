import { audioPublications, publicationHasTrack } from "./realtimeAudioEngine";
import { emitRealtimeAudioEvent } from "./realtimeAudioTelemetry";

export type RealtimeRemoteAudioContext = {
  sessionId?: string;
  correlationId?: string;
  roomType?: string;
  participantRole?: string;
  canSubscribe?: boolean;
};

export type RealtimeRemoteAudioResult = {
  discovered: number;
  subscriptionRequests: number;
  subscribed: number;
  enabled: number;
};

const announcedPublications = new WeakMap<object, Set<string>>();

function publicationKey(publication: any, participant: any, index: number): string {
  return String(
    publication?.trackSid ||
      publication?.sid ||
      publication?.track?.sid ||
      `${participant?.identity || "participant"}:${index}`
  );
}

/** Count every currently subscribed remote audio publication across all speakers. */
export function countSubscribedRemoteAudioTracks(room: any): number {
  return Array.from(room?.remoteParticipants?.values?.() || []).reduce(
    (total: number, participant: any) =>
      total + audioPublications(participant).filter(publicationHasTrack).length,
    0
  );
}

/**
 * Canonical remote-audio subscription and playback controller.
 *
 * Calls generally expose one remote publication; Live can expose a host plus
 * several approved guests. The controller intentionally walks all speakers,
 * deduplicates subscription telemetry by publication SID, and never creates a
 * microphone track. Authorization remains server-owned and must be passed in.
 */
export async function synchronizeRealtimeRemoteAudio(
  room: any,
  enabled: boolean,
  context: RealtimeRemoteAudioContext = {}
): Promise<RealtimeRemoteAudioResult> {
  if (context.canSubscribe === false) {
    return { discovered: 0, subscriptionRequests: 0, subscribed: 0, enabled: 0 };
  }

  const roomObject = room && typeof room === "object" ? room as object : null;
  const announced = roomObject ? announcedPublications.get(roomObject) || new Set<string>() : new Set<string>();
  const tasks: Promise<unknown>[] = [];
  let discovered = 0;
  let subscriptionRequests = 0;
  let subscribed = 0;
  let enabledCount = 0;

  for (const remote of Array.from(room?.remoteParticipants?.values?.() || []) as any[]) {
    const publications = audioPublications(remote);
    for (let index = 0; index < publications.length; index += 1) {
      const publication = publications[index];
      discovered += 1;
      const key = publicationKey(publication, remote, index);

      if (publication?.isSubscribed === false && typeof publication?.setSubscribed === "function") {
        tasks.push(Promise.resolve(publication.setSubscribed(true)));
        subscriptionRequests += 1;
      }

      const track = publication?.track;
      if (!track || publication?.isSubscribed === false) continue;
      subscribed += 1;
      if (!announced.has(key)) {
        announced.add(key);
        emitRealtimeAudioEvent({
          name: "remote_audio_subscribed",
          ...context,
          outcome: key,
          audioTrackCount: subscribed
        });
      }
      if (typeof track.setEnabled === "function") {
        tasks.push(Promise.resolve(track.setEnabled(enabled)));
        enabledCount += 1;
      } else if (track.mediaStreamTrack) {
        track.mediaStreamTrack.enabled = enabled;
        enabledCount += 1;
      }
    }
  }

  await Promise.all(tasks).catch(() => undefined);
  if (roomObject) announcedPublications.set(roomObject, announced);
  return { discovered, subscriptionRequests, subscribed, enabled: enabledCount };
}

/** Compatibility return shape for existing call and Live feature adapters. */
export async function applyRemoteAudioEnabled(room: any, enabled: boolean): Promise<number> {
  return (await synchronizeRealtimeRemoteAudio(room, enabled)).enabled;
}

export function resetRealtimeRemoteAudioTracking(room: any): void {
  if (room && typeof room === "object") announcedPublications.delete(room as object);
}
