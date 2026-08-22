/**
 * The media inside a suggestion card — §2, §4, §5, §6, §11, §12.
 *
 * ## Full-bleed
 *
 * The media is the card. It is absolutely positioned to the card's bounds and
 * cropped with `cover`, so there is no internal padding, no letterbox bar and no
 * gutter — §2's requirement restated as a layout rule. `cover` crops rather than
 * distorts; a preview is a sample, and a sample that has been squashed to fit
 * misrepresents the thing it is advertising.
 *
 * ## Why the poster is always rendered
 *
 * The `Video` is mounted lazily and takes a moment to produce its first frame.
 * A card that renders the player alone is a black rectangle for that moment —
 * §11's "no empty media container", arriving as a flash rather than as a
 * permanent state, which makes it harder to notice and no less ugly. The poster
 * sits underneath permanently and the video composites over it, so there is
 * never a frame where the card has nothing in it.
 *
 * ## Why the player is mounted lazily and never unmounted
 *
 * Mounting a `Video` for every suggestion would have the feed opening a decoder
 * per card, which is exactly what §12 forbids. So the player appears on first
 * activation. It is then *kept* — paused — for as long as the card is rendered,
 * because unmounting it would discard the frame playback stopped on and drop
 * the card back to its poster. It is also what makes the loop cheap: one
 * player instance serves every pass, so a card looping for a minute has created
 * exactly one decoder, not twelve. FlatList windowing bounds this: the player
 * dies with the card when the user scrolls the row away.
 *
 * ## The loop
 *
 * An active card plays its first five seconds and returns to zero, continuously,
 * for as long as it stays the active card. The turnover is driven by the
 * player's own position reports rather than by a timer, and the decision itself
 * lives in `previewPlayback` — see there for why it is latched and why a timer
 * is the wrong clock.
 *
 * ## Sound
 *
 * Previews are muted, always, with no way to unmute them. `isMuted` is a literal
 * `true` rather than a prop with a default, because a prop is something a future
 * call site can pass differently. Nothing here touches the audio session, the
 * audio mode, or any other player: a feed preview is not allowed to be the
 * reason a call goes quiet. Arbitration against everything that *does* own audio
 * goes through the existing `mediaPlaybackCoordinator` claim below.
 */
import { AVPlaybackStatus, ResizeMode, Video } from "expo-av";
import { LinearGradient } from "expo-linear-gradient";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import { Image, StyleSheet, Text, View } from "react-native";
import { claimMediaPlayback, releaseMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { createThemedStyles } from "../theme/themedStyles";
import {
  DISCOVERY_PREVIEW_PROGRESS_INTERVAL_MS,
  PreviewLoopState,
  advancePreviewLoop,
  initialPreviewLoopState
} from "./previewPlayback";

export type DiscoveryPreviewMediaProps = {
  /** Playable video for this card, when it has one. Absent for every non-video kind. */
  videoUrl?: string | null;
  posterUrl?: string | null;
  /** Rendered when there is neither a poster nor a video — never a blank frame. */
  fallbackLabel: string;
  width: number;
  height: number;
  /** True only for the one card that is the primary visible item of a visible row. */
  active: boolean;
  testID?: string;
  /** Reported so the card's border can go to its "playing" energy. */
  onPlayingChange?: (playing: boolean) => void;
};

export function DiscoveryPreviewMedia({
  videoUrl,
  posterUrl,
  fallbackLabel,
  width,
  height,
  active,
  testID,
  onPlayingChange
}: DiscoveryPreviewMediaProps) {
  const videoRef = useRef<Video>(null);
  // One playback-coordinator identity per mounted card. `useId` rather than the
  // preview key: two rows can legitimately suggest the same reel, and two owners
  // sharing an id would have each one's release cancel the other's claim.
  const ownerId = `discovery-preview:${useId()}`;
  const [engaged, setEngaged] = useState(false);
  const [playing, setPlaying] = useState(false);
  // Latches on the first frame and never clears. Fading the player back out
  // when the card stops being active would drop it to its poster with a visible
  // cross-fade every time the user swiped past — the card should settle on the
  // frame it stopped at, not blink.
  const [revealed, setRevealed] = useState(false);

  const reportPlaying = useRef(onPlayingChange);
  reportPlaying.current = onPlayingChange;

  // Read by the status callback, which must see the *current* value rather than
  // the one captured when it was created — it is created once, on purpose.
  const playingRef = useRef(false);
  const loopRef = useRef<PreviewLoopState>(initialPreviewLoopState());

  const setPlayingState = useCallback((next: boolean) => {
    playingRef.current = next;
    setPlaying(next);
    reportPlaying.current?.(next);
  }, []);

  /**
   * Stop and give the playback lease back.
   *
   * Also the `pause` callback handed to the coordinator, which is what makes an
   * incoming call, a Reel, or the app backgrounding silence this preview without
   * this component knowing any of those exist.
   */
  const stop = useCallback(() => {
    loopRef.current = initialPreviewLoopState();
    setPlayingState(false);
    videoRef.current?.pauseAsync?.().catch(() => undefined);
    releaseMediaPlayback(ownerId).catch(() => undefined);
  }, [ownerId, setPlayingState]);

  /**
   * The loop itself.
   *
   * Deliberately free of React state: this fires eight times a second, and a
   * `setState` per tick would re-render the card — and therefore its siblings'
   * shared parent — for the entire time a preview is on screen. The loop's
   * whole state is one boolean in a ref, and the only React state the preview
   * touches is the three transitions a human can see (engaged, revealed,
   * playing), each of which happens at most once per activation.
   */
  const onPlaybackStatusUpdate = useCallback((status: AVPlaybackStatus) => {
    if (!status.isLoaded) return;
    // A status arriving after we paused must not be able to restart anything.
    if (!playingRef.current) return;

    const { state, action } = advancePreviewLoop(loopRef.current, status.positionMillis);
    loopRef.current = state;
    if (action !== "rewind") return;

    // Seek *and* resume in one call: a bare seek would leave the player's
    // `shouldPlay` intact but has no obligation to still be playing after it,
    // and "the loop stopped after the first pass" is the failure that would
    // produce. The player instance is reused — this never remounts anything.
    videoRef.current?.playFromPositionAsync?.(0).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!videoUrl || !active) {
      if (!active) stop();
      return undefined;
    }

    let cancelled = false;

    setEngaged(true);
    claimMediaPlayback({ id: ownerId, kind: "feed", pause: stop })
      .then((granted) => {
        // Refused means something with a higher claim — a call, a live room — is
        // playing. The card simply stays on its poster.
        if (!granted || cancelled) return;
        loopRef.current = initialPreviewLoopState();
        setRevealed(true);
        setPlayingState(true);
        videoRef.current?.playFromPositionAsync?.(0).catch(() => undefined);
      })
      .catch(() => undefined);

    return () => {
      cancelled = true;
      stop();
    };
  }, [active, ownerId, stop, setPlayingState, videoUrl]);

  const frame = { width, height };
  const hasPoster = Boolean(posterUrl);

  return (
    <View style={[styles.media, frame]} testID={testID}>
      {hasPoster ? (
        <Image
          source={{ uri: posterUrl as string }}
          style={StyleSheet.absoluteFill}
          resizeMode="cover"
          testID={testID ? `${testID}-poster` : undefined}
          accessibilityIgnoresInvertColors
        />
      ) : (
        // §11: no media is a text-first card, not a reserved empty rectangle.
        <View style={StyleSheet.absoluteFill} testID={testID ? `${testID}-fallback` : undefined}>
          <LinearGradient
            colors={[colors.surfaceRaised, colors.surface]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 1 }}
            style={StyleSheet.absoluteFill}
          />
          <View style={styles.fallbackBody}>
            <Text style={styles.fallbackMonogram}>
              {fallbackLabel.trim().slice(0, 2).toUpperCase() || "PS"}
            </Text>
            <Text style={styles.fallbackLabel} numberOfLines={3}>
              {fallbackLabel}
            </Text>
          </View>
        </View>
      )}

      {engaged && videoUrl ? (
        // The tap target is the whole card, so the player must never take a
        // touch — a preview the user taps must open Reels, not swallow the press.
        <View style={StyleSheet.absoluteFill} pointerEvents="none">
          <Video
            ref={videoRef}
            testID={testID ? `${testID}-video` : undefined}
            source={{ uri: videoUrl }}
            style={[StyleSheet.absoluteFill, revealed ? styles.videoVisible : styles.videoHidden]}
            resizeMode={ResizeMode.COVER}
            shouldPlay={playing}
            // Covers the clip that is *shorter* than the loop window, which the
            // position guard never sees: it ends before reaching five seconds,
            // and the native wrap is seamless in a way a seek is not. For a
            // longer clip this never comes into play — the guard turns it over
            // at five seconds, far from the end.
            isLooping
            progressUpdateIntervalMillis={DISCOVERY_PREVIEW_PROGRESS_INTERVAL_MS}
            onPlaybackStatusUpdate={onPlaybackStatusUpdate}
            // Not a prop, not a default: a feed preview has no sound, ever.
            isMuted
            useNativeControls={false}
          />
        </View>
      ) : null}

      <LinearGradient
        pointerEvents="none"
        colors={["transparent", "rgba(5, 9, 16, 0.72)"]}
        style={styles.scrim}
      />
    </View>
  );
}

const styles = createThemedStyles(() => ({
  media: { overflow: "hidden", backgroundColor: colors.surfaceRaised },
  // Held at zero opacity until the first frame is on screen, so the transition
  // from poster to video is a cross-fade rather than a black flash.
  videoHidden: { opacity: 0 },
  videoVisible: { opacity: 1 },
  fallbackBody: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center",
    padding: logiNexus.spacing.lg,
    gap: logiNexus.spacing.sm
  },
  fallbackMonogram: { ...logiNexus.typography.title, color: colors.accent },
  fallbackLabel: {
    ...logiNexus.typography.home.cardMetadata,
    color: colors.muted,
    textAlign: "center"
  },
  scrim: { position: "absolute", left: 0, right: 0, bottom: 0, height: "45%" }
}));
