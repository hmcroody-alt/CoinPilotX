import {
  CreatorMixerSettings,
  creatorMixLevelToAgoraVolume,
  dbToLinearGain,
  resolveDuckGainDb
} from "../audio/creatorMixer";

/**
 * The translation layer between the shared creator mixer and Agora's volume API.
 *
 * Everything here is a pure function of numbers. That is not tidiness for its own
 * sake: this is the only part of the Live music path that can be unit-tested,
 * because the rest of it is an RTC engine that cannot exist inside Jest. So the
 * arithmetic that decides what a viewer actually hears lives here, and the hook
 * that owns the engine is left doing nothing but calling these and handing the
 * result to Agora.
 *
 * The physical claim this file rests on: Agora mixes the music file into the
 * published stream *inside the SDK*. The music never leaves the speaker and comes
 * back through the microphone. That is why a Live take does not need the phone to
 * be in a quiet room, and it is the same guarantee the recorded path gets from
 * mixing server-side with ffmpeg.
 */

/** Agora's publish/playout mixing volume range. */
export const LIVE_MIX_VOLUME_MAX = 100;

/**
 * Where the ducking envelope stands between callbacks.
 *
 * Carried as data rather than held in a closure so the hook can keep it in a ref
 * and reset it on stop without reaching into this module.
 */
export type LiveDuckState = {
  /** Music trim in dB, always <= 0. */
  gainDb: number;
  /** Timestamp of the last envelope step, for a frame-rate-independent smoother. */
  updatedAtMs: number;
  /** Last integer actually pushed to Agora, so an unchanged value is not re-sent. */
  publishedVolume: number;
};

export const IDLE_LIVE_DUCK_STATE: LiveDuckState = { gainDb: 0, updatedAtMs: 0, publishedVolume: -1 };

/**
 * One reading of the local speaking meter, as Agora reports it.
 *
 * `volume` is 0..255. `vad` is 1 when the SDK believes there is human voice in
 * the local capture, and is only populated when volume indication was enabled
 * with VAD reporting on.
 */
export type LiveMicActivitySample = {
  volume: number;
  vad: number;
};

/**
 * Reduce a local volume-indication sample to the 0..1 activity the shared
 * envelope expects.
 *
 * The VAD flag is treated as a gate rather than as extra information, and that
 * choice is load-bearing. Agora's local volume reading reflects the whole local
 * publish path, so on a stream that is already carrying mixed-in music the meter
 * reads loud whether or not anybody is talking. Ducking on level alone would
 * therefore make the music duck *itself*: the louder it plays the more it ducks,
 * and it never releases, because the thing holding the meter up is the thing
 * being turned down.
 *
 * So a sample only counts as speech when the SDK says it heard a voice. If a
 * platform never reports VAD the result is that ducking quietly never engages —
 * the music simply sits at the level the creator set. That is the failure worth
 * choosing: a mix that is missing an automatic refinement still sounds like a
 * mix, whereas a mix that pumps against itself sounds broken.
 */
export function liveMicActivityFrom(sample: LiveMicActivitySample | null | undefined) {
  if (!sample || Number(sample.vad) !== 1) return 0;
  const volume = Number(sample.volume);
  if (!Number.isFinite(volume) || volume <= 0) return 0;
  return Math.min(1, volume / 255);
}

/**
 * The music publish volume for a given mixer position and duck trim.
 *
 * `creatorMixLevelToAgoraVolume` deliberately skips the squared fader taper the
 * recorded path uses, because Agora applies a taper of its own; doubling up would
 * leave Live noticeably quieter than the recorded mix at the same fader position.
 * The duck trim, by contrast, *is* applied as linear amplitude — it is a relative
 * step expressed in dB, and scaling the volume integer by the equivalent
 * amplitude ratio is the only lever the SDK exposes for it.
 */
export function liveMusicPublishVolume(settings: CreatorMixerSettings, duckGainDb = 0) {
  const base = creatorMixLevelToAgoraVolume(settings.musicLevel, LIVE_MIX_VOLUME_MAX);
  const trim = dbToLinearGain(Math.min(0, Number.isFinite(duckGainDb) ? duckGainDb : 0));
  return clampVolume(Math.round(base * trim));
}

/** The microphone capture volume. Not ducked — ducking exists to protect this signal. */
export function liveMicPublishVolume(settings: CreatorMixerSettings) {
  return clampVolume(creatorMixLevelToAgoraVolume(settings.micLevel, LIVE_MIX_VOLUME_MAX));
}

/**
 * Advance the ducking envelope by one volume-indication callback.
 *
 * Returns the next state plus, when it changed, the volume to push. Returning
 * `null` for `publishVolume` is the common case and it matters: this runs a few
 * times a second for the whole broadcast, and re-sending an identical integer
 * across the bridge every time would be pure overhead on the exact thread that
 * must not be busy.
 */
export function stepLiveDuck(
  settings: CreatorMixerSettings,
  sample: LiveMicActivitySample | null | undefined,
  previous: LiveDuckState,
  nowMs: number
): { state: LiveDuckState; publishVolume: number | null } {
  // A first callback has no interval to smooth over. Treating it as one envelope
  // period keeps the very first step from being either a jump or a no-op.
  const elapsedMs = previous.updatedAtMs > 0 ? Math.max(0, nowMs - previous.updatedAtMs) : 200;
  const gainDb = resolveDuckGainDb(settings, liveMicActivityFrom(sample), previous.gainDb, elapsedMs);
  const volume = liveMusicPublishVolume(settings, gainDb);
  const changed = volume !== previous.publishedVolume;
  return {
    state: { gainDb, updatedAtMs: nowMs, publishedVolume: changed ? volume : previous.publishedVolume },
    publishVolume: changed ? volume : null
  };
}

/**
 * Whether the envelope needs to keep running.
 *
 * With ducking switched off the envelope still has to be stepped while it slides
 * back to unity — dropping it the instant the toggle flips would leave the music
 * stuck at whatever trim it happened to be holding, which is the one way an
 * "off" switch can make things quieter.
 */
export function liveDuckIsSettled(settings: CreatorMixerSettings, state: LiveDuckState) {
  return !settings.ducking.enabled && Math.abs(state.gainDb) < 0.05;
}

/** Agora takes the mixing start position in milliseconds. */
export function liveMusicStartPositionMs(startOffsetSeconds: number) {
  const offset = Number.isFinite(startOffsetSeconds) ? Math.max(0, startOffsetSeconds) : 0;
  return Math.round(offset * 1000);
}

function clampVolume(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(LIVE_MIX_VOLUME_MAX, Math.round(value)));
}
