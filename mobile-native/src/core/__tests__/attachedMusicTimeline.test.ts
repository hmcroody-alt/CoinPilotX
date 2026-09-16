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
    expect(planMusicCorrection(video({ positionMillis: 20000 }), music({ positionMillis: 4000 }), withMusic))
      .toEqual({ action: "resync", seekToMillis: 20000, driftMillis: -16000 });
  });

  it("leaves drift inside the deadband alone, because the correction is louder than the error", () => {
    const within = MUSIC_DRIFT_TOLERANCE_MS - 1;
    expect(planMusicCorrection(video({ positionMillis: 5000 }), music({ positionMillis: 5000 + within }), withMusic))
      .toEqual({ action: "none" });
  });

  it("corrects once drift exceeds the deadband, in either direction", () => {
    const beyond = MUSIC_DRIFT_TOLERANCE_MS + 1;
    expect(planMusicCorrection(video({ positionMillis: 5000 }), music({ positionMillis: 5000 + beyond }), withMusic))
      .toEqual({ action: "resync", seekToMillis: 5000, driftMillis: beyond });
    expect(planMusicCorrection(video({ positionMillis: 5000 }), music({ positionMillis: 5000 - beyond }), withMusic))
      .toEqual({ action: "resync", seekToMillis: 5000, driftMillis: -beyond });
  });

  it("does not seek a looping track against its own wrap", () => {
    // The track is 30s and the video is at 32s, so the track should be at 2s.
    // A correction computed without the wrap would see 30s of drift and seek
    // every tick for the rest of the reel.
    expect(planMusicCorrection(video({ positionMillis: 32000 }), music({ positionMillis: 2000 }), withMusic))
      .toEqual({ action: "none" });
  });

  it("does not issue a pause to a track that is already paused", () => {
    // Re-issuing pause every tick is how a surface ends up fighting its own
    // effect loop; "none" is what lets the caller treat the plan as idempotent.
    expect(planMusicCorrection(video({ isBuffering: true }), music({ isPlaying: false }), withMusic))
      .toEqual({ action: "none" });
  });
});
