import AsyncStorage from "@react-native-async-storage/async-storage";
import { Audio } from "expo-av";
import { useEffect, useMemo, useRef, useState } from "react";
import { Image, Keyboard, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import { createPost, listFeed, PulsePost } from "../api/feed";
import { ComposerMusicTrack, suggestComposerMusic } from "../api/composerMusic";
import { CreateReelPayload, createReel, listReels, PulseReel } from "../api/reels";
import { LogiNexusPanel } from "./LogiNexus";
import { ComposerMediaQueue } from "../media/ComposerMediaQueue";
import { NativeMediaAsset, NativeMediaUploadResult, uploadResultMediaId } from "../media/nativeMediaUpload";
import { useComposerMediaQueue } from "../media/useComposerMediaQueue";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { GlobalNavigationIdentity } from "../navigation/GlobalNavigation";

type ComposerMode = "post" | "reel" | "live" | "poll" | "scam_report";
type Visibility = "public" | "followers" | "private";

type Props = {
  onCreated: (post?: PulsePost) => void;
  onOpenCamera: (mode: "photo" | "video" | "reel") => void;
  onOpenLive: () => void;
  onOpenMusic: () => void;
  onOpenRoute: (route: string) => void;
  identity?: GlobalNavigationIdentity;
  initiallyExpanded?: boolean;
};

const MAX_BODY = 3000;
const DRAFT_KEY = "pulsesoc.native.home.composer.draft.v1";
const PRIMARY_MODES: Array<{ key: ComposerMode; label: string; note: string }> = [
  { key: "post", label: "Post", note: "Publish a PulseSoc feed signal." },
  { key: "reel", label: "Reel", note: "Attach a video or use Camera Studio for the native Reel path." },
  { key: "live", label: "Live", note: "Live hosting stays on the existing safe Studio flow." }
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

type FailedPublish = FailedPostPublish | FailedReelPublish;

export function HomePulseComposer({ onCreated, onOpenCamera, onOpenLive, onOpenMusic, onOpenRoute, identity, initiallyExpanded = false }: Props) {
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
  const mountedRef = useRef(false);
  const skipNextPersistRef = useRef(false);
  const musicPreviewRef = useRef<Audio.Sound | null>(null);
  const media = useComposerMediaQueue({ contextType: "pulse", contextId: "native-draft", target: "feed", destination: "feed", mode: "post" });
  const characters = body.length;
  const selectedMode = useMemo(() => ALL_MODES.find((item) => item.key === mode) || PRIMARY_MODES[0], [mode]);
  const hasDraft = Boolean(body || topic || musicTrack || media.items.length);
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
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (initiallyExpanded) setExpanded(true);
  }, [initiallyExpanded]);

  useEffect(() => () => {
    musicPreviewRef.current?.unloadAsync().catch(() => undefined);
    musicPreviewRef.current = null;
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
        musicTrack,
        savedAt: new Date().toISOString(),
        mediaItems: media.items.map((item) => ({ id: item.id, asset: item.asset, result: item.result })),
        failedPublish: lastFailedPublish
      };
      AsyncStorage.setItem(DRAFT_KEY, JSON.stringify(draft)).catch(() => undefined);
    }, 350);
    return () => clearTimeout(timer);
  }, [body, hasDraft, lastFailedPublish, media.items, mode, musicTrack, topic, visibility]);

  async function handlePublish() {
    if (mode === "live") {
      setNote("Opening existing PulseSoc Live Studio gateway.");
      onOpenLive();
      return;
    }
    const cleanBody = body.trim();
    if (!cleanBody && !media.items.length) {
      setError("Add text or media before publishing.");
      setNote("Transmission validation blocked an empty signal.");
      return;
    }
    if (media.uploading) {
      setError("Wait for the current media upload or cancel it before publishing.");
      setNote("Upload queue is active. PulseSoc will publish after media is ready.");
      return;
    }
    if (mode === "poll" && cleanBody && !cleanBody.endsWith("?")) {
      setError("Polls and questions must end with a question mark.");
      setNote("Finish the question before transmitting.");
      return;
    }
    if (mode === "scam_report" && cleanBody.length < 24) {
      setError("Add useful scam warning details before publishing.");
      setNote("Include who, what, where, and why so the warning is actionable.");
      return;
    }
    if (musicTrack && !media.items.length) {
      setError("Choose a photo or video before attaching approved music.");
      return;
    }
    if (mode === "reel" && (media.items.length !== 1 || media.items[0]?.asset.mediaType !== "video")) {
      setError("A Reel requires exactly one video. Remove other attachments or use Reel Camera.");
      return;
    }
    setPublishing(true);
    setError("");
    setNote("Transmitting through the PulseSoc backend.");
    try {
      const uploaded = media.items.length ? await media.uploadAll({ mode: mode === "reel" ? "reel" : "post", destination: "feed" }) : { mediaIds: [] };
      const mediaIds = uploaded.mediaIds;
      const hasVideo = media.items.some((item) => item.asset.mediaType === "video");
      const tags = [topic].filter(Boolean);
      if (mode === "reel") {
        const reelPayload: CreateReelPayload = {
          caption: body,
          visibility,
          media_ids: mediaIds,
          music_track_id: musicTrack?.id || "",
          share_to_feed: false
        };
        const failedReel: FailedReelPublish = { kind: "reel", payload: reelPayload, startedAt: new Date().toISOString() };
        setLastFailedPublish(failedReel);
        const reel = await createReel(reelPayload);
        if (!reel.reel_id || !reel.post_id) throw new Error("Reel was created without canonical identifiers. Refresh Reels before trying again.");
        await completePublish(undefined, reel.processing_status && reel.processing_status !== "ready" ? "Reel transmitted and processing." : "Reel transmitted.");
        return;
      }
      const postType = hasVideo ? "video" : mediaIds.length ? "image" : mode === "poll" ? "poll" : mode === "scam_report" ? "scam_report" : "text";
      const payload = buildCreatePayload({
        body,
        post_type: postType,
        visibility,
        media_ids: mediaIds,
        tags,
        music_track_id: musicTrack?.id || ""
      });
      setLastFailedPublish({ kind: "post", payload, startedAt: new Date().toISOString() });
      const response = await createPost(payload);
      if (!response.post_id || !response.post) throw new Error("Publication response is incomplete. Check My Posts before retrying.");
      await completePublish(response.post, response.post.moderation_status && response.post.moderation_status !== "approved" ? "Signal received and awaiting moderation." : "Signal transmitted. Refreshing Home.");
    } catch (publishError) {
      const message = publishError instanceof Error ? publishError.message : "Publish failed.";
      setError(message);
      setNote("Transmission interrupted. Your draft and completed uploads are preserved.");
    } finally {
      setPublishing(false);
    }
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

  function selectVisibility(nextVisibility: Visibility) {
    setVisibility(nextVisibility);
    setShowAudience(false);
    setNote("Audience selector uses existing server-side visibility rules.");
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
    if (!media.items.length) {
      setExpanded(true);
      setError("Choose a photo or video before attaching approved music.");
      return;
    }
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

  async function toggleMusicPreview(track: ComposerMusicTrack) {
    await musicPreviewRef.current?.unloadAsync().catch(() => undefined);
    musicPreviewRef.current = null;
    if (previewingTrackId === track.id) {
      setPreviewingTrackId("");
      return;
    }
    if (!track.previewUrl) {
      setError("This approved track does not have a preview available.");
      return;
    }
    try {
      const { sound } = await Audio.Sound.createAsync({ uri: track.previewUrl }, { shouldPlay: true, volume: 0.8 });
      musicPreviewRef.current = sound;
      setPreviewingTrackId(track.id);
      sound.setOnPlaybackStatusUpdate((status) => {
        if (status.isLoaded && status.didJustFinish) setPreviewingTrackId("");
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
      setNote("Reel mode requires video media. Use Video or Reel Camera for backend-safe creation.");
    }
  }

  const hasPublishPayload = Boolean(
    body.trim() ||
      media.items.length ||
      lastFailedPublish ||
      mode === "live"
  );
  const hasActiveComposerState = Boolean(
    error ||
      draftRecovered ||
      lastFailedPublish ||
      media.items.length
  );

  return (
    <LogiNexusPanel style={[styles.wrap, focused && styles.wrapFocused]} tone={mode === "live" ? "danger" : mode === "reel" ? "creator" : "default"}>
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
              <ComposerAction label="Camera" icon="◎" onPress={() => onOpenCamera("photo")} />
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
            <Pressable key={option} style={[styles.audienceOption, visibility === option && styles.audienceOptionActive]} onPress={() => selectVisibility(option)}>
              <Text style={[styles.audienceOptionText, visibility === option && styles.audienceOptionTextActive]}>{visibilityLabel(option)}</Text>
            </Pressable>
          ))}
        </View>
      ) : null}
      <View style={styles.modeRow}>
        {PRIMARY_MODES.map((item) => (
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
      <View style={styles.actionGrid}>
        <ComposerAction testID="home-composer-photo" label="Photo" icon="▧" onPress={() => media.chooseImages().then((assets) => setNote(assets.length ? `${assets.length} photo${assets.length === 1 ? "" : "s"} added to the upload queue.` : "No photos selected. If access was denied, allow Photos in Settings.")).catch((selectionError) => setError(selectionError instanceof Error ? selectionError.message : "Photos could not open."))} />
        <ComposerAction testID="home-composer-video" label="Video" icon="▶" onPress={() => media.chooseVideo().then((asset) => setNote(asset ? "Video selected for PulseSoc upload." : "No video selected. If access was denied, allow Photos in Settings.")).catch((selectionError) => setError(selectionError instanceof Error ? selectionError.message : "Videos could not open."))} />
        <ComposerAction label={musicTrack ? "Music ✓" : "Music"} icon="♪" selected={Boolean(musicTrack)} onPress={() => openMusicPicker().catch(() => undefined)} />
        <ComposerAction label="Feeling" icon="☺" onPress={() => {
          setError("Structured feelings are not supported by the production post contract yet.");
          setNote("PulseSoc will not rewrite your post body or create native-only feeling metadata.");
        }} />
        <ComposerAction testID="home-composer-camera" label="Camera" icon="◎" onPress={() => onOpenCamera(mode === "reel" ? "reel" : "photo")} />
        <ComposerAction testID="home-composer-more" label={showTools ? "Less" : "More"} icon="…" selected={showTools} onPress={() => setShowTools((current) => !current)} />
      </View>
      {showTools ? (
        <View testID="home-composer-more-tools" style={styles.toolsPanel}>
          <ComposerAction label={topic || "Topic"} icon="#" onPress={() => {
            const next = topic ? "" : "pulse";
            setTopic(next);
            setNote(next ? "Topic tag added for backend publish." : "Topic tag cleared.");
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
            {musicTrack ? <Pressable style={styles.musicUtility} onPress={() => { setMusicTrack(null); setNote("Music removed."); }}><Text style={styles.musicUtilityText}>Remove music</Text></Pressable> : null}
            <Pressable style={styles.musicUtility} onPress={onOpenMusic}><Text style={styles.musicUtilityText}>Open full library</Text></Pressable>
          </View>
        </View>
      ) : null}
      <View style={styles.publishRow}>
        <Text testID="home-composer-counter" style={[styles.counter, characters > MAX_BODY * 0.9 && styles.counterWarning]}>{characters.toLocaleString()}/{MAX_BODY.toLocaleString()}</Text>
        <Pressable
          testID="home-composer-publish"
          accessibilityRole="button"
          accessibilityLabel={mode === "live" ? "Open Live Studio" : "Publish Signal"}
          accessibilityState={{ disabled: publishing || !hasPublishPayload }}
          style={[styles.publishButton, (!hasPublishPayload || publishing) && styles.publishButtonDisabled]}
          disabled={publishing || !hasPublishPayload}
          onPress={handlePublish}
        >
          <Text style={styles.publishText}>{publishing ? "Transmitting…" : mode === "live" ? "Open Live" : "Transmit"}</Text>
        </Pressable>
      </View>
      <ComposerMediaQueue items={media.items} onCancel={media.cancel} onRetry={(id) => media.retry(id).catch(() => undefined)} onRemove={media.remove} onMove={media.move} />
      {draftRecovered ? (
        <View testID="home-composer-recovered-draft" style={styles.draftPanel}>
          <Text style={styles.draftText}>Recovered transmission draft.</Text>
          <Pressable testID="home-composer-clear-draft" style={styles.draftButton} onPress={() => clearDraft().catch(() => undefined)}>
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
        <Pressable testID="home-composer-retry" style={styles.retryButton} disabled={publishing} onPress={() => retryLastPublish().catch(() => undefined)}>
          <Text style={styles.retryText}>{publishing ? "Retrying..." : "Retry Last Publish"}</Text>
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
}) {
  return payload;
}

function normalizeDraft(raw: HomeComposerDraft) {
  if (!raw || typeof raw !== "object") return null;
  const mode = ["post", "reel", "live", "poll", "scam_report"].includes(raw.mode) ? raw.mode : "post";
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
  if (value.kind === "post" && typeof value.payload.body === "string") return value;
  return null;
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

const styles = StyleSheet.create({
  actionButton: {
    alignItems: "center",
    backgroundColor: "rgba(9, 20, 33, 0.54)",
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
    backgroundColor: "rgba(3, 10, 21, 0.54)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 18,
    borderWidth: 1,
    flexDirection: "row",
    gap: 12,
    minHeight: 52,
    paddingHorizontal: 8,
    paddingVertical: 7
  },
  collapsedShell: {
    gap: 7
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
    fontSize: 10,
    fontWeight: "800",
    marginTop: 2
  },
  collapsedOpen: {
    color: colors.accent,
    fontSize: 25,
    fontWeight: "700"
  },
  collapsedPrompt: {
    color: colors.muted,
    fontSize: 14,
    fontWeight: "700"
  },
  collapsedQuickRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 7
  },
  collapsedQuickTools: {
    alignItems: "center",
    backgroundColor: "rgba(3, 9, 18, 0.42)",
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: 16,
    borderWidth: 1,
    flex: 1,
    flexDirection: "row",
    gap: 0,
    minHeight: 44,
    overflow: "hidden",
    padding: 3
  },
  collapsedCreateButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderColor: "rgba(255,255,255,0.2)",
    borderRadius: 20,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 44,
    paddingHorizontal: 18
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
  identityImage: {
    borderRadius: 19,
    height: 38,
    width: 38
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
    fontSize: 9,
    fontWeight: "900",
    letterSpacing: 1.6
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
    backgroundColor: "rgba(3, 7, 18, 0.4)",
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
    backgroundColor: "rgba(3, 7, 18, 0.72)",
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
    backgroundColor: "rgba(9, 20, 33, 0.74)",
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
    backgroundColor: "rgba(3, 7, 18, 0.42)",
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
    backgroundColor: "rgba(7, 19, 32, 0.82)",
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
});
