export type LiveMusicMixingStatus = "idle" | "loading" | "playing" | "paused" | "error";

export type LiveMusicMixingTrack = {
  id: string;
  title: string;
  artist: string;
  audioUrl: string;
  coverArtUrl?: string;
};

export type LiveMusicMixingState = {
  status: LiveMusicMixingStatus;
  track: LiveMusicMixingTrack | null;
  musicVolume: number;
  micVolume: number;
  error: string;
};

export const DEFAULT_LIVE_MUSIC_MIXING_STATE: LiveMusicMixingState = {
  status: "idle",
  track: null,
  musicVolume: 0.42,
  micVolume: 1,
  error: ""
};

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
    coverArtUrl: String(input?.coverArtUrl || "").trim() || undefined
  };
}
