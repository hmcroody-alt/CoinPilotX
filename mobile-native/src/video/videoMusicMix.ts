import { Audio } from "expo-av";
import type { PulseMusicTrack } from "../api/music";
import type { PulseRadioState } from "../core/pulseRadio";
import { configureVideoCaptureMonitoringSession } from "../core/reelsAudioSession";
import { isPulseVideoMixerSupported, mixVideoWithMusic } from "pulse-video-mixer";

export type VideoMusicSource = {
  kind: "catalog" | "pulse_radio";
  trackId: string;
  title: string;
  artist: string;
  audioUrl: string;
  coverArtUrl?: string;
  startOffsetSeconds: number;
  licenseLabel: string;
};

export type VideoMixSettings = {
  musicVolume: number;
  micVolume: number;
};

export const DEFAULT_VIDEO_MIX_SETTINGS: VideoMixSettings = {
  musicVolume: 0.7,
  micVolume: 0.78
};

export function videoMusicSourceFromTrack(track: PulseMusicTrack, startOffsetSeconds = 0): VideoMusicSource {
  return {
    kind: "catalog",
    trackId: track.id,
    title: track.title,
    artist: track.artist,
    audioUrl: track.audioUrl || track.previewUrl,
    coverArtUrl: track.coverArtUrl,
    startOffsetSeconds: Math.max(0, startOffsetSeconds),
    licenseLabel: track.licenseLabel
  };
}

export function videoMusicSourceFromRadio(state: PulseRadioState): VideoMusicSource | null {
  if (!state.track?.audioUrl) return null;
  return {
    kind: "pulse_radio",
    trackId: state.track.id,
    title: state.track.title,
    artist: state.track.artist,
    audioUrl: state.track.audioUrl,
    coverArtUrl: state.track.coverArtUrl,
    startOffsetSeconds: Math.max(0, state.positionMillis / 1000),
    licenseLabel: "Pulse Radio video-eligible catalog"
  };
}

/**
 * Recording monitoring only. Final music comes from the native digital mix.
 *
 * The AVAudioSession mutation itself is delegated to core/reelsAudioSession because the
 * `expo_av_global_audio_mode` protected-path rule freezes which files may call
 * Audio.setAudioModeAsync; this module is not one of them.
 */
export async function configureVideoMusicMonitoring() {
  await configureVideoCaptureMonitoringSession();
}

export async function createVideoMusicMonitor(source: VideoMusicSource, volume: number) {
  await configureVideoMusicMonitoring();
  const created = await Audio.Sound.createAsync(
    { uri: source.audioUrl },
    {
      shouldPlay: true,
      positionMillis: Math.round(source.startOffsetSeconds * 1000),
      volume: safeMonitorVolume(volume),
      progressUpdateIntervalMillis: 250
    }
  );
  return created.sound;
}

export function safeMonitorVolume(value: number) {
  return Math.max(0, Math.min(1, value)) * 0.72;
}

export async function exportVideoMusicMix(
  videoUri: string,
  source: VideoMusicSource,
  settings: VideoMixSettings,
  startOffsetSeconds: number
) {
  if (!isPulseVideoMixerSupported) throw new Error("Install a fresh native iOS build to mix music into video.");
  return mixVideoWithMusic({
    videoUri,
    musicUri: source.audioUrl,
    musicStartSeconds: Math.max(0, startOffsetSeconds),
    musicVolume: Math.max(0, Math.min(1, settings.musicVolume)),
    micVolume: Math.max(0, Math.min(1, settings.micVolume))
  });
}

export function videoMusicAttribution(source: VideoMusicSource, settings: VideoMixSettings, durationUsed: number) {
  return {
    track_id: source.trackId,
    artist: source.artist,
    title: source.title,
    catalog_source: source.kind,
    station: source.kind === "pulse_radio" ? "Pulse Radio" : "PulseSoc Music",
    start_offset_seconds: source.startOffsetSeconds,
    duration_used_seconds: Math.max(0, durationUsed),
    music_volume: settings.musicVolume,
    mic_volume: settings.micVolume,
    license: source.licenseLabel
  };
}
