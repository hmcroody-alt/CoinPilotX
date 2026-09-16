/**
 * Where the attached music should be, given where the video is.
 *
 * `attachedMusicAudioPolicy` decides WHAT is audible -- which track, at what
 * volume, and whether the original camera audio is silenced. It has no notion of
 * time. This module is the other half: once both are playing, WHEN should the
 * music be, and what should be done when it drifts.
 *
 * WHY THIS IS A SEPARATE MODULE AND NOT A SECOND AUTHORITY
 *
 * The two answer different questions and change for different reasons. The
 * policy changes when the product changes its mind about audio priority; the
 * timeline changes when playback mechanics change. Folding the timeline into the
 * policy resolver would mean every surface that only needs "is the original
 * muted?" -- the composer previews, the carousel, the grid -- would have to
 * carry position state it has no use for. This module consumes an
 * `AttachedMusicPolicy`; it never re-derives one.
 *
 * THE VIDEO IS THE CLOCK
 *
 * Every decision here is computed from the video's position. That is deliberate
 * and it is what makes one function cover start, seek, pause, resume, buffer,
 * replay and loop without a branch for each: there is no separate "handle seek"
 * path that can disagree with the "handle resume" path, because both are just
 * "the video is at T, put the music where T says." The failure this avoids is
 * the one where each transition grew its own handler and the seek handler
 * forgot the start offset that the start handler applied.
 *
 * MUSIC DOES NOT RUN WHILE THE PICTURE IS STOPPED
 *
 * When the video stalls to rebuffer, the music is paused rather than left
 * running. Leaving it running is the more obvious implementation and it is
 * wrong twice: the track runs away from the picture so every stall permanently
 * increases drift, and a frozen frame over continuing music reads to the user as
 * a broken video rather than a slow one. Pausing makes a stall look like a
 * pause, which is what it is.
 */

import type { AttachedMusicPolicy } from "./attachedMusicAudioPolicy";

/**
 * How far the music may drift from the video before it is worth correcting.
 *
 * This is a deadband, not a target. Correcting drift means seeking the music,
 * and a seek is *itself* audible -- a click, or a fraction of a second of the
 * track repeating. Below this threshold the correction is more noticeable than
 * the error it fixes, so the right move is to leave it alone.
 *
 * 120ms is chosen for music-under-motion, not for lip-sync. Speech tolerates far
 * less (broadcast practice keeps audio within roughly -125ms to +45ms of video),
 * but a soundtrack has no articulation to disagree with, and the perceptual cue
 * is the beat landing against the cut. Tightening this to lip-sync numbers would
 * buy no audible improvement and would make the player seek on every ordinary
 * scheduling jitter.
 */
export const MUSIC_DRIFT_TOLERANCE_MS = 120;

/** What the video is doing right now, as reported by the player. */
export type VideoTimelineState = {
  isLoaded: boolean;
  positionMillis: number;
  isPlaying: boolean;
  /** The player is stalled fetching more data. */
  isBuffering: boolean;
};

/** What the attached music is doing right now. */
export type MusicTimelineState = {
  isLoaded: boolean;
  positionMillis: number;
  isPlaying: boolean;
  /** Track length, when known. Required to wrap a looping track correctly. */
  durationMillis?: number | null;
};

/**
 * The single instruction a surface must carry out this tick.
 *
 * `seekToMillis` is always absolute track position, never a delta, so a caller
 * that applies the same instruction twice cannot double-correct.
 */
export type MusicCorrection =
  | { action: "none" }
  | { action: "pause"; reason: "video_stalled" | "video_paused" }
  | { action: "play"; seekToMillis: number; driftMillis: number }
  | { action: "resync"; seekToMillis: number; driftMillis: number };

/**
 * Where the track should be when the video is at `videoPositionMillis`.
 *
 * The attached track starts at `musicStartMs` -- the in-track offset the creator
 * chose -- and advances with the video from there. A looping track wraps around
 * its own end rather than running past it; without the wrap, a 10-second track
 * under a 60-second reel would report five seconds of "drift" it could never
 * correct, and the player would seek on every tick forever.
 */
export function expectedMusicPosition(
  videoPositionMillis: number,
  policy: Pick<AttachedMusicPolicy, "musicStartMs" | "isLooping">,
  durationMillis?: number | null
): number {
  const video = Math.max(0, Number(videoPositionMillis) || 0);
  const start = Math.max(0, Number(policy.musicStartMs) || 0);
  const raw = start + video;
  const duration = Number(durationMillis) || 0;
  // Wrapping needs a real duration AND a loop. An unlooped track simply runs to
  // its end and stops; clamping it to the end would make a short track report a
  // growing drift and seek against its own tail once the video outlasts it.
  if (!policy.isLooping || duration <= 0) return raw;
  if (raw < duration) return raw;
  // Only the portion after the start offset repeats: a track told to begin at
  // 5s loops back to 5s, not to 0. Wrapping the whole value would silently
  // discard the creator's chosen in-point on the second pass.
  const loopSpan = duration - start;
  if (loopSpan <= 0) return start;
  return start + ((raw - start) % loopSpan);
}

/**
 * Signed distance between where the music is and where it should be.
 *
 * Positive means the music is AHEAD of the picture, which is the direction a
 * stall produces. Sign is preserved rather than reduced to a magnitude because
 * the two directions have different causes and a caller instrumenting drift
 * (§13) cannot diagnose a systematic lead from an absolute value.
 */
export function musicDriftMillis(
  video: VideoTimelineState,
  music: MusicTimelineState,
  policy: Pick<AttachedMusicPolicy, "musicStartMs" | "isLooping">
): number {
  const expected = expectedMusicPosition(video.positionMillis, policy, music.durationMillis);
  return Math.round((Number(music.positionMillis) || 0) - expected);
}

/**
 * The one instruction that keeps the track with the picture.
 *
 * Ordering matters and is not arbitrary. The stall check comes before the drift
 * check because a stalled video's drift grows without bound: correcting it would
 * seek the music repeatedly against a picture that is not moving, which is both
 * audible and pointless. Pause first, and the drift resolves itself when the
 * video resumes and the `play` branch re-aligns.
 */
export function planMusicCorrection(
  video: VideoTimelineState,
  music: MusicTimelineState,
  policy: AttachedMusicPolicy,
  toleranceMillis: number = MUSIC_DRIFT_TOLERANCE_MS
): MusicCorrection {
  // Nothing to align against. Acting on an unloaded player's position -- which
  // reads 0 -- would seek the music to the creator's start offset every tick
  // while the video is still opening.
  if (!policy.hasAttachedMusic || !video.isLoaded || !music.isLoaded) return { action: "none" };

  // §39. A stalled picture stops the track; the reason is preserved so a surface
  // can distinguish "the network is slow" from "the user pressed pause" without
  // re-deriving it from state this function already examined.
  if (video.isBuffering) {
    return music.isPlaying ? { action: "pause", reason: "video_stalled" } : { action: "none" };
  }
  if (!video.isPlaying) {
    return music.isPlaying ? { action: "pause", reason: "video_paused" } : { action: "none" };
  }

  const drift = musicDriftMillis(video, music, policy);
  const target = expectedMusicPosition(video.positionMillis, policy, music.durationMillis);

  // Starting or resuming: always land on the computed position rather than
  // wherever the track happened to be left. This is the branch that makes
  // "resume after a stall" and "resume after a user pause" identical, and it is
  // why there is no separate seek handler.
  if (!music.isPlaying) return { action: "play", seekToMillis: target, driftMillis: drift };

  if (Math.abs(drift) > toleranceMillis) {
    return { action: "resync", seekToMillis: target, driftMillis: drift };
  }
  return { action: "none" };
}
