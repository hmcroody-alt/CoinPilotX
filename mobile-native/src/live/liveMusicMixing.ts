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

/**
 * Stage 35 — what has to be re-applied to the engine after an audio-module
 * change, so a guest arriving does not stop the host's music.
 *
 * The hazard is narrow and easy to miss. Bringing the first guest on stage moves
 * the audio scenario to the echo-control profile, and changing the scenario
 * reconfigures Agora's audio module underneath any mixing already in flight.
 * The music does not error; it just goes quiet, at the exact moment a host is
 * least able to investigate — and the report that comes back is "the music
 * stops when someone joins", which sounds like a music bug rather than an audio
 * scenario one.
 *
 * Returned as a description rather than performed here so that the decision is
 * testable without the SDK, and so the answer for "playing" and the answer for
 * "paused" cannot drift apart. A paused track deliberately gets its volumes
 * restored but is not resumed: a host who paused the music did so on purpose,
 * and un-pausing it because a guest arrived would be the same class of bug in
 * the other direction.
 */
export type LiveMusicRestoration = {
  /** Re-apply publish and playout volumes for the mixed track. */
  reapplyVolumes: boolean;
  /** Re-apply the microphone recording level. */
  reapplyMicVolume: boolean;
  /** Resume mixing that was playing before the change. */
  resumePlayback: boolean;
};

export function musicRestorationAfterAudioChange(state: LiveMusicMixingState): LiveMusicRestoration {
  const status = state?.status;
  if (status === "playing") {
    return { reapplyVolumes: true, reapplyMicVolume: true, resumePlayback: true };
  }
  if (status === "paused") {
    return { reapplyVolumes: true, reapplyMicVolume: true, resumePlayback: false };
  }
  // Nothing is mixing, so there is nothing to protect and no reason to touch
  // volumes the host may have set for their voice alone.
  return { reapplyVolumes: false, reapplyMicVolume: false, resumePlayback: false };
}

/** Whether any restoration work is needed at all. */
export function musicRestorationIsRequired(state: LiveMusicMixingState): boolean {
  const plan = musicRestorationAfterAudioChange(state);
  return plan.reapplyVolumes || plan.reapplyMicVolume || plan.resumePlayback;
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
