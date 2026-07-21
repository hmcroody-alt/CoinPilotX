import type { PulseReelAudio } from "../api/reels";
import type { PulseStatusMusic } from "../api/status";

/**
 * Single source of truth for the product rule "attached music takes exclusive
 * audio priority": whenever a post has an attached music track, the original
 * media audio must be muted and only the music may be audible. Every composer
 * preview and every playback surface (Reels, carousels, statuses) derives its
 * mute/volume state from this resolver so the rule cannot drift per screen.
 */

export const ATTACHED_MUSIC_EXCLUSIVE = "ATTACHED_MUSIC_EXCLUSIVE" as const;
export const ORIGINAL_AUDIO = "ORIGINAL_AUDIO" as const;
export type AudioMode = typeof ATTACHED_MUSIC_EXCLUSIVE | typeof ORIGINAL_AUDIO;

const DEFAULT_MUSIC_VOLUME = 1;

/** Normalized, surface-agnostic description of a post's audio metadata. */
export type AttachedMusicSource = {
  /** URL of the attached music track, if one is attached. */
  musicUrl?: string | null;
  /** Where the music starts within the track, in seconds. */
  startSeconds?: number | null;
  /** User-selected music level, 0..1. */
  volume?: number | null;
  /** Whether the music track should loop under the visual content. */
  isLooping?: boolean;
};

export type AttachedMusicPolicy = {
  mode: AudioMode;
  hasAttachedMusic: boolean;
  /** True whenever music is attached — the original source must be silent. */
  muteOriginalAudio: boolean;
  musicUrl?: string;
  musicVolume: number;
  musicStartMs: number;
  isLooping: boolean;
};

function clampVolume(value: unknown): number {
  if (value === null || value === undefined || value === "") return DEFAULT_MUSIC_VOLUME;
  const numeric = Number(value);
  if (!Number.isFinite(numeric)) return DEFAULT_MUSIC_VOLUME;
  return Math.max(0, Math.min(1, numeric));
}

/**
 * Resolves the audio policy for a piece of content. When music is attached the
 * mode is exclusive and the original audio is always muted; the caller must
 * never fall back to source audio just because the track is still loading or
 * has failed (see failure handling at the call sites).
 */
export function resolveAttachedMusicPolicy(source?: AttachedMusicSource | null): AttachedMusicPolicy {
  const musicUrl = String(source?.musicUrl || "").trim();
  if (!musicUrl) {
    return {
      mode: ORIGINAL_AUDIO,
      hasAttachedMusic: false,
      muteOriginalAudio: false,
      musicUrl: undefined,
      musicVolume: DEFAULT_MUSIC_VOLUME,
      musicStartMs: 0,
      isLooping: false
    };
  }
  return {
    mode: ATTACHED_MUSIC_EXCLUSIVE,
    hasAttachedMusic: true,
    muteOriginalAudio: true,
    musicUrl,
    musicVolume: clampVolume(source?.volume),
    musicStartMs: Math.max(0, Math.round(Number(source?.startSeconds || 0) * 1000)),
    isLooping: source?.isLooping !== false
  };
}

export function reelAudioToMusicSource(audio?: PulseReelAudio | null): AttachedMusicSource {
  return {
    musicUrl: audio?.attached_audio_url || "",
    startSeconds: audio?.audio_start_time ?? 0,
    volume: audio?.audio_volume,
    isLooping: true
  };
}

export function statusMusicToMusicSource(music?: PulseStatusMusic | null): AttachedMusicSource {
  return {
    musicUrl: music?.attached_audio_url || music?.audio_url || "",
    startSeconds: 0,
    volume: undefined,
    isLooping: true
  };
}

/** Convenience resolvers so call sites never re-implement the adapter wiring. */
export function resolveReelAudioPolicy(audio?: PulseReelAudio | null): AttachedMusicPolicy {
  return resolveAttachedMusicPolicy(reelAudioToMusicSource(audio));
}

export function resolveStatusMusicPolicy(music?: PulseStatusMusic | null): AttachedMusicPolicy {
  return resolveAttachedMusicPolicy(statusMusicToMusicSource(music));
}
