import { useEffect, useState } from "react";
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { searchPulseMusic, PulseMusicTrack } from "../api/music";
import type { PulseRadioState } from "../core/pulseRadio";
import { colors } from "../theme/colors";
import { VideoMixSettings, VideoMusicSource, videoMusicSourceFromRadio, videoMusicSourceFromTrack } from "./videoMusicMix";

type Props = {
  visible: boolean;
  radio: PulseRadioState;
  selected: VideoMusicSource | null;
  settings: VideoMixSettings;
  onClose: () => void;
  onSelect: (source: VideoMusicSource) => void;
  onRemove: () => void;
  onSettings: (settings: VideoMixSettings) => void;
};

export function VideoMusicPicker({ visible, radio, selected, settings, onClose, onSelect, onRemove, onSettings }: Props) {
  const [query, setQuery] = useState("");
  const [tracks, setTracks] = useState<PulseMusicTrack[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!visible) return;
    loadTracks("");
  }, [visible]);

  async function loadTracks(search: string) {
    setLoading(true);
    setError("");
    try {
      const result = await searchPulseMusic({ query: search.trim(), lane: search.trim() ? "" : "trending", limit: 30 });
      if (!result.surfaces.includes("video")) throw new Error("PulseSoc Music is not currently enabled for video creation.");
      setTracks(result.tracks.filter((track) => track.active && track.moderationStatus === "approved" && Boolean(track.audioUrl || track.previewUrl)));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Music could not load.");
    } finally {
      setLoading(false);
    }
  }

  const radioSource = videoMusicSourceFromRadio(radio);
  return (
    <Modal visible={visible} animationType="slide" presentationStyle="pageSheet" onRequestClose={onClose}>
      <View style={styles.root}>
        <View style={styles.header}>
          <View><Text style={styles.eyebrow}>VIDEO SOUND</Text><Text style={styles.title}>PulseSoc Music</Text></View>
          <Pressable accessibilityRole="button" style={styles.close} onPress={onClose}><Text style={styles.closeText}>Done</Text></Pressable>
        </View>
        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          {selected ? (
            <View style={styles.mixer}>
              <Text style={styles.eyebrow}>{selected.kind === "pulse_radio" ? "PULSE RADIO" : "CURRENT TRACK"}</Text>
              <Text numberOfLines={1} style={styles.trackTitle}>{selected.title}</Text>
              <Text numberOfLines={1} style={styles.artist}>{selected.artist}</Text>
              <Level label="Music" value={settings.musicVolume} onChange={(musicVolume) => onSettings({ ...settings, musicVolume })} />
              <Level label="Mic" value={settings.micVolume} onChange={(micVolume) => onSettings({ ...settings, micVolume })} />
              <Text style={styles.note}>Levels use safe internal headroom. The final file uses the digital source, not speaker pickup.</Text>
              <Pressable accessibilityRole="button" onPress={onRemove}><Text style={styles.remove}>Remove music</Text></Pressable>
            </View>
          ) : null}
          {radioSource && radio.status === "playing" ? (
            <Pressable accessibilityRole="button" style={styles.radio} onPress={() => onSelect(radioSource)}>
              <View style={styles.radioBadge}><Text style={styles.radioBadgeText}>LIVE</Text></View>
              <View style={styles.grow}><Text style={styles.trackTitle} numberOfLines={1}>{radioSource.title}</Text><Text style={styles.artist} numberOfLines={1}>{radioSource.artist} · Pulse Radio</Text></View>
              <Text style={styles.use}>Use</Text>
            </Pressable>
          ) : null}
          <View style={styles.searchRow}>
            <TextInput value={query} onChangeText={setQuery} onSubmitEditing={() => loadTracks(query)} placeholder="Search songs or artists" placeholderTextColor={colors.muted} style={styles.search} />
            <Pressable accessibilityRole="button" style={styles.searchButton} onPress={() => loadTracks(query)}><Text style={styles.searchButtonText}>Search</Text></Pressable>
          </View>
          <Text style={styles.sectionTitle}>{query ? "Results" : "Trending for Video"}</Text>
          {loading ? <ActivityIndicator color={colors.accent} /> : null}
          {error ? <Text style={styles.error}>{error}</Text> : null}
          {tracks.map((track) => (
            <Pressable accessibilityRole="button" key={track.id} style={styles.track} onPress={() => onSelect(videoMusicSourceFromTrack(track))}>
              <View style={styles.art}><Text style={styles.artText}>♪</Text></View>
              <View style={styles.grow}><Text style={styles.trackTitle} numberOfLines={1}>{track.title}</Text><Text style={styles.artist} numberOfLines={1}>{track.artist} · {track.licenseLabel}</Text></View>
              <Text style={styles.use}>{selected?.trackId === track.id ? "Using" : "Use"}</Text>
            </Pressable>
          ))}
        </ScrollView>
      </View>
    </Modal>
  );
}

function Level({ label, value, onChange }: { label: string; value: number; onChange: (value: number) => void }) {
  const percent = Math.round(value * 100);
  return <View style={styles.level}><Text style={styles.levelLabel}>{label}</Text><Pressable accessibilityRole="button" accessibilityLabel={`Lower ${label}`} style={styles.step} onPress={() => onChange(Math.max(0, value - 0.1))}><Text style={styles.stepText}>−</Text></Pressable><Text style={styles.percent}>{percent}%</Text><Pressable accessibilityRole="button" accessibilityLabel={`Raise ${label}`} style={styles.step} onPress={() => onChange(Math.min(1, value + 0.1))}><Text style={styles.stepText}>+</Text></Pressable></View>;
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background }, content: { padding: 18, gap: 12, paddingBottom: 40 },
  header: { alignItems: "center", flexDirection: "row", justifyContent: "space-between", padding: 18, borderBottomColor: colors.border, borderBottomWidth: 1 },
  eyebrow: { color: colors.accent, fontSize: 11, fontWeight: "900", letterSpacing: 1.3 }, title: { color: colors.text, fontSize: 24, fontWeight: "900" },
  close: { backgroundColor: colors.accent, borderRadius: 18, paddingHorizontal: 16, paddingVertical: 9 }, closeText: { color: colors.background, fontWeight: "900" },
  mixer: { backgroundColor: colors.surface, borderColor: colors.accent, borderRadius: 16, borderWidth: 1, padding: 16, gap: 7 },
  radio: { alignItems: "center", backgroundColor: "rgba(14,44,63,.92)", borderColor: colors.accent, borderRadius: 14, borderWidth: 1, flexDirection: "row", gap: 12, padding: 13 },
  radioBadge: { backgroundColor: colors.danger, borderRadius: 6, paddingHorizontal: 7, paddingVertical: 4 }, radioBadgeText: { color: "white", fontSize: 10, fontWeight: "900" },
  grow: { flex: 1, minWidth: 0 }, trackTitle: { color: colors.text, fontSize: 15, fontWeight: "900" }, artist: { color: colors.muted, fontSize: 12, marginTop: 3 },
  searchRow: { flexDirection: "row", gap: 8 }, search: { flex: 1, minHeight: 46, backgroundColor: colors.surface, borderColor: colors.border, borderRadius: 10, borderWidth: 1, color: colors.text, paddingHorizontal: 12 },
  searchButton: { alignItems: "center", backgroundColor: colors.accent, borderRadius: 10, justifyContent: "center", paddingHorizontal: 14 }, searchButtonText: { color: colors.background, fontWeight: "900" },
  sectionTitle: { color: colors.text, fontSize: 18, fontWeight: "900", marginTop: 6 }, track: { alignItems: "center", borderBottomColor: colors.border, borderBottomWidth: 1, flexDirection: "row", gap: 12, paddingVertical: 11 },
  art: { alignItems: "center", backgroundColor: colors.surface, borderRadius: 10, height: 44, justifyContent: "center", width: 44 }, artText: { color: colors.accent, fontSize: 22 }, use: { color: colors.accent, fontWeight: "900" },
  level: { alignItems: "center", flexDirection: "row", gap: 10, marginTop: 8 }, levelLabel: { color: colors.text, flex: 1, fontWeight: "800" }, step: { alignItems: "center", backgroundColor: colors.background, borderColor: colors.border, borderRadius: 18, borderWidth: 1, height: 36, justifyContent: "center", width: 36 }, stepText: { color: colors.text, fontSize: 22 }, percent: { color: colors.text, fontVariant: ["tabular-nums"], minWidth: 42, textAlign: "center" },
  note: { color: colors.muted, fontSize: 11, lineHeight: 16, marginTop: 5 }, remove: { color: colors.danger, fontWeight: "800", marginTop: 7 }, error: { color: colors.danger, fontWeight: "700" }
});
