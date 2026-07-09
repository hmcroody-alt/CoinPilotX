import { useMemo, useState } from "react";
import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { createPost, PulsePost } from "../api/feed";
import { MediaUploadPreview } from "../media/MediaUploadPreview";
import { uploadResultMediaId } from "../media/nativeMediaUpload";
import { useNativeMediaUpload } from "../media/useNativeMediaUpload";
import { colors } from "../theme/colors";

type ComposerMode = "post" | "reel" | "live";
type Visibility = "public" | "followers" | "private";

type Props = {
  onCreated: (post?: PulsePost) => void;
  onOpenCamera: (mode: "photo" | "video" | "reel") => void;
  onOpenLive: () => void;
  onOpenMusic: () => void;
};

const MAX_BODY = 3000;
const MODES: Array<{ key: ComposerMode; label: string; note: string }> = [
  { key: "post", label: "Post", note: "Publish a PulseSoc feed signal." },
  { key: "reel", label: "Reel", note: "Attach a video or use Camera Studio for the native Reel path." },
  { key: "live", label: "Live", note: "Live hosting stays on the existing safe Studio flow." }
];
const VISIBILITY: Visibility[] = ["public", "followers", "private"];
const FEELINGS = ["Curious", "Focused", "Bullish", "Creative"];

export function HomePulseComposer({ onCreated, onOpenCamera, onOpenLive, onOpenMusic }: Props) {
  const [mode, setMode] = useState<ComposerMode>("post");
  const [body, setBody] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("public");
  const [topic, setTopic] = useState("");
  const [feeling, setFeeling] = useState("");
  const [note, setNote] = useState("Ready to publish.");
  const [error, setError] = useState("");
  const [publishing, setPublishing] = useState(false);
  const media = useNativeMediaUpload({ contextType: "pulse_post", target: "feed", destination: "feed", mode: "post" });
  const characters = body.length;
  const selectedMode = useMemo(() => MODES.find((item) => item.key === mode) || MODES[0], [mode]);

  async function handlePublish() {
    if (mode === "live") {
      setNote("Opening existing PulseSoc Live Studio gateway.");
      onOpenLive();
      return;
    }
    const cleanBody = body.trim();
    if (!cleanBody && !media.asset && !media.result) {
      setError("Add text or media before publishing.");
      setNote("Composer validation blocked an empty signal.");
      return;
    }
    setPublishing(true);
    setError("");
    setNote("Publishing through PulseSoc backend.");
    try {
      const uploaded = media.result || (media.asset ? await media.upload({ mode: mode === "reel" ? "reel" : media.asset.mediaType, destination: "feed" }) : null);
      const mediaId = uploaded ? uploadResultMediaId(uploaded) : 0;
      const postType = mediaId ? (media.asset?.mediaType === "video" || mode === "reel" ? "video" : "image") : "text";
      if (mode === "reel" && postType !== "video") {
        setError("Attach video or open Camera Studio to create a Reel.");
        setNote("Reel publishing needs video media from the existing media pipeline.");
        return;
      }
      const tags = [topic].filter(Boolean);
      const response = await createPost({
        body: [cleanBody, feeling ? `Feeling: ${feeling}` : ""].filter(Boolean).join("\n\n"),
        post_type: postType,
        visibility,
        media_ids: mediaId ? [mediaId] : [],
        tags
      });
      setBody("");
      setTopic("");
      setFeeling("");
      media.reset();
      setNote(response.post_id ? "Published. Refreshing Home." : response.message || "Published.");
      onCreated(response.post);
    } catch (publishError) {
      const message = publishError instanceof Error ? publishError.message : "Publish failed.";
      setError(message);
      setNote("Publish failed. Retry when the backend is reachable.");
    } finally {
      setPublishing(false);
    }
  }

  function cycleVisibility() {
    const index = VISIBILITY.indexOf(visibility);
    setVisibility(VISIBILITY[(index + 1) % VISIBILITY.length]);
    setNote("Audience selector uses existing server-side visibility rules.");
  }

  function cycleFeeling() {
    const index = FEELINGS.indexOf(feeling);
    const next = FEELINGS[(index + 1) % FEELINGS.length];
    setFeeling(next);
    setNote(`Feeling attached locally: ${next}.`);
  }

  function selectMode(nextMode: ComposerMode) {
    setMode(nextMode);
    setError("");
    setNote(MODES.find((item) => item.key === nextMode)?.note || "Ready to publish.");
  }

  return (
    <View style={styles.wrap}>
      <View style={styles.headerRow}>
        <Text style={styles.title}>Pulse Composer</Text>
        <Pressable style={styles.livePill} onPress={onOpenLive}>
          <Text style={styles.liveDot}>●</Text>
          <Text style={styles.liveText}>LIVE</Text>
        </Pressable>
      </View>
      <View style={styles.modeRow}>
        {MODES.map((item) => (
          <Pressable key={item.key} style={[styles.modeButton, mode === item.key && styles.modeButtonActive]} onPress={() => selectMode(item.key)}>
            <Text style={[styles.modeText, mode === item.key && styles.modeTextActive]}>{item.label}</Text>
          </Pressable>
        ))}
      </View>
      <View style={styles.inputWrap}>
        <TextInput
          multiline
          maxLength={MAX_BODY}
          placeholder="What’s happening in your world?"
          placeholderTextColor={colors.muted}
          style={styles.input}
          value={body}
          onChangeText={setBody}
        />
        <Text style={styles.counter}>{characters}/{MAX_BODY}</Text>
      </View>
      <View style={styles.actionGrid}>
        <ComposerAction label="Photo" icon="▧" onPress={() => media.chooseImage().then(() => setNote("Photo selected for PulseSoc upload.")).catch(() => undefined)} />
        <ComposerAction label="Video" icon="▶" onPress={() => media.chooseVideo().then(() => setNote("Video selected for PulseSoc upload.")).catch(() => undefined)} />
        <ComposerAction label="Music" icon="♪" onPress={() => {
          setNote("Music selection opens the existing PulseSoc media/music surface.");
          onOpenMusic();
        }} />
        <ComposerAction label={feeling || "Feeling"} icon="☺" onPress={cycleFeeling} />
        <ComposerAction label="Location" icon="⌖" onPress={() => {
          setNote("Location tagging stays server-authoritative and falls back until native contract is exposed.");
        }} />
        <ComposerAction label="Mention" icon="@" onPress={() => {
          setNote("Mention search uses existing PulseSoc people routing when full native picker lands.");
        }} />
        <ComposerAction label={topic || "Topic"} icon="#" onPress={() => {
          const next = topic ? "" : "pulse";
          setTopic(next);
          setNote(next ? "Topic tag added for backend publish." : "Topic tag cleared.");
        }} />
        <ComposerAction label={visibilityLabel(visibility)} icon="◎" onPress={cycleVisibility} />
      </View>
      {media.asset || media.result || media.progress.stage !== "idle" || media.error ? (
        <MediaUploadPreview
          asset={media.asset}
          media={media.result?.media || null}
          progress={media.progress}
          error={media.error}
          uploading={media.uploading}
          onCancel={media.cancel}
          onRetry={media.retry}
        />
      ) : null}
      <View style={styles.statusPanel}>
        <Text style={styles.statusTitle}>{error || selectedMode.note}</Text>
        <Text style={[styles.statusText, error ? styles.errorText : undefined]}>{error || note}</Text>
      </View>
      <Pressable style={[styles.publishButton, publishing && styles.publishButtonDisabled]} disabled={publishing} onPress={handlePublish}>
        <Text style={styles.publishText}>{publishing ? "Publishing..." : mode === "live" ? "Open Live Studio" : "Publish Signal"}</Text>
      </Pressable>
      <View style={styles.routeRow}>
        <Pressable style={styles.routeButton} onPress={() => onOpenCamera("photo")}>
          <Text style={styles.routeText}>Camera Photo</Text>
        </Pressable>
        <Pressable style={styles.routeButton} onPress={() => onOpenCamera("video")}>
          <Text style={styles.routeText}>Camera Video</Text>
        </Pressable>
        <Pressable style={styles.routeButton} onPress={() => onOpenCamera("reel")}>
          <Text style={styles.routeText}>Reel Camera</Text>
        </Pressable>
      </View>
    </View>
  );
}

function ComposerAction({ label, icon, onPress }: { label: string; icon: string; onPress: () => void }) {
  return (
    <Pressable style={styles.actionButton} onPress={onPress}>
      <Text style={styles.actionIcon}>{icon}</Text>
      <Text style={styles.actionText} numberOfLines={1}>{label}</Text>
    </Pressable>
  );
}

function visibilityLabel(visibility: Visibility) {
  if (visibility === "followers") return "Followers";
  if (visibility === "private") return "Private";
  return "Public";
}

const styles = StyleSheet.create({
  actionButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.045)",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexBasis: "23%",
    flexDirection: "row",
    gap: 8,
    minHeight: 54,
    paddingHorizontal: 10
  },
  actionGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    marginTop: 14
  },
  actionIcon: {
    color: colors.accent,
    fontSize: 17,
    fontWeight: "900"
  },
  actionText: {
    color: colors.text,
    flex: 1,
    fontSize: 13,
    fontWeight: "900"
  },
  counter: {
    bottom: 12,
    color: colors.muted,
    fontWeight: "900",
    position: "absolute",
    right: 14
  },
  errorText: {
    color: colors.danger
  },
  headerRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between"
  },
  input: {
    color: colors.text,
    fontSize: 19,
    lineHeight: 27,
    minHeight: 156,
    padding: 18,
    paddingBottom: 36,
    textAlignVertical: "top"
  },
  inputWrap: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginTop: 14,
    overflow: "hidden"
  },
  liveDot: {
    color: colors.danger,
    fontSize: 12
  },
  livePill: {
    alignItems: "center",
    borderColor: colors.danger,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  liveText: {
    color: colors.text,
    fontWeight: "900"
  },
  modeButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.045)",
    borderRadius: 8,
    flex: 1,
    justifyContent: "center",
    minHeight: 50
  },
  modeButtonActive: {
    backgroundColor: colors.accent
  },
  modeRow: {
    backgroundColor: "rgba(255,255,255,0.035)",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 8,
    marginTop: 14,
    padding: 6
  },
  modeText: {
    color: colors.muted,
    fontSize: 15,
    fontWeight: "900"
  },
  modeTextActive: {
    color: colors.background
  },
  publishButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    marginTop: 14,
    minHeight: 56,
    justifyContent: "center"
  },
  publishButtonDisabled: {
    opacity: 0.64
  },
  publishText: {
    color: colors.background,
    fontSize: 18,
    fontWeight: "900"
  },
  routeButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flex: 1,
    minHeight: 38,
    justifyContent: "center",
    paddingHorizontal: 8
  },
  routeRow: {
    flexDirection: "row",
    gap: 8,
    marginTop: 10
  },
  routeText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900",
    textAlign: "center"
  },
  statusPanel: {
    backgroundColor: "rgba(255,255,255,0.05)",
    borderRadius: 8,
    gap: 6,
    marginTop: 14,
    padding: 12
  },
  statusText: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  statusTitle: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  title: {
    color: colors.text,
    fontSize: 21,
    fontWeight: "900"
  },
  wrap: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginBottom: 14,
    padding: 16
  }
});
