import AsyncStorage from "@react-native-async-storage/async-storage";
import { Audio } from "expo-av";
import { useEffect, useMemo, useRef, useState } from "react";
import { Image, Keyboard, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { createPost, listFeed, PulsePost } from "../api/feed";
import { ComposerMusicTrack, suggestComposerMusic } from "../api/composerMusic";
import { composerMusicTrackFromPulseMusic, consumePulseMusicSelection } from "../api/music";
import { CreateReelPayload, createReel, listReels, PulseReel } from "../api/reels";
import { createStatus } from "../api/status";
import { consumeCreateCameraCaptureResult, CreateComposerMode } from "../create/createComposerHandoff";
import { ComposerDraftInput } from "../create/draftToContentModel";
import { PreviewPublishResult, stashPreviewHandoff } from "../create/previewHandoff";
import { resolvePreviewStop, resolvePreviewToggle } from "../create/musicPreviewLifecycle";
import { LogiNexusPanel } from "./LogiNexus";
import { ComposerMediaQueue } from "../media/ComposerMediaQueue";
import { NativeMediaAsset, NativeMediaUploadResult, uploadResultMediaId } from "../media/nativeMediaUpload";
import { useComposerMediaQueue } from "../media/useComposerMediaQueue";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { GlobalNavigationIdentity } from "../navigation/GlobalNavigation";
import { consumeShareComposerHandoff, mergeShareIntoComposerBody } from "../sharing/shareComposerHandoff";
import { createThemedStyles } from "../theme/themedStyles";

type ComposerMode = CreateComposerMode | "poll" | "scam_report";
type Visibility = "public" | "followers" | "private";

type Props = {
  onCreated: (post?: PulsePost) => void;
  onOpenCamera: (mode: "photo" | "video" | "reel", composerMode: CreateComposerMode) => void;
  onOpenMusic: (composerMode: CreateComposerMode) => void;
  onOpenRoute: (route: string) => void;
  onOpenPreview?: (token: string) => void;
  identity?: GlobalNavigationIdentity;
  initiallyExpanded?: boolean;
  initialMode?: CreateComposerMode;
  captureReturnNonce?: string;
  shareHandoffNonce?: string;
};

const MAX_BODY = 3000;
const DRAFT_KEY = "pulsesoc.native.home.composer.draft.v1";
const PRIMARY_MODES: Array<{ key: ComposerMode; label: string; note: string }> = [
  { key: "post", label: "Feed", note: "Publish a PulseSoc feed signal." },
  { key: "status", label: "Status", note: "Create a 24-hour PulseSoc Status with the same media pipeline." },
  { key: "reel", label: "Reel", note: "Attach one video or capture a native Reel." }
];
const SECONDARY_MODES: Array<{ key: ComposerMode; label: string; note: string; icon: string }> = [
  { key: "poll", label: "Poll", note: "Ask the PulseSoc community a question.", icon: "?" },
  { key: "scam_report", label: "Scam Alert", note: "Share a detailed warning with PulseSoc moderation.", icon: "!" }
];
const ALL_MODES = [...PRIMARY_MODES, ...SECONDARY_MODES];
const PRODUCTION_CREATION_ROUTES = [
  { label: "Marketplace", route: "/pulse/marketplace/create", icon: "◇" },
  { label: "Question", route: "/pulse/questions", icon: "?" }
];
const VISIBILITY: Visibility[] = ["public", "followers", "private"];
type HomeComposerDraft = {
  body: string;
  mode: ComposerMode;
  visibility: Visibility;
  topic: string;
  musicTrack?: ComposerMusicTrack | null;
  savedAt: string;
  mediaItems?: Array<{ id: string; asset: NativeMediaAsset; result: NativeMediaUploadResult | null }>;
  failedPublish?: FailedPublish | null;
  // Legacy single-media draft fields are read for safe migration only.
  mediaAsset?: NativeMediaAsset | null;
  mediaResult?: NativeMediaUploadResult | null;
};

type FailedPostPublish = {
  kind: "post";
  payload: ReturnType<typeof buildCreatePayload>;
  startedAt: string;
};

type FailedReelPublish = {
  kind: "reel";
  payload: CreateReelPayload;
  startedAt: string;
};

type FailedStatusPublish = {
  kind: "status";
  payload: Parameters<typeof createStatus>[0];
  startedAt: string;
};

type FailedPublish = FailedPostPublish | FailedReelPublish | FailedStatusPublish;

export function HomePulseComposer({ onCreated, onOpenCamera, onOpenMusic, onOpenRoute, onOpenPreview, identity, initiallyExpanded = false, initialMode = "post", captureReturnNonce = "", shareHandoffNonce = "" }: Props) {
  const [mode, setMode] = useState<ComposerMode>("post");
  const [body, setBody] = useState("");
  const [visibility, setVisibility] = useState<Visibility>("public");
  const [topic, setTopic] = useState("");
  const [musicTrack, setMusicTrack] = useState<ComposerMusicTrack | null>(null);
  const [musicOptions, setMusicOptions] = useState<ComposerMusicTrack[]>([]);
  const [musicLoading, setMusicLoading] = useState(false);
  const [showMusic, setShowMusic] = useState(false);
  const [previewingTrackId, setPreviewingTrackId] = useState("");
  const [note, setNote] = useState("Ready to publish.");
  const [error, setError] = useState("");
  const [publishing, setPublishing] = useState(false);
  const [lastFailedPublish, setLastFailedPublish] = useState<FailedPublish | null>(null);
  const [draftRecovered, setDraftRecovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const [showAudience, setShowAudience] = useState(false);
  const [showTools, setShowTools] = useState(false);
  const [expanded, setExpanded] = useState(initiallyExpanded);
  const [draftLoaded, setDraftLoaded] = useState(false);
  const mountedRef = useRef(false);
  const skipNextPersistRef = useRef(false);
  const lastCaptureNonceRef = useRef("");
  const lastShareHandoffNonceRef = useRef("");
  const musicPreviewRef = useRef<Audio.Sound | null>(null);
  const media = useComposerMediaQueue({ contextType: "pulse", contextId: "native-draft", target: "feed", destination: "feed", mode: "post" });
  const characters = body.length;
  const selectedMode = useMemo(() => ALL_MODES.find((item) => item.key === mode) || PRIMARY_MODES[0], [mode]);
  const hasDraft = Boolean(body || topic || musicTrack || media.items.length || visibility !== "public" || mode !== "post");
  const avatarLabel = identityInitials(identity);

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
        setMusicTrack(draft.musicTrack || null);
        setLastFailedPublish(draft.failedPublish || null);
        if (draft.mediaItems?.length) media.restore(draft.mediaItems);
        else if (draft.mediaAsset) {
          media.restore([{ id: `legacy-${Date.now()}`, asset: draft.mediaAsset, result: draft.mediaResult || null }]);
        }
        setDraftRecovered(true);
        setExpanded(true);
        setNote(draft.mediaItems?.some((item) => uploadResultMediaId(item.result || {})) || draft.mediaResult ? "Recovered transmission draft with uploaded media ready." : "Recovered transmission draft.");
      })
      .catch(() => undefined)
      .finally(() => {
        mountedRef.current = true;
        if (active) setDraftLoaded(true);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!initiallyExpanded) return;
    setExpanded(true);
    setMode(normalizeComposerMode(initialMode));
    const surface = musicSurfaceForComposer(initialMode);
    consumeComposerMusicSelection(surface)
      .then((selection) => {
        if (!selection?.track) return;
        const nextTrack = composerMusicTrackFromPulseMusic(selection.track);
        setMusicTrack(nextTrack);
        setShowMusic(false);
        setNote(`Approved music attached: ${nextTrack.title} · ${nextTrack.artist}`);
      })
      .catch(() => undefined);
  }, [initialMode, initiallyExpanded]);

  useEffect(() => {
    const nonce = captureReturnNonce || (initiallyExpanded ? "initial-open" : "");
    if (!initiallyExpanded && !captureReturnNonce) return;
    if (nonce && lastCaptureNonceRef.current === nonce) return;
    lastCaptureNonceRef.current = nonce;
    consumeCreateCameraCaptureResult()
      .then((capture) => {
        if (!capture?.asset?.uri) return;
        setExpanded(true);
        setMode(capture.composerMode);
        media.addAssets([capture.asset]);
        setNote(`${capture.captureMode === "video" ? "Video" : "Photo"} captured. Review it in the composer before publishing.`);
      })
      .catch(() => undefined);
  }, [captureReturnNonce, initiallyExpanded, media]);

  useEffect(() => {
    if (!draftLoaded || !shareHandoffNonce || lastShareHandoffNonceRef.current === shareHandoffNonce) return;
    lastShareHandoffNonceRef.current = shareHandoffNonce;
    consumeShareComposerHandoff(shareHandoffNonce)
      .then((handoff) => {
        if (!handoff) return;
        setExpanded(true);
        setMode(handoff.mode);
        setBody((current) => mergeShareIntoComposerBody(current, handoff.body));
        setNote(
          handoff.mode === "reel"
            ? "Shared PulseSoc link added. Attach one video, then review before publishing the Reel."
            : "Shared PulseSoc link added. Review the Status / Story before publishing."
        );
      })
      .catch(() => undefined);
  }, [draftLoaded, shareHandoffNonce]);

  useEffect(() => () => {
    // Leaving the composer must stop any preview (resolvePreviewStop documents
    // this as one of the mandated "preview ends" events).
    resolvePreviewStop(musicPreviewRef.current ? "active" : "");
    musicPreviewRef.current?.unloadAsync().catch(() => undefined);
    musicPreviewRef.current = null;
  }, []);

  // Closing the music picker (via the × control, selecting a track, an approved
  // full-library selection, or clearing the draft — all of which set showMusic
  // false) must stop preview playback. Centralizing it on the showMusic flag
  // covers every close path in one place.
  useEffect(() => {
    if (showMusic) return;
    if (resolvePreviewStop(previewingTrackId).stopCurrent || musicPreviewRef.current) {
      stopMusicPreview().catch(() => undefined);
    }
  }, [showMusic]);

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
        musicTrack,
        savedAt: new Date().toISOString(),
        mediaItems: media.items.map((item) => ({ id: item.id, asset: item.asset, result: item.result })),
        failedPublish: lastFailedPublish
      };
      AsyncStorage.setItem(DRAFT_KEY, JSON.stringify(draft)).catch(() => undefined);
    }, 350);
    return () => clearTimeout(timer);
  }, [body, hasDraft, lastFailedPublish, media.items, mode, musicTrack, topic, visibility]);

  /**
   * Pre-flight validation shared by the direct publish path and the preview
   * path. Returns a user-facing error + status note when the draft is not
   * publishable, or `null` when it is safe to proceed. Keeping this in one
   * place guarantees the preview screen can never publish something the
   * composer would have rejected.
   */
  function validatePublish(): { error: string; note: string } | null {
    const cleanBody = body.trim();
    if (!cleanBody && !media.items.length && !musicTrack) {
      return { error: "Add text or media before publishing.", note: "Transmission validation blocked an empty signal." };
    }
    if (media.uploading) {
      return { error: "Wait for the current media upload or cancel it before publishing.", note: "Upload queue is active. PulseSoc will publish after media is ready." };
    }
    if (mode === "poll" && cleanBody && !cleanBody.endsWith("?")) {
      return { error: "Polls and questions must end with a question mark.", note: "Finish the question before transmitting." };
    }
    if (mode === "scam_report" && cleanBody.length < 24) {
      return { error: "Add useful scam warning details before publishing.", note: "Include who, what, where, and why so the warning is actionable." };
    }
    if (musicTrack && !media.items.length && mode !== "status") {
      return { error: "Choose a photo or video before attaching approved music.", note: "Attach media before adding approved music." };
    }
    if (mode === "reel" && (media.items.length !== 1 || media.items[0]?.asset.mediaType !== "video")) {
      return { error: "A Reel requires exactly one video. Remove other attachments or use Reel Camera.", note: "A Reel requires exactly one video." };
    }
    return null;
  }

  /**
   * Performs the actual upload + publish. Returns a structured result instead
   * of only mutating local state so the preview screen can react (dismiss on
   * success, stay open and preserve the draft on failure). On success the
   * composer is reset via `completePublish`. This is the single publish path —
   * both the direct button and the preview delegate here, so duplicate-safe
   * behavior and "no false success" hold in exactly one place.
   */
  async function runPublish(): Promise<PreviewPublishResult> {
    setPublishing(true);
    setError("");
    setNote("Sending your post.");
    try {
      const uploaded = media.items.length
        ? await media.uploadAll({
            mode: mode === "reel" ? "reel" : mode === "status" ? "status" : "post",
            destination: mode === "status" ? "status" : "feed"
          })
        : { mediaIds: [] };
      const mediaIds = uploaded.mediaIds;
      const hasVideo = media.items.some((item) => item.asset.mediaType === "video");
      const tags = [topic].filter(Boolean);
      if (mode === "reel") {
        const reelPayload: CreateReelPayload = {
          caption: body,
          visibility,
          media_ids: mediaIds,
          music_track_id: musicTrack?.id || "",
          ...attachedMusicPublishFields(musicTrack),
          share_to_feed: false
        };
        const failedReel: FailedReelPublish = { kind: "reel", payload: reelPayload, startedAt: new Date().toISOString() };
        setLastFailedPublish(failedReel);
        const reel = await createReel(reelPayload);
        if (!reel.reel_id || !reel.post_id) throw new Error("Reel was created without canonical identifiers. Refresh Reels before trying again.");
        const message = reel.processing_status && reel.processing_status !== "ready" ? "Reel transmitted and processing." : "Reel transmitted.";
        await completePublish(undefined, message);
        return { ok: true, message };
      }
      if (mode === "status") {
        const statusPayload: Parameters<typeof createStatus>[0] = {
          status_type: hasVideo ? "video" : mediaIds.length ? "photo" : musicTrack ? "music" : "text",
          body,
          visibility,
          duration_hours: 24,
          media_ids: mediaIds,
          music_track_id: musicTrack?.id || "",
          ...attachedMusicPublishFields(musicTrack),
          ai_context: { source: "native_home_composer", topic }
        };
        setLastFailedPublish({ kind: "status", payload: statusPayload, startedAt: new Date().toISOString() });
        const status = await createStatus(statusPayload);
        if (!status.status_id) throw new Error("We could not confirm your status went out. Your draft is saved.");
        await completePublish(undefined, "Status transmitted. Refreshing PulseSoc.");
        return { ok: true, message: "Status transmitted." };
      }
      const postType = hasVideo ? "video" : mediaIds.length ? "image" : mode === "poll" ? "poll" : mode === "scam_report" ? "scam_report" : "text";
      const payload = buildCreatePayload({
        body,
        post_type: postType,
        visibility,
        media_ids: mediaIds,
        tags,
        music_track_id: musicTrack?.id || "",
        ...attachedMusicPublishFields(musicTrack)
      });
      setLastFailedPublish({ kind: "post", payload, startedAt: new Date().toISOString() });
      const response = await createPost(payload);
      if (!response.post_id || !response.post) throw new Error("Publication response is incomplete. Check My Posts before retrying.");
      const message = response.post.moderation_status && response.post.moderation_status !== "approved" ? "Signal received and awaiting moderation." : "Signal transmitted. Refreshing Home.";
      await completePublish(response.post, message);
      return { ok: true, message };
    } catch (publishError) {
      const message = publishError instanceof Error ? publishError.message : "Publish failed.";
      setError(message);
      setNote("Transmission interrupted. Your draft and completed uploads are preserved.");
      return { ok: false, message };
    } finally {
      setPublishing(false);
    }
  }

  async function handlePublish() {
    const validation = validatePublish();
    if (validation) {
      setError(validation.error);
      setNote(validation.note);
      return;
    }
    await runPublish();
  }

  /**
   * Primary composer action. Validates, then opens the full-screen
   * True-to-Publish preview rendered from the SAME canonical model the feed
   * uses. Publishing happens from the preview via `runPublish` so the user
   * always sees an accurate preview before anything is transmitted. Falls back
   * to a direct publish if no preview handler is wired (e.g. legacy embeds).
   */
  async function openPreview() {
    const validation = validatePublish();
    if (validation) {
      setError(validation.error);
      setNote(validation.note);
      return;
    }
    if (!onOpenPreview) {
      await handlePublish();
      return;
    }
    const draft: ComposerDraftInput = {
      mode,
      body,
      visibility,
      topic,
      musicTrack,
      media: media.items.map((item) => ({ asset: item.asset, result: item.result })),
      identity
    };
    const token = stashPreviewHandoff({ draft, publish: runPublish });
    await persistDraftNow();
    setError("");
    setNote("Opening preview. Publish from the preview when it looks right.");
    onOpenPreview(token);
  }

  async function retryLastPublish() {
    if (!lastFailedPublish || publishing) return;
    setPublishing(true);
    setError("");
    setNote(lastFailedPublish.kind === "reel" ? "Checking Reels before retrying to prevent a duplicate." : "Checking My Posts before retrying to prevent a duplicate.");
    try {
      if (lastFailedPublish.kind === "reel") {
        const posts = await listFeed({ feed: "my_posts", limit: 20 });
        const existingPost = findMatchingReelPost(posts.posts || [], lastFailedPublish);
        if (existingPost) {
          await completePublish(existingPost, "Server-confirmed Reel post restored without republishing.");
          return;
        }
        const feed = await listReels({ lane: "for_you", limit: 30 });
        const existingReel = findMatchingReel(feed.reels || [], lastFailedPublish);
        if (existingReel) {
          await completePublish(undefined, "Server-confirmed Reel restored without republishing.");
          return;
        }
        const response = await createReel(lastFailedPublish.payload);
        if (!response.reel_id || !response.post_id) throw new Error("Retry response is incomplete. Refresh Reels before another attempt.");
        await completePublish(undefined, "Reel transmitted after a duplicate-safe check.");
        return;
      }
      if (lastFailedPublish.kind === "status") {
        const status = await createStatus(lastFailedPublish.payload);
        if (!status.status_id) throw new Error("Retry response did not confirm the Status. Draft remains saved.");
        await completePublish(undefined, "Status transmitted after retry.");
        return;
      }
      const feed = await listFeed({ feed: "my_posts", limit: 20 });
      const existing = findMatchingPost(feed.posts || [], lastFailedPublish);
      if (existing) {
        await completePublish(existing, "Server-confirmed post restored without republishing.");
        return;
      }
      const response = await createPost(lastFailedPublish.payload);
      if (!response.post_id || !response.post) throw new Error("Retry response is incomplete. Check My Posts before another attempt.");
      await completePublish(response.post, "Signal transmitted after a duplicate-safe check.");
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
    setMusicTrack(null);
    setMusicOptions([]);
    setShowMusic(false);
    setMode("post");
    setVisibility("public");
    setLastFailedPublish(null);
    media.reset();
    skipNextPersistRef.current = true;
    await AsyncStorage.removeItem(DRAFT_KEY).catch(() => undefined);
    setDraftRecovered(false);
    setError("");
    setNote("Transmission draft cleared.");
    setExpanded(false);
  }

  async function persistDraftNow() {
    if (!hasDraft) return;
    const draft: HomeComposerDraft = {
      body,
      mode,
      visibility,
      topic,
      musicTrack,
      savedAt: new Date().toISOString(),
      mediaItems: media.items.map((item) => ({ id: item.id, asset: item.asset, result: item.result })),
      failedPublish: lastFailedPublish
    };
    await AsyncStorage.setItem(DRAFT_KEY, JSON.stringify(draft)).catch(() => undefined);
  }

  async function openCameraFromComposer(nextMode: "photo" | "video" | "reel") {
    const composerMode = mode === "status" ? "status" : nextMode === "reel" || mode === "reel" ? "reel" : "post";
    setExpanded(true);
    setError("");
    setNote("Opening dedicated Camera. Your composer draft is preserved.");
    await persistDraftNow();
    onOpenCamera(nextMode, composerMode);
  }

  function selectVisibility(nextVisibility: Visibility) {
    setVisibility(nextVisibility);
    setShowAudience(false);
    setNote("Choose who can see this.");
  }

  async function completePublish(post: PulsePost | undefined, message: string) {
    setBody("");
    setTopic("");
    setMusicTrack(null);
    setMusicOptions([]);
    setShowMusic(false);
    setMode("post");
    setVisibility("public");
    setLastFailedPublish(null);
    media.reset();
    skipNextPersistRef.current = true;
    await AsyncStorage.removeItem(DRAFT_KEY).catch(() => undefined);
    setDraftRecovered(false);
    setExpanded(false);
    setNote(message);
    onCreated(post);
  }

  async function openMusicPicker() {
    setExpanded(true);
    setShowMusic(true);
    setMusicLoading(true);
    setError("");
    try {
      setMusicOptions(await suggestComposerMusic(media.items.some((item) => item.asset.mediaType === "video") ? "video" : "photo"));
      setNote("Choose an approved creator-safe PulseSoc track.");
    } catch (musicError) {
      setError(musicError instanceof Error ? musicError.message : "Approved music could not load.");
    } finally {
      setMusicLoading(false);
    }
  }

  /**
   * Stop and unload any in-flight music preview. Idempotent and safe to call
   * from every "preview must end" path (picker close, track select, unmount,
   * clear draft) so a preview can never keep playing after the picker is gone.
   */
  async function stopMusicPreview() {
    const existing = musicPreviewRef.current;
    musicPreviewRef.current = null;
    setPreviewingTrackId("");
    if (existing) await existing.unloadAsync().catch(() => undefined);
  }

  async function toggleMusicPreview(track: ComposerMusicTrack) {
    const transition = resolvePreviewToggle(previewingTrackId, track.id);
    if (transition.stopCurrent) await stopMusicPreview();
    if (!transition.nextTrackId) return;
    if (!track.previewUrl) {
      setError("This approved track does not have a preview available.");
      return;
    }
    try {
      const { sound } = await Audio.Sound.createAsync({ uri: track.previewUrl }, { shouldPlay: true, volume: 0.8 });
      musicPreviewRef.current = sound;
      setPreviewingTrackId(track.id);
      sound.setOnPlaybackStatusUpdate((status) => {
        if (status.isLoaded && status.didJustFinish) stopMusicPreview().catch(() => undefined);
      });
    } catch {
      setPreviewingTrackId("");
      setError("Track preview is unavailable. You can still select another approved track.");
    }
  }

  function selectMode(nextMode: ComposerMode) {
    setMode(nextMode);
    setError("");
    setNote(ALL_MODES.find((item) => item.key === nextMode)?.note || "Ready to publish.");
    if (nextMode === "reel" && (media.items.length !== 1 || media.items[0]?.asset.mediaType !== "video")) {
      setNote("Reels need a video. Use Video or Reel Camera to record one.");
    }
  }

  const hasPublishPayload = Boolean(
    body.trim() ||
      media.items.length ||
      musicTrack ||
      lastFailedPublish
  );
  const hasActiveComposerState = Boolean(
    error ||
      draftRecovered ||
      lastFailedPublish ||
      media.items.length
  );

  return (
    <LogiNexusPanel style={[styles.wrap, focused && styles.wrapFocused]} tone={mode === "reel" ? "creator" : "default"}>
      <View style={styles.titleRow}>
        <Text style={styles.eyebrow}>CREATE A SIGNAL</Text>
        <View style={styles.titleActions}>
          {publishing || hasDraft ? <Text style={[styles.readiness, hasDraft && styles.readinessActive]}>{publishing ? "SENDING" : "DRAFT"}</Text> : null}
          {expanded ? (
            <Pressable testID="home-composer-collapse" accessibilityRole="button" accessibilityLabel="Collapse composer" style={styles.collapseButton} onPress={() => { Keyboard.dismiss(); setExpanded(false); }}>
              <Text style={styles.collapseButtonText}>⌃</Text>
            </Pressable>
          ) : null}
        </View>
      </View>
      {!expanded ? (
        <View style={styles.collapsedShell}>
          <Pressable
            testID="home-composer-expand"
            accessibilityRole="button"
            accessibilityLabel={hasDraft ? "Open saved Pulse composer draft" : "Open Pulse composer"}
            style={({ pressed }) => [styles.collapsedComposer, pressed && styles.pressed]}
            onPress={() => setExpanded(true)}
          >
            <View style={styles.identityOrb}>
              {identity?.avatarUrl ? <Image source={{ uri: identity.avatarUrl }} style={styles.identityImage} /> : <Text style={styles.identityOrbText}>{avatarLabel}</Text>}
              <View style={styles.identitySignal} />
            </View>
            <View style={styles.collapsedCopy}>
              <Text style={styles.collapsedPrompt} numberOfLines={1}>{body.trim() || "Transmit to the Pulse Network…"}</Text>
              <Text style={styles.collapsedMeta}>{hasDraft ? "Saved draft · Tap to continue" : `${visibilityLabel(visibility)} · Tap to create`}</Text>
            </View>
            <Text accessibilityLabel="Composer mood and emoji options" style={styles.collapsedOpen}>☺</Text>
          </Pressable>
          <View style={styles.collapsedQuickRow}>
            <View style={styles.collapsedQuickTools}>
              <ComposerAction label="Photo" icon="▧" onPress={() => { setExpanded(true); media.chooseImages().then((assets) => setNote(assets.length ? `${assets.length} photo${assets.length === 1 ? "" : "s"} added to the upload queue.` : "No photos selected. If access was denied, allow Photos in Settings.")).catch((selectionError) => setError(selectionError instanceof Error ? selectionError.message : "Photos could not open.")); }} />
              <ComposerAction label="Video" icon="▶" onPress={() => { setExpanded(true); media.chooseVideo().then((asset) => setNote(asset ? "Video selected for PulseSoc upload." : "No video selected. If access was denied, allow Photos in Settings.")).catch((selectionError) => setError(selectionError instanceof Error ? selectionError.message : "Videos could not open.")); }} />
              <ComposerAction label="Camera" icon="◎" onPress={() => openCameraFromComposer("photo").catch(() => undefined)} />
            </View>
            <Pressable
              testID="home-composer-create-compact"
              accessibilityRole="button"
              accessibilityLabel="Open Pulse composer"
              style={({ pressed }) => [styles.collapsedCreateButton, pressed && styles.pressed]}
              onPress={() => {
                selectMode("post");
                setExpanded(true);
              }}
            >
              <Text style={styles.collapsedCreateText}>Create</Text>
            </Pressable>
          </View>
        </View>
      ) : (
      <>
      <View accessible accessibilityLabel="Transmission Console" style={styles.headerRow}>
        <View style={styles.identityOrb}>
          {identity?.avatarUrl ? <Image source={{ uri: identity.avatarUrl }} style={styles.identityImage} /> : <Text style={styles.identityOrbText}>{avatarLabel}</Text>}
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
          onFocus={() => setFocused(true)}
          onBlur={() => setFocused(false)}
        />
        <Pressable testID="home-composer-audience" accessibilityRole="button" accessibilityLabel={`Audience ${visibilityLabel(visibility)}`} style={styles.audiencePill} onPress={() => setShowAudience((current) => !current)}>
          <Text style={styles.audienceText}>{visibilityLabel(visibility)}</Text>
          <Text style={styles.audienceArrow}>⌄</Text>
        </Pressable>
      </View>
      {showAudience ? (
        <View testID="home-composer-audience-options" style={styles.audienceOptions}>
          {VISIBILITY.map((option) => (
            <Pressable accessibilityRole="button" key={option} style={[styles.audienceOption, visibility === option && styles.audienceOptionActive]} onPress={() => selectVisibility(option)}>
              <Text style={[styles.audienceOptionText, visibility === option && styles.audienceOptionTextActive]}>{visibilityLabel(option)}</Text>
            </Pressable>
          ))}
        </View>
      ) : null}
      <View style={styles.modeRow}>
        {PRIMARY_MODES.map((item) => (
          <Pressable accessibilityRole="button"
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
      <View style={styles.actionGrid}>
        <ComposerAction testID="home-composer-gallery" label="Gallery" icon="▧" onPress={() => media.chooseImages().then((assets) => setNote(assets.length ? `${assets.length} photo${assets.length === 1 ? "" : "s"} added to the upload queue.` : "No photos selected. If access was denied, allow Photos in Settings.")).catch((selectionError) => setError(selectionError instanceof Error ? selectionError.message : "Photos could not open."))} />
        <ComposerAction testID="home-composer-video" label="Video" icon="▶" onPress={() => media.chooseVideo().then((asset) => setNote(asset ? "Video selected for PulseSoc upload." : "No video selected. If access was denied, allow Photos in Settings.")).catch((selectionError) => setError(selectionError instanceof Error ? selectionError.message : "Videos could not open."))} />
        <ComposerAction label={musicTrack ? "Music ✓" : "Music"} icon="♪" selected={Boolean(musicTrack)} onPress={() => openMusicPicker().catch(() => undefined)} />
        <ComposerAction label="Feeling" icon="☺" onPress={() => {
          setError("Structured feelings are not supported by the production post contract yet.");
          setNote("PulseSoc will not rewrite your post body or create native-only feeling metadata.");
        }} />
        <ComposerAction testID="home-composer-camera" label="Camera" icon="◎" onPress={() => openCameraFromComposer(mode === "reel" ? "reel" : "photo").catch(() => undefined)} />
        <ComposerAction testID="home-composer-more" label={showTools ? "Less" : "More"} icon="…" selected={showTools} onPress={() => setShowTools((current) => !current)} />
      </View>
      {showTools ? (
        <View testID="home-composer-more-tools" style={styles.toolsPanel}>
          <ComposerAction label={topic || "Topic"} icon="#" onPress={() => {
            const next = topic ? "" : "pulse";
            setTopic(next);
            setNote(next ? "Topic tag added." : "Topic tag cleared.");
          }} />
          {SECONDARY_MODES.map((item) => <ComposerAction key={item.key} label={item.label} icon={item.icon} selected={mode === item.key} onPress={() => selectMode(item.key)} />)}
          {PRODUCTION_CREATION_ROUTES.map((item) => <ComposerAction key={item.label} label={item.label} icon={item.icon} onPress={() => onOpenRoute(item.route)} />)}
          <ComposerAction label="Dismiss" icon="⌄" onPress={() => { Keyboard.dismiss(); setShowTools(false); }} />
        </View>
      ) : null}
      {showMusic ? (
        <View testID="home-composer-music-picker" style={styles.musicPanel} accessibilityLiveRegion="polite">
          <View style={styles.musicHeader}>
            <View style={styles.musicHeaderCopy}>
              <Text style={styles.musicTitle}>Creator-safe music</Text>
              <Text style={styles.musicMeta}>{musicLoading ? "Loading approved catalog…" : "Only server-approved tracks can be attached."}</Text>
            </View>
            <Pressable accessibilityRole="button" accessibilityLabel="Close music picker" style={styles.musicClose} onPress={() => setShowMusic(false)}><Text style={styles.musicCloseText}>×</Text></Pressable>
          </View>
          {musicOptions.slice(0, 6).map((track) => (
            <View key={track.id} style={[styles.musicRow, musicTrack?.id === track.id && styles.musicRowActive]}>
              <View style={styles.musicRowCopy}><Text style={styles.musicRowTitle}>{track.title}</Text><Text style={styles.musicMeta}>{track.artist} · {track.licenseLabel}</Text></View>
              <Pressable accessibilityRole="button" accessibilityLabel={`${previewingTrackId === track.id ? "Stop" : "Preview"} ${track.title}`} style={styles.musicRowAction} onPress={() => toggleMusicPreview(track).catch(() => undefined)}><Text style={styles.musicSelect}>{previewingTrackId === track.id ? "Stop" : "Preview"}</Text></Pressable>
              <Pressable accessibilityRole="button" accessibilityLabel={`Select ${track.title} by ${track.artist}`} style={styles.musicRowAction} onPress={() => { setMusicTrack(track); setShowMusic(false); setNote(`Approved music attached: ${track.title} · ${track.artist}`); }}><Text style={styles.musicSelect}>{musicTrack?.id === track.id ? "Selected" : "Select"}</Text></Pressable>
            </View>
          ))}
          {!musicLoading && !musicOptions.length ? <Text style={styles.musicMeta}>No approved tracks matched this media.</Text> : null}
          <View style={styles.musicFooter}>
            {musicTrack ? <Pressable accessibilityRole="button" style={styles.musicUtility} onPress={() => { setMusicTrack(null); setNote("Music removed."); }}><Text style={styles.musicUtilityText}>Remove music</Text></Pressable> : null}
            <Pressable accessibilityRole="button" style={styles.musicUtility} onPress={() => onOpenMusic(mode === "status" ? "status" : mode === "reel" ? "reel" : "post")}><Text style={styles.musicUtilityText}>Open full library</Text></Pressable>
          </View>
        </View>
      ) : null}
      <View style={styles.publishRow}>
        <Text testID="home-composer-counter" style={[styles.counter, characters > MAX_BODY * 0.9 && styles.counterWarning]}>{characters.toLocaleString()}/{MAX_BODY.toLocaleString()}</Text>
        <Pressable
          testID="home-composer-publish"
          accessibilityRole="button"
          accessibilityLabel="Preview before publishing"
          accessibilityHint="Opens a full-screen preview showing exactly how this will publish"
          accessibilityState={{ disabled: publishing || !hasPublishPayload }}
          style={[styles.publishButton, (!hasPublishPayload || publishing) && styles.publishButtonDisabled]}
          disabled={publishing || !hasPublishPayload}
          onPress={() => openPreview().catch(() => undefined)}
        >
          <Text style={styles.publishText}>{publishing ? "Transmitting…" : "Preview"}</Text>
        </Pressable>
      </View>
      <ComposerMediaQueue items={media.items} onCancel={media.cancel} onRetry={(id) => media.retry(id).catch(() => undefined)} onRemove={media.remove} onMove={media.move} />
      {draftRecovered ? (
        <View testID="home-composer-recovered-draft" style={styles.draftPanel}>
          <Text style={styles.draftText}>Recovered transmission draft.</Text>
          <Pressable accessibilityRole="button" testID="home-composer-clear-draft" style={styles.draftButton} onPress={() => clearDraft().catch(() => undefined)}>
            <Text style={styles.draftButtonText}>Clear Draft</Text>
          </Pressable>
        </View>
      ) : null}
      {hasActiveComposerState ? (
        <View testID="home-composer-status" accessibilityLiveRegion="polite" style={styles.statusPanel}>
          <Text style={styles.statusTitle}>{error || selectedMode.note}</Text>
          <Text style={[styles.statusText, error ? styles.errorText : undefined]}>{error || note}</Text>
        </View>
      ) : null}
      {lastFailedPublish ? (
        <Pressable accessibilityRole="button" accessibilityState={{ disabled: publishing }} testID="home-composer-retry" style={styles.retryButton} disabled={publishing} onPress={() => retryLastPublish().catch(() => undefined)}>
          <Text style={styles.retryText}>{publishing ? "Retrying..." : "Retry Last Publish"}</Text>
        </Pressable>
      ) : null}
      {mode !== "post" ? (
        <View style={styles.routeRow}>
          <Pressable accessibilityRole="button" style={styles.routeButton} onPress={() => openCameraFromComposer("photo").catch(() => undefined)}>
            <Text style={styles.routeText}>Camera Photo</Text>
          </Pressable>
          <Pressable accessibilityRole="button" style={styles.routeButton} onPress={() => openCameraFromComposer("video").catch(() => undefined)}>
            <Text style={styles.routeText}>Camera Video</Text>
          </Pressable>
          <Pressable accessibilityRole="button" style={styles.routeButton} onPress={() => openCameraFromComposer("reel").catch(() => undefined)}>
            <Text style={styles.routeText}>Reel Camera</Text>
          </Pressable>
        </View>
      ) : null}
      </>
      )}
    </LogiNexusPanel>
  );
}

function buildCreatePayload(payload: {
  body: string;
  post_type: string;
  visibility: Visibility;
  media_ids: number[];
  tags: string[];
  music_track_id: string;
  attached_audio_url?: string;
  original_audio_muted?: boolean;
  audio_start_time?: number;
  audio_volume?: number;
}) {
  return payload;
}

/**
 * Defense-in-depth music metadata carried on every create payload. The backend
 * remains the source of truth for the attachment (via `music_track_id`), but
 * sending the resolved track URL + exclusive-audio flags guarantees playback
 * honors the selected music even if server-side enrichment is bypassed.
 */
function attachedMusicPublishFields(track?: ComposerMusicTrack | null) {
  if (!track?.id) return {};
  return {
    attached_audio_url: track.previewUrl || "",
    original_audio_muted: true,
    audio_start_time: 0,
    audio_volume: 1
  };
}

function normalizeDraft(raw: HomeComposerDraft) {
  if (!raw || typeof raw !== "object") return null;
  const mode = ["post", "status", "reel", "poll", "scam_report"].includes(raw.mode) ? raw.mode : "post";
  const visibility = VISIBILITY.includes(raw.visibility) ? raw.visibility : "public";
  return {
    body: String(raw.body || "").slice(0, MAX_BODY),
    mode,
    visibility,
    topic: String(raw.topic || "").slice(0, 40),
    musicTrack: raw.musicTrack?.id ? raw.musicTrack : null,
    savedAt: String(raw.savedAt || ""),
    failedPublish: normalizeFailedPublish(raw.failedPublish),
    mediaItems: Array.isArray(raw.mediaItems)
      ? raw.mediaItems.filter((item) => item?.asset?.uri).slice(0, 4).map((item) => ({ id: String(item.id || `restored-${Date.now()}`), asset: item.asset, result: uploadResultMediaId(item.result || {}) ? item.result : null }))
      : [],
    mediaAsset: raw.mediaAsset?.uri ? raw.mediaAsset : null,
    mediaResult: uploadResultMediaId(raw.mediaResult || {}) ? raw.mediaResult : null
  } satisfies HomeComposerDraft;
}

function findMatchingPost(posts: PulsePost[], failed: FailedPublish) {
  if (failed.kind !== "post") return undefined;
  const startedAt = Date.parse(failed.startedAt) || Date.now();
  const expectedMedia = [...failed.payload.media_ids].map(Number).sort((a, b) => a - b);
  return posts.find((post) => {
    const createdAt = Date.parse(post.created_at || "") || 0;
    if (createdAt && createdAt < startedAt - 15_000) return false;
    if (post.body !== failed.payload.body || post.visibility !== failed.payload.visibility) return false;
    const actualMedia = (post.media || []).map((item) => Number(item.id || 0)).filter(Boolean).sort((a, b) => a - b);
    return expectedMedia.length === actualMedia.length && expectedMedia.every((id, index) => id === actualMedia[index]);
  });
}

function findMatchingReel(reels: PulseReel[], failed: FailedReelPublish) {
  const startedAt = Date.parse(failed.startedAt) || Date.now();
  const expectedMedia = [...failed.payload.media_ids].map(Number).sort((a, b) => a - b);
  return reels.find((reel) => {
    const createdAt = Date.parse(reel.created_at || "") || 0;
    if (createdAt && createdAt < startedAt - 15_000) return false;
    if ((reel.caption || reel.body || "") !== (failed.payload.caption || "")) return false;
    const actualMedia = (reel.media || []).map((item) => Number(item.id || 0)).filter(Boolean).sort((a, b) => a - b);
    return expectedMedia.length === actualMedia.length && expectedMedia.every((id, index) => id === actualMedia[index]);
  });
}

function findMatchingReelPost(posts: PulsePost[], failed: FailedReelPublish) {
  const startedAt = Date.parse(failed.startedAt) || Date.now();
  const expectedMedia = [...failed.payload.media_ids].map(Number).sort((a, b) => a - b);
  return posts.find((post) => {
    const createdAt = Date.parse(post.created_at || "") || 0;
    if (createdAt && createdAt < startedAt - 15_000) return false;
    if (post.body !== (failed.payload.caption || "") || post.visibility !== failed.payload.visibility) return false;
    const actualMedia = (post.media || []).map((item) => Number(item.id || 0)).filter(Boolean).sort((a, b) => a - b);
    return expectedMedia.length === actualMedia.length && expectedMedia.every((id, index) => id === actualMedia[index]);
  });
}

function normalizeFailedPublish(value: FailedPublish | null | undefined): FailedPublish | null {
  if (!value || typeof value !== "object" || !value.startedAt || !value.payload) return null;
  if (value.kind === "reel" && Array.isArray(value.payload.media_ids)) return value;
  if (value.kind === "status" && typeof value.payload.status_type === "string") return value;
  if (value.kind === "post" && typeof value.payload.body === "string") return value;
  return null;
}

function normalizeComposerMode(value: CreateComposerMode): CreateComposerMode {
  if (value === "status" || value === "reel") return value;
  return "post";
}

function musicSurfaceForComposer(value: CreateComposerMode) {
  if (value === "status") return "status";
  if (value === "reel") return "reel";
  return "post";
}

async function consumeComposerMusicSelection(surface: "post" | "status" | "reel") {
  const primary = await consumePulseMusicSelection(surface);
  if (primary || surface !== "post") return primary;
  return consumePulseMusicSelection("video");
}

function ComposerAction({ label, icon, onPress, testID, selected = false }: { label: string; icon: string; onPress: () => void; testID?: string; selected?: boolean }) {
  return (
    <Pressable testID={testID} accessibilityLabel={label} accessibilityRole="button" style={({ pressed }) => [styles.actionButton, selected && styles.actionButtonSelected, pressed && styles.pressed]} onPress={onPress}>
      <Text style={styles.actionIcon}>{icon}</Text>
      <Text style={styles.actionText} numberOfLines={1}>{label}</Text>
    </Pressable>
  );
}

function identityInitials(identity?: GlobalNavigationIdentity) {
  const source = identity?.displayName || identity?.username || "You";
  return source.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function visibilityLabel(visibility: Visibility) {
  if (visibility === "followers") return "Followers";
  if (visibility === "private") return "Private";
  return "Public";
}

const styles = createThemedStyles(() => ({
  actionButton: {
    alignItems: "center",
    backgroundColor: "rgba(18, 26, 61, 0.03)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 16,
    borderWidth: 1,
    flexGrow: 1,
    gap: 2,
    justifyContent: "center",
    minHeight: 44,
    minWidth: 72,
    paddingHorizontal: 8,
    paddingVertical: 6
  },
  actionButtonSelected: {
    backgroundColor: "rgba(47, 225, 180, 0.14)",
    borderColor: colors.accent
  },
  actionGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 6
  },
  actionIcon: {
    color: "#b5c7ff",
    fontSize: 17,
    fontWeight: "900"
  },
  actionText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900",
    maxWidth: "100%",
    textAlign: "center"
  },
  counter: {
    color: colors.accentStrong,
    fontSize: 12,
    fontWeight: "900",
    lineHeight: 15,
    textAlign: "right"
  },
  counterWarning: {
    color: colors.danger
  },
  collapsedComposer: {
    alignItems: "center",
    backgroundColor: "rgba(10, 18, 43, 0.03)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 28,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    minHeight: 70,
    paddingHorizontal: 14,
    paddingVertical: 11
  },
  collapsedShell: {
    gap: 12
  },
  collapsedModeRow: {
    marginTop: 0
  },
  collapsedActionGrid: {
    marginTop: 0
  },
  collapsedPublishRow: {
    marginTop: 0
  },
  collapsedCopy: {
    flex: 1,
    minWidth: 0
  },
  collapsedMeta: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800",
    marginTop: 5
  },
  collapsedOpen: {
    color: colors.accent,
    fontSize: 28,
    fontWeight: "700"
  },
  collapsedPrompt: {
    color: colors.muted,
    fontSize: 18,
    fontWeight: "700"
  },
  collapsedQuickRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 7
  },
  collapsedQuickTools: {
    alignItems: "center",
    backgroundColor: "rgba(18, 26, 61, 0.03)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 23,
    borderWidth: 1,
    flex: 1,
    flexDirection: "row",
    gap: 0,
    minHeight: 58,
    overflow: "hidden",
    padding: 5
  },
  collapsedCreateButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderColor: "rgba(255,255,255,0.2)",
    borderRadius: 26,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 58,
    paddingHorizontal: 24,
    shadowColor: colors.accent,
    shadowOpacity: 0.28,
    shadowRadius: 18
  },
  collapsedCreateText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "900"
  },
  collapseButton: {
    alignItems: "center",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 12,
    borderWidth: 1,
    height: 44,
    justifyContent: "center",
    width: 44
  },
  collapseButtonText: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "900"
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
    gap: 6,
    justifyContent: "space-between"
  },
  identityOrb: {
    alignItems: "center",
    backgroundColor: "rgba(159, 124, 255, 0.18)",
    borderColor: logiNexus.colors.home.borderIntelligence,
    borderRadius: 31,
    borderWidth: 1,
    height: 62,
    justifyContent: "center",
    overflow: "hidden",
    width: 62
  },
  identityOrbText: {
    color: colors.accentStrong,
    fontSize: 17,
    fontWeight: "900"
  },
  identityImage: {
    borderRadius: 30,
    height: 60,
    width: 60
  },
  identitySignal: {
    backgroundColor: colors.accent,
    borderColor: logiNexus.colors.home.backgroundDeepSpace,
    borderRadius: 8,
    borderWidth: 2,
    bottom: 3,
    height: 15,
    position: "absolute",
    right: 3,
    width: 15
  },
  input: {
    color: colors.text,
    flex: 1,
    fontSize: 17,
    lineHeight: 22,
    minHeight: 42,
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
    minHeight: 44,
    paddingHorizontal: 8
  },
  audienceText: {
    color: colors.text,
    fontSize: 11,
    fontWeight: "900"
  },
  audienceOptions: {
    flexDirection: "row",
    gap: 6,
    marginTop: 7
  },
  audienceOption: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 14,
    borderWidth: 1,
    flex: 1,
    minHeight: 44,
    justifyContent: "center"
  },
  audienceOptionActive: {
    backgroundColor: "rgba(47, 225, 180, 0.14)",
    borderColor: colors.accent
  },
  audienceOptionText: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800"
  },
  audienceOptionTextActive: {
    color: colors.accent
  },
  eyebrow: {
    color: colors.accent,
    fontSize: 18,
    fontWeight: "900",
    letterSpacing: 4,
    textTransform: "uppercase"
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
    paddingHorizontal: 10,
    paddingVertical: 7
  },
  liveText: {
    color: colors.text,
    fontWeight: "900"
  },
  modeButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.045)",
    borderRadius: logiNexus.radius.large,
    justifyContent: "center",
    minHeight: 44,
    flex: 1,
    minWidth: 0,
    paddingHorizontal: 10
  },
  modeButtonActive: {
    backgroundColor: colors.accent
  },
  modeRow: {
    backgroundColor: "rgba(18, 26, 61, 0.03)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 21,
    borderWidth: 1,
    marginTop: 6,
    flexDirection: "row",
    gap: 4,
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
  musicClose: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 14,
    borderWidth: 1,
    height: 44,
    justifyContent: "center",
    width: 44
  },
  musicCloseText: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "700"
  },
  musicFooter: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 7
  },
  musicHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between"
  },
  musicHeaderCopy: {
    flex: 1
  },
  musicMeta: {
    color: colors.muted,
    fontSize: 11,
    lineHeight: 15
  },
  musicPanel: {
    backgroundColor: "rgba(18, 26, 61, 0.03)",
    borderColor: logiNexus.colors.home.borderIntelligence,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    gap: 7,
    marginTop: 8,
    padding: 9
  },
  musicRow: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 48,
    paddingHorizontal: 10,
    paddingVertical: 7
  },
  musicRowActive: {
    backgroundColor: "rgba(47, 225, 180, 0.12)",
    borderColor: colors.accent
  },
  musicRowCopy: {
    flex: 1
  },
  musicRowAction: {
    alignItems: "center",
    justifyContent: "center",
    minHeight: 44,
    minWidth: 54,
    paddingHorizontal: 5
  },
  musicRowTitle: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900"
  },
  musicSelect: {
    color: colors.accent,
    fontSize: 11,
    fontWeight: "900"
  },
  musicTitle: {
    color: colors.text,
    fontSize: 13,
    fontWeight: "900"
  },
  musicUtility: {
    borderColor: colors.border,
    borderRadius: 9,
    borderWidth: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 10
  },
  musicUtilityText: {
    color: colors.text,
    fontSize: 11,
    fontWeight: "900"
  },
  publishButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 18,
    justifyContent: "center",
    minHeight: 44,
    minWidth: 112,
    paddingHorizontal: 18
  },
  publishButtonDisabled: {
    opacity: 0.64
  },
  publishText: {
    color: colors.background,
    fontSize: 15,
    fontWeight: "900"
  },
  publishRow: {
    alignItems: "center",
    borderTopColor: logiNexus.colors.home.borderSubtle,
    borderTopWidth: 1,
    flexDirection: "row",
    justifyContent: "space-between",
    marginTop: 8,
    paddingTop: 8
  },
  pressed: {
    opacity: 0.72,
    transform: [{ scale: 0.98 }]
  },
  readiness: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "900",
    letterSpacing: 1.2
  },
  readinessActive: {
    color: colors.accent
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
    minHeight: 44,
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
    minHeight: 44,
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
    backgroundColor: logiNexus.colors.home.surfaceGlass,
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    gap: 3,
    marginTop: 6,
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
    fontSize: 20,
    fontWeight: "900",
  },
  toolsPanel: {
    backgroundColor: "rgba(18, 26, 61, 0.03)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6,
    marginTop: 6,
    padding: 6
  },
  titleRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 6
  },
  titleActions: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8
  },
  wrap: {
    backgroundColor: "rgba(7, 19, 32, 0.03)",
    borderColor: "rgba(97, 216, 255, 0.38)",
    borderRadius: 20,
    marginBottom: 10,
    padding: 8
  },
  wrapFocused: {
    borderColor: colors.accent,
    shadowColor: colors.accent,
    shadowOpacity: 0.18,
    shadowRadius: 10
  }
}));
