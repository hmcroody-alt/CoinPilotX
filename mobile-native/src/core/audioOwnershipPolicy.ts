import type { RealtimeAudioMode } from "./realtimeAudioEngine";

/**
 * PulseSoc audio ownership policy.
 *
 * The device has exactly one AVAudioSession, so exactly one PulseSoc feature may
 * own it at a time. Before this module the owner was a single mutable slot with
 * last-writer-wins semantics: any feature could silently displace any other, the
 * displaced feature was never told it had lost the session, and the displacing
 * feature's release would stop a session another feature believed it still held.
 *
 * This module makes the arbitration explicit and pure so it can be unit tested
 * without a device. It answers one question: given the current owner, may this
 * new owner take the session, and what happens to the incumbent?
 *
 * Priority rationale (higher wins):
 *   - Calls outrank everything. A 1:1 conversation is synchronous and the OS
 *     will interrupt for CallKit anyway, so PulseSoc must not fight it.
 *   - Voice message recording outranks livestreams: it is a deliberate,
 *     short-lived capture the user just initiated by tapping record.
 *   - Live host/guest (publishing) outranks live viewer (listening).
 *   - Media playback yields to everything realtime.
 */

export const AUDIO_OWNER_PRIORITY: Record<RealtimeAudioMode, number> = {
  audio_call: 100,
  video_call: 100,
  voice_message: 90,
  live_host: 80,
  live_guest: 80,
  live_viewer: 40,
  music_playback: 10,
  none: 0
};

export type OwnershipDecision =
  | { outcome: "granted"; displaces: null }
  | { outcome: "reacquired"; displaces: null }
  | { outcome: "displaced"; displaces: string }
  | { outcome: "denied"; blockedBy: string; blockedByMode: RealtimeAudioMode };

export type OwnershipCandidate = {
  ownerId: string;
  mode: RealtimeAudioMode;
};

export function audioOwnerPriority(mode: RealtimeAudioMode): number {
  return AUDIO_OWNER_PRIORITY[mode] ?? 0;
}

export function isLivestreamOwner(mode: RealtimeAudioMode): boolean {
  return mode === "live_host" || mode === "live_guest" || mode === "live_viewer";
}

export function isCallOwner(mode: RealtimeAudioMode): boolean {
  return mode === "audio_call" || mode === "video_call";
}

/**
 * Decide whether `requested` may take the audio session from `current`.
 *
 * - No incumbent            -> granted
 * - Same ownerId            -> reacquired (idempotent; must not restart the session)
 * - Strictly lower priority -> denied (this is the fix: a Live can no longer
 *                              steal the session out from under an active call)
 * - Otherwise               -> displaced (incumbent must be told to tear down)
 *
 * Equal priority displaces rather than denies, so a second call replacing a
 * first, or a host promoting to guest, still works.
 */
export function resolveOwnershipDecision(
  current: OwnershipCandidate | null,
  requested: OwnershipCandidate
): OwnershipDecision {
  if (!current) return { outcome: "granted", displaces: null };
  if (current.ownerId === requested.ownerId) return { outcome: "reacquired", displaces: null };

  const currentPriority = audioOwnerPriority(current.mode);
  const requestedPriority = audioOwnerPriority(requested.mode);

  if (requestedPriority < currentPriority) {
    return { outcome: "denied", blockedBy: current.ownerId, blockedByMode: current.mode };
  }
  return { outcome: "displaced", displaces: current.ownerId };
}

/**
 * Human-readable reason surfaced to the user when a claim is denied. Kept free
 * of identifiers so it is safe to render in UI and safe to log.
 */
export function ownershipDenialMessage(blockedByMode: RealtimeAudioMode): string {
  if (isCallOwner(blockedByMode)) return "Audio is in use by an active call. End the call to continue.";
  if (blockedByMode === "voice_message") return "Audio is in use by a voice message recording.";
  if (isLivestreamOwner(blockedByMode)) return "Audio is in use by an active broadcast.";
  return "Audio is in use by another PulseSoc feature.";
}

export class RealtimeAudioOwnershipError extends Error {
  readonly code = "AUDIO_SESSION_BUSY";
  readonly blockedBy: string;
  readonly blockedByMode: RealtimeAudioMode;

  constructor(blockedBy: string, blockedByMode: RealtimeAudioMode) {
    super(ownershipDenialMessage(blockedByMode));
    this.name = "RealtimeAudioOwnershipError";
    this.blockedBy = blockedBy;
    this.blockedByMode = blockedByMode;
  }
}
