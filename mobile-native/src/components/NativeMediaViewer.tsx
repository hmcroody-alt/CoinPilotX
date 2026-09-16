import { Audio, ResizeMode, Video } from "expo-av";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Animated, Dimensions, Image, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import { PanGestureHandler, PinchGestureHandler, State, TapGestureHandler } from "react-native-gesture-handler";
import { mediaDisplayUrl, mediaKind, PulseAuthor, PulseMedia } from "../api/feed";
import { pollNativeMediaProcessing } from "../media/nativeMediaUpload";
import { colors } from "../theme/colors";
import { saveMediaToGallery, shareMedia, type MediaActionTarget } from "../media/mediaActions";
import { namespacedMediaId } from "../media/mediaCache";
import { claimMediaPlayback, releaseMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { configureReelsAudioSession } from "../core/reelsAudioSession";
import { AttachedMusicPolicy, resolveViewerAudioPlan } from "../core/attachedMusicAudioPolicy";
import { LikeBurst, LikeBurstHandle } from "../media/MediaGestureFeedback";
import { createThemedStyles } from "../theme/themedStyles";

export type NativeMediaViewerItem = {
  id?: number;
  /**
   * What the underlying file is cached under, namespaced by the id space it came
   * from — see `namespacedMediaId`.
   *
   * Deliberately not derived from `id`. Producers set `id` to whichever row they
   * built the item from, and Messenger sets a *message* id there, so keying the
   * media cache on it would file a chat attachment under a feed media row's
   * number and hand one of them the other's bytes.
   */
  cacheIdentity?: string | null;
  media?: PulseMedia;
  kind?: "image" | "video" | "file";
  /**
   * The wire MIME type, for producers that have one without a `media` record.
   *
   * This is not cosmetic. It is the only input that gives the cached file an
   * extension: the download engine derives one from the MIME type, falling back
   * to the URL's own suffix, and a Messenger access URL ends in `/download`. An
   * item that arrives here without a MIME type is therefore written to disk with
   * no extension at all, which renders and shares fine but which the photo
   * library write rejects outright — Photos routes on the extension, not on the
   * bytes. The user sees "could not save this to your library" for a photo that
   * is sitting decoded on their screen.
   */
  mimeType?: string;
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
  /**
   * Mint a fresh access URL for this exact item, for Save and Share (§8).
   *
   * Only producers whose URLs are time-limited credentials set this — Messenger
   * does, feed media does not. Absent, Save and Share behave as before: one
   * attempt against `url`, and an authorization failure is reported as one.
   */
  refreshUrl?: () => Promise<string>;
};

/** Horizontal travel, in points, that commits a swipe to the next/previous item. */
export const SWIPE_COMMIT_DISTANCE = 60;
/** Vertical travel that commits a dismiss. Unchanged; named so the two can be compared. */
export const DISMISS_COMMIT_DISTANCE = 90;
/** Ceiling on pinch zoom. Beyond this a photo is texture, not content. */
export const MAX_ZOOM = 4;
/** What a double-tap zooms to, and toggles back from. */
export const DOUBLE_TAP_ZOOM = 2.5;
/**
 * How long a video may show nothing before the viewer calls it a failure.
 *
 * Generous on purpose: a cold segment fetch on a poor connection is allowed to
 * take a while, and the poster is showing throughout, so this is not a deadline
 * for a good load. It is the floor under a source that will never report
 * anything at all.
 */
export const FIRST_FRAME_TIMEOUT_MS = 15_000;

type Props = {
  visible: boolean;
  items: NativeMediaViewerItem[];
  initialIndex?: number;
  /**
   * Drive the position from outside. Pass this together with `onIndexChange` to
   * make the viewer controlled.
   *
   * The chat gallery must be controlled, and the reason is specific: its
   * collection grows while the viewer is open. When an older page lands, every
   * item is pushed up by however many arrived, so an index the viewer had kept
   * privately would now name a different photo — the picture would change under
   * the user's hands. The owner tracks the item by key and recomputes the
   * position, which is a thing only the owner can do.
   *
   * Left undefined, the viewer stays uncontrolled and every existing caller
   * behaves exactly as before.
   */
  index?: number;
  onIndexChange?: (index: number) => void;
  /**
   * Size of the whole collection when more of it exists than has been paged in,
   * so the counter can say "12 of 43" rather than "12 of 60". Falls back to
   * `items.length`.
   */
  totalCount?: number;
  /**
   * Swipe left/right to move through `items`.
   *
   * Off by default. The surfaces that show a single item, or that rely on the
   * horizontal axis for something else, must not grow a gesture they never
   * asked for.
   */
  swipeToNavigate?: boolean;
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
  /**
   * Which product surface this viewer instance is showing, for per-surface
   * media telemetry. Never carries a URL.
   */
  surface?: string;
  /**
   * Share the canonical PulseSoc link rather than the file itself.
   *
   * Off by default because the viewer's job is showing a *file*, and a person
   * who taps Share on a photo wants the photo. Feed and Reels turn it on: a post
   * has an author, comments and an OpenGraph preview, none of which survive
   * being flattened into a JPEG (Stage 8).
   */
  shareAsLink?: boolean;
  /**
   * Hide "Save to Photos" where product policy forbids downloading — disappearing
   * media, DRM-bound marketplace assets. Defaults to on, because a viewer that
   * cannot save is the gap this foundation exists to close.
   */
  allowGallerySave?: boolean;
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

export function NativeMediaViewer({
  visible,
  items,
  initialIndex = 0,
  index: controlledIndex,
  onIndexChange,
  totalCount,
  swipeToNavigate = false,
  title = "Media",
  onClose,
  onSave,
  onShare,
  onAuthorPress,
  onLike,
  surface,
  shareAsLink = false,
  allowGallerySave = true
}: Props) {
  const [internalIndex, setInternalIndex] = useState(initialIndex);
  const [failed, setFailed] = useState(false);
  const [buffering, setBuffering] = useState(false);
  const [checking, setChecking] = useState(false);
  const [processingMessage, setProcessingMessage] = useState("");
  /**
   * Transient status line under the actions. The viewer reports the *actual*
   * outcome of a save — including "we asked and you said no" and "downloaded but
   * the write failed" — because Stage 7 forbids reporting "Saved" for anything
   * other than a completed write.
   */
  const [actionStatus, setActionStatus] = useState("");
  const [savingToGallery, setSavingToGallery] = useState(false);
  const videoRef = useRef<Video>(null);
  const attachedSoundRef = useRef<Audio.Sound | null>(null);
  const videoPlayingRef = useRef(false);
  /** Has THIS source ever reported a loaded status? Drives the watchdog below. */
  const loadedOnceRef = useRef(false);
  /**
   * Zoom is two values multiplied, not one value assigned.
   *
   * `baseScale` is what the photo is zoomed to right now and survives the end of
   * a gesture; `pinchScale` is the live gesture, which always starts at 1. A
   * single value cannot express both, which is why the previous implementation
   * had to spring back to 1 when the fingers lifted — the zoom had nowhere to
   * live. Requirement §10 is that the zoom *stays*.
   */
  const baseScale = useRef(new Animated.Value(1)).current;
  const pinchScale = useRef(new Animated.Value(1)).current;
  const scale = useRef(Animated.multiply(baseScale, pinchScale)).current;
  /** Committed zoom, readable synchronously. Animated.Value is not. */
  const zoomRef = useRef(1);
  const translateX = useRef(new Animated.Value(0)).current;
  const translateY = useRef(new Animated.Value(0)).current;
  /** Where the zoomed photo has been dragged to, committed across gestures. */
  const panOffset = useRef({ x: 0, y: 0 });
  const likeBurstRef = useRef<LikeBurstHandle>(null);
  const panRef = useRef<PanGestureHandler>(null);
  const pinchRef = useRef<PinchGestureHandler>(null);
  const doubleTapRef = useRef<TapGestureHandler>(null);
  const controlled = typeof controlledIndex === "number";
  // Clamped on read, because a controlled owner whose collection just shrank can
  // legitimately hand us a position that no longer exists for one render.
  const index = controlled
    ? Math.max(0, Math.min(controlledIndex as number, items.length - 1))
    : internalIndex;
  const item = items[index] || items[0];
  const author = item?.author || {};
  const kind = item?.kind || (item?.media ? mediaKind(item.media) : "file");
  const processing = isProcessing(item);
  const canGoPrevious = index > 0;
  const canGoNext = index < items.length - 1;
  // Photos accepts pictures and movies only, and `saveMediaToGallery` refuses
  // anything else. Hiding the button for a document is more honest than showing
  // one that can only ever answer "unsupported".
  const canSaveToGallery = allowGallerySave && (kind === "image" || kind === "video") && Boolean(item?.url) && !processing;
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
    // Capture the player while the ref is still attached.
    //
    // React detaches refs during the commit, and a `useEffect` cleanup runs
    // afterwards — so reading `videoRef.current` from the cleanup finds `null`
    // in precisely the case that matters: swiping from a video to a photo, when
    // the <Video> is being unmounted. Holding the instance in the effect's own
    // closure is what makes the pause below reach a real player.
    const player = videoRef.current;
    claimMediaPlayback({
      id: playbackOwnerId,
      kind: "viewer",
      // These run while the component is mounted and the coordinator wants the
      // *current* player, so they stay on the live ref.
      pause: () => videoRef.current?.pauseAsync().then(() => undefined),
      stop: () => videoRef.current?.stopAsync().then(() => undefined)
    }).then((granted) => granted ? videoRef.current?.playAsync() : undefined).catch(() => undefined);
    return () => {
      // Pause the outgoing video *before* handing the claim back. Releasing only
      // tells the coordinator nobody owns playback any more; it does not stop a
      // player that is already running, and on a video→video swipe the same
      // <Video> survives with a new source. Without this, audio from the item
      // you swiped away keeps playing over the next one.
      // `pauseAsync` touches this player only — it is not an audio-session call.
      player?.pauseAsync().catch(() => undefined);
      releaseMediaPlayback(playbackOwnerId).catch(() => undefined);
    };
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
    // A controlled viewer takes its position from its owner. Seeding from
    // `initialIndex` here would fight that owner on every collection change —
    // the gallery pages in older media, `items.length` moves, this effect fires,
    // and the photo on screen jumps back to wherever the caller first opened.
    if (!visible || controlled) return;
    setInternalIndex(Math.max(0, Math.min(initialIndex, items.length - 1)));
  }, [controlled, initialIndex, items.length, visible]);

  useEffect(() => {
    setBuffering(false);
    setProcessingMessage("");
    // The status line describes one specific file. Swiping to the next item must
    // not leave "Saved to your library." sitting under a photo that was not saved.
    setActionStatus("");
    // A new photo arrives unzoomed and centred. Carrying the previous item's zoom
    // over would open the next picture already cropped into its middle.
    zoomRef.current = 1;
    panOffset.current = { x: 0, y: 0 };
    baseScale.setValue(1);
    pinchScale.setValue(1);
    translateX.setOffset(0);
    translateX.setValue(0);
    translateY.setOffset(0);
    translateY.setValue(0);
  }, [baseScale, index, pinchScale, translateX, translateY]);

  const pinchEvent = useMemo(
    () =>
      Animated.event([{ nativeEvent: { scale: pinchScale } }], {
        useNativeDriver: true
      }),
    [pinchScale]
  );

  const panEvent = useMemo(
    () =>
      Animated.event([{ nativeEvent: { translationX: translateX, translationY: translateY } }], {
        useNativeDriver: true
      }),
    [translateX, translateY]
  );

  /**
   * Per-item load state, reset DURING RENDER and keyed on the item's identity.
   *
   * Two bugs live in the obvious alternative (`useEffect(..., [index])`):
   *
   *   - It keys on a POSITION. The gallery pages older media in underneath, so
   *     the same photo's index moves while the photo does not, and a different
   *     photo can arrive at the same index. When the item changes but the index
   *     does not, the effect never fires and the previous item's `failed` stays
   *     on — one dead video reads as "everything after it is broken too".
   *   - It fires AFTER the commit. React paints one frame with the new item and
   *     the old item's state, which is a visible flash of the previous item's
   *     error card over the new item's media.
   *
   * Assigning during render is React's documented pattern for exactly this: the
   * re-render happens before anything is painted, so there is no intermediate
   * frame and no effect ordering to reason about.
   */
  const identityKey = String(item?.cacheIdentity || item?.id || item?.url || "");
  const [loadStateKey, setLoadStateKey] = useState(identityKey);
  if (loadStateKey !== identityKey) {
    setLoadStateKey(identityKey);
    setFailed(false);
    loadedOnceRef.current = false;
  }

  /**
   * A video that never loads and never errors must still stop looking like one
   * that is loading.
   *
   * This is the generalisation of the bug that opened this mission. AVPlayer
   * rejected a site-relative URL with NSURLErrorUnsupportedURL and expo-av
   * surfaced it as `isLoaded: false` with no `error`, so there was no event to
   * render — the viewer sat on a black rectangle indefinitely with every control
   * working and no way for the user (or a test) to tell it had failed.
   *
   * The URL bug is fixed at the source. This exists so the NEXT source that
   * fails without an error event is reported instead of disappearing: an
   * unrecognised codec, a 302 to somewhere unreachable, a DNS hole. Bounded to
   * "has never reported a loaded status", so a buffering stall on a video that
   * already played is untouched.
   */
  const watchdogUrl = kind === "video" ? item?.url || "" : "";
  useEffect(() => {
    if (!visible || !watchdogUrl) return;
    const timer = setTimeout(() => {
      if (!loadedOnceRef.current) {
        console.warn(`[NativeMediaViewer] no first frame in ${FIRST_FRAME_TIMEOUT_MS}ms: ${watchdogUrl.slice(0, 120)}`);
        setFailed(true);
      }
    }, FIRST_FRAME_TIMEOUT_MS);
    return () => clearTimeout(timer);
  }, [visible, watchdogUrl, loadStateKey]);

  if (!item) return null;

  /**
   * The one place the position changes, controlled or not.
   *
   * Controlled callers are *told*; they are not written to. Writing local state
   * as well would give the viewer a second opinion about which photo is showing,
   * and the two would disagree the moment the owner's collection moved.
   */
  function goToIndex(next: number) {
    const clamped = Math.max(0, Math.min(next, items.length - 1));
    if (clamped === index) return;
    if (!controlled) setInternalIndex(clamped);
    onIndexChange?.(clamped);
  }

  /** How far a photo at this zoom can be dragged before it shows empty space. */
  function panBounds() {
    const window = Dimensions.get("window");
    const overflow = Math.max(0, zoomRef.current - 1) / 2;
    return { x: window.width * overflow, y: window.height * overflow };
  }

  function settleZoom(next: number) {
    const clamped = Math.max(1, Math.min(next, MAX_ZOOM));
    zoomRef.current = clamped;
    pinchScale.setValue(1);
    Animated.spring(baseScale, { toValue: clamped, useNativeDriver: true }).start();
    if (clamped === 1) recentre();
    else clampPan();
  }

  function recentre() {
    panOffset.current = { x: 0, y: 0 };
    translateX.setOffset(0);
    translateY.setOffset(0);
    Animated.spring(translateX, { toValue: 0, useNativeDriver: true }).start();
    Animated.spring(translateY, { toValue: 0, useNativeDriver: true }).start();
  }

  /** Commit the live drag into the persistent offset, inside the zoom's bounds. */
  function clampPan(translationX = 0, translationY = 0) {
    const bounds = panBounds();
    const x = Math.max(-bounds.x, Math.min(panOffset.current.x + translationX, bounds.x));
    const y = Math.max(-bounds.y, Math.min(panOffset.current.y + translationY, bounds.y));
    panOffset.current = { x, y };
    translateX.setOffset(x);
    translateY.setOffset(y);
    translateX.setValue(0);
    translateY.setValue(0);
  }

  function handleImageDoubleTap(event: { nativeEvent: { state: number; x: number; y: number } }) {
    if (event.nativeEvent.state !== State.ACTIVE) return;
    // Double-tap already means "like" on the surfaces that pass `onLike` (feed,
    // status). Those surfaces keep it. Only a viewer with no like handler — the
    // chat gallery, Marketplace — is free to spend the gesture on zoom.
    if (onLike) {
      onLike(item);
      likeBurstRef.current?.trigger(event.nativeEvent.x, event.nativeEvent.y);
      return;
    }
    settleZoom(zoomRef.current > 1 ? 1 : DOUBLE_TAP_ZOOM);
  }

  function handlePanEnd(translationX: number, translationY: number) {
    // Zoomed in, the horizontal axis belongs to panning the photo. Swiping to
    // the next item from inside a zoom would make it impossible to look at the
    // right-hand side of anything.
    if (zoomRef.current > 1) {
      clampPan(translationX, translationY);
      return;
    }
    const horizontal = Math.abs(translationX);
    const vertical = Math.abs(translationY);
    if (swipeToNavigate && horizontal > vertical && horizontal > SWIPE_COMMIT_DISTANCE) {
      goToIndex(translationX < 0 ? index + 1 : index - 1);
    } else if (vertical > DISMISS_COMMIT_DISTANCE) {
      onClose();
    }
    Animated.spring(translateX, { toValue: 0, useNativeDriver: true }).start();
    Animated.spring(translateY, { toValue: 0, useNativeDriver: true }).start();
  }

  function actionTargetFor(current: NativeMediaViewerItem): MediaActionTarget {
    return {
      url: current.url,
      mediaId: current.cacheIdentity || null,
      kind: (current.kind === "file" ? "file" : current.kind) as MediaActionTarget["kind"],
      // `mimeType` first: producers that carry a `media` record set both, and
      // producers that do not (Messenger) can only set this one.
      mimeType: current.mimeType || current.media?.mime_type,
      expectedBytes: Number(current.media?.file_size || 0) || undefined,
      surface,
      sourceUrl: current.sourceUrl,
      title: current.title || title,
      description: current.subtitle,
      author: current.author?.display_name || current.author?.name || current.author?.username,
      thumbnailUrl: current.thumbnailUrl || (current.kind === "image" ? current.url : undefined),
      refreshUrl: current.refreshUrl
    };
  }

  async function shareItem() {
    if (onShare) {
      onShare(item);
      return;
    }
    // Shares the real file by default and degrades to the canonical link when
    // the file cannot be produced — see `shareMedia`.
    const result = await shareMedia(actionTargetFor(item), { preferLink: shareAsLink });
    if (result.status === "failed") setActionStatus(result.message);
  }

  /**
   * Save to Photos, owned here so that all seven surfaces consuming this viewer
   * inherit one implementation. Stage 2's rule — screens call the service, they
   * do not own media logic — is only true if the shared component is where the
   * action lives.
   */
  async function saveItemToGallery() {
    if (savingToGallery) return;
    setSavingToGallery(true);
    setActionStatus("Saving to your library…");
    try {
      const result = await saveMediaToGallery(actionTargetFor(item));
      setActionStatus(
        result.status === "saved"
          ? result.limited
            ? "Saved to your selected photos."
            : "Saved to your library."
          : result.message
      );
    } finally {
      setSavingToGallery(false);
    }
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
          simultaneousHandlers={[pinchRef]}
          onGestureEvent={panEvent}
          onHandlerStateChange={(event) => {
            if (event.nativeEvent.state === State.END) {
              handlePanEnd(event.nativeEvent.translationX, event.nativeEvent.translationY);
            }
          }}
        >
          <Animated.View style={[styles.stage, { transform: [{ translateX }, { translateY }] }]}>
            {processing ? (
              <ProcessingState checking={checking} message={processingMessage || item.processingStatus || "PulseSoc is processing this media."} onRetry={checkProcessing} />
            ) : kind === "image" && item.url ? (
              <PinchGestureHandler
                ref={pinchRef}
                simultaneousHandlers={[doubleTapRef, panRef]}
                onGestureEvent={pinchEvent}
                onHandlerStateChange={(event) => {
                  if (event.nativeEvent.state === State.END) {
                    settleZoom(zoomRef.current * event.nativeEvent.scale);
                  }
                }}
              >
                <Animated.View style={styles.imageWrap}>
                  <TapGestureHandler ref={doubleTapRef} numberOfTaps={2} simultaneousHandlers={[pinchRef]} onHandlerStateChange={handleImageDoubleTap}>
                    <Animated.Image
                      testID="native-media-viewer-image"
                      source={{ uri: item.url }}
                      style={[styles.image, { transform: [{ scale }] }]}
                      resizeMode="contain"
                      onError={() => setFailed(true)}
                    />
                  </TapGestureHandler>
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
                    // `setFailed(Boolean(status.error))` used to live here, and it
                    // is why a dead video looked like a loading video forever.
                    // expo-av reports `isLoaded: false` with NO `error` field for
                    // a source AVPlayer rejected outright — a relative URL fails
                    // exactly this way — so the assignment CLEARED the failure
                    // flag on every tick, including one `onError` had just set.
                    // An unloaded status is the absence of news, not good news:
                    // it may never clear a failure, only the watchdog or a real
                    // error may set one.
                    if (status.error) setFailed(true);
                    setBuffering(false);
                    return;
                  }
                  // Genuine recovery — a later successful load clears an earlier
                  // failure, so a re-minted grant can heal the surface in place.
                  setFailed(false);
                  loadedOnceRef.current = true;
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
            <Text testID="native-media-viewer-position" style={styles.subtitle} numberOfLines={1}>{item.subtitle || item.alt || `${index + 1} of ${Math.max(totalCount || 0, items.length)}`}</Text>
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
            <Pressable testID="native-media-viewer-prev" accessibilityRole="button" accessibilityLabel="Previous media" style={[styles.actionButton, !canGoPrevious && styles.disabled]} disabled={!canGoPrevious} onPress={() => goToIndex(index - 1)}>
              <Text style={styles.actionText}>Prev</Text>
            </Pressable>
            <Pressable testID="native-media-viewer-next" accessibilityRole="button" accessibilityLabel="Next media" style={[styles.actionButton, !canGoNext && styles.disabled]} disabled={!canGoNext} onPress={() => goToIndex(index + 1)}>
              <Text style={styles.actionText}>Next</Text>
            </Pressable>
            {onSave ? (
              <Pressable testID="native-media-viewer-bookmark" accessibilityRole="button" accessibilityLabel="Save media to your collection" style={styles.actionButton} onPress={() => onSave(item)}>
                <Text style={styles.actionText}>Save</Text>
              </Pressable>
            ) : null}
            {canSaveToGallery ? (
              <Pressable
                testID="native-media-viewer-save-to-photos"
                accessibilityRole="button"
                accessibilityLabel="Save media to your photo library"
                accessibilityState={{ disabled: savingToGallery, busy: savingToGallery }}
                style={[styles.actionButton, savingToGallery && styles.disabled]}
                disabled={savingToGallery}
                onPress={saveItemToGallery}
              >
                <Text style={styles.actionText}>{savingToGallery ? "Saving" : "Save to Photos"}</Text>
              </Pressable>
            ) : null}
            <Pressable testID="native-media-viewer-share" accessibilityRole="button" accessibilityLabel="Share media" style={styles.actionButton} onPress={shareItem}>
              <Text style={styles.actionText}>Share</Text>
            </Pressable>
          </View>
        </View>

        {actionStatus ? (
          <View style={styles.statusBar} pointerEvents="none">
            <Text testID="native-media-viewer-action-status" accessibilityLiveRegion="polite" accessibilityLabel={actionStatus} style={styles.statusText} numberOfLines={3}>
              {actionStatus}
            </Text>
          </View>
        ) : null}
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
    cacheIdentity: namespacedMediaId("pulse_media", media.id),
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
  statusBar: {
    backgroundColor: "rgba(8,15,28,0.86)",
    borderColor: "rgba(255,255,255,0.14)",
    borderRadius: 10,
    borderWidth: 1,
    bottom: 78,
    left: 14,
    paddingHorizontal: 12,
    paddingVertical: 9,
    position: "absolute",
    right: 14,
    zIndex: 11
  },
  statusText: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 18
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
