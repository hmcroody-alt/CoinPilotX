import {
  isRealtimeAudioLeaseActive,
  type RealtimeAudioLease,
  type RealtimeAudioMode
} from "./realtimeAudioEngine";
import {
  publishRealtimeMicrophone,
  type RealtimePublicationContext,
  type RealtimePublishResult
} from "./realtimeMicrophonePublisher";
import {
  synchronizeRealtimeRemoteAudio,
  type RealtimeRemoteAudioResult
} from "./realtimeRemoteAudioController";

export type RealtimeAudioFeature = "audio_call" | "video_call" | "livestream";
export type RealtimeAudioPath = "shared_governed" | "legacy_fallback";

export type GovernedAudioContext = RealtimePublicationContext & {
  sessionId: string;
  roomType: RealtimeAudioFeature;
  participantRole: string;
};

const selectedPaths = new WeakMap<object, RealtimeAudioPath>();

function roomState(room: any): string {
  return String(room?.state || room?.connectionState || "").toLowerCase();
}

function expectedMode(feature: RealtimeAudioFeature, role: string): RealtimeAudioMode | null {
  if (feature === "audio_call") return "audio_call";
  if (feature === "video_call") return "video_call";
  if (role === "host") return "live_host";
  if (["approved_guest", "guest", "cohost"].includes(role)) return "live_guest";
  return null;
}

/**
 * Lock one room to one media implementation. A rollback requires a new room;
 * legacy and shared microphone paths can therefore never race in one session.
 */
export function claimRealtimeAudioPath(room: any, path: RealtimeAudioPath): void {
  if (!room || typeof room !== "object") throw new Error("Realtime audio room is unavailable.");
  const existing = selectedPaths.get(room as object);
  if (existing && existing !== path) {
    const error = new Error("Competing realtime audio paths are prohibited for one room.");
    Object.assign(error, { code: "REALTIME_AUDIO_PATH_CONFLICT", existingPath: existing, requestedPath: path });
    throw error;
  }
  selectedPaths.set(room as object, path);
}

export function releaseRealtimeAudioPath(room: any): void {
  if (room && typeof room === "object") selectedPaths.delete(room as object);
}

/**
 * Single governed entry point for microphone publication across calls, video,
 * Live hosts, and approved Live guests. It cannot mint permissions: the
 * adapter must supply the server-authorized role and current audio lease.
 */
export async function startPublishingAudio(options: {
  room: any;
  lease: RealtimeAudioLease | null | undefined;
  feature: RealtimeAudioFeature;
  role: string;
  canPublishMicrophone: boolean;
  context: GovernedAudioContext;
  timeoutMs?: number;
}): Promise<RealtimePublishResult> {
  claimRealtimeAudioPath(options.room, "shared_governed");
  const state = roomState(options.room);
  if (state && state !== "connected") {
    const error = new Error("Realtime audio publication requires a connected room.");
    Object.assign(error, { code: "REALTIME_AUDIO_ROOM_NOT_CONNECTED", state });
    throw error;
  }
  if (!options.canPublishMicrophone) {
    const error = new Error("This participant is not authorized to publish microphone audio.");
    Object.assign(error, { code: "REALTIME_AUDIO_PUBLISH_FORBIDDEN" });
    throw error;
  }
  const requiredMode = expectedMode(options.feature, options.role);
  if (!requiredMode || options.lease?.mode !== requiredMode || !isRealtimeAudioLeaseActive(options.lease)) {
    const error = new Error("The active audio owner does not authorize this publication.");
    Object.assign(error, { code: "REALTIME_AUDIO_STALE_OR_WRONG_OWNER", requiredMode });
    throw error;
  }
  return publishRealtimeMicrophone(options.room, {
    timeoutMs: options.timeoutMs,
    context: { ...options.context, canPublishMicrophone: true }
  });
}

/**
 * Canonical local-media transition for every microphone publisher.
 *
 * Audio calls stop after the first publication. Video calls and Live publishers
 * add their feature-owned camera, then reassert the exact same microphone
 * publication and republish only if the camera transition removed it. Keeping
 * this ordering here prevents feature adapters from inventing subtly different
 * microphone/camera races.
 */
export async function initializeRealtimePublisherMedia(options: {
  publishMicrophone: () => Promise<number>;
  enableCamera?: () => Promise<void>;
  reassertMicrophone?: () => Promise<number>;
}): Promise<number> {
  let audioTrackCount = await options.publishMicrophone();
  if (!options.enableCamera) return audioTrackCount;

  await options.enableCamera();
  if (options.reassertMicrophone) {
    audioTrackCount = await options.reassertMicrophone();
  }
  if (audioTrackCount <= 0) {
    audioTrackCount = await options.publishMicrophone();
  }
  return audioTrackCount;
}

/** Shared multi-speaker receive path. It never claims microphone ownership. */
export async function startReceivingAudio(options: {
  room: any;
  enabled: boolean;
  canSubscribe: boolean;
  context: GovernedAudioContext;
}): Promise<RealtimeRemoteAudioResult> {
  return synchronizeRealtimeRemoteAudio(options.room, options.enabled, {
    ...options.context,
    canSubscribe: options.canSubscribe
  });
}
