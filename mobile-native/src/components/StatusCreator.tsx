import { useEffect, useMemo, useState } from "react";
import AsyncStorage from "@react-native-async-storage/async-storage";
import { ActivityIndicator, Modal, Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
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
  }, [visible]);

  useEffect(() => {
    if (!visible) return;
    const timer = setTimeout(() => AsyncStorage.setItem(STATUS_DRAFT_KEY, JSON.stringify({ body, visibility, durationHours, mode, aiPrompt })).catch(() => undefined), 180);
    return () => clearTimeout(timer);
  }, [aiPrompt, body, durationHours, mode, visibility, visible]);

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
          <Pressable style={styles.headerButton} disabled={publishing || mediaUpload.uploading} onPress={closeCreator}>
            <Text style={styles.headerButtonText}>Cancel</Text>
          </Pressable>
          <View><Text style={styles.headerKicker}>STATUS STUDIO</Text><Text style={styles.headerTitle}>Create Status</Text></View>
          <Pressable style={[styles.publishButton, (!canPublish || publishing) && styles.disabled]} disabled={!canPublish || publishing} onPress={publish}>
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
              const selected = selectedMusicTrackId(selectedMusic) === selectedMusicTrackId(track);
              return (
                <Pressable key={`${id}-${index}`} style={[styles.musicItem, selected && styles.musicSelected]} onPress={() => setSelectedMusic(selected ? null : track)}>
                  <Text style={styles.musicTitle} numberOfLines={1}>{track.title || track.audio_title || "PulseSoc sound"}</Text>
                  <Text style={styles.musicMeta} numberOfLines={1}>{track.artist || track.audio_artist || track.mood || "Creator-safe music"}</Text>
                </Pressable>
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
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    padding: 10
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
