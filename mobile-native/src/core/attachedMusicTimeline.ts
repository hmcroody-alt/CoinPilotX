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
 * How often both players are asked to report their position.
 *
 * Exported because it is not a tuning knob, it is the RESOLUTION OF EVERY
 * MEASUREMENT IN THIS FILE, and the deadband below is derived from it. The card
 * configures both the video and the sound with this same number; letting the two
 * report on different grids, or letting this drift away from the tolerance, is
 * what produced the defect described under `MUSIC_DRIFT_TOLERANCE_MS`.
 */
export const MUSIC_STATUS_INTERVAL_MS = 250;

/**
 * How far the music may drift from the video before it is worth correcting.
 *
 * This is a deadband, not a target. Correcting drift means seeking the music,
 * and a seek is *itself* audible -- a click, or a fraction of a second of the
 * track repeating. Below this threshold the correction is more noticeable than
 * the error it fixes, so the right move is to leave it alone.
 *
 * IT MUST EXCEED ONE REPORTING INTERVAL, AND THAT IS NOT A STYLE PREFERENCE.
 *
 * This was 120ms, chosen on perceptual grounds for music-under-motion. The
 * reasoning was sound and the number was unusable, because it is finer than the
 * measurement it thresholds. Both players report `positionMillis` on a
 * MUSIC_STATUS_INTERVAL_MS grid, so the two readings are never taken at the same
 * instant and the difference between them is dominated by which side of the grid
 * each callback happened to land on.
 *
 * MEASURED on a physical iPhone 16 Pro, 796 ticks over 195 seconds:
 *
 *   - `videoPosition - musicPosition` took exactly two values, 0 and 250. It
 *     NEVER exceeded one step. The track did not depart from the picture once.
 *   - The computed drift was identically `age - 250` on all 365 of the 369
 *     phase-250 ticks, and identically `age` on 338 of the 341 phase-0 ticks --
 *     where `age` is how long before the video's callback the music's callback
 *     happened to land. The number was a property of callback scheduling, not of
 *     the audio.
 *   - Against a 120ms deadband that produced 126 seeks in 195 seconds. Every one
 *     was real by the loop's own arithmetic and every one was chasing the grid.
 *
 * So the floor on what can be resolved here is one interval, and a threshold
 * below the floor does not buy tighter sync -- it buys corrections. Nothing
 * genuine lives in the band between the old number and this one either: a stall
 * is handled by pausing (§39), a resume and a start by the `play` branch, and a
 * scrub or a loop wrap is seconds out, not milliseconds.
 *
 * The margin over one interval absorbs the few ticks where the two callbacks
 * straddled a step and read 251 or 249 rather than 250.
 *
 * If sync tighter than this is ever genuinely required, the lever is a finer
 * reporting grid -- lower MUSIC_STATUS_INTERVAL_MS, and pay for it in callback
 * volume across every mounted card -- not a tighter threshold on a measurement
 * that cannot see the difference.
 */
export const MUSIC_DRIFT_TOLERANCE_MS = MUSIC_STATUS_INTERVAL_MS + 150;

/** What the video is doing right now, as reported by the player. */
export type VideoTimelineState = {
  isLoaded: boolean;
  positionMillis: number;
  isPlaying: boolean;
  /** The player is stalled fetching more data. */
  isBuffering: boolean;
};

/**
 * What the attached music is doing right now.
 *
 * `positionMillis` is where the track was when the player last reported, which
 * is NOT the same instant the video reported. See `sampledAtMillis`.
 */
export type MusicTimelineState = {
  isLoaded: boolean;
  positionMillis: number;
  isPlaying: boolean;
  /** Track length, when known. Required to wrap a looping track correctly. */
  durationMillis?: number | null;
  /**
   * Wall-clock time at which the player reported `positionMillis`.
   *
   * The two clocks are sampled by different callbacks at different rates: the
   * video's status arrives on its own interval and we act on it immediately,
   * while the music's position is whatever its last callback left behind. Both
   * numbers are honest; subtracting them without accounting for the gap is not.
   *
   * Leaving this out manufactures drift exactly equal to the sampling gap, and
   * because that gap is larger than MUSIC_DRIFT_TOLERANCE_MS the deadband can
   * never absorb it: every tick reads as drift, every tick seeks, and the seek
   * republishes a position one tick old, which produces the same reading again.
   * Measured on device, that pinned drift at exactly one video tick and seeked
   * the track four times a second -- continuously audible, and self-sustaining.
   *
   * Optional because a caller that cannot timestamp its samples is better off
   * comparing raw positions than inventing a timestamp; projection is skipped
   * when this or `nowMillis` is absent.
   */
  sampledAtMillis?: number | null;
};

/**
 * Where the track actually is *now*, given a reading taken `now - sampledAt`
 * milliseconds ago.
 *
 * A playing track advances in real time, so the correction for a stale reading
 * is the elapsed wall time. A paused one has not moved, which is why the
 * projection is gated on `isPlaying` rather than applied unconditionally --
 * projecting a paused track would invent forward motion and seek against it.
 *
 * The projection is clamped to non-negative elapsed time so a clock that jumps
 * backwards (NTP, or a caller passing a stale `nowMillis`) cannot rewind the
 * track's estimated position and trigger a correction in the wrong direction.
 */
export function projectedMusicPosition(music: MusicTimelineState, nowMillis?: number | null): number {
  const position = Math.max(0, Number(music.positionMillis) || 0);
  const sampledAt = Number(music.sampledAtMillis) || 0;
  const now = Number(nowMillis) || 0;
  if (!music.isPlaying || !sampledAt || !now) return position;
  return position + Math.max(0, now - sampledAt);
}

/**
 * The single instruction a surface must carry out this tick.
 *
 * `seekToMillis` is always absolute track position, never a delta, so a caller
 * that applies the same instruction twice cannot double-correct.
 */
export type MusicCorrection =
  /**
   * Nothing to do this tick.
   *
   * `driftMillis` is present only when this tick actually produced a comparable
   * reading -- both players loaded, the video playing and not stalled. That is
   * what a caller feeds back as `previousDriftMillis` on the next tick, and
   * carrying it here rather than letting the caller re-derive it keeps one
   * authority on when a reading is meaningful. Its ABSENCE is equally load
   * bearing: a tick that could not measure clears the caller's history, so the
   * first reading after a pause or a stall has nothing to confirm against and
   * cannot, on its own, seek.
   */
  | { action: "none"; driftMillis?: number }
  | { action: "pause"; reason: "video_stalled" | "video_paused" }
  /**
   * Start the track, because the video is playing and the track is not.
   *
   * `seekToMillis` is null when the track is ALREADY within the deadband of
   * where it belongs: start it where it stands rather than moving it first.
   *
   * MEASURED ON DEVICE, and not a micro-optimisation. `isPlaying` does not turn
   * true the instant a start is issued -- the player has to decode before it
   * reports playing -- so this branch runs again on the next tick, and used to
   * re-issue the seek. A capture caught the consequence: 30 consecutive `play`
   * ticks over 7.2 SECONDS in which the music position on every tick was
   * exactly the seek issued on the tick before, never once the track's own
   * clock. Each seek restarted the start-up it was waiting to complete, so the
   * correction sustained the silence it was trying to end. Two other rounds on
   * the same reel started on a single `play`, which is what makes this a
   * livelock rather than a constant cost: it bites when the decode is slow.
   *
   * 29 of those 30 seeks were moving a track that was already inside the
   * deadband. So the rule is the same one every other correction obeys -- do
   * not move the track for a difference this small -- and the `play` branch
   * being exempt from it was the defect.
   */
  | { action: "play"; seekToMillis: number | null; driftMillis: number }
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
  policy: Pick<AttachedMusicPolicy, "musicStartMs" | "isLooping">,
  nowMillis?: number | null
): number {
  const expected = expectedMusicPosition(video.positionMillis, policy, music.durationMillis);
  // Both sides of this subtraction have to refer to the same instant. The video
  // side does by construction -- we are called from its status callback -- so
  // it is the music reading that has to be carried forward to meet it.
  return Math.round(projectedMusicPosition(music, nowMillis) - expected);
}

/**
 * Whether a drift reading is worth acting on, given the one before it.
 *
 * MEASURED ON DEVICE, and the reason this rule exists at all. Both players
 * report position quantised to their update interval, so `positionMillis` is a
 * step function, not a continuous reading. Projecting a step function forward
 * from the instant it was *received* therefore carries an error of up to one
 * whole step, and on an iPhone 16 Pro that step is 250ms against a 120ms
 * deadband. A 195-second capture showed `videoPosition - musicPosition` sitting
 * at exactly 250 on 369 ticks and exactly 0 on 335 -- the two quantisation
 * phases -- which swung the computed drift between +63ms and -186ms around a
 * true value near -60ms.
 *
 * The result was 190 corrections in 195 seconds, and EVERY ONE of them was a
 * run of length one: never twice in a row, because the next tick landed in the
 * other phase and read as aligned. That is the signature of noise rather than
 * drift, and it is what makes persistence the right discriminator: a real
 * divergence -- a stall, a loop, a scrub -- does not alternate. It is still
 * there on the following tick, and on the one after that.
 *
 * So a correction requires two consecutive readings that are both past the
 * deadband AND on the same side of it. Against that capture this rule issues
 * zero of the 190 seeks, while leaving every genuine transition untouched: the
 * start and resume cases are handled by the `play` branch, which decides
 * whether to MOVE the track on the same deadband but does not wait for a second
 * reading to confirm it -- a start is not noise, and delaying it would be
 * audible as a late entry rather than as a seek.
 *
 * The cost is one tick of delay -- 250ms -- before a genuine drift is repaired.
 * That is the right trade: the correction is an audible seek, so paying a tick
 * to be sure beats seeking four times a second at a track that was never out.
 */
function driftIsWorthCorrecting(
  drift: number,
  previousDrift: number | null | undefined,
  toleranceMillis: number
): boolean {
  if (Math.abs(drift) <= toleranceMillis) return false;
  const previous = Number(previousDrift);
  // No previous reading means this is the first tick of a correction loop that
  // has nothing to confirm against. Waiting one tick is the whole point.
  if (!Number.isFinite(previous)) return false;
  if (Math.abs(previous) <= toleranceMillis) return false;
  // Same side. A +150 followed by a -150 is the quantisation swing, not a track
  // that is 150ms out; acting on it would seek in a direction the previous tick
  // disagreed with.
  return drift > 0 === previous > 0;
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
  toleranceMillis: number = MUSIC_DRIFT_TOLERANCE_MS,
  /**
   * The instant the video reading was taken. Passed rather than read from
   * `Date.now()` so this stays pure and a test can state the sampling gap it
   * means to exercise instead of racing the real clock.
   */
  nowMillis?: number | null,
  /**
   * The drift this same loop computed on the previous tick, or null on the
   * first. Held by the caller rather than here so this function stays a pure
   * mapping from state to instruction -- the alternative, a module-level
   * variable, would make two Reels on screen share one history and correct
   * each other's tracks.
   */
  previousDriftMillis?: number | null
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

  const drift = musicDriftMillis(video, music, policy, nowMillis);
  const target = expectedMusicPosition(video.positionMillis, policy, music.durationMillis);

  // Starting or resuming. Land on the computed position when the track is
  // genuinely somewhere else -- this is the branch that makes "resume after a
  // stall" and "resume after a user pause" identical, and it is why there is no
  // separate seek handler. But a track already within the deadband is started
  // where it stands: see the `play` member of MusicCorrection for the capture
  // that made re-seeking it a livelock rather than a redundancy.
  if (!music.isPlaying) {
    return {
      action: "play",
      seekToMillis: Math.abs(drift) <= toleranceMillis ? null : target,
      driftMillis: drift
    };
  }

  if (driftIsWorthCorrecting(drift, previousDriftMillis, toleranceMillis)) {
    return { action: "resync", seekToMillis: target, driftMillis: drift };
  }
  // Reported even though nothing is done, because an unconfirmed over-deadband
  // reading is exactly what the next tick needs in order to confirm it.
  return { action: "none", driftMillis: drift };
}
