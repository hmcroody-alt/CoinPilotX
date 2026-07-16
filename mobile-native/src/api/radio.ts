import { PULSE_API_BASE_URL } from "./config";
import { pulseApi } from "./pulseApi";

export type PulseRadioTrack = {
  id: string;
  title: string;
  artist: string;
  audioUrl: string;
  coverArtUrl?: string;
};

type PulseRadioResponse = {
  items?: Array<Record<string, unknown>>;
};

export async function listPulseRadioTracks(limit = 40) {
  const response = await pulseApi<PulseRadioResponse>(`/api/pulse/music/radio?limit=${encodeURIComponent(String(limit))}`);
  return (response.items || []).map(normalizeTrack).filter((track): track is PulseRadioTrack => Boolean(track));
}

export async function recordPulseRadioPlay(trackId: string) {
  if (!trackId) return;
  await pulseApi(`/api/pulse/music/${encodeURIComponent(trackId)}/event`, {
    method: "POST",
    body: JSON.stringify({ event_type: "play", surface: "native_home_radio" })
  });
}

function normalizeTrack(input: Record<string, unknown>): PulseRadioTrack | null {
  const id = String(input.id || input.track_id || "").trim();
  const audioUrl = absoluteMediaUrl(String(input.audio_url || input.preview_url || ""));
  if (!id || !audioUrl) return null;
  return {
    id,
    title: String(input.title || "PulseSoc Radio").trim(),
    artist: String(input.artist || "PulseSoc Music").trim(),
    audioUrl,
    coverArtUrl: absoluteMediaUrl(String(input.cover_art_url || input.cover_url || "")) || undefined
  };
}

function absoluteMediaUrl(value: string) {
  const url = value.trim();
  if (!url) return "";
  if (/^https?:\/\//i.test(url)) return url;
  return `${PULSE_API_BASE_URL}${url.startsWith("/") ? "" : "/"}${url}`;
}
