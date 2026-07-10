import AsyncStorage from "@react-native-async-storage/async-storage";
import { useEffect, useMemo, useRef, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, TextInput, View } from "react-native";
import { createPost, PulsePost } from "../api/feed";
import { LogiNexusPanel } from "./LogiNexus";
import { MediaUploadPreview } from "../media/MediaUploadPreview";
import { NativeMediaAsset, NativeMediaUploadResult, uploadResultMediaId } from "../media/nativeMediaUpload";
import { useNativeMediaUpload } from "../media/useNativeMediaUpload";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";

type ComposerMode = "post" | "reel" | "live";
type Visibility = "public" | "followers" | "private";

type Props = {
  onCreated: (post?: PulsePost) => void;
  onOpenCamera: (mode: "photo" | "video" | "reel") => void;
  onOpenLive: () => void;
  onOpenMusic: () => void;
};

const MAX_BODY = 3000;
const DRAFT_KEY = "pulsesoc.native.home.composer.draft.v1";
const MODES: Array<{ key: ComposerMode; label: string; note: string }> = [
  { key: "post", label: "Post", note: "Publish a PulseSoc feed signal." },
  { key: "reel", label: "Reel", note: "Attach a video or use Camera Studio for the native Reel path." },
  { key: "live", label: "Live", note: "Live hosting stays on the existing safe Studio flow." }
];
const VISIBILITY: Visibility[] = ["public", "followers", "private"];
const FEELINGS = ["Curious", "Focused", "Bullish", "Creative"];

type HomeComposerDraft = {
  body: string;
  mode: ComposerMode;
  visibility: Visibility;
  topic: string;
  feeling: string;
  savedAt: string;
  mediaAsset?: NativeMediaAsset | null;
  mediaResult?: NativeMediaUploadResult | null;
  uploadStage?: string;
};

export function HomePulseComposer({ onCreated, onOpenCamera, onOpenLive, onOpenMusic }: Props) {
  const [mode, setMode] = useState<ComposerMode>("post");
  const [body, setBody] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("public");
  const [topic, setTopic] = useState("");
  const [feeling, setFeeling] = useState("");
  const [note, setNote] = useState("Ready to publish.");
  const [error, setError] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [lastFailedPayload, setLastFailedPayload] = useState<ReturnType<typeof buildCreatePayload> | null>(null);
  const [restoredMediaResult, setRestoredMediaResult] = useState<NativeMediaUploadResult | null>(null);
  const [draftRecovered, setDraftRecovered] = useState(false);
  const mountedRef = useRef(false);
  const skipNextPersistRef = useRef(false);
  const media = useNativeMediaUpload({ contextType: "pulse_post", target: "feed", destination: "feed", mode: "post" });
  const characters = body.length;
  const selectedMode = useMemo(() => MODES.find((item) => item.key === mode) || MODES[0], [mode]);
  const hasDraft = Boolean(body.trim() || topic || feeling || media.asset || media.result || restoredMediaResult);

  useEffect(() => {
    let active = true;
    AsyncStorage.getItem(DRAFT_KEY)
      .then((raw) => {
        if (!active || !raw) return;
        const draft = normalizeDraft(JSON.parse(raw) as HomeComposerDraft);
        if (!draft) return;
        setBody(draft.body);
        setMode(draft.mode);
        setVisibility(draft.visibility);
        setTopic(draft.topic);
        setFeeling(draft.feeling);
        if (draft.mediaAsset) media.setAsset(draft.mediaAsset);
        if (draft.mediaResult) setRestoredMediaResult(draft.mediaResult);
        setDraftRecovered(true);
      setNote(draft.mediaResult ? "Recovered transmission draft with uploaded media ready." : "Recovered transmission draft.");
      })
      .catch(() => undefined)
      .finally(() => {
        mountedRef.current = true;
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!mountedRef.current) return;
    if (skipNextPersistRef.current) {
      skipNextPersistRef.current = false;
      return;
    }
    const timer = setTimeout(() => {
      if (!hasDraft) {
        AsyncStorage.removeItem(DRAFT_KEY).catch(() => undefined);
        return;
      }
      const draft: HomeComposerDraft = {
        body,
        mode,
        visibility,
        topic,
        feeling,
        savedAt: new Date().toISOString(),
        mediaAsset: media.asset,
        mediaResult: media.result || restoredMediaResult,
        uploadStage: media.progress.stage
      };
      AsyncStorage.setItem(DRAFT_KEY, JSON.stringify(draft)).catch(() => undefined);
    }, 350);
    return () => clearTimeout(timer);
  }, [body, feeling, hasDraft, media.asset, media.progress.stage, media.result, mode, restoredMediaResult, topic, visibility]);

  async function handlePublish() {
    if (mode === "live") {
      setNote("Opening existing PulseSoc Live Studio gateway.");
      onOpenLive();
      return;
    }
    const cleanBody = body.trim();
    if (!cleanBody && !media.asset && !media.result && !restoredMediaResult) {
      setError("Add text or media before publishing.");
      setNote("Transmission validation blocked an empty signal.");
      return;
    }
    if (media.uploading) {
      setError("Wait for the current media upload or cancel it before publishing.");
      setNote("Upload queue is active. PulseSoc will publish after media is ready.");
      return;
    }
    setPublishing(true);
    setError("");
      setNote("Transmitting through the PulseSoc backend.");
    try {
      const uploaded = media.result || restoredMediaResult || (media.asset ? await media.upload({ mode: mode === "reel" ? "reel" : media.asset.mediaType, destination: "feed" }) : null);
      const mediaId = uploaded ? uploadResultMediaId(uploaded) : 0;
      const restoredMediaType = restoredMediaKind(restoredMediaResult);
      const postType = mediaId ? (media.asset?.mediaType === "video" || restoredMediaType === "video" || mode === "reel" ? "video" : "image") : "text";
      if (mode === "reel" && postType !== "video") {
        setError("Attach video or open Camera Studio to create a Reel.");
        setNote("Reel publishing needs video media from the existing media pipeline.");
        return;
      }
      const tags = [topic].filter(Boolean);
      const payload = buildCreatePayload({
        body: [cleanBody, feeling ? `Feeling: ${feeling}` : ""].filter(Boolean).join("\n\n"),
        post_type: postType,
        visibility,
        media_ids: mediaId ? [mediaId] : [],
        tags
      });
      setLastFailedPayload(payload);
      const response = await createPost(payload);
      setBody("");
      setTopic("");
      setFeeling("");
      setMode("post");
      setVisibility("public");
      setRestoredMediaResult(null);
      media.reset();
      skipNextPersistRef.current = true;
      await AsyncStorage.removeItem(DRAFT_KEY).catch(() => undefined);
      setDraftRecovered(false);
      setLastFailedPayload(null);
      setNote(response.post_id ? "Signal transmitted. Refreshing Home." : response.message || "Signal transmitted.");
      onCreated(response.post);
    } catch (publishError) {
      const message = publishError instanceof Error ? publishError.message : "Publish failed.";
      setError(message);
      setNote("Transmission interrupted. Retry when the backend is reachable.");
    } finally {
      setPublishing(false);
    }
  }

  async function retryLastPublish() {
    if (!lastFailedPayload || publishing) return;
    setPublishing(true);
    setError("");
    setNote("Retrying the last server-authoritative transmission.");
    try {
      const response = await createPost(lastFailedPayload);
      setBody("");
      setTopic("");
      setFeeling("");
      setMode("post");
      setVisibility("public");
      setRestoredMediaResult(null);
      media.reset();
      skipNextPersistRef.current = true;
      await AsyncStorage.removeItem(DRAFT_KEY).catch(() => undefined);
      setDraftRecovered(false);
      setLastFailedPayload(null);
      setNote(response.post_id ? "Signal transmitted after retry. Refreshing Home." : response.message || "Signal transmitted after retry.");
      onCreated(response.post);
    } catch (retryError) {
      const message = retryError instanceof Error ? retryError.message : "Retry failed.";
      setError(message);
      setNote("Retry failed. Draft remains saved for recovery.");
    } finally {
      setPublishing(false);
    }
  }

  async function clearDraft() {
    setBody("");
    setTopic("");
    setFeeling("");
    setMode("post");
    setVisibility("public");
    setRestoredMediaResult(null);
    setLastFailedPayload(null);
    media.reset();
    skipNextPersistRef.current = true;
    await AsyncStorage.removeItem(DRAFT_KEY).catch(() => undefined);
    setDraftRecovered(false);
    setError("");
    setNote("Transmission draft cleared.");
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
    if (nextMode === "reel" && media.asset?.mediaType !== "video") {
      setNote("Reel mode requires video media. Use Video or Reel Camera for backend-safe creation.");
    }
  }

  const hasPublishPayload = Boolean(
    body.trim() ||
      media.asset ||
      media.result ||
      restoredMediaResult ||
      lastFailedPayload ||
      mode === "live"
  );
  const hasActiveComposerState = Boolean(
    error ||
      draftRecovered ||
      lastFailedPayload ||
      media.asset ||
      media.result ||
      restoredMediaResult ||
      media.progress.stage !== "idle" ||
      media.error
  );

  return (
    <LogiNexusPanel style={styles.wrap} tone={mode === "live" ? "danger" : mode === "reel" ? "creator" : "default"}>
      <View accessible accessibilityLabel="Transmission Console" style={styles.headerRow}>
        <View style={styles.identityOrb}>
          <Text style={styles.identityOrbText}>LN</Text>
          <View style={styles.identitySignal} />
        </View>
        <TextInput
          testID="home-composer-input"
          accessibilityLabel="Home composer text"
          multiline
          maxLength={MAX_BODY}
          placeholder="Transmit to the Pulse Network..."
          placeholderTextColor={colors.muted}
          style={styles.input}
          value={body}
          onChangeText={setBody}
        />
        <Pressable style={styles.audiencePill} onPress={cycleVisibility}>
          <Text style={styles.audienceText}>{visibilityLabel(visibility)}</Text>
          <Text style={styles.audienceArrow}>⌄</Text>
        </Pressable>
      </View>
      <View style={styles.modeRow}>
        {MODES.map((item) => (
          <Pressable
            key={item.key}
            testID={`home-composer-mode-${item.key}`}
            accessibilityLabel={`Composer mode ${item.label}`}
            style={[styles.modeButton, mode === item.key && styles.modeButtonActive]}
            onPress={() => selectMode(item.key)}
          >
            <Text style={[styles.modeText, mode === item.key && styles.modeTextActive]}>{item.label}</Text>
          </Pressable>
        ))}
      </View>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.actionGrid}>
        <ComposerAction testID="home-composer-photo" label="Photo" icon="▧" onPress={() => media.chooseImage().then(() => setNote("Photo selected for PulseSoc upload.")).catch(() => undefined)} />
        <ComposerAction testID="home-composer-video" label="Video" icon="▶" onPress={() => media.chooseVideo().then(() => setNote("Video selected for PulseSoc upload.")).catch(() => undefined)} />
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
        <ComposerAction label="More" icon="…" onPress={cycleVisibility} />
      </ScrollView>
      <Text testID="home-composer-counter" style={styles.counter}>{characters}/{MAX_BODY}</Text>
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
      {restoredMediaResult && !media.result ? (
        <View style={styles.restoredPanel}>
          <Text style={styles.restoredTitle}>Uploaded media restored</Text>
          <Text style={styles.restoredText}>Server media #{uploadResultMediaId(restoredMediaResult)} will be reused unless you choose new media.</Text>
        </View>
      ) : null}
      {draftRecovered ? (
        <View testID="home-composer-recovered-draft" style={styles.draftPanel}>
          <Text style={styles.draftText}>Recovered transmission draft.</Text>
          <Pressable testID="home-composer-clear-draft" style={styles.draftButton} onPress={() => clearDraft().catch(() => undefined)}>
            <Text style={styles.draftButtonText}>Clear Draft</Text>
          </Pressable>
        </View>
      ) : null}
      {hasActiveComposerState ? (
        <View testID="home-composer-status" style={styles.statusPanel}>
          <Text style={styles.statusTitle}>{error || selectedMode.note}</Text>
          <Text style={[styles.statusText, error ? styles.errorText : undefined]}>{error || note}</Text>
        </View>
      ) : null}
      {lastFailedPayload ? (
        <Pressable testID="home-composer-retry" style={styles.retryButton} disabled={publishing} onPress={() => retryLastPublish().catch(() => undefined)}>
          <Text style={styles.retryText}>{publishing ? "Retrying..." : "Retry Last Publish"}</Text>
        </Pressable>
      ) : null}
      {hasPublishPayload ? (
        <Pressable
          testID="home-composer-publish"
          accessibilityRole="button"
          accessibilityLabel={mode === "live" ? "Open Live Studio" : "Publish Signal"}
          style={[styles.publishButton, publishing && styles.publishButtonDisabled]}
          disabled={publishing}
          onPress={handlePublish}
        >
          <Text style={styles.publishText}>{publishing ? "Transmitting..." : mode === "live" ? "Open Live Studio" : "Publish Signal"}</Text>
        </Pressable>
      ) : null}
      {mode !== "post" ? (
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
      ) : null}
    </LogiNexusPanel>
  );
}

function buildCreatePayload(payload: {
  body: string;
  post_type: string;
  visibility: Visibility;
  media_ids: number[];
  tags: string[];
}) {
  return payload;
}

function normalizeDraft(raw: HomeComposerDraft) {
  if (!raw || typeof raw !== "object") return null;
  const mode = ["post", "reel", "live"].includes(raw.mode) ? raw.mode : "post";
  const visibility = VISIBILITY.includes(raw.visibility) ? raw.visibility : "public";
  return {
    body: String(raw.body || "").slice(0, MAX_BODY),
    mode,
    visibility,
    topic: String(raw.topic || "").slice(0, 40),
    feeling: String(raw.feeling || "").slice(0, 40),
    savedAt: String(raw.savedAt || ""),
    mediaAsset: raw.mediaAsset?.uri ? raw.mediaAsset : null,
    mediaResult: uploadResultMediaId(raw.mediaResult || {}) ? raw.mediaResult : null,
    uploadStage: String(raw.uploadStage || "idle")
  } satisfies HomeComposerDraft;
}

function restoredMediaKind(result: NativeMediaUploadResult | null) {
  const type = String(result?.media?.media_type || result?.media?.type || result?.media?.mime_type || "").toLowerCase();
  if (type.includes("video") || String(result?.playback_url || result?.media_url || "").match(/\.(mp4|mov|m3u8|webm)(\?|$)/i)) return "video";
  if (type.includes("image")) return "image";
  return "";
}

function ComposerAction({ label, icon, onPress, testID }: { label: string; icon: string; onPress: () => void; testID?: string }) {
  return (
    <Pressable testID={testID} accessibilityLabel={label} style={styles.actionButton} onPress={onPress}>
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
    backgroundColor: "rgba(9, 20, 33, 0.74)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    flexGrow: 0,
    gap: 4,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 5,
    paddingVertical: 5,
    width: 70
  },
  actionGrid: {
    gap: 6,
    marginTop: 8
  },
  actionIcon: {
    color: colors.accent,
    fontSize: 16,
    fontWeight: "900"
  },
  actionText: {
    color: colors.text,
    fontSize: 9,
    fontWeight: "900",
    maxWidth: "100%",
    textAlign: "center"
  },
  counter: {
    color: colors.accentStrong,
    fontSize: 12,
    fontWeight: "900",
    lineHeight: 15,
    marginTop: 3,
    textAlign: "right"
  },
  errorText: {
    color: colors.danger
  },
  draftButton: {
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 8
  },
  draftButtonText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900"
  },
  draftPanel: {
    alignItems: "center",
    backgroundColor: "rgba(37, 208, 167, 0.1)",
    borderColor: logiNexus.colors.home.borderActive,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 12,
    padding: 10
  },
  draftText: {
    color: colors.accent,
    flex: 1,
    fontSize: 13,
    fontWeight: "900"
  },
  headerRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 7,
    justifyContent: "space-between"
  },
  identityOrb: {
    alignItems: "center",
    backgroundColor: "rgba(159, 124, 255, 0.18)",
    borderColor: logiNexus.colors.home.borderIntelligence,
    borderRadius: 20,
    borderWidth: 1,
    height: 40,
    justifyContent: "center",
    width: 40
  },
  identityOrbText: {
    color: colors.accentStrong,
    fontSize: 14,
    fontWeight: "900"
  },
  identitySignal: {
    backgroundColor: colors.accent,
    borderColor: logiNexus.colors.home.backgroundDeepSpace,
    borderRadius: 5,
    borderWidth: 2,
    bottom: 1,
    height: 10,
    position: "absolute",
    right: 1,
    width: 10
  },
  input: {
    color: colors.text,
    flex: 1,
    fontSize: 15,
    lineHeight: 20,
    minHeight: 40,
    paddingVertical: 2,
    textAlignVertical: "center"
  },
  audienceArrow: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "900"
  },
  audiencePill: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.055)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    flexDirection: "row",
    gap: 5,
    minHeight: 30,
    paddingHorizontal: 8
  },
  audienceText: {
    color: colors.text,
    fontSize: 11,
    fontWeight: "900"
  },
  liveDot: {
    color: colors.danger,
    fontSize: 12
  },
  livePill: {
    alignItems: "center",
    borderColor: colors.danger,
    backgroundColor: "rgba(255, 95, 126, 0.1)",
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    flexDirection: "row",
    gap: 6,
    paddingHorizontal: 11,
    paddingVertical: 8
  },
  liveText: {
    color: colors.text,
    fontWeight: "900"
  },
  modeButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.045)",
    borderRadius: logiNexus.radius.large,
    flex: 1,
    justifyContent: "center",
    minHeight: 32
  },
  modeButtonActive: {
    backgroundColor: colors.accent
  },
  modeRow: {
    backgroundColor: "rgba(3, 7, 18, 0.4)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 21,
    borderWidth: 1,
    flexDirection: "row",
    gap: 5,
    marginTop: 8,
    padding: 3
  },
  modeText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "900"
  },
  modeTextActive: {
    color: colors.background
  },
  publishButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 20,
    marginTop: 9,
    minHeight: 46,
    justifyContent: "center"
  },
  publishButtonDisabled: {
    opacity: 0.64
  },
  publishText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "900"
  },
  restoredPanel: {
    backgroundColor: "rgba(255,255,255,0.04)",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    gap: 4,
    marginTop: 12,
    padding: 10
  },
  restoredText: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  restoredTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  retryButton: {
    alignItems: "center",
    borderColor: colors.danger,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    marginTop: 12,
    minHeight: 42,
    justifyContent: "center"
  },
  retryText: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  routeButton: {
    borderColor: colors.border,
    borderRadius: logiNexus.radius.medium,
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
    backgroundColor: "rgba(9, 20, 33, 0.74)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    gap: 3,
    marginTop: 7,
    padding: 7
  },
  statusText: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  statusTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  title: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900",
    marginTop: 5
  },
  wrap: {
    backgroundColor: "rgba(10, 23, 39, 0.92)",
    borderColor: "rgba(97, 216, 255, 0.62)",
    marginBottom: 8,
    padding: 8
  }
});
