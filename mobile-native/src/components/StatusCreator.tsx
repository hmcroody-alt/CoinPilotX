import { useEffect, useMemo, useRef, useState } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { Audio } from "expo-av";
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { resolvePreviewStop, resolvePreviewToggle } from "../create/musicPreviewLifecycle";
import {
  createStatus,
  generateStatusAiStory,
  listTrendingStatusMusic,
  PulseStatus,
  PulseStatusMusic,
  searchStatusMusic,
  StatusType,
  StatusVisibility
} from "../api/status";
import { consumePulseMusicSelection } from "../api/music";
import { uploadResultMediaId } from "../media/nativeMediaUpload";
import { MediaUploadPreview } from "../media/MediaUploadPreview";
import { useNativeMediaUpload } from "../media/useNativeMediaUpload";
import { colors } from "../theme/colors";

type Props = {
  visible: boolean;
  onClose: () => void;
  onCreated: (status?: PulseStatus) => void;
};

const STATUS_DRAFT_KEY = "pulsesoc.native.status.creator.draft";

const visibilityOptions = [
  { label: "Public", value: "public" },
  { label: "Followers", value: "followers" },
  { label: "Private", value: "private" }
] as const;

const durationOptions = [
  { label: "24h", value: 24 },
  { label: "48h", value: 48 },
  { label: "72h", value: 72 },
  { label: "7d", value: 168 }
] as const;

const modeOptions = [
  { label: "Text", value: "text" },
  { label: "Photo", value: "photo" },
  { label: "Video", value: "video" },
  { label: "AI", value: "ai" }
] as const;

export function StatusCreator({ visible, onClose, onCreated }: Props) {
  const insets = useSafeAreaInsets();
  const uploadOptions = useMemo(() => ({ contextType: "pulse_status", contextId: "draft" }), []);
  const mediaUpload = useNativeMediaUpload(uploadOptions);
  const [body, setBody] = useState("");
  const [mode, setMode] = useState<StatusType>("text");
  const [visibility, setVisibility] = useState<StatusVisibility>("public");
  const [durationHours, setDurationHours] = useState(24);
  const [publishing, setPublishing] = useState(false);
  const [error, setError] = useState("");
  const [musicQuery, setMusicQuery] = useState("");
  const [musicItems, setMusicItems] = useState<PulseStatusMusic[]>([]);
  const [selectedMusic, setSelectedMusic] = useState<PulseStatusMusic | null>(null);
  const [previewingTrackId, setPreviewingTrackId] = useState("");
  const musicPreviewRef = useRef<Audio.Sound | null>(null);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiBusy, setAiBusy] = useState(false);
  const canPublish = Boolean(body.trim() || mediaUpload.asset || selectedMusic || mode === "ai");

  useEffect(() => {
    if (!visible) return;
    AsyncStorage.getItem(STATUS_DRAFT_KEY).then((value) => {
      if (!value) return;
      const draft = JSON.parse(value) as { body?: string; visibility?: StatusVisibility; durationHours?: number; mode?: StatusType; aiPrompt?: string };
      setBody(draft.body || "");
      setVisibility(draft.visibility || "public");
      setDurationHours(Number(draft.durationHours || 24));
      setMode(draft.mode || "text");
      setAiPrompt(draft.aiPrompt || "");
    }).catch(() => undefined);
    listTrendingStatusMusic({ limit: 6 })
      .then((result) => setMusicItems(result.items || []))
      .catch(() => undefined);
    consumePulseMusicSelection("status")
      .then((selection) => {
        if (!selection?.track) return;
        const track = selection.track;
        const statusTrack: PulseStatusMusic = {
          id: track.id,
          track_id: track.id,
          title: track.title,
          artist: track.artist,
          audio_title: track.title,
          audio_artist: track.artist,
          audio_url: track.audioUrl,
          preview_url: track.previewUrl || track.audioUrl,
          mood: track.mood,
          genre: track.genre,
          duration_seconds: track.durationSeconds
        };
        setSelectedMusic(statusTrack);
        setMode("text");
        setBody((current) => current || `${track.title} · ${track.artist}`);
      })
      .catch(() => undefined);
  }, [visible]);

  useEffect(() => {
    if (!visible) return;
    const timer = setTimeout(() => AsyncStorage.setItem(STATUS_DRAFT_KEY, JSON.stringify({ body, visibility, durationHours, mode, aiPrompt })).catch(() => undefined), 180);
    return () => clearTimeout(timer);
  }, [aiPrompt, body, durationHours, mode, visibility, visible]);

  // Closing the Status Studio (visible -> false) is a mandated "preview ends"
  // event: a preview must never keep playing after the picker/modal is gone.
  useEffect(() => {
    if (visible) return;
    if (resolvePreviewStop(previewingTrackId).stopCurrent || musicPreviewRef.current) {
      stopMusicPreview().catch(() => undefined);
    }
  }, [visible]);

  // Unmounting must also stop any in-flight preview sound.
  useEffect(() => () => {
    resolvePreviewStop(musicPreviewRef.current ? "active" : "");
    musicPreviewRef.current?.unloadAsync().catch(() => undefined);
    musicPreviewRef.current = null;
  }, []);

  /**
   * Stop and unload any in-flight preview. Idempotent and safe to call from
   * every "preview must end" path (switch track, select, picker close, unmount).
   */
  async function stopMusicPreview() {
    const existing = musicPreviewRef.current;
    musicPreviewRef.current = null;
    setPreviewingTrackId("");
    if (existing) await existing.unloadAsync().catch(() => undefined);
  }

  /** Tapping a row's dedicated Preview/Stop control — listen before selecting. */
  async function toggleMusicPreview(track: PulseStatusMusic) {
    const trackId = String(selectedMusicTrackId(track) || track.preview_url || track.audio_url || "");
    const transition = resolvePreviewToggle(previewingTrackId, trackId);
    if (transition.stopCurrent) await stopMusicPreview();
    if (!transition.nextTrackId) return;
    const previewUri = track.preview_url || track.audio_url || "";
    if (!previewUri) {
      setError("This approved track does not have a preview available.");
      return;
    }
    try {
      const { sound } = await Audio.Sound.createAsync({ uri: previewUri }, { shouldPlay: true, volume: 0.8 });
      musicPreviewRef.current = sound;
      setPreviewingTrackId(trackId);
      sound.setOnPlaybackStatusUpdate((status) => {
        if (status.isLoaded && status.didJustFinish) stopMusicPreview().catch(() => undefined);
      });
    } catch {
      setPreviewingTrackId("");
      setError("Track preview is unavailable. You can still select another approved track.");
    }
  }

  /** Selecting (or clearing) a track stops preview first, then sets selection. */
  function chooseMusic(track: PulseStatusMusic | null) {
    stopMusicPreview().catch(() => undefined);
    setSelectedMusic(track);
  }

  async function publish() {
    if (!canPublish || publishing) {
      setError("Add text, media, music, or an AI Story before posting.");
      return;
    }
    setPublishing(true);
    setError("");
    try {
      let mediaIds: number[] = [];
      if (mediaUpload.asset) {
        const uploadResult = await mediaUpload.upload({ contextType: "pulse_status", contextId: "draft" });
        const mediaId = uploadResult ? uploadResultMediaId(uploadResult) : 0;
        if (!mediaId) throw new Error("Upload completed but media did not attach. Please retry.");
        mediaIds = [mediaId];
      }
      const statusType = inferStatusType(mode, mediaUpload.asset?.mediaType, mediaIds.length > 0, Boolean(selectedMusic));
      const result = await createStatus({
        status_type: statusType,
        body: body.trim(),
        visibility,
        duration_hours: durationHours,
        media_ids: mediaIds,
        music_track_id: selectedMusicTrackId(selectedMusic),
        ai_context: {
          source: "native_status_creator",
          editor_mode: mode,
          music_track_id: selectedMusicTrackId(selectedMusic)
        }
      });
      resetCreator();
      await AsyncStorage.removeItem(STATUS_DRAFT_KEY).catch(() => undefined);
      onCreated(result.status);
      onClose();
    } catch (publishError) {
      setError(publishError instanceof Error ? publishError.message : "Status could not publish.");
    } finally {
      setPublishing(false);
    }
  }

  async function searchMusic() {
    setError("");
    try {
      const result = await searchStatusMusic({ query: musicQuery, limit: 8 });
      setMusicItems(result.items || []);
    } catch (searchError) {
      setError(searchError instanceof Error ? searchError.message : "Music search failed.");
    }
  }

  async function generateAiStory() {
    if (!aiPrompt.trim() || aiBusy) {
      setError("Describe the AI Story first.");
      return;
    }
    setAiBusy(true);
    setError("");
    try {
      const result = await generateStatusAiStory(aiPrompt.trim(), "cinematic");
      const story = result.story || {};
      setMode("ai");
      setBody(String(story.caption || aiPrompt.trim()));
    } catch (aiError) {
      setError(aiError instanceof Error ? aiError.message : "AI Story could not be generated.");
    } finally {
      setAiBusy(false);
    }
  }

  function resetCreator() {
    stopMusicPreview().catch(() => undefined);
    setBody("");
    setMode("text");
    setVisibility("public");
    setDurationHours(24);
    setError("");
    setMusicQuery("");
    setSelectedMusic(null);
    setAiPrompt("");
    mediaUpload.reset();
  }

  function closeCreator() {
    if (!publishing && !mediaUpload.uploading) onClose();
  }

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={closeCreator}>
      <View style={styles.root}>
        <View style={[styles.header, { paddingTop: insets.top + 8 }]}>
          <Pressable accessibilityRole="button" accessibilityLabel="Cancel Status creation" style={styles.headerButton} disabled={publishing || mediaUpload.uploading} onPress={closeCreator}>
            <Text style={styles.headerButtonText}>Cancel</Text>
          </Pressable>
          <View><Text style={styles.headerKicker}>STATUS STUDIO</Text><Text style={styles.headerTitle}>Create Status</Text></View>
          <Pressable accessibilityRole="button" accessibilityLabel="Publish Status" style={[styles.publishButton, (!canPublish || publishing) && styles.disabled]} disabled={!canPublish || publishing} onPress={publish}>
            {publishing ? <ActivityIndicator color={colors.background} /> : <Text style={styles.publishText}>Post</Text>}
          </Pressable>
        </View>

        <ScrollView contentContainerStyle={styles.content} keyboardShouldPersistTaps="handled">
          <View style={styles.selector}>
            {modeOptions.map((option) => (
              <Pressable
                key={option.value}
                style={[styles.pill, mode === option.value && styles.pillActive]}
                onPress={() => setMode(option.value)}
                accessibilityRole="button"
                accessibilityState={{ selected: mode === option.value }}
              >
                <Text style={[styles.pillText, mode === option.value && styles.pillTextActive]}>{option.label}</Text>
              </Pressable>
            ))}
          </View>

          <TextInput
            style={styles.bodyInput}
            value={body}
            onChangeText={setBody}
            placeholder="Share a Status"
            placeholderTextColor={colors.muted}
            multiline
            textAlignVertical="top"
            accessibilityLabel="Status text"
          />

          <View style={styles.selector}>
            {visibilityOptions.map((option) => (
              <Pressable
                key={option.value}
                style={[styles.pill, visibility === option.value && styles.pillActive]}
                onPress={() => setVisibility(option.value)}
                accessibilityRole="button"
                accessibilityState={{ selected: visibility === option.value }}
              >
                <Text style={[styles.pillText, visibility === option.value && styles.pillTextActive]}>{option.label}</Text>
              </Pressable>
            ))}
          </View>

          <View style={styles.selector}>
            {durationOptions.map((option) => (
              <Pressable
                key={option.value}
                style={[styles.pill, durationHours === option.value && styles.pillActive]}
                onPress={() => setDurationHours(option.value)}
                accessibilityRole="button"
                accessibilityState={{ selected: durationHours === option.value }}
              >
                <Text style={[styles.pillText, durationHours === option.value && styles.pillTextActive]}>{option.label}</Text>
              </Pressable>
            ))}
          </View>

          <View style={styles.mediaActions}>
            <Pressable
              style={styles.secondaryButton}
              disabled={publishing || mediaUpload.uploading}
              onPress={() => {
                setMode("photo");
                mediaUpload.chooseImage().catch((pickerError) => setError(pickerError instanceof Error ? pickerError.message : "Image picker failed."));
              }}
            >
              <Text style={styles.secondaryText}>Image</Text>
            </Pressable>
            <Pressable
              style={styles.secondaryButton}
              disabled={publishing || mediaUpload.uploading}
              onPress={() => {
                setMode("video");
                mediaUpload.chooseVideo().catch((pickerError) => setError(pickerError instanceof Error ? pickerError.message : "Video picker failed."));
              }}
            >
              <Text style={styles.secondaryText}>Video</Text>
            </Pressable>
            <Pressable
              style={styles.secondaryButton}
              disabled={publishing || mediaUpload.uploading}
              onPress={() => {
                setMode("photo");
                mediaUpload.openCamera("image").catch((cameraError) => setError(cameraError instanceof Error ? cameraError.message : "Camera failed."));
              }}
            >
              <Text style={styles.secondaryText}>Camera</Text>
            </Pressable>
          </View>

          <MediaUploadPreview
            asset={mediaUpload.asset}
            media={mediaUpload.result?.media}
            progress={mediaUpload.progress}
            error={mediaUpload.error}
            uploading={mediaUpload.uploading}
            onRetry={mediaUpload.retry}
            onCancel={mediaUpload.cancel}
          />

          <View style={styles.panel}>
            <Text style={styles.panelTitle}>Music</Text>
            <View style={styles.searchRow}>
              <TextInput
                style={styles.searchInput}
                value={musicQuery}
                onChangeText={setMusicQuery}
                placeholder="Search creator-safe music"
                placeholderTextColor={colors.muted}
              />
              <Pressable style={styles.searchButton} onPress={searchMusic}>
                <Text style={styles.searchButtonText}>Search</Text>
              </Pressable>
            </View>
            {musicItems.slice(0, 6).map((track, index) => {
              const id = selectedMusicTrackId(track) || String(index);
              const trackPreviewId = String(selectedMusicTrackId(track) || track.preview_url || track.audio_url || "");
              const selected = selectedMusicTrackId(selectedMusic) === selectedMusicTrackId(track);
              const previewing = Boolean(trackPreviewId) && previewingTrackId === trackPreviewId;
              return (
                <View key={`${id}-${index}`} style={[styles.musicItem, selected && styles.musicSelected]}>
                  <Pressable
                    style={styles.musicInfo}
                    accessibilityRole="button"
                    accessibilityLabel={`${selected ? "Deselect" : "Select"} ${track.title || track.audio_title || "track"}`}
                    accessibilityState={{ selected }}
                    onPress={() => chooseMusic(selected ? null : track)}
                  >
                    <Text style={styles.musicTitle} numberOfLines={1}>{track.title || track.audio_title || "PulseSoc sound"}</Text>
                    <Text style={styles.musicMeta} numberOfLines={1}>{track.artist || track.audio_artist || track.mood || "Creator-safe music"}</Text>
                  </Pressable>
                  <Pressable
                    style={[styles.musicPreviewButton, previewing && styles.musicPreviewButtonActive]}
                    accessibilityRole="button"
                    accessibilityLabel={`${previewing ? "Stop" : "Preview"} ${track.title || track.audio_title || "track"}`}
                    onPress={() => toggleMusicPreview(track).catch(() => undefined)}
                  >
                    <Text style={[styles.musicPreviewText, previewing && styles.musicPreviewTextActive]}>{previewing ? "⏸ Stop" : "▶ Preview"}</Text>
                  </Pressable>
                </View>
              );
            })}
          </View>

          <View style={styles.panel}>
            <Text style={styles.panelTitle}>AI Story</Text>
            <TextInput
              style={styles.aiInput}
              value={aiPrompt}
              onChangeText={setAiPrompt}
              placeholder="Describe the story"
              placeholderTextColor={colors.muted}
              multiline
            />
            <Pressable style={[styles.secondaryButton, (!aiPrompt.trim() || aiBusy) && styles.disabled]} disabled={!aiPrompt.trim() || aiBusy} onPress={generateAiStory}>
              <Text style={styles.secondaryText}>{aiBusy ? "Generating" : "Generate AI Story"}</Text>
            </Pressable>
          </View>

          {error ? <Text accessibilityLiveRegion="polite" style={styles.error}>{error}</Text> : null}
        </ScrollView>
      </View>
    </Modal>
  );
}

function inferStatusType(mode: StatusType, mediaType: "image" | "video" | undefined, hasMedia: boolean, hasMusic: boolean): StatusType {
  if (hasMedia && mediaType === "video") return "video";
  if (hasMedia) return "photo";
  if (mode === "ai") return "ai";
  if (hasMusic) return "music";
  return "text";
}

function selectedMusicTrackId(track?: PulseStatusMusic | null) {
  return track?.track_id || track?.id || track?.music_id || track?.audio_id || "";
}

const styles = StyleSheet.create({
  aiInput: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    minHeight: 88,
    padding: 12,
    textAlignVertical: "top"
  },
  bodyInput: {
    backgroundColor: "rgba(10,23,38,0.92)",
    borderColor: "rgba(91,230,194,0.28)",
    borderRadius: 22,
    borderWidth: 1,
    color: colors.text,
    fontSize: 18,
    lineHeight: 25,
    minHeight: 180,
    padding: 18
  },
  content: {
    gap: 14,
    padding: 16,
    paddingBottom: 42
  },
  disabled: {
    opacity: 0.5
  },
  error: {
    color: colors.danger,
    fontWeight: "800"
  },
  header: {
    alignItems: "center",
    backgroundColor: "rgba(4,11,20,0.96)",
    borderBottomColor: colors.border,
    borderBottomWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: 14,
    paddingVertical: 12
  },
  headerButton: {
    minWidth: 76,
    paddingVertical: 9
  },
  headerButtonText: {
    color: colors.text,
    fontWeight: "800"
  },
  headerTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  headerKicker: { color: colors.accent, fontSize: 9, fontWeight: "900", letterSpacing: 1.5, textAlign: "center" },
  mediaActions: {
    flexDirection: "row",
    gap: 10
  },
  musicItem: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    padding: 10
  },
  musicInfo: {
    flex: 1
  },
  musicPreviewButton: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    justifyContent: "center",
    minWidth: 92,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  musicPreviewButtonActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  musicPreviewText: {
    color: colors.text,
    fontWeight: "900"
  },
  musicPreviewTextActive: {
    color: colors.background
  },
  musicMeta: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 3
  },
  musicSelected: {
    borderColor: colors.accent,
    backgroundColor: "rgba(54,229,143,0.1)"
  },
  musicTitle: {
    color: colors.text,
    fontWeight: "900"
  },
  panel: {
    backgroundColor: "rgba(10,23,38,0.82)",
    borderColor: "rgba(91,230,194,0.2)",
    borderRadius: 20,
    borderWidth: 1,
    gap: 10,
    padding: 12
  },
  panelTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  pill: {
    borderColor: colors.border,
    borderRadius: 999,
    borderWidth: 1,
    flex: 1,
    paddingVertical: 10
  },
  pillActive: {
    backgroundColor: colors.accent,
    borderColor: colors.accent
  },
  pillText: {
    color: colors.text,
    fontWeight: "900",
    textAlign: "center"
  },
  pillTextActive: {
    color: colors.background
  },
  publishButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    minHeight: 38,
    minWidth: 76,
    paddingHorizontal: 12
  },
  publishText: {
    color: colors.background,
    fontWeight: "900"
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  searchButton: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  searchButtonText: {
    color: colors.text,
    fontWeight: "900"
  },
  searchInput: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    flex: 1,
    padding: 12
  },
  searchRow: {
    flexDirection: "row",
    gap: 8
  },
  secondaryButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    paddingVertical: 11
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "900",
    textAlign: "center"
  },
  selector: {
    flexDirection: "row",
    gap: 8
  }
});
