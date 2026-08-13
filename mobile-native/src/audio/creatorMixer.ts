import AsyncStorage from "@react-native-async-storage/async-storage";

/**
 * The creator mixer: one description of "how loud is the music against my voice",
 * shared by recorded video and by Live.
 *
 * The two surfaces render this description with completely different machinery.
 * Live hands it to Agora, which mixes on the device and publishes the result to
 * remote viewers. Recorded video hands it to the server, which mixes with ffmpeg
 * against the original track file. Neither path ever plays music out of the
 * speaker so the microphone can hear it — that would be a re-recording, and it
 * would arrive muddy, phase-smeared, and full of room.
 *
 * Because the two renderers are so different, the thing they share has to be
 * pure data plus pure functions. Nothing here touches an audio session, an
 * engine, or a track. That is deliberate: it is what lets the same settings
 * object be unit-tested, persisted, and sent over the wire without dragging a
 * media stack behind it.
 */

export type CreatorMixerPreset = "balanced" | "voice_focus" | "music_focus" | "custom";

export type CreatorDuckingSettings = {
  /** Ducking is opt-out rather than opt-in: unattended music over speech is the common failure. */
  enabled: boolean;
  /** Mic level (dBFS-ish, derived from the engine's activity meter) above which the music steps back. */
  thresholdDb: number;
  /** How far the music is pulled down while the creator is speaking. Positive number of dB. */
  depthDb: number;
  /** Ballistics. Slow enough not to chatter on consonants, fast enough not to bury the first word. */
  attackMs: number;
  releaseMs: number;
};

export type CreatorMixerSettings = {
  preset: CreatorMixerPreset;
  /** 0..1 fader positions. Percent is a UI concern; see creatorMixPercent helpers. */
  musicLevel: number;
  micLevel: number;
  ducking: CreatorDuckingSettings;
};

/**
 * Headroom trims, in dB, applied on top of the fader positions.
 *
 * Two full-scale sources summed into one bus will clip, and a limiter that is
 * always working sounds like a limiter. Trimming each bus before the sum means
 * the limiter is a safety net rather than a tone control. The mic gets less trim
 * than the music because speech is the thing the viewer is here for, and because
 * a voice that has already been through a high-pass and a compressor has far
 * lower peak-to-average energy than a mastered song.
 */
export const CREATOR_MUSIC_HEADROOM_DB = -3;
export const CREATOR_MIC_HEADROOM_DB = -1;

/** The mix bus never asks the limiter for more than this. Leaves room for lossy encode overshoot. */
export const CREATOR_MIX_CEILING_DB = -1.5;

const DEFAULT_DUCKING: CreatorDuckingSettings = {
  enabled: true,
  thresholdDb: -34,
  depthDb: 7,
  attackMs: 120,
  releaseMs: 420
};

const PRESETS: Record<Exclude<CreatorMixerPreset, "custom">, CreatorMixerSettings> = {
  /** The default. Music is clearly present but a spoken sentence still wins. */
  balanced: {
    preset: "balanced",
    musicLevel: 0.45,
    micLevel: 0.85,
    ducking: { ...DEFAULT_DUCKING }
  },
  /** Talking-head content. Music is a bed; ducking is deeper and faster. */
  voice_focus: {
    preset: "voice_focus",
    musicLevel: 0.26,
    micLevel: 1,
    ducking: { ...DEFAULT_DUCKING, depthDb: 10, attackMs: 90, releaseMs: 360 }
  },
  /**
   * Dance, performance, instrument content. The music is the subject, so ducking
   * is shallow — deep ducking here would pump the track every time the creator
   * breathes near the mic, which is the single most common way this feature is
   * made to sound amateur.
   */
  music_focus: {
    preset: "music_focus",
    musicLevel: 0.74,
    micLevel: 0.62,
    ducking: { ...DEFAULT_DUCKING, depthDb: 3.5, attackMs: 180, releaseMs: 600 }
  }
};

export const DEFAULT_CREATOR_MIXER_SETTINGS: CreatorMixerSettings = clonePreset("balanced");

const MIXER_STORAGE_KEY = "pulsesoc.native.creator.mixer.v1";

/* ------------------------------------------------------------------ *
 * Levels
 * ------------------------------------------------------------------ */

export function clampMixLevel(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(value, 1));
}

/** UI works in whole percent; the model works in 0..1. Keep the conversion in one place. */
export function creatorMixLevelToPercent(level: number) {
  return Math.round(clampMixLevel(level) * 100);
}

export function creatorMixPercentToLevel(percent: number) {
  return clampMixLevel((Number.isFinite(percent) ? percent : 0) / 100);
}

/**
 * Fader position to linear amplitude.
 *
 * A linear fader feels wrong: loudness is roughly logarithmic, so a straight
 * mapping crams every useful setting into the bottom third of the slider and the
 * top half does almost nothing audible. Squaring the position approximates a
 * standard audio taper closely enough for a two-fader mixer, and it keeps the
 * endpoints exact — 0 is silence, 1 is unity — which matters because both
 * renderers special-case those.
 */
export function creatorMixLevelToLinearGain(level: number) {
  const clamped = clampMixLevel(level);
  return clamped * clamped;
}

export function linearGainToDb(gain: number) {
  if (!Number.isFinite(gain) || gain <= 0) return -120;
  return 20 * Math.log10(gain);
}

export function dbToLinearGain(db: number) {
  if (!Number.isFinite(db)) return 0;
  if (db <= -120) return 0;
  return Math.pow(10, db / 20);
}

/**
 * The music bus gain actually applied by a renderer: fader taper plus headroom.
 * Returned in dB because both ffmpeg's `volume` filter and human review read dB.
 */
export function creatorMusicBusGainDb(settings: CreatorMixerSettings) {
  return linearGainToDb(creatorMixLevelToLinearGain(settings.musicLevel)) + CREATOR_MUSIC_HEADROOM_DB;
}

export function creatorMicBusGainDb(settings: CreatorMixerSettings) {
  return linearGainToDb(creatorMixLevelToLinearGain(settings.micLevel)) + CREATOR_MIC_HEADROOM_DB;
}

/**
 * Agora takes integer volumes rather than gains. It applies its own taper, so
 * the squared curve is deliberately *not* used here — doing both would leave the
 * Live mix noticeably quieter than the recorded mix at the same fader position.
 */
export function creatorMixLevelToAgoraVolume(level: number, maxVolume = 100) {
  return Math.round(clampMixLevel(level) * maxVolume);
}

/* ------------------------------------------------------------------ *
 * Presets
 * ------------------------------------------------------------------ */

function clonePreset(preset: Exclude<CreatorMixerPreset, "custom">): CreatorMixerSettings {
  const source = PRESETS[preset];
  return { ...source, ducking: { ...source.ducking } };
}

export function creatorMixerPresetSettings(preset: CreatorMixerPreset): CreatorMixerSettings {
  if (preset === "custom") return clonePreset("balanced");
  return clonePreset(preset);
}

export const CREATOR_MIXER_PRESETS: Array<Exclude<CreatorMixerPreset, "custom">> = [
  "balanced",
  "voice_focus",
  "music_focus"
];

export function applyCreatorMixerPreset(preset: CreatorMixerPreset): CreatorMixerSettings {
  return creatorMixerPresetSettings(preset);
}

/**
 * Does this settings object still describe one of the named presets?
 *
 * This exists so the UI can drop back out of CUSTOM on its own. A creator who
 * nudges a fader and then nudges it back should not be left staring at a CUSTOM
 * badge — that reads like unsaved state and invites them to go hunting for a
 * reset button that does not exist.
 */
export function matchCreatorMixerPreset(settings: CreatorMixerSettings): CreatorMixerPreset {
  for (const preset of CREATOR_MIXER_PRESETS) {
    const candidate = PRESETS[preset];
    if (
      levelsEqual(candidate.musicLevel, settings.musicLevel) &&
      levelsEqual(candidate.micLevel, settings.micLevel) &&
      duckingEqual(candidate.ducking, settings.ducking)
    ) {
      return preset;
    }
  }
  return "custom";
}

function levelsEqual(a: number, b: number) {
  return Math.abs(a - b) < 0.005;
}

function duckingEqual(a: CreatorDuckingSettings, b: CreatorDuckingSettings) {
  return (
    a.enabled === b.enabled &&
    Math.abs(a.thresholdDb - b.thresholdDb) < 0.51 &&
    Math.abs(a.depthDb - b.depthDb) < 0.26 &&
    Math.abs(a.attackMs - b.attackMs) < 1 &&
    Math.abs(a.releaseMs - b.releaseMs) < 1
  );
}

export function withMusicLevel(settings: CreatorMixerSettings, level: number): CreatorMixerSettings {
  return reconcilePreset({ ...settings, musicLevel: clampMixLevel(level) });
}

export function withMicLevel(settings: CreatorMixerSettings, level: number): CreatorMixerSettings {
  return reconcilePreset({ ...settings, micLevel: clampMixLevel(level) });
}

export function withDucking(settings: CreatorMixerSettings, ducking: Partial<CreatorDuckingSettings>): CreatorMixerSettings {
  return reconcilePreset({ ...settings, ducking: clampDucking({ ...settings.ducking, ...ducking }) });
}

function reconcilePreset(settings: CreatorMixerSettings): CreatorMixerSettings {
  return { ...settings, preset: matchCreatorMixerPreset(settings) };
}

export function clampDucking(ducking: CreatorDuckingSettings): CreatorDuckingSettings {
  return {
    enabled: Boolean(ducking.enabled),
    thresholdDb: clampNumber(ducking.thresholdDb, -70, -6, DEFAULT_DUCKING.thresholdDb),
    // Beyond ~18 dB the music does not "step back", it disappears and comes back.
    depthDb: clampNumber(ducking.depthDb, 0, 18, DEFAULT_DUCKING.depthDb),
    // Floors chosen to keep the envelope musical: faster than these and it chatters.
    attackMs: clampNumber(ducking.attackMs, 20, 1200, DEFAULT_DUCKING.attackMs),
    releaseMs: clampNumber(ducking.releaseMs, 80, 4000, DEFAULT_DUCKING.releaseMs)
  };
}

function clampNumber(value: number, min: number, max: number, fallback: number) {
  if (!Number.isFinite(value)) return fallback;
  return Math.max(min, Math.min(value, max));
}

export function normalizeCreatorMixerSettings(input: Partial<CreatorMixerSettings> | null | undefined): CreatorMixerSettings {
  const base = DEFAULT_CREATOR_MIXER_SETTINGS;
  const settings: CreatorMixerSettings = {
    preset: "custom",
    musicLevel: clampMixLevel(Number(input?.musicLevel ?? base.musicLevel)),
    micLevel: clampMixLevel(Number(input?.micLevel ?? base.micLevel)),
    ducking: clampDucking({ ...base.ducking, ...(input?.ducking || {}) })
  };
  return reconcilePreset(settings);
}

/* ------------------------------------------------------------------ *
 * Ducking envelope
 * ------------------------------------------------------------------ */

/**
 * Convert a 0..1 activity meter reading into something comparable to a threshold.
 *
 * Engines report speech activity on a linear 0..1 (or 0..255) scale, but a
 * threshold in dB is the only form that stays meaningful across microphones and
 * gain settings. The floor stops log10 from running away on digital silence.
 */
export function micActivityToDb(activity: number) {
  const clamped = Math.max(0, Math.min(Number.isFinite(activity) ? activity : 0, 1));
  return linearGainToDb(Math.max(clamped, 1e-5));
}

/**
 * One step of the ducking envelope.
 *
 * This is called from an engine callback at roughly 5 Hz, not per audio sample —
 * that is the whole reason it is allowed to live in JavaScript. It is a one-pole
 * smoother: it never jumps to the target, so a single loud consonant cannot
 * yank the music down and a single pause cannot snap it back. Attack and release
 * are separate time constants because a duck that recovers as fast as it engages
 * is the definition of pumping.
 *
 * Returns the music trim in dB (<= 0). Feed the result back in as `previousDb`.
 */
export function resolveDuckGainDb(
  settings: CreatorMixerSettings,
  micActivity: number,
  previousDb: number,
  elapsedMs: number
) {
  const previous = Number.isFinite(previousDb) ? Math.min(0, previousDb) : 0;
  if (!settings.ducking.enabled) {
    // Still smooth back to unity rather than jumping, so toggling ducking mid-take is inaudible.
    return smoothTowards(previous, 0, settings.ducking.releaseMs, elapsedMs);
  }
  const speaking = micActivityToDb(micActivity) >= settings.ducking.thresholdDb;
  const target = speaking ? -Math.abs(settings.ducking.depthDb) : 0;
  const timeConstantMs = target < previous ? settings.ducking.attackMs : settings.ducking.releaseMs;
  return smoothTowards(previous, target, timeConstantMs, elapsedMs);
}

function smoothTowards(current: number, target: number, timeConstantMs: number, elapsedMs: number) {
  const dt = Number.isFinite(elapsedMs) ? Math.max(0, elapsedMs) : 0;
  const tau = Math.max(1, timeConstantMs);
  // Exponential approach. Frame-rate independent, so an irregular callback
  // cadence changes the smoothness of the curve but never its shape.
  const coefficient = 1 - Math.exp(-dt / tau);
  const next = current + (target - current) * coefficient;
  return Math.min(0, roundDb(next));
}

function roundDb(value: number) {
  return Math.round(value * 100) / 100;
}

/* ------------------------------------------------------------------ *
 * Persistence
 * ------------------------------------------------------------------ */

export async function loadCreatorMixerSettings(): Promise<CreatorMixerSettings> {
  try {
    const raw = await AsyncStorage.getItem(MIXER_STORAGE_KEY);
    if (!raw) return { ...DEFAULT_CREATOR_MIXER_SETTINGS, ducking: { ...DEFAULT_CREATOR_MIXER_SETTINGS.ducking } };
    return normalizeCreatorMixerSettings(JSON.parse(raw) as Partial<CreatorMixerSettings>);
  } catch {
    // A corrupt blob must never cost the creator their camera. Drop it and move on.
    await AsyncStorage.removeItem(MIXER_STORAGE_KEY).catch(() => undefined);
    return { ...DEFAULT_CREATOR_MIXER_SETTINGS, ducking: { ...DEFAULT_CREATOR_MIXER_SETTINGS.ducking } };
  }
}

export async function saveCreatorMixerSettings(settings: CreatorMixerSettings) {
  try {
    await AsyncStorage.setItem(MIXER_STORAGE_KEY, JSON.stringify(normalizeCreatorMixerSettings(settings)));
  } catch {
    // Persistence is a convenience. Losing it is not worth failing a take for.
  }
}
