/**
 * Whether the music stays with the picture.
 *
 * These tests are written against the drift the user would actually hear, not
 * against the shape of the return value. The mission's acceptance is "video and
 * attached audio start together, and stay together through seek, pause, resume
 * and buffering" -- so the cases below are those transitions, and each asserts a
 * position rather than merely that some instruction was produced.
 *
 * The distinction matters because the defect this module exists to remove was
 * not "no correction happened". It was that the correction that happened was
 * computed from the wrong clock: the music was started at its own offset and
 * then left alone, so every rebuffer moved it permanently further from the
 * video and nothing ever measured the gap.
 */

import {
  MUSIC_DRIFT_TOLERANCE_MS,
  MUSIC_STATUS_INTERVAL_MS,
  expectedMusicPosition,
  musicDriftMillis,
  planMusicCorrection
} from "../attachedMusicTimeline";
import type { MusicTimelineState, VideoTimelineState } from "../attachedMusicTimeline";
import { ATTACHED_MUSIC_EXCLUSIVE, ORIGINAL_AUDIO } from "../attachedMusicAudioPolicy";
import type { AttachedMusicPolicy } from "../attachedMusicAudioPolicy";

const withMusic: AttachedMusicPolicy = {
  mode: ATTACHED_MUSIC_EXCLUSIVE,
  hasAttachedMusic: true,
  muteOriginalAudio: true,
  musicUrl: "https://cdn.example/track.m4a",
  musicVolume: 1,
  musicStartMs: 0,
  isLooping: true
};

const noMusic: AttachedMusicPolicy = {
  mode: ORIGINAL_AUDIO,
  hasAttachedMusic: false,
  muteOriginalAudio: false,
  musicUrl: undefined,
  musicVolume: 1,
  musicStartMs: 0,
  isLooping: false
};

const video = (over: Partial<VideoTimelineState> = {}): VideoTimelineState => ({
  isLoaded: true,
  positionMillis: 0,
  isPlaying: true,
  isBuffering: false,
  ...over
});

const music = (over: Partial<MusicTimelineState> = {}): MusicTimelineState => ({
  isLoaded: true,
  positionMillis: 0,
  isPlaying: true,
  durationMillis: 30000,
  ...over
});

/**
 * A second tick, carrying forward whatever drift the first one reported.
 *
 * Corrections now need two consecutive readings, so most of the cases below are
 * two-tick sequences. Threading `previousDriftMillis` by hand each time buried
 * the case being tested under argument lists.
 */
const confirmedBy = (
  previous: ReturnType<typeof planMusicCorrection>,
  videoState: VideoTimelineState,
  musicState: MusicTimelineState,
  nowMillis: number | null = null
) =>
  planMusicCorrection(
    videoState,
    musicState,
    withMusic,
    MUSIC_DRIFT_TOLERANCE_MS,
    nowMillis,
    previous.action === "none" ? previous.driftMillis ?? null : null
  );

/** The video at 5s, the track at `position`, after a tick that read `previous`. */
const withPrevious = (position: number, previous: number | null) =>
  planMusicCorrection(
    video({ positionMillis: 5000 }),
    music({ positionMillis: position }),
    withMusic,
    MUSIC_DRIFT_TOLERANCE_MS,
    null,
    previous
  );

/**
 * The deadband against the grid the drift is measured on.
 *
 * MEASURED on a physical iPhone 16 Pro over 195 seconds: `videoPosition -
 * musicPosition` took exactly two values, 0 and 250, and never once exceeded a
 * single reporting step. The track did not leave the picture. Yet a 120ms
 * deadband produced 126 seeks in those 195 seconds, because every one of them
 * was reading the phase between two callbacks on a 250ms grid, not a track that
 * was out.
 *
 * So these are not tests of a tuning preference. A threshold below one reporting
 * interval cannot distinguish "the track is out" from "the two callbacks landed
 * in different frames", and a loop built on that difference will seek forever.
 */
describe("the deadband against the grid it is measured on", () => {
  it("cannot be tightened below the resolution that feeds it", () => {
    expect(MUSIC_DRIFT_TOLERANCE_MS).toBeGreaterThan(MUSIC_STATUS_INTERVAL_MS);
  });

  it("does not chase a drift of one reporting step, however persistent", () => {
    // A whole step apart, on the same side, on both ticks -- so the persistence
    // rule is satisfied and the deadband is the only thing left to decline it.
    // This is the exact reading the device produced 126 times.
    const step = MUSIC_STATUS_INTERVAL_MS;
    const first = withPrevious(5000 - step, null);
    const second = confirmedBy(
      first,
      video({ positionMillis: 5000 }),
      music({ positionMillis: 5000 - step })
    );

    expect(first.action).toBe("none");
    expect(second.action).toBe("none");
  });

  it("still corrects a gap that is larger than the grid could explain", () => {
    // Two steps out is not quantisation phase; no callback ordering produces it.
    const gap = MUSIC_STATUS_INTERVAL_MS * 4;
    // Starting cold, so the first reading is the one that gets confirmed rather
    // than one that already acted -- a tick that seeks clears the history.
    const first = withPrevious(5000 - gap, null);
    const second = confirmedBy(
      first,
      video({ positionMillis: 5000 }),
      music({ positionMillis: 5000 - gap })
    );

    expect(second).toEqual({ action: "resync", seekToMillis: 5000, driftMillis: -gap });
  });
});

describe("where the track should be", () => {
  it("advances with the video from the creator's chosen in-point", () => {
    // The offset is not a one-time seek applied at start; it is part of the
    // mapping, so it still holds ten seconds in.
    expect(expectedMusicPosition(10000, { musicStartMs: 5000, isLooping: true }, 30000)).toBe(15000);
  });

  it("loops back to the in-point, not to zero", () => {
    // A track told to start at 5s has a 25s loop span. At 30s of video the
    // track has played 5s->30s and wrapped 5s past the in-point.
    // Wrapping the whole value instead would land on 5000 and silently discard
    // the creator's in-point on every pass after the first.
    expect(expectedMusicPosition(30000, { musicStartMs: 5000, isLooping: true }, 30000)).toBe(10000);
  });

  it("lets an unlooped track run past its end rather than clamping to it", () => {
    // Clamping would peg the expected position at the duration, so a short
    // track under a long reel would report an ever-growing drift and seek
    // against its own tail forever.
    expect(expectedMusicPosition(45000, { musicStartMs: 0, isLooping: false }, 30000)).toBe(45000);
  });

  it("reports drift signed, so a lead is distinguishable from a lag", () => {
    // Positive = music ahead of picture, the direction a stall produces.
    expect(musicDriftMillis(video({ positionMillis: 4000 }), music({ positionMillis: 4300 }), withMusic)).toBe(300);
    expect(musicDriftMillis(video({ positionMillis: 4000 }), music({ positionMillis: 3700 }), withMusic)).toBe(-300);
  });
});

describe("keeping the track with the picture", () => {
  it("does nothing when no music is attached", () => {
    expect(planMusicCorrection(video(), music(), noMusic)).toEqual({ action: "none" });
  });

  it("does not act on an unloaded player's position", () => {
    // An unloaded player reports 0. Acting on it would seek the music to the
    // in-point on every tick while the video is still opening.
    expect(planMusicCorrection(video({ isLoaded: false, positionMillis: 0 }), music(), withMusic))
      .toEqual({ action: "none" });
    expect(planMusicCorrection(video(), music({ isLoaded: false }), withMusic))
      .toEqual({ action: "none" });
  });

  it("starts the music at the position the video is already at", () => {
    // The start case and the resume case are the same branch on purpose. If the
    // video is 4s in when the music becomes ready, the music must enter at 4s --
    // starting it at 0 is the "audio starts late and stays late" defect.
    expect(planMusicCorrection(video({ positionMillis: 4000 }), music({ isPlaying: false }), withMusic))
      .toEqual({ action: "play", seekToMillis: 4000, driftMillis: -4000 });
  });

  it("pauses the music when the video stalls to rebuffer", () => {
    // §39. Letting it run is what makes every stall permanently increase drift,
    // and makes a frozen frame over continuing music read as a broken video.
    expect(planMusicCorrection(video({ isBuffering: true, positionMillis: 4000 }), music({ positionMillis: 4000 }), withMusic))
      .toEqual({ action: "pause", reason: "video_stalled" });
  });

  it("pauses the music when the video is paused", () => {
    expect(planMusicCorrection(video({ isPlaying: false, positionMillis: 4000 }), music({ positionMillis: 4000 }), withMusic))
      .toEqual({ action: "pause", reason: "video_paused" });
  });

  it("re-aligns on resume rather than continuing from where it was left", () => {
    // After a 3s stall the video has moved on. Resuming the music in place
    // would preserve the gap the stall created; this is the branch that repays
    // it. A seek during the stall would have been pointless -- the picture was
    // not moving -- which is why the stall branch pauses and this one corrects.
    const resumed = planMusicCorrection(
      video({ positionMillis: 7000 }),
      music({ isPlaying: false, positionMillis: 4000 }),
      withMusic
    );
    expect(resumed).toEqual({ action: "play", seekToMillis: 7000, driftMillis: -3000 });
  });

  it("follows the video through a seek without a seek-specific branch", () => {
    // The user scrubs to 20s. Nothing here knows a seek happened; the video is
    // simply at a new position and the music is told to match. That is the whole
    // reason the video is the clock.
    //
    // It takes two ticks, because a first reading of a 16s gap is -- to this
    // function -- the same shape as a first reading of a quantisation spike,
    // and the device capture was full of spikes. Tick one reports the drift
    // without acting; tick two confirms it and seeks.
    const first = planMusicCorrection(video({ positionMillis: 20000 }), music({ positionMillis: 4000 }), withMusic);
    expect(first).toEqual({ action: "none", driftMillis: -16000 });
    expect(confirmedBy(first, video({ positionMillis: 20000 }), music({ positionMillis: 4000 })))
      .toEqual({ action: "resync", seekToMillis: 20000, driftMillis: -16000 });
  });

  it("leaves drift inside the deadband alone, because the correction is louder than the error", () => {
    const within = MUSIC_DRIFT_TOLERANCE_MS - 1;
    expect(planMusicCorrection(video({ positionMillis: 5000 }), music({ positionMillis: 5000 + within }), withMusic))
      .toEqual({ action: "none", driftMillis: within });
  });

  it("corrects once a second reading confirms the drift, in either direction", () => {
    const beyond = MUSIC_DRIFT_TOLERANCE_MS + 1;
    expect(withPrevious(5000 + beyond, beyond)).toEqual({ action: "resync", seekToMillis: 5000, driftMillis: beyond });
    expect(withPrevious(5000 - beyond, -beyond)).toEqual({ action: "resync", seekToMillis: 5000, driftMillis: -beyond });
  });

  it("does not act on a single over-deadband reading", () => {
    // MEASURED, and the reason the deadband alone is not enough. A 195-second
    // capture on a physical iPhone 16 Pro produced 190 corrections and not one
    // was real: every one was a run of length ONE. `videoPosition -
    // musicPosition` alternated between exactly 250ms and exactly 0ms -- the two
    // phases of a 250ms quantisation step -- which swung the computed drift
    // between +63 and -186 around a true value near -60.
    //
    // So a first reading is not evidence. This assertion is what turns those
    // 190 audible seeks into zero.
    const beyond = MUSIC_DRIFT_TOLERANCE_MS + 1;
    expect(planMusicCorrection(video({ positionMillis: 5000 }), music({ positionMillis: 5000 + beyond }), withMusic))
      .toEqual({ action: "none", driftMillis: beyond });
  });

  it("does not let an alternating reading confirm a spike", () => {
    // The device's noise did not merely exceed the deadband, it exceeded it on
    // BOTH sides. A rule that asked only for "two large readings" would still
    // fire on a +150/-150 swing and seek in the direction the previous tick
    // disagreed with, so sameness of sign is part of the rule, not a nicety.
    const beyond = MUSIC_DRIFT_TOLERANCE_MS + 1;
    expect(withPrevious(5000 + beyond, -beyond)).toEqual({ action: "none", driftMillis: beyond });
  });

  it("does not let a settled previous reading confirm a spike", () => {
    // A previous tick comfortably inside the deadband is evidence the track was
    // fine a moment ago, which makes the spike less credible rather than more.
    const beyond = MUSIC_DRIFT_TOLERANCE_MS + 1;
    expect(withPrevious(5000 + beyond, 0)).toEqual({ action: "none", driftMillis: beyond });
  });

  it("reports the drift it declined to act on, so the next tick can confirm it", () => {
    // The feedback channel itself. Without a drift on the `none` plan the
    // caller has nothing to hold, every tick looks like a first tick, and the
    // persistence rule silently degrades into "never correct anything".
    const beyond = MUSIC_DRIFT_TOLERANCE_MS + 1;
    const plan = planMusicCorrection(video({ positionMillis: 5000 }), music({ positionMillis: 5000 + beyond }), withMusic);
    expect(plan.action === "none" && plan.driftMillis).toBe(beyond);
  });

  it("does not seek a looping track against its own wrap", () => {
    // The track is 30s and the video is at 32s, so the track should be at 2s.
    // A correction computed without the wrap would see 30s of drift and seek
    // every tick for the rest of the reel.
    expect(planMusicCorrection(video({ positionMillis: 32000 }), music({ positionMillis: 2000 }), withMusic))
      .toEqual({ action: "none", driftMillis: 0 });
  });

  it("does not read a stale music sample as drift", () => {
    // MEASURED, not imagined. An instrumented Release build on the simulator
    // emitted MEDIA_AUDIO_RESYNC on every one of the video's 250ms ticks with
    // drift pinned at exactly -250 -- one tick, never growing, never settling.
    //
    // That is the signature of comparing two clocks sampled at different
    // instants. The video reading is current; the music reading is whatever its
    // own callback last left behind. The seek issued to "fix" it republished a
    // position one tick old, which produced the identical reading next tick, so
    // the loop sustained itself and the track was seeked four times a second
    // for the life of the reel.
    //
    // The numbers below are that capture: video at 3000ms, a music sample taken
    // 250ms ago that read 2750ms. Projected forward it is 3000ms -- aligned.
    const sampled = planMusicCorrection(
      video({ positionMillis: 3000 }),
      music({ positionMillis: 2750, sampledAtMillis: 1_000_000 }),
      withMusic,
      MUSIC_DRIFT_TOLERANCE_MS,
      1_000_250
    );
    expect(sampled).toEqual({ action: "none", driftMillis: 0 });
  });

  it("still corrects real drift when the sample is fresh", () => {
    // The projection must not become a blanket excuse. With no elapsed time
    // between the two readings there is nothing to carry forward, so a track
    // that genuinely sits a second behind the picture is still corrected --
    // once a second reading agrees with the first.
    const behind = music({ positionMillis: 4000, sampledAtMillis: 1_000_000 });
    const first = planMusicCorrection(
      video({ positionMillis: 5000 }),
      behind,
      withMusic,
      MUSIC_DRIFT_TOLERANCE_MS,
      1_000_000
    );
    expect(first).toEqual({ action: "none", driftMillis: -1000 });
    expect(confirmedBy(first, video({ positionMillis: 5000 }), behind, 1_000_000))
      .toEqual({ action: "resync", seekToMillis: 5000, driftMillis: -1000 });
  });

  it("does not carry a paused track forward", () => {
    // A paused track has not moved since it was sampled, so ageing it would
    // invent motion. This is the branch that keeps a resume from landing on a
    // position the track never reached: the `play` plan below seeks to where
    // the video says, and its reported drift is the true gap.
    expect(
      planMusicCorrection(
        video({ positionMillis: 8000 }),
        music({ positionMillis: 2000, isPlaying: false, sampledAtMillis: 1_000_000 }),
        withMusic,
        MUSIC_DRIFT_TOLERANCE_MS,
        1_006_000
      )
    ).toEqual({ action: "play", seekToMillis: 8000, driftMillis: -6000 });
  });

  it("ignores a clock that runs backwards rather than rewinding the track", () => {
    // `nowMillis` earlier than the sample means the wall clock moved, not the
    // track. Subtracting would report drift in the wrong direction and seek the
    // music backwards -- audibly repeating a fraction of the song for a reason
    // that has nothing to do with playback.
    expect(
      planMusicCorrection(
        video({ positionMillis: 5000 }),
        music({ positionMillis: 5000, sampledAtMillis: 1_000_000 }),
        withMusic,
        MUSIC_DRIFT_TOLERANCE_MS,
        999_000
      )
    ).toEqual({ action: "none", driftMillis: 0 });
  });

  it("does not issue a pause to a track that is already paused", () => {
    // Re-issuing pause every tick is how a surface ends up fighting its own
    // effect loop; "none" is what lets the caller treat the plan as idempotent.
    expect(planMusicCorrection(video({ isBuffering: true }), music({ isPlaying: false }), withMusic))
      .toEqual({ action: "none" });
  });
});
