import { audioPublications, publicationHasTrack } from "../core/realtimeAudioEngine";

/**
 * Deterministic livestream microphone publishing.
 *
 * The previous helper (`ensureMicrophonePublished`) enabled the mic, slept
 * 150ms, and if it did not yet see a publication it toggled the mic off and on
 * again. On a slow publish the first track landed *after* the toggle, producing
 * two live audio publications for one speaker - the duplicate-track failure.
 * The broadcast hook then called that helper twice per connect, so a single
 * "go live" could run four enable/disable cycles.
 *
 * This module replaces polling with the event LiveKit already emits, adds a
 * per-room mutex so concurrent callers cannot race, and actively reconciles the
 * publication set down to exactly one audio track.
 */

export const LIVE_AUDIO_PUBLISH_TIMEOUT_MS = 8000;

export type PublishOutcome = "already_published" | "published" | "timeout" | "no_participant";

export type PublishResult = {
  outcome: PublishOutcome;
  audioTrackCount: number;
  duplicatesRemoved: number;
  durationMs: number;
};

/** LiveKit's RoomEvent.LocalTrackPublished string value. */
const LOCAL_TRACK_PUBLISHED = "localTrackPublished";

/**
 * One in-flight publish per room. Without this, the two back-to-back publish
 * calls in the connect path each start their own enable cycle.
 */
const inFlight = new WeakMap<object, Promise<PublishResult>>();

function localAudioPublications(room: any): any[] {
  return audioPublications(room?.localParticipant).filter(publicationHasTrack);
}

/**
 * Reduce the local participant to a single published audio track. Extra
 * publications are unpublished oldest-last so the earliest healthy track wins.
 */
async function reconcileDuplicates(room: any): Promise<number> {
  const publications = localAudioPublications(room);
  if (publications.length <= 1) return 0;

  const extras = publications.slice(1);
  let removed = 0;
  for (const publication of extras) {
    const track = publication?.track;
    if (!track) continue;
    try {
      await room.localParticipant.unpublishTrack(track);
      removed += 1;
    } catch {
      // A failed cleanup is reported via the returned count rather than thrown:
      // one extra track is a quality problem, not a reason to fail the stream.
    }
  }
  return removed;
}

function waitForLocalAudioPublication(room: any, timeoutMs: number): Promise<boolean> {
  return new Promise((resolve) => {
    let settled = false;
    const finish = (value: boolean) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try {
        room.off?.(LOCAL_TRACK_PUBLISHED, onPublished);
      } catch {
        // Detaching a listener from an already-torn-down room is not fatal.
      }
      resolve(value);
    };

    const onPublished = (publication: any) => {
      const kind = String(publication?.kind || publication?.track?.kind || "");
      if (kind === "audio") finish(true);
    };

    const timer = setTimeout(() => finish(false), timeoutMs);
    try {
      room.on?.(LOCAL_TRACK_PUBLISHED, onPublished);
    } catch {
      finish(false);
    }
  });
}

async function runPublish(room: any, timeoutMs: number): Promise<PublishResult> {
  const startedAt = Date.now();
  const localParticipant = room?.localParticipant;
  if (!localParticipant) {
    return { outcome: "no_participant", audioTrackCount: 0, duplicatesRemoved: 0, durationMs: 0 };
  }

  // Already publishing: reconcile and return without touching the mic. This is
  // what makes repeat calls (camera switch, reconnect, camera toggle) free.
  if (localAudioPublications(room).length > 0) {
    const duplicatesRemoved = await reconcileDuplicates(room);
    return {
      outcome: "already_published",
      audioTrackCount: localAudioPublications(room).length,
      duplicatesRemoved,
      durationMs: Date.now() - startedAt
    };
  }

  // Subscribe to the publication event BEFORE enabling, so a fast publish
  // cannot land in the gap between the call and the listener attaching.
  const published = waitForLocalAudioPublication(room, timeoutMs);
  await localParticipant.setMicrophoneEnabled(true);
  await published;

  const duplicatesRemoved = await reconcileDuplicates(room);
  const audioTrackCount = localAudioPublications(room).length;

  return {
    outcome: audioTrackCount > 0 ? "published" : "timeout",
    audioTrackCount,
    duplicatesRemoved,
    durationMs: Date.now() - startedAt
  };
}

/**
 * Publish the local microphone exactly once for a livestream room.
 *
 * Safe to call repeatedly and concurrently: overlapping callers share one
 * in-flight operation, and a room that is already publishing is left alone.
 */
export async function publishLiveMicrophone(
  room: any,
  options: { timeoutMs?: number } = {}
): Promise<PublishResult> {
  if (!room) return { outcome: "no_participant", audioTrackCount: 0, duplicatesRemoved: 0, durationMs: 0 };

  const existing = inFlight.get(room);
  if (existing) return existing;

  const pending = runPublish(room, options.timeoutMs ?? LIVE_AUDIO_PUBLISH_TIMEOUT_MS).finally(() => {
    inFlight.delete(room);
  });
  inFlight.set(room, pending);
  return pending;
}

/** Count of healthy published local audio tracks. Used by callers for gating. */
export function publishedLiveAudioTrackCount(room: any): number {
  return localAudioPublications(room).length;
}
