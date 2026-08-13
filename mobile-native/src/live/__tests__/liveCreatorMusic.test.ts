/**
 * The Live ducking sidechain and volume translation.
 *
 * Agora cannot be instantiated in Jest, so this file is where the arithmetic
 * that decides what a remote viewer actually hears gets tested. The properties
 * pinned here are the ones whose failure is audible rather than visible:
 *
 * 1. The music never ducks against itself. Agora's local level meter reflects the
 *    whole publish path, music included, so level alone would make the bed pull
 *    itself down and never let go.
 * 2. The envelope moves, it does not jump. A step change in publish volume is a
 *    click; a fast recovery is pumping.
 * 3. An unchanged volume is not re-sent, because this runs for the length of the
 *    broadcast.
 * 4. Turning ducking off releases rather than snapping.
 */

import {
  applyCreatorMixerPreset,
  withDucking,
  withMusicLevel,
  type CreatorMixerSettings
} from "../../audio/creatorMixer";
import {
  IDLE_LIVE_DUCK_STATE,
  LIVE_MIX_VOLUME_MAX,
  liveDuckIsSettled,
  liveMicActivityFrom,
  liveMicPublishVolume,
  liveMusicPublishVolume,
  liveMusicStartPositionMs,
  stepLiveDuck
} from "../liveCreatorMusic";

const balanced = applyCreatorMixerPreset("balanced");

/** Run the envelope forward at the real callback cadence. */
function run(settings: CreatorMixerSettings, sample: { volume: number; vad: number }, steps: number, from = IDLE_LIVE_DUCK_STATE) {
  let state = from;
  let now = 1_000;
  for (let index = 0; index < steps; index += 1) {
    now += 200;
    state = stepLiveDuck(settings, sample, state, now).state;
  }
  return state;
}

const speaking = { volume: 180, vad: 1 };
const silent = { volume: 4, vad: 0 };

describe("reading the local activity meter", () => {
  it("only counts a sample the SDK tagged as human voice", () => {
    // This is the whole defence against the music ducking itself: a loud meter
    // with no voice flag is the track playing, not the host talking.
    expect(liveMicActivityFrom({ volume: 255, vad: 0 })).toBe(0);
    expect(liveMicActivityFrom({ volume: 255, vad: 1 })).toBe(1);
  });

  it("normalises Agora's 0..255 scale into the 0..1 the shared envelope expects", () => {
    expect(liveMicActivityFrom({ volume: 128, vad: 1 })).toBeCloseTo(128 / 255, 5);
  });

  it("treats a missing or empty sample as silence", () => {
    expect(liveMicActivityFrom(null)).toBe(0);
    expect(liveMicActivityFrom(undefined)).toBe(0);
    expect(liveMicActivityFrom({ volume: 0, vad: 1 })).toBe(0);
  });
});

describe("fader positions become Agora volumes", () => {
  it("does not apply the squared taper a second time", () => {
    // Agora tapers internally. Squaring here too would put Live noticeably
    // quieter than the recorded mix at the same fader position.
    expect(liveMusicPublishVolume(withMusicLevel(balanced, 0.5))).toBe(50);
    expect(liveMicPublishVolume(applyCreatorMixerPreset("voice_focus"))).toBe(100);
  });

  it("folds the duck trim in as amplitude, not as another fader position", () => {
    const full = liveMusicPublishVolume(withMusicLevel(balanced, 1));
    // -6 dB is half amplitude.
    expect(liveMusicPublishVolume(withMusicLevel(balanced, 1), -6)).toBeCloseTo(full / 2, 0);
  });

  it("ignores a positive trim rather than boosting past the fader", () => {
    expect(liveMusicPublishVolume(withMusicLevel(balanced, 0.5), 6)).toBe(50);
  });

  it("never leaves the range Agora accepts", () => {
    expect(liveMusicPublishVolume(withMusicLevel(balanced, 1), 0)).toBeLessThanOrEqual(LIVE_MIX_VOLUME_MAX);
    expect(liveMusicPublishVolume(withMusicLevel(balanced, 0), -60)).toBe(0);
    expect(liveMusicPublishVolume(withMusicLevel(balanced, 1), -400)).toBe(0);
  });
});

describe("the ducking envelope", () => {
  it("pulls the music back while the host speaks", () => {
    const settled = run(balanced, speaking, 40);
    expect(settled.gainDb).toBeLessThan(-6);
    expect(settled.gainDb).toBeGreaterThanOrEqual(-balanced.ducking.depthDb);
  });

  it("does not get there in one callback", () => {
    // A single step to full depth is a click in the published stream.
    const first = stepLiveDuck(balanced, speaking, IDLE_LIVE_DUCK_STATE, 1_200);
    expect(first.state.gainDb).toBeGreaterThan(-balanced.ducking.depthDb);
    expect(first.state.gainDb).toBeLessThan(0);
  });

  it("recovers more slowly than it engages, which is what stops it pumping", () => {
    const ducked = run(balanced, speaking, 40);
    const oneStepDown = stepLiveDuck(balanced, speaking, IDLE_LIVE_DUCK_STATE, 1_200).state.gainDb;
    const oneStepUp = stepLiveDuck(balanced, silent, ducked, ducked.updatedAtMs + 200).state.gainDb - ducked.gainDb;
    expect(oneStepUp).toBeLessThan(Math.abs(oneStepDown));
  });

  it("returns to unity once the host stops talking", () => {
    const released = run(balanced, silent, 60, run(balanced, speaking, 40));
    expect(released.gainDb).toBeGreaterThan(-0.25);
  });

  it("never ducks against the music itself", () => {
    // A loud meter with no voice flag is exactly what a published music bed
    // looks like. If this ever ducks, the bed pulls itself down and stays there,
    // because the thing holding the meter up is the thing being turned down.
    const settled = run(balanced, { volume: 250, vad: 0 }, 40);
    expect(settled.gainDb).toBe(0);
  });

  it("stays out of the way entirely when the creator switched ducking off", () => {
    const off = withDucking(balanced, { enabled: false });
    expect(run(off, speaking, 40).gainDb).toBe(0);
  });

  it("releases smoothly when ducking is switched off mid-duck rather than snapping back", () => {
    const ducked = run(balanced, speaking, 40);
    const off = withDucking(balanced, { enabled: false });
    const oneStep = stepLiveDuck(off, speaking, ducked, ducked.updatedAtMs + 200).state;
    expect(oneStep.gainDb).toBeGreaterThan(ducked.gainDb);
    expect(oneStep.gainDb).toBeLessThan(0);
    expect(liveDuckIsSettled(off, oneStep)).toBe(false);
    expect(liveDuckIsSettled(off, run(off, speaking, 60, ducked))).toBe(true);
  });

  it("uses a shallower duck on the music-focus preset", () => {
    // Deep ducking on performance content pumps the track every time the creator
    // breathes near the mic — the most common way this feature sounds amateur.
    const focus = run(applyCreatorMixerPreset("music_focus"), speaking, 60);
    const voice = run(applyCreatorMixerPreset("voice_focus"), speaking, 60);
    expect(focus.gainDb).toBeGreaterThan(voice.gainDb);
  });

  it("is frame-rate independent, so an irregular callback cadence changes smoothness and not shape", () => {
    // A device under load reports the meter late and unevenly. If the envelope
    // integrated per callback instead of per elapsed millisecond, the duck would
    // get deeper whenever the phone got busy — audible exactly when the creator
    // can least afford it.
    const base = { gainDb: 0, updatedAtMs: 1_000, publishedVolume: -1 };
    let fast = base;
    for (let index = 1; index <= 20; index += 1) {
      fast = stepLiveDuck(balanced, speaking, fast, 1_000 + index * 50).state;
    }
    const slow = stepLiveDuck(balanced, speaking, base, 2_000).state.gainDb;
    // Same elapsed second, two very different cadences.
    expect(Math.abs(fast.gainDb - slow)).toBeLessThan(0.2);
  });
});

describe("what actually gets pushed at the engine", () => {
  it("reports a volume on the first step so the level is not left at the SDK default", () => {
    expect(stepLiveDuck(balanced, silent, IDLE_LIVE_DUCK_STATE, 1_200).publishVolume).toBe(
      liveMusicPublishVolume(balanced, 0)
    );
  });

  it("stays silent when the integer has not moved", () => {
    // This fires several times a second for the whole broadcast. Re-sending an
    // identical integer across the bridge is pure overhead on the one thread
    // that must not be busy.
    const settled = run(balanced, silent, 20);
    expect(stepLiveDuck(balanced, silent, settled, settled.updatedAtMs + 200).publishVolume).toBeNull();
  });

  it("pushes again as soon as the duck moves the integer", () => {
    const settled = run(balanced, silent, 20);
    expect(stepLiveDuck(balanced, speaking, settled, settled.updatedAtMs + 200).publishVolume).not.toBeNull();
  });

  it("remembers the last pushed value rather than the last computed one", () => {
    const settled = run(balanced, silent, 20);
    const unchanged = stepLiveDuck(balanced, silent, settled, settled.updatedAtMs + 200);
    expect(unchanged.state.publishedVolume).toBe(settled.publishedVolume);
  });
});

describe("cue points", () => {
  it("converts to the milliseconds Agora expects", () => {
    expect(liveMusicStartPositionMs(12.4)).toBe(12_400);
  });

  it("refuses to cue before the start of the track", () => {
    expect(liveMusicStartPositionMs(-8)).toBe(0);
    expect(liveMusicStartPositionMs(Number.NaN)).toBe(0);
  });
});
