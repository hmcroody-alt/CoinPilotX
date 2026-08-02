import { audioPublications, publicationHasTrack } from "./realtimeAudioEngine";
import { emitRealtimeAudioEvent } from "./realtimeAudioTelemetry";
import { reportRealtimeAudioInvariant } from "./realtimeAudioInvariants";

export const REALTIME_AUDIO_PUBLISH_TIMEOUT_MS = 8000;

export type RealtimePublishOutcome = "already_published" | "published" | "timeout" | "no_participant" | "forbidden";

export type RealtimePublishResult = {
  outcome: RealtimePublishOutcome;
  audioTrackCount: number;
  duplicatesRemoved: number;
  durationMs: number;
};

export type RealtimePublicationContext = {
  sessionId?: string;
  correlationId?: string;
  roomType?: string;
  participantRole?: string;
  canPublishMicrophone?: boolean;
};

const LOCAL_TRACK_PUBLISHED = "localTrackPublished";
const inFlight = new WeakMap<object, Promise<RealtimePublishResult>>();

function localAudioPublications(room: any): any[] {
  return audioPublications(room?.localParticipant).filter(publicationHasTrack);
}

async function reconcileDuplicates(room: any): Promise<number> {
  const extras = localAudioPublications(room).slice(1);
  let removed = 0;
  for (const publication of extras) {
    const track = publication?.track;
    if (!track) continue;
    try {
      await room.localParticipant.unpublishTrack(track);
      removed += 1;
    } catch {
      // The result exposes incomplete reconciliation without dropping the room.
    }
  }
  if (removed > 0) {
    // The reconciliation already happened; this records that it was needed.
    // A duplicate track reaching production is heard as an echo, and it is the
    // one invariant whose repair is silent by design.
    reportRealtimeAudioInvariant({
      id: "duplicate_microphone_tracks",
      action: "reconciled",
      detail: "unpublished_extra"
    });
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
      try { room.off?.(LOCAL_TRACK_PUBLISHED, onPublished); } catch { /* room already closed */ }
      resolve(value);
    };
    const onPublished = (publication: any) => {
      if (String(publication?.kind || publication?.track?.kind || "") === "audio") finish(true);
    };
    const timer = setTimeout(() => finish(false), timeoutMs);
    try { room.on?.(LOCAL_TRACK_PUBLISHED, onPublished); } catch { finish(false); }
  });
}

async function runPublish(room: any, timeoutMs: number, context: RealtimePublicationContext): Promise<RealtimePublishResult> {
  const startedAt = Date.now();
  const localParticipant = room?.localParticipant;
  if (!localParticipant) return { outcome: "no_participant", audioTrackCount: 0, duplicatesRemoved: 0, durationMs: 0 };
  if (context.canPublishMicrophone === false) {
    // Refusal is unchanged. It is recorded because a viewer reaching this line
    // means some surface believes it may publish - either a stale role after a
    // guest was removed, or a code path that skipped the gate. Both are
    // invisible in a build where the refusal works.
    reportRealtimeAudioInvariant({
      id: "viewer_publication_attempt",
      action: "rejected",
      detail: "publish_denied",
      sessionId: context.sessionId,
      correlationId: context.correlationId,
      roomType: context.roomType,
      participantRole: context.participantRole
    });
    return { outcome: "forbidden", audioTrackCount: localAudioPublications(room).length, duplicatesRemoved: 0, durationMs: 0 };
  }
  if (localAudioPublications(room).length > 0) {
    const duplicatesRemoved = await reconcileDuplicates(room);
    return {
      outcome: "already_published",
      audioTrackCount: localAudioPublications(room).length,
      duplicatesRemoved,
      durationMs: Date.now() - startedAt
    };
  }

  emitRealtimeAudioEvent({ name: "microphone_publish_started", ...context });
  const published = waitForLocalAudioPublication(room, timeoutMs);
  await localParticipant.setMicrophoneEnabled(true);
  await published;
  const duplicatesRemoved = await reconcileDuplicates(room);
  const audioTrackCount = localAudioPublications(room).length;
  const result: RealtimePublishResult = {
    outcome: audioTrackCount > 0 ? "published" : "timeout",
    audioTrackCount,
    duplicatesRemoved,
    durationMs: Date.now() - startedAt
  };
  emitRealtimeAudioEvent({
    name: audioTrackCount > 0 ? "microphone_published" : "microphone_publish_failed",
    ...context,
    outcome: result.outcome,
    failureCategory: audioTrackCount > 0 ? undefined : "publication_timeout",
    durationMs: result.durationMs,
    audioTrackCount,
    duplicatesRemoved
  });
  return result;
}

export async function publishRealtimeMicrophone(
  room: any,
  options: { timeoutMs?: number; context?: RealtimePublicationContext } = {}
): Promise<RealtimePublishResult> {
  if (!room) return { outcome: "no_participant", audioTrackCount: 0, duplicatesRemoved: 0, durationMs: 0 };
  const existing = inFlight.get(room);
  if (existing) return existing;
  const pending = runPublish(room, options.timeoutMs ?? REALTIME_AUDIO_PUBLISH_TIMEOUT_MS, options.context || {}).finally(() => {
    inFlight.delete(room);
  });
  inFlight.set(room, pending);
  return pending;
}

export function publishedRealtimeAudioTrackCount(room: any): number {
  return localAudioPublications(room).length;
}

export async function setRealtimeMicrophoneEnabled(room: any, enabled: boolean): Promise<number> {
  const participant = room?.localParticipant;
  if (!participant) return 0;
  if (!enabled) {
    await participant.setMicrophoneEnabled(false);
    return localAudioPublications(room).length;
  }
  const result = await publishRealtimeMicrophone(room);
  return result.audioTrackCount;
}
