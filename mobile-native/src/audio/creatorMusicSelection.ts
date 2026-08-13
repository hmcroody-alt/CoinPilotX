import { PulseMusicTrack } from "../api/music";
import {
  CreatorMixerSettings,
  creatorMicBusGainDb,
  creatorMusicBusGainDb,
  normalizeCreatorMixerSettings
} from "./creatorMixer";

/**
 * What a creator picked, in a form worth keeping.
 *
 * The important word is *reference*. A recorded video that stores "Midnight Drive
 * — Ava Lang" has stored a caption; it cannot be re-mixed, re-licensed, taken
 * down when the rights lapse, or credited with a working link. So the selection
 * carries the track id, the artist's user id, and the licence the server issued
 * it under, and the display strings ride along only so the UI has something to
 * show before the server answers.
 *
 * The server re-resolves every one of these from `trackId` at mix time. Nothing
 * here is trusted for eligibility — a client that lies about `licenseLabel` gets
 * a rejected mix, not a licensed one.
 */
export type CreatorMusicTrackRef = {
  trackId: string;
  title: string;
  artist: string;
  artistUserId: number;
  audioUrl: string;
  coverArtUrl: string;
  durationSeconds: number;
  /** The rights reference the catalog issued. Persisted, not just displayed. */
  licenseLabel: string;
  moderationStatus: string;
};

export type CreatorMusicSelection = {
  track: CreatorMusicTrackRef;
  /** Where in the track the creator wants the take to begin. */
  startOffsetSeconds: number;
  mixer: CreatorMixerSettings;
};

/**
 * Client-side eligibility.
 *
 * This is a courtesy filter, not a gate: it exists so a creator is not offered a
 * track that will obviously fail, and so the picker does not show rows that
 * cannot play. The catalog endpoint already filters on approval, licence and
 * safety status, and the mixer re-checks server-side.
 */
export function creatorMusicTrackIsUsable(track: Pick<PulseMusicTrack, "audioUrl" | "previewUrl" | "active" | "moderationStatus">) {
  if (!track.active) return false;
  if (!(track.audioUrl || track.previewUrl)) return false;
  const status = String(track.moderationStatus || "").toLowerCase();
  return status === "" || status === "approved";
}

export function creatorMusicTrackRefFromPulseMusic(track: PulseMusicTrack): CreatorMusicTrackRef {
  return {
    trackId: track.id,
    title: track.title,
    artist: track.artist,
    artistUserId: Number(track.artistUserId || 0),
    // Prefer the full asset. A preview URL is often a truncated encode, and a
    // 30-second preview silently truncates a three-minute take.
    audioUrl: track.audioUrl || track.previewUrl,
    coverArtUrl: track.coverArtUrl,
    durationSeconds: Math.max(0, Number(track.durationSeconds || 0)),
    licenseLabel: track.licenseLabel,
    moderationStatus: track.moderationStatus
  };
}

/**
 * Keep the start point inside the track.
 *
 * The tail guard matters more than it looks: a start point two seconds from the
 * end produces a take that is almost entirely silence, which reads as "the music
 * feature is broken" rather than "I chose a bad start point".
 */
export const CREATOR_MUSIC_MIN_TAIL_SECONDS = 5;

export function clampCreatorMusicStartOffset(offsetSeconds: number, durationSeconds: number) {
  const offset = Number.isFinite(offsetSeconds) ? Math.max(0, offsetSeconds) : 0;
  const duration = Number.isFinite(durationSeconds) ? Math.max(0, durationSeconds) : 0;
  if (duration <= 0) return Math.round(offset * 100) / 100;
  const latest = Math.max(0, duration - CREATOR_MUSIC_MIN_TAIL_SECONDS);
  return Math.round(Math.min(offset, latest) * 100) / 100;
}

export function createCreatorMusicSelection(
  track: PulseMusicTrack,
  mixer: CreatorMixerSettings,
  startOffsetSeconds = 0
): CreatorMusicSelection {
  const ref = creatorMusicTrackRefFromPulseMusic(track);
  return {
    track: ref,
    startOffsetSeconds: clampCreatorMusicStartOffset(startOffsetSeconds, ref.durationSeconds),
    mixer: normalizeCreatorMixerSettings(mixer)
  };
}

export function withCreatorMusicStartOffset(selection: CreatorMusicSelection, offsetSeconds: number): CreatorMusicSelection {
  return { ...selection, startOffsetSeconds: clampCreatorMusicStartOffset(offsetSeconds, selection.track.durationSeconds) };
}

export function withCreatorMusicMixer(selection: CreatorMusicSelection, mixer: CreatorMixerSettings): CreatorMusicSelection {
  return { ...selection, mixer: normalizeCreatorMixerSettings(mixer) };
}

/**
 * Flatten a selection into upload form fields.
 *
 * Flat string pairs because this rides on the same multipart upload as the video
 * itself, and that transport has no notion of nested objects. The `music_`
 * prefix keeps it obvious at the server which fields belong to the mix, and
 * keeps them from colliding with the camera's existing filter/effect fields.
 *
 * Levels are sent as fader positions rather than dB. The server derives the dB
 * itself with the same functions the client uses, so a change to the taper
 * cannot leave old drafts mixed against a curve that no longer exists.
 */
export function creatorMusicAttributionFields(selection: CreatorMusicSelection | null | undefined): Record<string, string> {
  if (!selection?.track?.trackId) return {};
  const { track, mixer } = selection;
  return {
    music_track_id: track.trackId,
    music_artist_user_id: String(track.artistUserId || 0),
    music_rights_ref: track.licenseLabel || "",
    music_moderation_status: track.moderationStatus || "",
    music_duration_seconds: String(track.durationSeconds || 0),
    music_start_offset_seconds: String(selection.startOffsetSeconds || 0),
    music_level: String(mixer.musicLevel),
    mic_level: String(mixer.micLevel),
    music_preset: mixer.preset,
    music_duck_enabled: mixer.ducking.enabled ? "1" : "0",
    music_duck_threshold_db: String(mixer.ducking.thresholdDb),
    music_duck_depth_db: String(mixer.ducking.depthDb),
    music_duck_attack_ms: String(mixer.ducking.attackMs),
    music_duck_release_ms: String(mixer.ducking.releaseMs),
    // Display strings last, and explicitly secondary: useful for logs and for
    // rendering a credit before the catalog round-trip returns, authoritative
    // for nothing.
    music_title: track.title,
    music_artist: track.artist
  };
}

/**
 * The same fields, gated on what the asset actually is.
 *
 * The upload route persists `music_*` for whatever it is given and marks the row
 * `music_mix_status='pending'`; the worker only ever mixes video. A photo that
 * carried a track id would therefore sit pending forever, waiting to be mixed
 * into a file with no audio track — invisible in the app, and a slow leak in the
 * backlog query.
 *
 * This is a function rather than an inline check at the one call site because
 * the attribution also rides in the upload hook's *default* options, so that a
 * retry (which passes no overrides) still carries the soundtrack. Two places
 * make the same decision; only one of them may be forgotten.
 */
export function creatorMusicAttributionFieldsForAsset(
  selection: CreatorMusicSelection | null | undefined,
  mediaType: string | null | undefined
): Record<string, string> {
  return String(mediaType || "").toLowerCase() === "video" ? creatorMusicAttributionFields(selection) : {};
}

/**
 * A compact summary for logs and for the "what will I get" line in the sheet.
 * Rendering dB here — rather than percent — because this is the number a human
 * debugging a bad-sounding take actually needs.
 */
export function describeCreatorMusicSelection(selection: CreatorMusicSelection) {
  const music = creatorMusicBusGainDb(selection.mixer);
  const mic = creatorMicBusGainDb(selection.mixer);
  return {
    trackId: selection.track.trackId,
    startOffsetSeconds: selection.startOffsetSeconds,
    preset: selection.mixer.preset,
    musicBusGainDb: Math.round(music * 10) / 10,
    micBusGainDb: Math.round(mic * 10) / 10,
    duckDepthDb: selection.mixer.ducking.enabled ? selection.mixer.ducking.depthDb : 0
  };
}
