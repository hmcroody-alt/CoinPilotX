import { Audio, ResizeMode, Video } from "expo-av";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Animated, Image, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { PanGestureHandler, PinchGestureHandler, State, TapGestureHandler } from "react-native-gesture-handler";
import { mediaDisplayUrl, mediaKind, PulseAuthor, PulseMedia } from "../api/feed";
import { pollNativeMediaProcessing } from "../media/nativeMediaUpload";
import { colors } from "../theme/colors";
import { sharePulseObject } from "../sharing/nativeShare";
import { claimMediaPlayback, releaseMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { configureReelsAudioSession } from "../core/reelsAudioSession";
import { AttachedMusicPolicy, resolveViewerAudioPlan } from "../core/attachedMusicAudioPolicy";
import { LikeBurst, LikeBurstHandle } from "../media/MediaGestureFeedback";
import { createThemedStyles } from "../theme/themedStyles";

export type NativeMediaViewerItem = {
  id?: number;
  media?: PulseMedia;
  kind?: "image" | "video" | "file";
  url: string;
  thumbnailUrl?: string;
  title?: string;
  subtitle?: string;
  alt?: string;
  author?: PulseAuthor;
  sourceUrl?: string;
  processingStatus?: string;
  /**
   * Attached-music audio policy for this item, resolved from the post/reel/status
   * metadata by the caller (e.g. `resolvePostAudioPolicy`). When present and
   * exclusive, the viewer mutes the video's original audio and plays the attached
   * track instead — matching the inline feed player so opening a post never drops
   * its selected soundtrack.
   */
  musicPolicy?: AttachedMusicPolicy;
};

type Props = {
  visible: boolean;
  items: NativeMediaViewerItem[];
  initialIndex?: number;
  title?: string;
  onClose: () => void;
  onSave?: (item: NativeMediaViewerItem) => void;
  onShare?: (item: NativeMediaViewerItem) => void;
  onAuthorPress?: (item: NativeMediaViewerItem) => void;
  /**
   * Opt-in double-tap-to-like for image media (photo statuses, mixed-media
   * posts). Only rendered when a handler is passed, so callers that don't
   * need it (Marketplace, Messenger) are completely unaffected. Scoped to
   * images: video items keep their native scrubber controls untouched, so a
   * gesture layer here never eats the taps that open/close native chrome.
   */
  onLike?: (item: NativeMediaViewerItem) => void;
};

export const nativeMediaViewerIntegrationTargets = [
  "Feed/Post",
  "Messenger",
  "Profile",
  "Status",
  "Reels",
  "Marketplace",
  "Creator Studio"
];

export function NativeMediaViewer({ visible, items, initialIndex = 0, title = "Media", onClose, onSave, onShare, onAuthorPress, onLike }: Props) {
  const [index, setIndex] = useState(initialIndex);
  const [failed, setFailed] = useState(false);
  const [buffering, setBuffering] = useState(false);
  const [checking, setChecking] = useState(false);
  const [processingMessage, setProcessingMessage] = useState("");
  const videoRef = useRef<Video>(null);
  const attachedSoundRef = useRef<Audio.Sound | null>(null);
  const videoPlayingRef = useRef(false);
  const scale = useRef(new Animated.Value(1)).current;
  const translateY = useRef(new Animated.Value(0)).current;
  const likeBurstRef = useRef<LikeBurstHandle>(null);
  const panRef = useRef<PanGestureHandler>(null);
  const pinchRef = useRef<PinchGestureHandler>(null);
  const doubleTapRef = useRef<TapGestureHandler>(null);
  const item = items[index] || items[0];
  const author = item?.author || {};
  const kind = item?.kind || (item?.media ? mediaKind(item.media) : "file");
  const processing = isProcessing(item);
  const canGoPrevious = index > 0;
  const canGoNext = index < items.length - 1;
  const playbackOwnerId = `media-viewer:${item?.id || index}`;
  // Attached music takes exclusive audio priority everywhere a post is played,
  // including this expanded/fullscreen viewer. Derive the same plan the inline
  // feed player uses so opening a post keeps its selected soundtrack instead of
  // reverting to the original video audio.
  const audioPlan = useMemo(() => resolveViewerAudioPlan(item?.musicPolicy), [item?.musicPolicy]);
  const videoMuted = kind === "video" && audioPlan.muteOriginalAudio;
  const shouldPlayAttachedMusic = kind === "video" && audioPlan.shouldPlayMusic && Boolean(audioPlan.musicUrl);

  useEffect(() => {
    if (!visible || kind !== "video" || !item?.url) {
      releaseMediaPlayback(playbackOwnerId).catch(() => undefined);
      return;
    }
    claimMediaPlayback({
      id: playbackOwnerId,
      kind: "viewer",
      pause: () => videoRef.current?.pauseAsync().then(() => undefined),
      stop: () => videoRef.current?.stopAsync().then(() => undefined)
    }).then((granted) => granted ? videoRef.current?.playAsync() : undefined).catch(() => undefined);
    return () => { releaseMediaPlayback(playbackOwnerId).catch(() => undefined); };
  }, [item?.url, kind, playbackOwnerId, visible]);

  // Load (and tear down) the attached-music track that must play in place of the
  // original video audio. Re-runs whenever the visible item, its track, or the
  // modal visibility changes so switching media in the gallery swaps the track.
  useEffect(() => {
    let cancelled = false;
    videoPlayingRef.current = false;
    async function syncAttachedMusic() {
      const existing = attachedSoundRef.current;
      attachedSoundRef.current = null;
      if (existing) await existing.unloadAsync().catch(() => undefined);
      if (!visible || !shouldPlayAttachedMusic || !audioPlan.musicUrl) return;
      // Put the iOS audio session into playback mode so the track is audible even
      // when the ringer switch is silent, mirroring the inline/Reels players.
      await configureReelsAudioSession().catch(() => undefined);
      const created = await Audio.Sound.createAsync(
        { uri: audioPlan.musicUrl },
        {
          isLooping: audioPlan.isLooping,
          positionMillis: audioPlan.musicStartMs,
          volume: audioPlan.musicVolume,
          // Start paused; the video's playback status drives play/pause so the
          // music stays synchronized with the (muted) video's pause state.
          shouldPlay: false
        }
      );
      if (cancelled || attachedSoundRef.current) {
        await created.sound.unloadAsync().catch(() => undefined);
        return;
      }
      attachedSoundRef.current = created.sound;
      // If the video is already reported playing, catch the track up immediately.
      if (videoPlayingRef.current) {
        await created.sound.playAsync().catch(() => undefined);
      }
    }
    syncAttachedMusic().catch(() => undefined);
    return () => { cancelled = true; };
  }, [audioPlan.isLooping, audioPlan.musicStartMs, audioPlan.musicUrl, audioPlan.musicVolume, shouldPlayAttachedMusic, visible]);

  // Guaranteed teardown: never leave a music track playing after the viewer
  // unmounts (e.g. the parent screen navigates away while the modal is open).
  useEffect(() => () => {
    attachedSoundRef.current?.unloadAsync().catch(() => undefined);
    attachedSoundRef.current = null;
  }, []);

  useEffect(() => {
    if (!visible) return;
    setIndex(Math.max(0, Math.min(initialIndex, items.length - 1)));
  }, [initialIndex, items.length, visible]);

  useEffect(() => {
    setFailed(false);
    setBuffering(false);
    setProcessingMessage("");
    scale.setValue(1);
    translateY.setValue(0);
  }, [index, scale, translateY]);

  const pinchEvent = useMemo(
    () =>
      Animated.event([{ nativeEvent: { scale } }], {
        useNativeDriver: true
      }),
    [scale]
  );

  const panEvent = useMemo(
    () =>
      Animated.event([{ nativeEvent: { translationY: translateY } }], {
        useNativeDriver: true
      }),
    [translateY]
  );

  if (!item) return null;

  function handleImageDoubleTap(event: { nativeEvent: { state: number; x: number; y: number } }) {
    if (event.nativeEvent.state !== State.ACTIVE || !onLike) return;
    onLike(item);
    likeBurstRef.current?.trigger(event.nativeEvent.x, event.nativeEvent.y);
  }

  async function shareItem() {
    if (onShare) {
      onShare(item);
      return;
    }
    await sharePulseObject({
      kind: "media",
      url: item.sourceUrl || item.url,
      title: item.title || title,
      description: item.subtitle,
      author: item.author?.display_name || item.author?.name || item.author?.username,
      previewImageUrl: item.thumbnailUrl || (item.kind === "image" ? item.url : undefined)
    }).catch(() => undefined);
  }

  async function checkProcessing() {
    const mediaId = Number(item.id || item.media?.id || 0);
    if (!mediaId || checking) return;
    setChecking(true);
    setProcessingMessage("Checking media processing status.");
    try {
      const result = await pollNativeMediaProcessing(mediaId, 4, 1200, (progress) => setProcessingMessage(progress.message));
      const status = result.processing_status || result.media?.processing_status || "processing";
      setProcessingMessage(status === "ready" ? "Media is ready. Refresh the surface to reload it." : "Media is still processing.");
    } catch (error) {
      setProcessingMessage(error instanceof Error ? error.message : "Media processing check failed.");
    } finally {
      setChecking(false);
    }
  }

  return (
    <Modal visible={visible} animationType="fade" onRequestClose={onClose}>
      <View style={styles.root} testID="native-media-viewer" accessibilityLabel="Native media viewer">
        <PanGestureHandler
          ref={panRef}
          onGestureEvent={panEvent}
          onHandlerStateChange={(event) => {
            if (event.nativeEvent.state === State.END) {
              if (Math.abs(event.nativeEvent.translationY) > 90) onClose();
              Animated.spring(translateY, { toValue: 0, useNativeDriver: true }).start();
            }
          }}
        >
          <Animated.View style={[styles.stage, { transform: [{ translateY }] }]}>
            {processing ? (
              <ProcessingState checking={checking} message={processingMessage || item.processingStatus || "PulseSoc is processing this media."} onRetry={checkProcessing} />
            ) : kind === "image" && item.url ? (
              <PinchGestureHandler
                ref={pinchRef}
                simultaneousHandlers={onLike ? [doubleTapRef] : undefined}
                onGestureEvent={pinchEvent}
                onHandlerStateChange={(event) => {
                  if (event.nativeEvent.state === State.END) {
                    Animated.spring(scale, { toValue: 1, useNativeDriver: true }).start();
                  }
                }}
              >
                <Animated.View style={styles.imageWrap}>
                  {onLike ? (
                    <TapGestureHandler ref={doubleTapRef} numberOfTaps={2} simultaneousHandlers={[pinchRef]} onHandlerStateChange={handleImageDoubleTap}>
                      <Animated.Image source={{ uri: item.url }} style={[styles.image, { transform: [{ scale }] }]} resizeMode="contain" onError={() => setFailed(true)} />
                    </TapGestureHandler>
                  ) : (
                    <Animated.Image source={{ uri: item.url }} style={[styles.image, { transform: [{ scale }] }]} resizeMode="contain" onError={() => setFailed(true)} />
                  )}
                </Animated.View>
              </PinchGestureHandler>
            ) : kind === "video" && item.url && !failed ? (
              <Video
                ref={videoRef}
                source={{ uri: item.url }}
                style={styles.video}
                resizeMode={ResizeMode.CONTAIN}
                useNativeControls
                shouldPlay={false}
                isLooping={false}
                isMuted={videoMuted}
                usePoster={Boolean(item.thumbnailUrl)}
                posterSource={item.thumbnailUrl ? { uri: item.thumbnailUrl } : undefined}
                onPlaybackStatusUpdate={(status) => {
                  if (!status.isLoaded) {
                    setFailed(Boolean(status.error));
                    setBuffering(false);
                    return;
                  }
                  setBuffering(Boolean(status.isBuffering));
                  // Keep the attached-music track in lockstep with the video's
                  // play/pause state. Only act on transitions so we don't spam
                  // the sound API on every progress tick.
                  const music = attachedSoundRef.current;
                  const playing = Boolean(status.isPlaying);
                  if (videoPlayingRef.current !== playing) {
                    videoPlayingRef.current = playing;
                    if (music) {
                      music.setStatusAsync({ shouldPlay: playing }).catch(() => undefined);
                    }
                  }
                }}
                onError={() => setFailed(true)}
              />
            ) : (
              <UnsupportedState item={item} failed={failed} onShare={shareItem} />
            )}
          </Animated.View>
        </PanGestureHandler>

        {onLike && kind === "image" ? <LikeBurst ref={likeBurstRef} /> : null}

        {buffering ? (
          <View style={styles.buffering}>
            <ActivityIndicator color={colors.accent} />
          </View>
        ) : null}

        <View style={styles.topBar}>
          <Pressable testID="native-media-viewer-close" accessibilityRole="button" accessibilityLabel="Close media viewer" style={styles.closeButton} onPress={onClose}>
            <Text style={styles.closeText}>Close</Text>
          </Pressable>
          <View style={styles.titleWrap}>
            <Text style={styles.title} numberOfLines={1}>{item.title || title}</Text>
            <Text style={styles.subtitle} numberOfLines={1}>{item.subtitle || item.alt || `${index + 1} of ${items.length}`}</Text>
          </View>
        </View>

        <View style={styles.footer}>
          {author.display_name || author.username ? (
            <Pressable style={styles.authorButton} disabled={!onAuthorPress} onPress={() => onAuthorPress?.(item)}>
              {author.avatar_url ? <Image source={{ uri: author.avatar_url }} style={styles.avatar} /> : <View style={styles.avatarFallback} />}
              <View style={styles.authorText}>
                <Text style={styles.authorName} numberOfLines={1}>{author.display_name || author.name || "PulseSoc"}</Text>
                <Text style={styles.authorMeta} numberOfLines={1}>{author.username ? `@${author.username}` : "Profile"}</Text>
              </View>
            </Pressable>
          ) : null}
          <View style={styles.actions}>
            <Pressable testID="native-media-viewer-prev" accessibilityRole="button" accessibilityLabel="Previous media" style={[styles.actionButton, !canGoPrevious && styles.disabled]} disabled={!canGoPrevious} onPress={() => setIndex((current) => Math.max(0, current - 1))}>
              <Text style={styles.actionText}>Prev</Text>
            </Pressable>
            <Pressable testID="native-media-viewer-next" accessibilityRole="button" accessibilityLabel="Next media" style={[styles.actionButton, !canGoNext && styles.disabled]} disabled={!canGoNext} onPress={() => setIndex((current) => Math.min(items.length - 1, current + 1))}>
              <Text style={styles.actionText}>Next</Text>
            </Pressable>
            {onSave ? (
              <Pressable style={styles.actionButton} onPress={() => onSave(item)}>
                <Text style={styles.actionText}>Save</Text>
              </Pressable>
            ) : null}
            <Pressable testID="native-media-viewer-share" accessibilityRole="button" accessibilityLabel="Share media" style={styles.actionButton} onPress={shareItem}>
              <Text style={styles.actionText}>Share</Text>
            </Pressable>
          </View>
        </View>
      </View>
    </Modal>
  );
}

export function mediaViewerItemFromPulseMedia(media: PulseMedia, context: Partial<NativeMediaViewerItem> = {}): NativeMediaViewerItem {
  const playbackUrl = mediaDisplayUrl({
    ...media,
    media_url: media.playback_url || media.hls_url || media.mux_hls_url || media.valid_url || media.media_url || media.url || ""
  });
  const thumbnailUrl = mediaDisplayUrl({
    ...media,
    media_url: media.thumbnail_url || media.poster_url || media.valid_url || media.media_url || media.url || ""
  });
  const kind = mediaKind({
    ...media,
    media_url: playbackUrl || thumbnailUrl
  }) as NativeMediaViewerItem["kind"];
  return {
    id: Number(media.id || 0),
    media,
    kind,
    url: playbackUrl || thumbnailUrl,
    thumbnailUrl,
    alt: media.alt,
    processingStatus: String(media.status || ""),
    ...context
  };
}

function isProcessing(item?: NativeMediaViewerItem) {
  const status = String(item?.processingStatus || item?.media?.status || "").toLowerCase();
  return ["processing", "queued", "pending", "transcoding", "uploading"].includes(status);
}

function ProcessingState({ checking, message, onRetry }: { checking: boolean; message: string; onRetry: () => void }) {
  return (
    <View style={styles.statePanel}>
      <ActivityIndicator color={colors.accent} />
      <Text style={styles.stateTitle}>Processing media</Text>
      <Text style={styles.stateText}>{message}</Text>
      <Pressable style={styles.stateButton} disabled={checking} onPress={onRetry}>
        <Text style={styles.stateButtonText}>{checking ? "Checking" : "Check status"}</Text>
      </Pressable>
    </View>
  );
}

function UnsupportedState({ item, failed, onShare }: { item: NativeMediaViewerItem; failed: boolean; onShare: () => void }) {
  return (
    <View style={styles.statePanel}>
      <Text style={styles.stateTitle}>{failed ? "Media unavailable" : "Unsupported media"}</Text>
      <Text style={styles.stateText}>The app cannot show this file yet. Open it from its source instead.</Text>
      <Pressable style={styles.stateButton} onPress={onShare}>
        <Text style={styles.stateButtonText}>Share link</Text>
      </Pressable>
      {item.sourceUrl ? <Text style={styles.sourceText} numberOfLines={1}>{item.sourceUrl}</Text> : null}
    </View>
  );
}

const styles = createThemedStyles(() => ({
  actionButton: {
    borderColor: "rgba(255,255,255,0.16)",
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 38,
    paddingHorizontal: 12,
    paddingVertical: 9
  },
  actionText: {
    color: colors.text,
    fontWeight: "900"
  },
  actions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    justifyContent: "flex-end"
  },
  authorButton: {
    alignItems: "center",
    flex: 1,
    flexDirection: "row",
    gap: 10,
    minWidth: 160
  },
  authorMeta: {
    color: "rgba(244,247,251,0.72)",
    fontSize: 12
  },
  authorName: {
    color: colors.text,
    fontWeight: "900"
  },
  authorText: {
    flex: 1
  },
  avatar: {
    borderRadius: 18,
    height: 36,
    width: 36
  },
  avatarFallback: {
    backgroundColor: colors.accent,
    borderRadius: 18,
    height: 36,
    width: 36
  },
  buffering: {
    left: 0,
    position: "absolute",
    right: 0,
    top: "46%",
    zIndex: 8
  },
  closeButton: {
    backgroundColor: "rgba(8,15,28,0.72)",
    borderColor: "rgba(255,255,255,0.14)",
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 9
  },
  closeText: {
    color: colors.text,
    fontWeight: "900"
  },
  disabled: {
    opacity: 0.4
  },
  footer: {
    alignItems: "center",
    bottom: 22,
    flexDirection: "row",
    gap: 12,
    left: 14,
    position: "absolute",
    right: 14,
    zIndex: 10
  },
  image: {
    height: "100%",
    width: "100%"
  },
  imageWrap: {
    flex: 1
  },
  root: {
    backgroundColor: "#02050b",
    flex: 1
  },
  sourceText: {
    color: colors.muted,
    fontSize: 11,
    marginTop: 8,
    maxWidth: "90%"
  },
  stage: {
    flex: 1
  },
  stateButton: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    marginTop: 12,
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  stateButtonText: {
    color: colors.background,
    fontWeight: "900"
  },
  statePanel: {
    alignItems: "center",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  stateText: {
    color: colors.muted,
    lineHeight: 21,
    marginTop: 8,
    textAlign: "center"
  },
  stateTitle: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900",
    marginTop: 10
  },
  subtitle: {
    color: "rgba(244,247,251,0.72)",
    fontSize: 12,
    marginTop: 2
  },
  title: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  titleWrap: {
    flex: 1
  },
  topBar: {
    alignItems: "center",
    flexDirection: "row",
    gap: 12,
    left: 14,
    position: "absolute",
    right: 14,
    top: 42,
    zIndex: 10
  },
  video: {
    flex: 1
  }
}));
