import AsyncStorage from "@react-native-async-storage/async-storage";

import {
  listPulseMusicRadioTracks,
  loadCachedPulseMusicSnapshot,
  PulseMusicTrack,
  searchPulseMusic
} from "../api/music";
import { creatorMusicTrackIsUsable } from "./creatorMusicSelection";

/**
 * The creator picker's view of the PulseSoc Music catalog.
 *
 * There is exactly one catalog and this file does not add a second one. Every
 * lane below resolves to an endpoint the product already serves — the picker
 * chooses lane, query and genre; the server decides what exists, what is
 * approved, and what this account may use. Nothing here is a source of truth,
 * which is why a stale local row can only ever cost a creator one rejected
 * upload rather than publish an unlicensed take.
 */

export type CreatorMusicLane = "trending" | "new" | "radio" | "recent";

export const CREATOR_MUSIC_LANES: Array<{ key: CreatorMusicLane; label: string }> = [
  { key: "trending", label: "Trending" },
  { key: "new", label: "New" },
  { key: "radio", label: "Radio" },
  { key: "recent", label: "Recently used" }
];

const RECENT_STORAGE_KEY = "pulsesoc.native.creator.music.recent.v1";

/**
 * How many recently used tracks to keep.
 *
 * Small on purpose. This list exists so the creator can grab the song they used
 * an hour ago without searching for it again; past a couple of dozen entries it
 * stops being a shortcut and starts being a worse copy of the catalog, which is
 * the thing this file is not allowed to become.
 */
export const CREATOR_MUSIC_RECENT_LIMIT = 24;

export type CreatorMusicLoadRequest = {
  lane: CreatorMusicLane;
  query?: string;
  genre?: string;
  limit?: number;
};

export type CreatorMusicLoadResult = {
  tracks: PulseMusicTrack[];
  /** True when the network failed and these rows came from the offline snapshot. */
  offline: boolean;
  /** Empty when the load succeeded; a human-readable line when it did not. */
  message: string;
};

/**
 * Load one lane.
 *
 * A typed search overrides the lane: someone who types a song name wants that
 * song, not the trending shelf filtered by it. Radio is the exception that
 * proves it — its endpoint takes no query, so a search there falls back to the
 * searchable catalog rather than silently ignoring what was typed.
 */
export async function loadCreatorMusicLane(request: CreatorMusicLoadRequest): Promise<CreatorMusicLoadResult> {
  const query = String(request.query || "").trim();
  const genre = String(request.genre || "").trim();
  const limit = Math.max(1, Math.min(Number(request.limit || 40), 80));

  if (request.lane === "recent" && !query && !genre) {
    const recent = await loadRecentCreatorMusicTracks();
    return {
      tracks: recent,
      offline: false,
      message: recent.length ? "" : "Songs you use will show up here."
    };
  }

  try {
    if (request.lane === "radio" && !query && !genre) {
      const tracks = usableOnly(await listPulseMusicRadioTracks(limit));
      return { tracks, offline: false, message: tracks.length ? "" : "Radio has no tracks cleared for creator use right now." };
    }
    const result = await searchPulseMusic({
      query,
      genre,
      lane: request.lane === "new" ? "new" : request.lane === "trending" ? "trending" : "",
      limit
    });
    const tracks = usableOnly(result.tracks);
    return { tracks, offline: false, message: tracks.length ? "" : "No approved tracks matched." };
  } catch (error) {
    // Falling back to the snapshot rather than to an error screen: the creator is
    // standing in front of a camera, and losing the whole picker because the
    // network blinked is a worse outcome than showing them the songs they saw a
    // minute ago. The server re-checks eligibility at upload either way.
    const cached = usableOnly(await loadCachedPulseMusicSnapshot().catch(() => []));
    const filtered = filterTracks(cached, query, genre);
    if (filtered.length) {
      return { tracks: filtered, offline: true, message: "Offline — showing recently loaded songs." };
    }
    return {
      tracks: [],
      offline: true,
      message: error instanceof Error ? error.message : "PulseSoc Music could not load."
    };
  }
}

/** Genre chips built from what actually came back, so no chip can return nothing. */
export function creatorMusicGenresFrom(tracks: PulseMusicTrack[], limit = 8) {
  const seen = new Map<string, string>();
  for (const track of tracks) {
    const label = String(track.genre || "").trim();
    const key = label.toLowerCase();
    // "genre" is the api layer's placeholder for a track with no genre set.
    if (!label || key === "genre" || seen.has(key)) continue;
    seen.set(key, label);
    if (seen.size >= limit) break;
  }
  return Array.from(seen.values());
}

/* ------------------------------------------------------------------ *
 * Recently used
 * ------------------------------------------------------------------ */

export async function loadRecentCreatorMusicTracks(): Promise<PulseMusicTrack[]> {
  try {
    const raw = await AsyncStorage.getItem(RECENT_STORAGE_KEY);
    if (!raw) return [];
    const parsed = JSON.parse(raw) as PulseMusicTrack[];
    if (!Array.isArray(parsed)) return [];
    return usableOnly(parsed.filter((track) => Boolean(track && track.id)));
  } catch {
    await AsyncStorage.removeItem(RECENT_STORAGE_KEY).catch(() => undefined);
    return [];
  }
}

/**
 * Record that a track was actually used, most recent first.
 *
 * Called on selection rather than on preview. Previewing is browsing; a list
 * that filled up with every song the creator auditioned for two seconds would
 * bury the one they actually shot with.
 */
export async function rememberCreatorMusicTrack(track: PulseMusicTrack) {
  if (!track?.id) return;
  try {
    const current = await loadRecentCreatorMusicTracks();
    const next = [track, ...current.filter((item) => item.id !== track.id)].slice(0, CREATOR_MUSIC_RECENT_LIMIT);
    await AsyncStorage.setItem(RECENT_STORAGE_KEY, JSON.stringify(next));
  } catch {
    // A shortcut list is not worth failing a take for.
  }
}

export async function clearRecentCreatorMusicTracks() {
  await AsyncStorage.removeItem(RECENT_STORAGE_KEY).catch(() => undefined);
}

/* ------------------------------------------------------------------ *
 * Internals
 * ------------------------------------------------------------------ */

function usableOnly(tracks: PulseMusicTrack[]) {
  return tracks.filter((track) => creatorMusicTrackIsUsable(track));
}

function filterTracks(tracks: PulseMusicTrack[], query: string, genre: string) {
  const needle = query.toLowerCase();
  const wantedGenre = genre.toLowerCase();
  return tracks.filter((track) => {
    if (wantedGenre && String(track.genre || "").toLowerCase() !== wantedGenre) return false;
    if (!needle) return true;
    return `${track.title} ${track.artist}`.toLowerCase().includes(needle);
  });
}
