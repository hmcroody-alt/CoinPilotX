import { CreatorMixerSettings, DEFAULT_CREATOR_MIXER_SETTINGS } from "../audio/creatorMixer";

export type LiveMusicMixingStatus = "idle" | "loading" | "playing" | "paused" | "error";

export type LiveMusicMixingTrack = {
  id: string;
  title: string;
  artist: string;
  audioUrl: string;
  coverArtUrl?: string;
  /** Present when the track came from the catalog picker; 0 for a radio shuffle. */
  durationSeconds?: number;
  /** The rights reference, so a Live take can be credited from the same data a recorded one is. */
  licenseLabel?: string;
  artistUserId?: number;
};

export type LiveMusicMixingState = {
  status: LiveMusicMixingStatus;
  track: LiveMusicMixingTrack | null;
  /**
   * Mirrors of `mixer.musicLevel` / `mixer.micLevel`.
   *
   * Kept because they are what the existing Live console faders read, and so a
   * caller that only wants "how loud is the music" need not know the shape of
   * the mixer. `mixer` is the source of truth; these two are written from it and
   * never set independently — see `withLiveMixer`.
   */
  musicVolume: number;
  micVolume: number;
  /** The shared creator mixer — the same shape the camera path persists. */
  mixer: CreatorMixerSettings;
  /** Where the current track was cued from, in seconds. */
  startOffsetSeconds: number;
  /** The live ducking trim, surfaced so the console can show the music stepping back. */
  duckGainDb: number;
  error: string;
};

export const DEFAULT_LIVE_MUSIC_MIXING_STATE: LiveMusicMixingState = {
  status: "idle",
  track: null,
  musicVolume: DEFAULT_CREATOR_MIXER_SETTINGS.musicLevel,
  micVolume: DEFAULT_CREATOR_MIXER_SETTINGS.micLevel,
  mixer: { ...DEFAULT_CREATOR_MIXER_SETTINGS, ducking: { ...DEFAULT_CREATOR_MIXER_SETTINGS.ducking } },
  startOffsetSeconds: 0,
  duckGainDb: 0,
  error: ""
};

/** Fold a mixer into the state, keeping the two mirrored levels honest. */
export function withLiveMixer(state: LiveMusicMixingState, mixer: CreatorMixerSettings): LiveMusicMixingState {
  return { ...state, mixer, musicVolume: mixer.musicLevel, micVolume: mixer.micLevel };
}

export function clampLiveMixLevel(value: number) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(value, 1));
}

export function liveMixLevelToAgoraVolume(value: number, maxVolume = 100) {
  return Math.round(clampLiveMixLevel(value) * maxVolume);
}

export function normalizeLiveMusicTrack(input: Partial<LiveMusicMixingTrack> | null | undefined): LiveMusicMixingTrack | null {
  const id = String(input?.id || "").trim();
  const title = String(input?.title || "").trim();
  const artist = String(input?.artist || "").trim();
  const audioUrl = String(input?.audioUrl || "").trim();
  if (!id || !title || !audioUrl) return null;
  return {
    id,
    title,
    artist: artist || "PulseSoc Music",
    audioUrl,
    coverArtUrl: String(input?.coverArtUrl || "").trim() || undefined,
    durationSeconds: Math.max(0, Number(input?.durationSeconds || 0)),
    licenseLabel: String(input?.licenseLabel || "").trim() || undefined,
    artistUserId: Math.max(0, Number(input?.artistUserId || 0))
  };
}
