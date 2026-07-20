import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { Ionicons } from "@expo/vector-icons";
import { Audio } from "expo-av";
import * as DocumentPicker from "expo-document-picker";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  Share,
  StyleSheet,
  Switch,
  Text,
  TextInput,
  View
} from "react-native";
import {
  loadCachedPulseMusicSnapshot,
  PulseMusicLane,
  PulseMusicTrack,
  PulseMusicUploadAsset,
  pulseMusicWebUrl,
  recordPulseMusicEvent,
  reportPulseMusic,
  searchPulseMusic,
  selectPulseMusicForSurface,
  uploadPulseMusic
} from "../api/music";
import { getMyProfile, PulseProfile } from "../api/profile";
import {
  cyclePulseRadioRepeatMode,
  getPulseRadioState,
  playNextTrack,
  playPreviousTrack,
  PulseRadioState,
  seekPulseRadioBy,
  subscribePulseRadio,
  togglePulseRadio,
  togglePulseRadioShuffle
} from "../core/pulseRadio";
import { claimMediaPlayback, releaseMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";

type Props = NativeStackScreenProps<RootStackParamList, "Music">;

type UploadDraft = {
  audio: PulseMusicUploadAsset | null;
  cover: PulseMusicUploadAsset | null;
  title: string;
  artist: string;
  genre: string;
  language: string;
  mood: string;
  description: string;
  tags: string;
  rightsConfirmed: boolean;
};

const lanes: Array<{ key: PulseMusicLane; label: string }> = [
  { key: "", label: "Best match" },
  { key: "trending", label: "Trending" },
  { key: "new", label: "New releases" }
];

const emptyDraft: UploadDraft = {
  audio: null,
  cover: null,
  title: "",
  artist: "",
  genre: "",
  language: "",
  mood: "",
  description: "",
  tags: "",
  rightsConfirmed: false
};

const PREVIEW_OWNER = "native-music-preview";

export function MusicScreen({ route, navigation }: Props) {
  const initialTrackId = String(route.params?.trackId || route.params?.track || "");
  const [tracks, setTracks] = useState<PulseMusicTrack[]>([]);
  const [profile, setProfile] = useState<PulseProfile | null>(null);
  const [query, setQuery] = useState("");
  const [genre, setGenre] = useState("");
  const [language, setLanguage] = useState("");
  const [mood, setMood] = useState("");
  const [lane, setLane] = useState<PulseMusicLane>(initialTrackId ? "" : "trending");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [message, setMessage] = useState("");
  const [busyTrackId, setBusyTrackId] = useState("");
  const [previewingTrackId, setPreviewingTrackId] = useState("");
  const [uploading, setUploading] = useState(false);
  const [draft, setDraft] = useState<UploadDraft>(emptyDraft);
  const [radio, setRadio] = useState<PulseRadioState>(getPulseRadioState());
  const previewSound = useRef<Audio.Sound | null>(null);

  const focusedTracks = useMemo(() => {
    if (!initialTrackId) return tracks;
    const focusIndex = tracks.findIndex((track) => track.id === initialTrackId);
    if (focusIndex <= 0) return tracks;
    return [tracks[focusIndex], ...tracks.slice(0, focusIndex), ...tracks.slice(focusIndex + 1)];
  }, [initialTrackId, tracks]);

  const uploaderName = profile?.display_name || profile?.username || "";
  const uploadReadyHint = Boolean(
    profile?.verified_badge ||
      String(profile?.premium_status || "").toLowerCase().includes("premium") ||
      String(profile?.premium_status || "").toLowerCase().includes("founder") ||
      Number((profile as Record<string, unknown> | null)?.email_verified || 0)
  );

  const load = useCallback(
    async (mode: "initial" | "refresh" | "search" = "initial") => {
      setMessage("");
      setOffline(false);
      if (mode === "initial") setLoading(true);
      if (mode === "refresh") setRefreshing(true);
      try {
        const result = await searchPulseMusic({ query, genre, language, mood, lane, limit: 40 });
        setTracks(result.tracks);
        if (!result.tracks.length) setMessage("No approved tracks matched this search.");
      } catch (error) {
        const cached = await loadCachedPulseMusicSnapshot();
        if (cached.length) {
          setTracks(cached);
          setOffline(true);
          setMessage("Showing cached PulseSoc Music. Reconnect to upload or refresh the pool.");
        } else {
          setMessage(error instanceof Error ? error.message : "PulseSoc Music could not load.");
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [genre, language, lane, mood, query]
  );

  useEffect(() => {
    getMyProfile()
      .then((next) => {
        setProfile(next);
        setDraft((current) => (current.artist ? current : { ...current, artist: next.display_name || next.username || "" }));
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => load("search").catch(() => undefined), 360);
    return () => clearTimeout(timer);
  }, [query, genre, language, mood, lane]);

  useEffect(() => {
    return () => {
      stopPreview().catch(() => undefined);
    };
  }, []);

  useEffect(() => subscribePulseRadio(setRadio), []);

  async function stopPreview() {
    const sound = previewSound.current;
    previewSound.current = null;
    setPreviewingTrackId("");
    if (sound) await sound.unloadAsync().catch(() => undefined);
    await releaseMediaPlayback(PREVIEW_OWNER).catch(() => undefined);
  }

  async function previewTrack(track: PulseMusicTrack) {
    if (previewingTrackId === track.id) {
      await stopPreview();
      return;
    }
    const url = track.previewUrl || track.audioUrl;
    if (!url) {
      setMessage("Preview is not available for this track yet.");
      return;
    }
    await stopPreview();
    const granted = await claimMediaPlayback({ id: PREVIEW_OWNER, kind: "music_preview", pause: () => stopPreview(), stop: () => stopPreview() });
    if (!granted) {
      setMessage("Another media surface is active. Pause it before previewing music.");
      return;
    }
    setBusyTrackId(track.id);
    setMessage(`Previewing ${track.title}.`);
    try {
      await Audio.setAudioModeAsync({ allowsRecordingIOS: false, playsInSilentModeIOS: true, staysActiveInBackground: false });
      const created = await Audio.Sound.createAsync({ uri: url }, { shouldPlay: true, volume: 0.82 }, (status) => {
        if (status.isLoaded && status.didJustFinish) stopPreview().catch(() => undefined);
      });
      previewSound.current = created.sound;
      setPreviewingTrackId(track.id);
      await recordPulseMusicEvent(track.id, "play", "native_music_library").catch(() => undefined);
    } catch (error) {
      await releaseMediaPlayback(PREVIEW_OWNER).catch(() => undefined);
      setMessage(error instanceof Error ? error.message : "Preview could not play.");
    } finally {
      setBusyTrackId("");
    }
  }

  async function saveTrack(track: PulseMusicTrack) {
    setBusyTrackId(track.id);
    setMessage("");
    try {
      await recordPulseMusicEvent(track.id, "save", "native_music_library");
      setTracks((current) => current.map((item) => (item.id === track.id ? { ...item, saveCount: item.saveCount + 1 } : item)));
      setMessage("Song saved to your PulseSoc sounds.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Song could not be saved.");
    } finally {
      setBusyTrackId("");
    }
  }

  async function shareTrack(track: PulseMusicTrack) {
    setBusyTrackId(track.id);
    setMessage("");
    try {
      await recordPulseMusicEvent(track.id, "share", "native_music_library").catch(() => undefined);
      await Share.share({ title: track.title, message: `${track.title} · ${track.artist}\n${pulseMusicWebUrl(track.id)}` });
      setMessage("Music share opened.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Share could not open.");
    } finally {
      setBusyTrackId("");
    }
  }

  async function reportTrack(track: PulseMusicTrack) {
    Alert.alert("Report music", `Send ${track.title} to PulseSoc safety review?`, [
      { text: "Cancel", style: "cancel" },
      {
        text: "Report",
        style: "destructive",
        onPress: () => {
          setBusyTrackId(track.id);
          reportPulseMusic(track.id)
            .then(() => setMessage("Song report sent for review."))
            .catch((error) => setMessage(error instanceof Error ? error.message : "Report could not be sent."))
            .finally(() => setBusyTrackId(""));
        }
      }
    ]);
  }

  async function useTrack(track: PulseMusicTrack, surface: "reel" | "video" | "status" | "post") {
    const composerSurface = surface === "video" ? "post" : surface;
    await selectPulseMusicForSurface(track, composerSurface);
    setMessage(`Selected ${track.title} for ${composerSurface === "post" ? "the feed composer" : composerSurface}.`);
    navigation.navigate("Tabs", { screen: "Home", params: { openComposer: true, composerMode: composerSurface } });
  }

  async function pickAudio() {
    const result = await DocumentPicker.getDocumentAsync({
      copyToCacheDirectory: true,
      multiple: false,
      type: ["audio/mpeg", "audio/wav", "audio/x-wav", "audio/mp4", "audio/aac", "audio/m4a", "audio/*"]
    });
    if (result.canceled || !result.assets?.[0]) return;
    const asset = result.assets[0];
    setDraft((current) => ({
      ...current,
      audio: {
        uri: asset.uri,
        name: asset.name || `pulsesoc-music-${Date.now()}.m4a`,
        mimeType: normalizeAudioMime(asset.mimeType || asset.name || ""),
        size: asset.size || 0
      },
      title: current.title || titleFromFilename(asset.name || "")
    }));
    setMessage("Audio file selected.");
  }

  async function pickCover() {
    const result = await DocumentPicker.getDocumentAsync({
      copyToCacheDirectory: true,
      multiple: false,
      type: ["image/jpeg", "image/png", "image/webp"]
    });
    if (result.canceled || !result.assets?.[0]) return;
    const asset = result.assets[0];
    setDraft((current) => ({
      ...current,
      cover: {
        uri: asset.uri,
        name: asset.name || `pulsesoc-cover-${Date.now()}.jpg`,
        mimeType: asset.mimeType || "image/jpeg",
        size: asset.size || 0
      }
    }));
    setMessage("Cover artwork selected.");
  }

  async function uploadDraft() {
    if (!draft.audio || uploading) return;
    setUploading(true);
    setMessage("Uploading song for rights review…");
    try {
      const result = await uploadPulseMusic({
        audio: draft.audio,
        cover: draft.cover,
        title: draft.title,
        artist: draft.artist || uploaderName || "PulseSoc Artist",
        genre: draft.genre,
        language: draft.language,
        mood: draft.mood,
        description: draft.description,
        tags: draft.tags,
        rightsConfirmed: draft.rightsConfirmed
      });
      setMessage(result.message || "Song uploaded for admin review.");
      setDraft({ ...emptyDraft, artist: uploaderName });
      await load("refresh");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Music upload failed.");
    } finally {
      setUploading(false);
    }
  }

  if (loading && !tracks.length) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading PulseSoc Music</Text>
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <FlatList
        data={focusedTracks}
        keyExtractor={(item) => item.id}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
        contentContainerStyle={styles.content}
        keyboardShouldPersistTaps="handled"
        ListHeaderComponent={
          <View style={styles.header}>
            <View style={styles.hero}>
              <View style={styles.heroCopy}>
                <Text style={styles.kicker}>PulseSoc Music</Text>
                <Text style={styles.title}>Artist-uploaded music for Reels, Videos, and Status.</Text>
                <Text style={styles.subtitle}>
                  Upload, discover, preview, report, and attach rights-confirmed music to PulseSoc content.
                </Text>
              </View>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={radio.status === "playing" ? "Pause PulseSoc Radio" : radio.userWantsPlayback && radio.interruptedBy ? "Keep PulseSoc Radio paused" : "Play PulseSoc Radio"}
                accessibilityHint={radio.userWantsPlayback && radio.interruptedBy ? "Prevents PulseSoc Radio from resuming after active audio ends." : "PulseSoc Radio continues across native screens after playback starts."}
                style={styles.radioCard}
                onPress={() => togglePulseRadio().catch((error) => setMessage(error instanceof Error ? error.message : "Pulse Radio could not start."))}
              >
                <Text style={styles.radioIcon}>{radio.status === "playing" ? "Ⅱ" : radio.status === "connecting" || radio.status === "buffering" ? "…" : "▶"}</Text>
                <View style={styles.radioCopy}>
                  <Text style={styles.radioTitle}>Pulse Radio</Text>
                  <Text style={styles.radioBody} numberOfLines={2}>{radio.message || "Approved music pool"}</Text>
                  <Waveform waveform={radio.track ? [0.18, 0.38, 0.66, 0.42, 0.72, 0.5, 0.3, 0.58] : [0.12, 0.22, 0.3, 0.18, 0.28, 0.2]} active={radio.status === "playing" || radio.status === "buffering"} />
                </View>
              </Pressable>

              {radio.track ? (
                <View style={styles.radioControls}>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Previous track"
                    testID="music-radio-previous"
                    style={styles.radioControlButton}
                    onPress={() => playPreviousTrack().catch(() => undefined)}
                  >
                    <Ionicons name="play-skip-back" size={18} color={colors.text} />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Seek backward 15 seconds"
                    testID="music-radio-seek-back"
                    style={styles.radioControlButton}
                    onPress={() => seekPulseRadioBy(-15000).catch(() => undefined)}
                  >
                    <Ionicons name="play-back" size={16} color={colors.text} />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Seek forward 15 seconds"
                    testID="music-radio-seek-forward"
                    style={styles.radioControlButton}
                    onPress={() => seekPulseRadioBy(15000).catch(() => undefined)}
                  >
                    <Ionicons name="play-forward" size={16} color={colors.text} />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Next track"
                    testID="music-radio-next"
                    style={styles.radioControlButton}
                    onPress={() => playNextTrack().catch(() => undefined)}
                  >
                    <Ionicons name="play-skip-forward" size={18} color={colors.text} />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={radio.shuffle ? "Disable shuffle" : "Enable shuffle"}
                    testID="music-radio-shuffle"
                    style={[styles.radioControlButton, radio.shuffle && styles.radioControlButtonActive]}
                    onPress={() => togglePulseRadioShuffle()}
                  >
                    <Ionicons name="shuffle" size={16} color={radio.shuffle ? colors.background : colors.text} />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel={radio.repeatMode === "one" ? "Repeat one track" : radio.repeatMode === "queue" ? "Repeat queue" : "Repeat off"}
                    testID="music-radio-repeat"
                    style={[styles.radioControlButton, radio.repeatMode !== "off" && styles.radioControlButtonActive]}
                    onPress={() => cyclePulseRadioRepeatMode()}
                  >
                    <Ionicons
                      name={radio.repeatMode === "one" ? "repeat-outline" : "repeat"}
                      size={16}
                      color={radio.repeatMode !== "off" ? colors.background : colors.text}
                    />
                  </Pressable>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="View and manage queue"
                    testID="music-radio-open-queue"
                    style={[styles.radioControlButton, styles.radioControlButtonWide]}
                    onPress={() => navigation.navigate("PulseQueue", { title: "Queue" })}
                  >
                    <Ionicons name="list" size={16} color={colors.text} />
                    <Text style={styles.radioControlLabel}>Queue</Text>
                  </Pressable>
                </View>
              ) : null}
            </View>

            <View style={styles.uploadPanel}>
              <View style={styles.panelHeader}>
                <View>
                  <Text style={styles.panelKicker}>Music Upload Portal</Text>
                  <Text style={styles.panelTitle}>Upload for Review</Text>
                </View>
                <Text style={[styles.statusPill, uploadReadyHint && styles.statusPillReady]}>{uploadReadyHint ? "Ready" : "Server verified"}</Text>
              </View>
              <Text style={styles.muted}>Supported: MP3, WAV, M4A, AAC. Uploaded songs require rights review before public use.</Text>
              <View style={styles.fileRow}>
                <Pressable accessibilityRole="button" accessibilityLabel="Choose audio file" style={styles.fileButton} disabled={uploading} onPress={() => pickAudio().catch((error) => setMessage(error instanceof Error ? error.message : "Audio picker failed."))}>
                  <Text style={styles.fileButtonTitle}>Audio file</Text>
                  <Text style={styles.fileButtonMeta} numberOfLines={1}>{draft.audio?.name || "Choose MP3, WAV, M4A, or AAC"}</Text>
                </Pressable>
                <Pressable accessibilityRole="button" accessibilityLabel="Choose cover artwork" style={styles.fileButton} disabled={uploading} onPress={() => pickCover().catch((error) => setMessage(error instanceof Error ? error.message : "Cover picker failed."))}>
                  <Text style={styles.fileButtonTitle}>Cover artwork</Text>
                  <Text style={styles.fileButtonMeta} numberOfLines={1}>{draft.cover?.name || "Optional JPG, PNG, WEBP"}</Text>
                </Pressable>
              </View>
              <TextInput style={styles.input} value={draft.title} onChangeText={(title) => setDraft((current) => ({ ...current, title }))} placeholder="Song title" placeholderTextColor={colors.muted} editable={!uploading} />
              <TextInput style={styles.input} value={draft.artist} onChangeText={(artist) => setDraft((current) => ({ ...current, artist }))} placeholder="Artist name" placeholderTextColor={colors.muted} editable={!uploading} />
              <View style={styles.inputGrid}>
                <TextInput style={[styles.input, styles.gridInput]} value={draft.genre} onChangeText={(genre) => setDraft((current) => ({ ...current, genre }))} placeholder="Genre" placeholderTextColor={colors.muted} editable={!uploading} />
                <TextInput style={[styles.input, styles.gridInput]} value={draft.language} onChangeText={(nextLanguage) => setDraft((current) => ({ ...current, language: nextLanguage }))} placeholder="Language" placeholderTextColor={colors.muted} editable={!uploading} />
                <TextInput style={[styles.input, styles.gridInput]} value={draft.mood} onChangeText={(nextMood) => setDraft((current) => ({ ...current, mood: nextMood }))} placeholder="Mood" placeholderTextColor={colors.muted} editable={!uploading} />
              </View>
              <TextInput style={[styles.input, styles.textArea]} value={draft.description} onChangeText={(description) => setDraft((current) => ({ ...current, description }))} placeholder="Description" placeholderTextColor={colors.muted} multiline editable={!uploading} />
              <TextInput style={styles.input} value={draft.tags} onChangeText={(tags) => setDraft((current) => ({ ...current, tags }))} placeholder="#drill #kompa #lofi" placeholderTextColor={colors.muted} editable={!uploading} />
              <View style={styles.rightsRow}>
                <Switch value={draft.rightsConfirmed} onValueChange={(rightsConfirmed) => setDraft((current) => ({ ...current, rightsConfirmed }))} disabled={uploading} thumbColor={draft.rightsConfirmed ? colors.accent : colors.muted} trackColor={{ false: colors.border, true: colors.signalDim }} />
                <Text style={styles.rightsText}>I confirm that I own this music or have the legal right to upload it.</Text>
              </View>
              <Pressable accessibilityRole="button" accessibilityLabel="Upload music for review" style={[styles.uploadButton, (!draft.audio || uploading) && styles.disabled]} disabled={!draft.audio || uploading} onPress={uploadDraft}>
                {uploading ? <ActivityIndicator color={colors.background} /> : <Text style={styles.uploadButtonText}>Upload for Review</Text>}
              </Pressable>
            </View>

            <View style={styles.searchPanel}>
              <Text style={styles.panelKicker}>Music Library</Text>
              <View style={styles.searchRow}>
                <TextInput style={styles.searchInput} value={query} onChangeText={setQuery} placeholder="Search artist or song title" placeholderTextColor={colors.muted} returnKeyType="search" onSubmitEditing={() => load("search").catch(() => undefined)} />
                <Pressable accessibilityRole="button" accessibilityLabel="Search music" style={styles.searchButton} onPress={() => load("search").catch(() => undefined)}>
                  <Text style={styles.searchButtonText}>Search</Text>
                </Pressable>
              </View>
              <View style={styles.inputGrid}>
                <TextInput style={[styles.input, styles.gridInput]} value={genre} onChangeText={setGenre} placeholder="Genre" placeholderTextColor={colors.muted} />
                <TextInput style={[styles.input, styles.gridInput]} value={language} onChangeText={setLanguage} placeholder="Language" placeholderTextColor={colors.muted} />
                <TextInput style={[styles.input, styles.gridInput]} value={mood} onChangeText={setMood} placeholder="Mood" placeholderTextColor={colors.muted} />
              </View>
              <View style={styles.laneRow}>
                {lanes.map((item) => (
                  <Pressable key={item.key || "best"} style={[styles.laneChip, lane === item.key && styles.laneChipActive]} onPress={() => setLane(item.key)} accessibilityRole="button" accessibilityState={{ selected: lane === item.key }}>
                    <Text style={[styles.laneText, lane === item.key && styles.laneTextActive]}>{item.label}</Text>
                  </Pressable>
                ))}
              </View>
              {message ? <Text accessibilityLiveRegion="polite" style={[styles.message, offline && styles.warningMessage]}>{message}</Text> : null}
            </View>
          </View>
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>No tracks found</Text>
            <Text style={styles.emptyText}>Approved PulseSoc Music will appear here after review.</Text>
          </View>
        }
        renderItem={({ item }) => (
          <TrackCard
            track={item}
            busy={busyTrackId === item.id}
            previewing={previewingTrackId === item.id}
            highlighted={initialTrackId === item.id}
            onPreview={previewTrack}
            onSave={saveTrack}
            onShare={shareTrack}
            onReport={reportTrack}
            onUse={useTrack}
          />
        )}
      />
    </View>
  );
}

function TrackCard({
  track,
  busy,
  previewing,
  highlighted,
  onPreview,
  onSave,
  onShare,
  onReport,
  onUse
}: {
  track: PulseMusicTrack;
  busy: boolean;
  previewing: boolean;
  highlighted: boolean;
  onPreview: (track: PulseMusicTrack) => void | Promise<void>;
  onSave: (track: PulseMusicTrack) => void | Promise<void>;
  onShare: (track: PulseMusicTrack) => void | Promise<void>;
  onReport: (track: PulseMusicTrack) => void | Promise<void>;
  onUse: (track: PulseMusicTrack, surface: "reel" | "video" | "status" | "post") => void | Promise<void>;
}) {
  return (
    <View style={[styles.trackCard, highlighted && styles.trackCardHighlighted]}>
      <View style={styles.trackTop}>
        <View style={styles.cover}>
          {track.coverArtUrl ? <Image source={{ uri: track.coverArtUrl }} style={styles.coverImage} /> : <Text style={styles.coverIcon}>♪</Text>}
        </View>
        <View style={styles.trackBody}>
          <Text style={styles.trackTitle} numberOfLines={1}>{track.title}</Text>
          <Text style={styles.trackMeta} numberOfLines={1}>{track.artist} · {track.genre} · {track.language} · {track.mood}</Text>
          <Waveform waveform={track.waveform} active={previewing} />
          <Text style={styles.trackStats}>{track.playCount} plays · {track.usageCount} uses · trend {track.trendScore}</Text>
        </View>
      </View>
      <View style={styles.actionRow}>
        <ActionButton label={previewing ? "Stop" : "Preview"} disabled={busy} onPress={() => onPreview(track)} />
        <ActionButton label="Save" disabled={busy} onPress={() => onSave(track)} />
        <ActionButton label="Share" disabled={busy} onPress={() => onShare(track)} />
        <ActionButton label="Report" disabled={busy} warning onPress={() => onReport(track)} />
      </View>
      <View style={styles.useRow}>
        <ActionButton label="Use in Reel" primary onPress={() => onUse(track, "reel")} />
        <ActionButton label="Use in Video" onPress={() => onUse(track, "video")} />
        <ActionButton label="Use in Status" onPress={() => onUse(track, "status")} />
      </View>
    </View>
  );
}

function ActionButton({ label, onPress, primary, warning, disabled }: { label: string; onPress: () => void; primary?: boolean; warning?: boolean; disabled?: boolean }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={label} disabled={disabled} style={[styles.actionButton, primary && styles.actionPrimary, warning && styles.actionWarning, disabled && styles.disabled]} onPress={onPress}>
      <Text style={[styles.actionText, primary && styles.actionPrimaryText, warning && styles.actionWarningText]}>{label}</Text>
    </Pressable>
  );
}

function Waveform({ waveform, active }: { waveform: number[]; active?: boolean }) {
  const bars = waveform.length ? waveform : [0.16, 0.32, 0.48, 0.62, 0.44, 0.7, 0.56, 0.38];
  return (
    <View style={styles.wave} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      {bars.slice(0, 16).map((bar, index) => (
        <View key={`${index}-${bar}`} style={[styles.waveBar, { height: 8 + Math.max(0.08, Math.min(bar, 1)) * 22 }, active && styles.waveBarActive]} />
      ))}
    </View>
  );
}

function titleFromFilename(name: string) {
  return String(name || "")
    .replace(/\.[a-z0-9]+$/i, "")
    .replace(/[-_]+/g, " ")
    .trim();
}

function normalizeAudioMime(value: string) {
  const text = String(value || "").toLowerCase();
  if (text.includes("wav")) return "audio/wav";
  if (text.includes("aac")) return "audio/aac";
  if (text.includes("mp3") || text.includes("mpeg")) return "audio/mpeg";
  return "audio/mp4";
}

const styles = StyleSheet.create({
  actionButton: {
    alignItems: "center",
    borderColor: "rgba(121,210,255,0.24)",
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    flexGrow: 1,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  actionPrimary: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  actionPrimaryText: {
    color: colors.background
  },
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  actionText: {
    color: colors.text,
    fontWeight: "900"
  },
  actionWarning: {
    borderColor: "rgba(255,95,126,0.36)"
  },
  actionWarningText: {
    color: colors.danger
  },
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.text,
    fontWeight: "900",
    marginTop: 12
  },
  content: {
    gap: 14,
    padding: 16,
    paddingBottom: 116
  },
  cover: {
    alignItems: "center",
    backgroundColor: "rgba(50,230,179,0.12)",
    borderColor: "rgba(50,230,179,0.34)",
    borderRadius: 18,
    borderWidth: 1,
    height: 72,
    justifyContent: "center",
    overflow: "hidden",
    width: 72
  },
  coverIcon: {
    color: colors.accent,
    fontSize: 31,
    fontWeight: "900"
  },
  coverImage: {
    height: "100%",
    width: "100%"
  },
  disabled: {
    opacity: 0.52
  },
  empty: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 22,
    borderWidth: 1,
    padding: 28
  },
  emptyText: {
    color: colors.muted,
    marginTop: 6,
    textAlign: "center"
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900"
  },
  fileButton: {
    borderColor: "rgba(121,210,255,0.24)",
    borderRadius: 18,
    borderWidth: 1,
    flex: 1,
    gap: 5,
    minHeight: 72,
    padding: 12
  },
  fileButtonMeta: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "700"
  },
  fileButtonTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  fileRow: {
    flexDirection: "row",
    gap: 10
  },
  gridInput: {
    flex: 1,
    minWidth: 96
  },
  header: {
    gap: 14
  },
  hero: {
    backgroundColor: "rgba(7,18,31,0.88)",
    borderColor: "rgba(50,230,179,0.24)",
    borderRadius: 28,
    borderWidth: 1,
    gap: 16,
    overflow: "hidden",
    padding: 18
  },
  heroCopy: {
    gap: 8
  },
  input: {
    backgroundColor: "rgba(5,12,22,0.72)",
    borderColor: "rgba(121,210,255,0.2)",
    borderRadius: 15,
    borderWidth: 1,
    color: colors.text,
    minHeight: 48,
    paddingHorizontal: 13,
    paddingVertical: 10
  },
  inputGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 9
  },
  kicker: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 3,
    textTransform: "uppercase"
  },
  laneChip: {
    borderColor: colors.border,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    minHeight: 40,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  laneChipActive: {
    backgroundColor: colors.signalDim,
    borderColor: colors.accent
  },
  laneRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  laneText: {
    color: colors.muted,
    fontWeight: "900"
  },
  laneTextActive: {
    color: colors.accent
  },
  message: {
    color: colors.accent,
    fontWeight: "800",
    lineHeight: 20
  },
  muted: {
    color: colors.muted,
    lineHeight: 20
  },
  panelHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    gap: 12
  },
  panelKicker: {
    color: colors.accentStrong,
    fontSize: 11,
    fontWeight: "900",
    letterSpacing: 2,
    textTransform: "uppercase"
  },
  panelTitle: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900"
  },
  radioBody: {
    color: colors.accent,
    fontWeight: "800"
  },
  radioCard: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.055)",
    borderColor: "rgba(121,210,255,0.24)",
    borderRadius: 24,
    borderWidth: 1,
    flexDirection: "row",
    gap: 14,
    padding: 14
  },
  radioControlButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.06)",
    borderColor: "rgba(121,210,255,0.2)",
    borderRadius: 999,
    borderWidth: 1,
    flexDirection: "row",
    gap: 4,
    height: 36,
    justifyContent: "center",
    width: 36
  },
  radioControlButtonActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  radioControlButtonWide: {
    paddingHorizontal: 12,
    width: "auto"
  },
  radioControlLabel: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  radioControls: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 10
  },
  radioCopy: {
    flex: 1,
    gap: 4
  },
  radioIcon: {
    backgroundColor: colors.accent,
    borderRadius: 999,
    color: colors.background,
    fontSize: 20,
    fontWeight: "900",
    height: 54,
    lineHeight: 54,
    overflow: "hidden",
    textAlign: "center",
    width: 54
  },
  radioTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900",
    letterSpacing: 2,
    textTransform: "uppercase"
  },
  rightsRow: {
    alignItems: "center",
    backgroundColor: "rgba(243,196,97,0.1)",
    borderColor: "rgba(243,196,97,0.28)",
    borderRadius: 18,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    padding: 10
  },
  rightsText: {
    color: colors.text,
    flex: 1,
    fontWeight: "800",
    lineHeight: 20
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  searchButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 15,
    justifyContent: "center",
    minHeight: 48,
    paddingHorizontal: 16
  },
  searchButtonText: {
    color: colors.background,
    fontWeight: "900"
  },
  searchInput: {
    backgroundColor: "rgba(5,12,22,0.72)",
    borderColor: "rgba(121,210,255,0.2)",
    borderRadius: 15,
    borderWidth: 1,
    color: colors.text,
    flex: 1,
    minHeight: 48,
    paddingHorizontal: 13
  },
  searchPanel: {
    backgroundColor: colors.glass,
    borderColor: "rgba(121,210,255,0.2)",
    borderRadius: 24,
    borderWidth: 1,
    gap: 11,
    padding: 14
  },
  searchRow: {
    flexDirection: "row",
    gap: 9
  },
  statusPill: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    overflow: "hidden",
    paddingHorizontal: 10,
    paddingVertical: 7
  },
  statusPillReady: {
    borderColor: colors.accent,
    color: colors.accent
  },
  subtitle: {
    color: colors.muted,
    fontSize: 15,
    fontWeight: "700",
    lineHeight: 22
  },
  textArea: {
    minHeight: 90,
    textAlignVertical: "top"
  },
  title: {
    color: colors.text,
    fontSize: 29,
    fontWeight: "900",
    lineHeight: 34
  },
  trackBody: {
    flex: 1,
    gap: 4
  },
  trackCard: {
    backgroundColor: "rgba(11,24,34,0.86)",
    borderColor: "rgba(121,210,255,0.18)",
    borderRadius: 24,
    borderWidth: 1,
    gap: 12,
    padding: 13
  },
  trackCardHighlighted: {
    borderColor: colors.accent,
    shadowColor: colors.accent,
    shadowOpacity: 0.25,
    shadowRadius: 12
  },
  trackMeta: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "700"
  },
  trackStats: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800"
  },
  trackTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  trackTop: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12
  },
  uploadButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 18,
    justifyContent: "center",
    minHeight: 50,
    padding: 12
  },
  uploadButtonText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "900"
  },
  uploadPanel: {
    backgroundColor: colors.glassStrong,
    borderColor: "rgba(50,230,179,0.24)",
    borderRadius: 24,
    borderWidth: 1,
    gap: 11,
    padding: 14
  },
  useRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  warningMessage: {
    color: colors.warning
  },
  wave: {
    alignItems: "flex-end",
    flexDirection: "row",
    gap: 3,
    height: 34,
    marginTop: 2
  },
  waveBar: {
    backgroundColor: "rgba(97,216,255,0.72)",
    borderRadius: 999,
    width: 4
  },
  waveBarActive: {
    backgroundColor: colors.accent
  }
});
