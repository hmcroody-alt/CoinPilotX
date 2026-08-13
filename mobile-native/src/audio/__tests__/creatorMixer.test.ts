import {
  CREATOR_MIXER_PRESETS,
  CREATOR_MIC_HEADROOM_DB,
  CREATOR_MUSIC_HEADROOM_DB,
  DEFAULT_CREATOR_MIXER_SETTINGS,
  applyCreatorMixerPreset,
  clampDucking,
  clampMixLevel,
  creatorMicBusGainDb,
  creatorMixLevelToAgoraVolume,
  creatorMixLevelToLinearGain,
  creatorMixLevelToPercent,
  creatorMixPercentToLevel,
  creatorMusicBusGainDb,
  linearGainToDb,
  loadCreatorMixerSettings,
  matchCreatorMixerPreset,
  micActivityToDb,
  normalizeCreatorMixerSettings,
  resolveDuckGainDb,
  saveCreatorMixerSettings,
  withDucking,
  withMicLevel,
  withMusicLevel
} from "../creatorMixer";

describe("creator mixer levels", () => {
  it("clamps fader positions into 0..1 and survives garbage", () => {
    expect(clampMixLevel(-3)).toBe(0);
    expect(clampMixLevel(4)).toBe(1);
    expect(clampMixLevel(Number.NaN)).toBe(0);
    expect(clampMixLevel(0.42)).toBeCloseTo(0.42);
  });

  it("round-trips between percent and level", () => {
    expect(creatorMixLevelToPercent(0.45)).toBe(45);
    expect(creatorMixPercentToLevel(45)).toBeCloseTo(0.45);
    expect(creatorMixPercentToLevel(400)).toBe(1);
  });

  it("keeps the fader endpoints exact so silence and unity are not approximated", () => {
    expect(creatorMixLevelToLinearGain(0)).toBe(0);
    expect(creatorMixLevelToLinearGain(1)).toBe(1);
  });

  it("uses an audio taper rather than a linear one", () => {
    // Halfway up the slider should be well below half the amplitude, otherwise
    // the top half of the fader does nothing audible.
    expect(creatorMixLevelToLinearGain(0.5)).toBeLessThan(0.35);
  });

  it("applies bus headroom on top of the fader so two full sources cannot sum to clipping", () => {
    const unity = { ...DEFAULT_CREATOR_MIXER_SETTINGS, musicLevel: 1, micLevel: 1 };
    expect(creatorMusicBusGainDb(unity)).toBeCloseTo(CREATOR_MUSIC_HEADROOM_DB, 5);
    expect(creatorMicBusGainDb(unity)).toBeCloseTo(CREATOR_MIC_HEADROOM_DB, 5);
    expect(creatorMusicBusGainDb(unity)).toBeLessThan(0);
    expect(creatorMicBusGainDb(unity)).toBeLessThan(0);
  });

  it("reports silence as a floor rather than negative infinity", () => {
    expect(linearGainToDb(0)).toBe(-120);
    expect(Number.isFinite(creatorMusicBusGainDb({ ...DEFAULT_CREATOR_MIXER_SETTINGS, musicLevel: 0 }))).toBe(true);
  });

  it("does not double-taper the Agora volume", () => {
    // Agora applies its own curve. Squaring here too would make Live quieter
    // than the recorded mix at an identical fader position.
    expect(creatorMixLevelToAgoraVolume(0.5)).toBe(50);
    expect(creatorMixLevelToAgoraVolume(1)).toBe(100);
  });
});

describe("creator mixer presets", () => {
  it("ships a preset for every named mode and defaults to balanced", () => {
    expect(CREATOR_MIXER_PRESETS).toEqual(["balanced", "voice_focus", "music_focus"]);
    expect(DEFAULT_CREATOR_MIXER_SETTINGS.preset).toBe("balanced");
  });

  it("orders the presets the way a creator would expect them to sound", () => {
    const voice = applyCreatorMixerPreset("voice_focus");
    const balanced = applyCreatorMixerPreset("balanced");
    const music = applyCreatorMixerPreset("music_focus");
    expect(voice.musicLevel).toBeLessThan(balanced.musicLevel);
    expect(balanced.musicLevel).toBeLessThan(music.musicLevel);
    expect(voice.micLevel).toBeGreaterThan(music.micLevel);
  });

  it("ducks least in music focus, because deep ducking there pumps on every breath", () => {
    expect(applyCreatorMixerPreset("music_focus").ducking.depthDb).toBeLessThan(
      applyCreatorMixerPreset("voice_focus").ducking.depthDb
    );
  });

  it("falls into custom when a fader moves and returns to the preset when it moves back", () => {
    const balanced = applyCreatorMixerPreset("balanced");
    const nudged = withMusicLevel(balanced, 0.9);
    expect(nudged.preset).toBe("custom");
    const restored = withMusicLevel(nudged, balanced.musicLevel);
    expect(restored.preset).toBe("balanced");
  });

  it("treats a ducking edit as a custom mix too", () => {
    const custom = withDucking(applyCreatorMixerPreset("balanced"), { depthDb: 15 });
    expect(custom.preset).toBe("custom");
    expect(matchCreatorMixerPreset(custom)).toBe("custom");
  });

  it("mutating a returned preset cannot poison the next caller", () => {
    const first = applyCreatorMixerPreset("balanced");
    first.ducking.depthDb = 99;
    expect(applyCreatorMixerPreset("balanced").ducking.depthDb).not.toBe(99);
  });

  it("keeps mic edits independent of music edits", () => {
    const settings = withMicLevel(withMusicLevel(applyCreatorMixerPreset("balanced"), 0.2), 0.7);
    expect(settings.musicLevel).toBeCloseTo(0.2);
    expect(settings.micLevel).toBeCloseTo(0.7);
  });
});

describe("ducking parameter safety", () => {
  it("refuses depths that make the music vanish instead of step back", () => {
    expect(clampDucking({ ...DEFAULT_CREATOR_MIXER_SETTINGS.ducking, depthDb: 90 }).depthDb).toBeLessThanOrEqual(18);
  });

  it("refuses ballistics fast enough to chatter on consonants", () => {
    const clamped = clampDucking({ ...DEFAULT_CREATOR_MIXER_SETTINGS.ducking, attackMs: 1, releaseMs: 2 });
    expect(clamped.attackMs).toBeGreaterThanOrEqual(20);
    expect(clamped.releaseMs).toBeGreaterThanOrEqual(80);
  });

  it("substitutes defaults for non-numeric input rather than producing NaN gains", () => {
    const clamped = clampDucking({
      enabled: true,
      thresholdDb: Number.NaN,
      depthDb: Number.NaN,
      attackMs: Number.NaN,
      releaseMs: Number.NaN
    });
    expect(Number.isFinite(clamped.thresholdDb)).toBe(true);
    expect(Number.isFinite(clamped.depthDb)).toBe(true);
    expect(Number.isFinite(clamped.attackMs)).toBe(true);
    expect(Number.isFinite(clamped.releaseMs)).toBe(true);
  });
});

describe("ducking envelope", () => {
  const settings = applyCreatorMixerPreset("balanced");

  it("reads a silent meter as far below the threshold", () => {
    expect(micActivityToDb(0)).toBeLessThan(settings.ducking.thresholdDb);
    expect(micActivityToDb(1)).toBeCloseTo(0, 5);
  });

  it("never jumps straight to full depth on the first speaking frame", () => {
    const first = resolveDuckGainDb(settings, 1, 0, 200);
    expect(first).toBeLessThan(0);
    expect(first).toBeGreaterThan(-settings.ducking.depthDb);
  });

  it("approaches the configured depth while speech continues", () => {
    let gain = 0;
    for (let step = 0; step < 40; step += 1) gain = resolveDuckGainDb(settings, 1, gain, 200);
    expect(gain).toBeLessThan(-settings.ducking.depthDb + 0.5);
    expect(gain).toBeGreaterThanOrEqual(-settings.ducking.depthDb);
  });

  it("recovers more slowly than it engages, which is what stops it pumping", () => {
    const engaged = Math.abs(resolveDuckGainDb(settings, 1, 0, 100));
    const recovered = Math.abs(-settings.ducking.depthDb - resolveDuckGainDb(settings, 0, -settings.ducking.depthDb, 100));
    expect(engaged).toBeGreaterThan(recovered);
  });

  it("returns to unity once speech stops", () => {
    let gain = -settings.ducking.depthDb;
    for (let step = 0; step < 60; step += 1) gain = resolveDuckGainDb(settings, 0, gain, 200);
    expect(gain).toBeGreaterThan(-0.2);
  });

  it("holds the music down while the meter sits above the threshold", () => {
    const justAbove = Math.pow(10, (settings.ducking.thresholdDb + 2) / 20);
    let gain = 0;
    for (let step = 0; step < 30; step += 1) gain = resolveDuckGainDb(settings, justAbove, gain, 200);
    expect(gain).toBeLessThan(-1);
  });

  it("leaves the music alone for meter noise below the threshold", () => {
    const justBelow = Math.pow(10, (settings.ducking.thresholdDb - 6) / 20);
    let gain = 0;
    for (let step = 0; step < 30; step += 1) gain = resolveDuckGainDb(settings, justBelow, gain, 200);
    expect(gain).toBeGreaterThan(-0.2);
  });

  it("glides back to unity when ducking is switched off mid-take", () => {
    const off = withDucking(settings, { enabled: false });
    const next = resolveDuckGainDb(off, 1, -7, 100);
    expect(next).toBeGreaterThan(-7);
    expect(next).toBeLessThanOrEqual(0);
  });

  it("is frame-rate independent, so an irregular callback cannot change the depth reached", () => {
    let coarse = 0;
    coarse = resolveDuckGainDb(settings, 1, coarse, 400);
    let fine = 0;
    for (let step = 0; step < 4; step += 1) fine = resolveDuckGainDb(settings, 1, fine, 100);
    expect(Math.abs(coarse - fine)).toBeLessThan(0.35);
  });

  it("never returns a positive gain, so ducking can only ever attenuate", () => {
    expect(resolveDuckGainDb(settings, 0, 0, 1000)).toBeLessThanOrEqual(0);
    expect(resolveDuckGainDb(settings, 1, 0, 1000)).toBeLessThanOrEqual(0);
  });
});

describe("normalization and persistence", () => {
  it("fills a partial blob with defaults instead of producing NaN levels", () => {
    const settings = normalizeCreatorMixerSettings({ musicLevel: 0.3 });
    expect(settings.musicLevel).toBeCloseTo(0.3);
    expect(Number.isFinite(settings.micLevel)).toBe(true);
    expect(settings.ducking.enabled).toBe(true);
  });

  it("round-trips through storage", async () => {
    const settings = withMusicLevel(applyCreatorMixerPreset("music_focus"), 0.61);
    await saveCreatorMixerSettings(settings);
    const loaded = await loadCreatorMixerSettings();
    expect(loaded.musicLevel).toBeCloseTo(0.61);
    expect(loaded.preset).toBe("custom");
  });
});
