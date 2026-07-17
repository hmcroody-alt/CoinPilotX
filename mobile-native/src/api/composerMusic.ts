import { PULSE_API_BASE_URL } from "./config";
import { pulseApi } from "./pulseApi";

export type ComposerMusicTrack = {
  id: string;
  title: string;
  artist: string;
  previewUrl: string;
  durationSeconds: number;
  licenseLabel: string;
};

type ComposerMusicResponse = {
  items?: Array<Record<string, unknown>>;
  sounds?: Array<Record<string, unknown>>;
};

export async function suggestComposerMusic(topic = "creator content") {
  const data = await pulseApi<ComposerMusicResponse>("/api/pulse/music/ai-suggest", {
    method: "POST",
    body: JSON.stringify({ topic, mood: "", genre: "", length: "" })
  });
  return (data.items || data.sounds || []).map(normalizeTrack).filter((track): track is ComposerMusicTrack => Boolean(track));
}

function normalizeTrack(input: Record<string, unknown>): ComposerMusicTrack | null {
  const id = String(input.id || input.track_id || "").trim();
  if (!id) return null;
  return {
    id,
    title: String(input.title || "Approved track"),
    artist: String(input.artist || "PulseSoc Music"),
    previewUrl: absoluteUrl(String(input.preview_url || input.audio_url || "")),
    durationSeconds: Number(input.duration_seconds || input.duration || 0),
    licenseLabel: String(input.license_type || input.license || "approved")
  };
}

function absoluteUrl(value: string) {
  const url = value.trim();
  if (!url) return "";
  if (/^https?:\/\//i.test(url)) return url;
  return `${PULSE_API_BASE_URL}${url.startsWith("/") ? "" : "/"}${url}`;
}
