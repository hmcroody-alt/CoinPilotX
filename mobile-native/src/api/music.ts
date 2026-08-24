import AsyncStorage from "@react-native-async-storage/async-storage";
import { PULSE_API_BASE_URL } from "./config";
import { PulseApiError, pulseApi } from "./pulseApi";

const MUSIC_CACHE_KEY = "pulsesoc.native.music.library.v1";
const MUSIC_SELECTION_PREFIX = "pulsesoc.native.music.pending.";

export type PulseMusicLane = "" | "trending" | "new";

export type PulseMusicTrack = {
  id: string;
  title: string;
  artist: string;
  artistUserId: number;
  durationSeconds: number;
  previewUrl: string;
  audioUrl: string;
  coverArtUrl: string;
  waveform: number[];
  genre: string;
  language: string;
  mood: string;
  licenseLabel: string;
  moderationStatus: string;
  approvedByAdmin: boolean;
  active: boolean;
  playCount: number;
  usageCount: number;
  trendScore: number;
  saveCount: number;
  shareCount: number;
};

export type PulseMusicSearchParams = {
  query?: string;
  genre?: string;
  language?: string;
  mood?: string;
  lane?: PulseMusicLane;
  limit?: number;
};

export type PulseMusicUploadAsset = {
  uri: string;
  name: string;
  mimeType: string;
  size?: number;
};

export type PulseMusicUploadInput = {
  audio: PulseMusicUploadAsset;
  cover?: PulseMusicUploadAsset | null;
  title: string;
  artist: string;
  genre?: string;
  language?: string;
  mood?: string;
  description?: string;
  tags?: string;
  rightsConfirmed: boolean;
};

export type PulseMusicUploadResult = {
  ok?: boolean;
  message?: string;
  track_id?: number;
  status?: string;
  media?: Record<string, unknown>;
  cover_art_url?: string;
};

export type PulseMusicSurface = "reel" | "video" | "status" | "post";

export type PulseMusicPendingSelection = {
  surface: PulseMusicSurface;
  selectedAt: string;
  track: PulseMusicTrack;
};

type MusicSearchResponse = {
  items?: Array<Record<string, unknown>>;
  sounds?: Array<Record<string, unknown>>;
  surfaces?: string[];
  provider?: Record<string, unknown>;
};

export async function searchPulseMusic(params: PulseMusicSearchParams = {}) {
  const query = new URLSearchParams();
  if (params.query) query.set("q", params.query);
  if (params.genre) query.set("genre", params.genre);
  if (params.language) query.set("language", params.language);
  if (params.mood) query.set("mood", params.mood);
  if (params.lane) query.set("lane", params.lane);
  query.set("limit", String(Math.max(1, Math.min(Number(params.limit || 40), 80))));
  const data = await pulseApi<MusicSearchResponse>(`/api/pulse/music/search?${query.toString()}`);
  const tracks = (data.items || data.sounds || []).map(normalizeMusicTrack).filter((track): track is PulseMusicTrack => Boolean(track));
  await cachePulseMusicSnapshot(tracks).catch(() => undefined);
  return {
    tracks,
    surfaces: data.surfaces || ["reel", "video", "status", "post"],
    provider: data.provider || {}
  };
}

/**
 * Resolve one track by id. `null` means the catalog has no approved track under
 * that id — a real answer, distinct from the request failing, which throws.
 */
export async function getPulseMusicTrack(trackId: string) {
  const id = String(trackId || "").trim();
  if (!id) return null;
  try {
    const data = await pulseApi<{ item?: Record<string, unknown> }>(`/api/pulse/music/${encodeURIComponent(id)}`);
    return data.item ? normalizeMusicTrack(data.item) : null;
  } catch (error) {
    if (error instanceof PulseApiError && error.status === 404) return null;
    throw error;
  }
}

export async function loadCachedPulseMusicSnapshot() {
  try {
    const raw = await AsyncStorage.getItem(MUSIC_CACHE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as Array<Record<string, unknown>>;
    return parsed.map((item) => normalizeMusicTrack(item)).filter((track): track is PulseMusicTrack => Boolean(track));
  } catch {
    await AsyncStorage.removeItem(MUSIC_CACHE_KEY).catch(() => undefined);
    return [];
  }
}

export async function uploadPulseMusic(input: PulseMusicUploadInput) {
  const validation = validateMusicUpload(input);
  if (validation) throw new Error(validation);
  const form = new FormData();
  form.append("audio", {
    uri: input.audio.uri,
    name: input.audio.name,
    type: input.audio.mimeType
  } as unknown as Blob);
  if (input.cover?.uri) {
    form.append("cover", {
      uri: input.cover.uri,
      name: input.cover.name,
      type: input.cover.mimeType
    } as unknown as Blob);
  }
  form.append("title", input.title.trim());
  form.append("artist", input.artist.trim());
  form.append("genre", String(input.genre || "").trim());
  form.append("language", String(input.language || "").trim());
  form.append("mood", String(input.mood || "").trim());
  form.append("description", String(input.description || "").trim());
  form.append("tags", String(input.tags || "").trim());
  form.append("rights_confirmed", input.rightsConfirmed ? "1" : "0");
  return pulseApi<PulseMusicUploadResult>("/api/pulse/music/upload", {
    method: "POST",
    body: form
  });
}

export async function selectPulseMusicForSurface(track: PulseMusicTrack, surface: PulseMusicSurface) {
  const selection: PulseMusicPendingSelection = {
    surface,
    selectedAt: new Date().toISOString(),
    track
  };
  await AsyncStorage.setItem(`${MUSIC_SELECTION_PREFIX}${surface}.v1`, JSON.stringify(selection));
}

export async function consumePulseMusicSelection(surface: PulseMusicSurface) {
  const key = `${MUSIC_SELECTION_PREFIX}${surface}.v1`;
  try {
    const raw = await AsyncStorage.getItem(key);
    if (!raw) return null;
    await AsyncStorage.removeItem(key).catch(() => undefined);
    const selection = JSON.parse(raw) as PulseMusicPendingSelection;
    const track = normalizeMusicTrack(selection.track as unknown as Record<string, unknown>);
    return track ? { ...selection, track } : null;
  } catch {
    await AsyncStorage.removeItem(key).catch(() => undefined);
    return null;
  }
}

export function composerMusicTrackFromPulseMusic(track: PulseMusicTrack) {
  return {
    id: track.id,
    title: track.title,
    artist: track.artist,
    previewUrl: track.previewUrl || track.audioUrl,
    durationSeconds: track.durationSeconds,
    licenseLabel: track.licenseLabel
  };
}

export async function recordPulseMusicEvent(trackId: string, eventType: "play" | "save" | "share" | "use_reel" | "use_video" | "use_status", surface = "native_music") {
  if (!trackId) return;
  await pulseApi<{ ok?: boolean; message?: string }>(`/api/pulse/music/${encodeURIComponent(trackId)}/event`, {
    method: "POST",
    body: JSON.stringify({ event_type: eventType, surface })
  });
}

export async function reportPulseMusic(trackId: string, reason = "rights concern", details = "Reported from native PulseSoc Music.") {
  return pulseApi<{ ok?: boolean; message?: string }>(`/api/pulse/music/${encodeURIComponent(trackId)}/report`, {
    method: "POST",
    body: JSON.stringify({ reason, details })
  });
}

export function pulseMusicWebUrl(trackId?: string) {
  return `${PULSE_API_BASE_URL}/pulse/music${trackId ? `?track=${encodeURIComponent(trackId)}` : ""}`;
}

function validateMusicUpload(input: PulseMusicUploadInput) {
  if (!input.audio?.uri) return "Choose an audio file.";
  const audioExt = extensionFor(input.audio.name);
  if (audioExt && !["mp3", "wav", "m4a", "aac"].includes(audioExt)) return "Upload MP3, WAV, M4A, or AAC audio.";
  if (input.cover?.name) {
    const coverExt = extensionFor(input.cover.name);
    if (coverExt && !["jpg", "jpeg", "png", "webp"].includes(coverExt)) return "Cover artwork must be JPG, PNG, or WEBP.";
  }
  if (!input.title.trim()) return "Song title is required.";
  if (!input.artist.trim()) return "Artist name is required.";
  if (!input.rightsConfirmed) return "Confirm that you own this music or have the legal right to upload it.";
  return "";
}

function cachePulseMusicSnapshot(tracks: PulseMusicTrack[]) {
  return AsyncStorage.setItem(MUSIC_CACHE_KEY, JSON.stringify(tracks.slice(0, 80)));
}

function normalizeMusicTrack(input: Record<string, unknown>): PulseMusicTrack | null {
  const id = String(input.id || input.track_id || "").trim();
  if (!id) return null;
  const previewUrl = absoluteMediaUrl(String(input.preview_url || input.previewUrl || input.audio_url || input.audioUrl || ""));
  const audioUrl = absoluteMediaUrl(String(input.audio_url || input.audioUrl || input.preview_url || input.previewUrl || ""));
  return {
    id,
    title: cleanString(input.title, "PulseSoc Song"),
    artist: cleanString(input.artist || input.artist_name, "PulseSoc Music"),
    artistUserId: Number(input.artist_user_id || input.artistUserId || input.uploader_user_id || input.uploaderUserId || 0),
    durationSeconds: Number(input.duration_seconds || input.durationSeconds || input.duration || 0),
    previewUrl,
    audioUrl,
    coverArtUrl: absoluteMediaUrl(String(input.cover_art_url || input.coverArtUrl || input.cover_url || input.coverUrl || input.artwork_url || input.artworkUrl || "")),
    waveform: normalizeWaveform(input.waveform || input.waveform_json),
    genre: cleanString(input.genre, "genre"),
    language: cleanString(input.language, "language"),
    mood: cleanString(input.mood, "mood"),
    licenseLabel: cleanString(input.license_type || input.license, "approved"),
    moderationStatus: cleanString(input.moderation_status || input.safety_status || input.status, "approved"),
    approvedByAdmin: Boolean(input.approved_by_admin || input.approvedByAdmin),
    active: input.active === undefined ? true : Boolean(input.active),
    playCount: Number(input.play_count || input.playCount || input.plays || 0),
    usageCount: Number(input.usage_count || input.usageCount || input.uses || 0),
    trendScore: Number(input.trend_score || input.trendScore || 0),
    saveCount: Number(input.save_count || input.saveCount || 0),
    shareCount: Number(input.share_count || input.shareCount || 0)
  };
}

function normalizeWaveform(raw: unknown) {
  let value = raw;
  if (typeof value === "string") {
    try {
      value = JSON.parse(value);
    } catch {
      value = [];
    }
  }
  if (!Array.isArray(value)) return [0.16, 0.32, 0.48, 0.62, 0.44, 0.7, 0.56, 0.38];
  return value.map((item) => Math.max(0.08, Math.min(Number(item) || 0.24, 1))).slice(0, 24);
}

function cleanString(value: unknown, fallback: string) {
  const text = String(value || "").trim();
  return text || fallback;
}

function extensionFor(name: string) {
  const match = String(name || "").toLowerCase().match(/\.([a-z0-9]+)(?:\?|#)?$/);
  return match?.[1] || "";
}

function absoluteMediaUrl(value: string) {
  const url = value.trim();
  if (!url) return "";
  if (/^https?:\/\//i.test(url)) return url;
  return `${PULSE_API_BASE_URL}${url.startsWith("/") ? "" : "/"}${url}`;
}
