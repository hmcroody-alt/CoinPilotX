import { Audio, ResizeMode, Video } from "expo-av";
import type { AVPlaybackStatusSuccess } from "expo-av";
import { LinearGradient } from "expo-linear-gradient";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Image, Pressable, StyleSheet, Text, View } from "react-native";
import { PulseReel, reelIsPlayable, reelPosterUrl, reelVideoUrl, reelWebUrl } from "../api/reels";
import { claimMediaPlayback, releaseMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { resolveReelAudioPolicy } from "../core/attachedMusicAudioPolicy";
import { MUSIC_DRIFT_TOLERANCE_MS, MUSIC_STATUS_INTERVAL_MS, planMusicCorrection } from "../core/attachedMusicTimeline";
import type { MusicTimelineState } from "../core/attachedMusicTimeline";
import { trackMediaEvent } from "../media/mediaTelemetry";
import { refreshCanonicalMediaAccess } from "../media/mediaAccess";
import { LikeBurst, LikeBurstHandle, MuteGlyphPulse, MuteGlyphPulseHandle } from "../media/MediaGestureFeedback";
import { useTapMuteLike } from "../media/useTapMuteLike";
import { classifyReelMedia } from "../reels/reelMediaKind";
import { useSavedState } from "../social/savedStore";
import { ReelPhotoSurface } from "./reels/ReelPhotoSurface";
import { ReelCarouselSurface } from "./reels/ReelCarouselSurface";
import { ReelLiveViewerSurface } from "./reels/ReelLiveViewerSurface";
import { colors } from "../theme/colors";
import { sharePulseObject } from "../sharing/nativeShare";
import { ContentTranslation } from "./ContentTranslation";
import { createThemedStyles } from "../theme/themedStyles";

type ReelPlayerCardProps = {
  reel: PulseReel;
  active: boolean;
  muted: boolean;
  offline?: boolean;
  /**
   * Render the reel edge-to-edge instead of as an inset, rounded card.
   *
   * The legacy Reels player floats the media inside a bordered card with fixed
   * 112/96/8pt margins. The spatial player fills the viewport, and the header
   * and bottom navigator overlay the media rather than bracketing it. Both
   * layouts ship: this is the flag boundary between them, so turning spatial
   * Reels off restores the card without a second code path to keep in sync.
   */
  fullBleed?: boolean;
  /** Full-bleed only: where the creator row starts, clear of the header. */
  contentTop?: number;
  /**
   * Full-bleed only: where the caption sits, clear of where the navigator
   * *lives* — not of where it currently is.
   *
   * Deriving this from the dock's actual visibility would resize the reel's
   * content every time the dock hid or revealed, which is the layout jump the
   * mission forbids. It is a constant so a hide/reveal moves the navigator and
   * nothing else.
   */
  contentBottom?: number;
  /** Full-bleed only: bottom safe-area inset, for non-interactive chrome. */
  safeBottom?: number;
  /**
   * Full-bleed only: a confirmed single tap the media surface received and no
   * child control claimed. The product already maps that tap to mute; this is
   * additive, and it is the recovery path that brings a hidden navigator back.
   */
  onSurfaceTap?: () => void;
  busy?: boolean;
  onToggleMuted: () => void;
  onReact: (reel: PulseReel, reactionType?: string) => void;
  onOpenReactions: (reel: PulseReel) => void;
  onOpenComments: (reel: PulseReel) => void;
  onSave: (reel: PulseReel) => void;
  onRepost: (reel: PulseReel) => void;
  onPromote?: (reel: PulseReel) => void;
  onShare: (reel: PulseReel) => void;
  onNotInterested: (reel: PulseReel) => void;
  onReport: (reel: PulseReel) => void;
  onFollowCreator: (reel: PulseReel) => void;
  onAuthorPress: (reel: PulseReel) => void;
  onOpenMusic: (reel: PulseReel) => void;
  onOpenMore: (reel: PulseReel) => void;
  onJoinLive: (reel: PulseReel) => void;
  onViewable?: (reel: PulseReel, watchMs: number) => void;
};

export function ReelPlayerCard({
  reel,
  active,
  muted,
  offline = false,
  fullBleed = false,
  contentTop = 68,
  contentBottom = 132,
  safeBottom = 0,
  onSurfaceTap,
  busy,
  onToggleMuted,
  onReact,
  onOpenReactions,
  onOpenComments,
  onSave,
  onRepost,
  onPromote,
  onShare,
  onNotInterested,
  onReport,
  onFollowCreator,
  onAuthorPress,
  onOpenMusic,
  onOpenMore,
  onJoinLive,
  onViewable
}: ReelPlayerCardProps) {
  const videoRef = useRef<Video>(null);
  const attachedSoundRef = useRef<Audio.Sound | null>(null);
  /** Last status the attached track reported; the input side of the correction loop. */
  const musicStatusRef = useRef<MusicTimelineState | null>(null);
  /** Serialises corrections so a slow seek cannot overlap the next tick's. */
  const correctingMusic = useRef(false);
  /**
   * The drift the previous tick measured, so a one-tick quantisation spike can
   * be told from a track that is really out.
   *
   * Per-card, not per-module: two Reels are mounted at once during a swipe, and
   * a shared history would let one card's reading confirm the other card's
   * spike and seek a track it has never looked at.
   */
  const previousDriftRef = useRef<number | null>(null);
  const likeBurstRef = useRef<LikeBurstHandle>(null);
  const muteGlyphRef = useRef<MuteGlyphPulseHandle>(null);
  const refreshAttempted = useRef(false);
  const watchStartedAt = useRef(0);
  /** Monotonic stamp for the playback effect; see the claim race note below. */
  const playGeneration = useRef(0);
  const [buffering, setBuffering] = useState(false);
  const [progress, setProgress] = useState(0);
  const [failed, setFailed] = useState(false);
  const [ownsPlayback, setOwnsPlayback] = useState(false);
  const [refreshingUrl, setRefreshingUrl] = useState(false);
  const [source, setSource] = useState(() => reelVideoUrl(reel) || reel.live?.playback_url || "");
  const initialSource = useMemo(() => reelVideoUrl(reel) || reel.live?.playback_url || "", [reel]);
  /**
   * Saved state comes from the shared store, not from `reel.saved`.
   *
   * The same Reel is also a post in the feed, and both cards can be mounted at
   * once. Reading the screen's copy is what let one of them show Saved while the
   * other showed Save. The payload's flag is still handed over, but only as a
   * seed for content the store has not heard of yet.
   */
  const saveState = useSavedState("reel", reel.id, typeof reel.saved === "boolean" ? reel.saved : undefined);
  const poster = useMemo(() => reelPosterUrl(reel), [reel]);
  const author = reel.author || {};
  const kind = useMemo(() => classifyReelMedia(reel), [reel]);
  const isVideoKind = kind === "video" || kind === "replay";
  const isLive = kind === "livestream";
  const musicPolicy = useMemo(() => resolveReelAudioPolicy(reel.audio), [reel.audio]);
  const drivesPlayback = isVideoKind || (kind === "carousel" && musicPolicy.hasAttachedMusic);
  const playbackOwnerId = `reel:${reel.id}`;
  const media = reel.media?.[0];
  const contentState = reelContentState(reel, offline, failed);

  useEffect(() => {
    setSource(initialSource);
    setFailed(false);
    setRefreshingUrl(false);
    refreshAttempted.current = false;
  }, [initialSource, reel.id]);

  useEffect(() => {
    if (!drivesPlayback) {
      if (active) {
        watchStartedAt.current = Date.now();
      } else if (watchStartedAt.current) {
        onViewable?.(reel, Date.now() - watchStartedAt.current);
        watchStartedAt.current = 0;
      }
      return;
    }
    /**
     * Which run of this effect is allowed to start playback.
     *
     * `claimMediaPlayback` is asynchronous — it awaits the outgoing owner's
     * `pause()` before it resolves — so there is a real window between asking to
     * play and being told yes. If the user leaves Reels inside that window, the
     * inactive branch below pauses the video and *then* the stale claim resolves
     * and calls `playAsync()`, restarting the reel on a screen that is no longer
     * on display. Stamping the run and re-checking it on resolution is what
     * makes "paused" stick.
     */
    const generation = ++playGeneration.current;
    if (active && !muted) {
      watchStartedAt.current = Date.now();
      claimMediaPlayback({
        id: playbackOwnerId,
        kind: "reel",
        pause: () => Promise.all([
          videoRef.current?.pauseAsync().catch(() => undefined),
          attachedSoundRef.current?.pauseAsync().catch(() => undefined)
        ]).then(() => undefined),
        stop: () => Promise.all([
          videoRef.current?.stopAsync().catch(() => undefined),
          attachedSoundRef.current?.stopAsync().catch(() => undefined)
        ]).then(() => undefined)
      }).then((granted) => {
        if (generation !== playGeneration.current) {
          // Superseded while the claim was in flight. Hand ownership straight
          // back rather than playing: releasing also pauses, so a grant that
          // arrives late cannot leave a reel running behind another screen.
          if (granted) releaseMediaPlayback(playbackOwnerId).catch(() => undefined);
          return undefined;
        }
        setOwnsPlayback(granted);
        return granted ? videoRef.current?.playAsync() : undefined;
      }).catch(() => setOwnsPlayback(false));
    } else if (active) {
      watchStartedAt.current = Date.now();
      setOwnsPlayback(false);
      releaseMediaPlayback(playbackOwnerId).catch(() => undefined);
      videoRef.current?.playAsync().catch(() => undefined);
    } else {
      setOwnsPlayback(false);
      if (watchStartedAt.current) {
        onViewable?.(reel, Date.now() - watchStartedAt.current);
        watchStartedAt.current = 0;
      }
      videoRef.current?.pauseAsync().catch(() => undefined);
      // Silence the attached music bed directly instead of relying on
      // `releaseMediaPlayback`, which no-ops unless this card happens to be the
      // registered owner — and a muted reel deliberately owns nothing. The
      // unload in `syncAttachedAudio` does eventually catch it, but only after
      // `setOwnsPlayback(false)` has re-rendered, which is a window where the
      // bed is still audible on a screen the user has already left.
      attachedSoundRef.current?.pauseAsync().catch(() => undefined);
      releaseMediaPlayback(playbackOwnerId).catch(() => undefined);
    }
    return () => { releaseMediaPlayback(playbackOwnerId).catch(() => undefined); };
  }, [active, muted, onViewable, playbackOwnerId, reel, drivesPlayback]);

  /**
   * Load the attached track as soon as the card exists, not when it wins playback.
   *
   * This effect used to be gated on `ownsPlayback`, which is set from the
   * resolution of an asynchronous claim. That ordering is the reason music
   * arrived after picture: the claim resolved, `playAsync()` started the video
   * immediately, and only the *re-render* caused by `setOwnsPlayback(true)` ran
   * this effect -- which then began a cold network fetch of the track. Video
   * start and music start were separated by a React commit plus a whole asset
   * download.
   *
   * Loading here with `shouldPlay: false` separates being ready from being
   * audible, so by the time the claim resolves the track is already decoded and
   * starting it is a local operation. The bound on how many tracks this loads is
   * the list's own render window (FlatList `windowSize`), which is why there is
   * no second scheduler here: the card that exists is the card worth warming,
   * and the neighbours the window keeps mounted are exactly the N+1/N-1 the
   * prefetch policy would have chosen anyway.
   */
  useEffect(() => {
    if (!drivesPlayback || !musicPolicy.hasAttachedMusic || !musicPolicy.musicUrl) return;
    let cancelled = false;
    Audio.Sound.createAsync(
      { uri: musicPolicy.musicUrl },
      {
        isLooping: musicPolicy.isLooping,
        positionMillis: musicPolicy.musicStartMs,
        // The SAME grid the video reports on, and the same one the deadband is
        // derived from. Correctness is not independent of this number: it is
        // the resolution of every drift reading the loop takes, which is why it
        // comes from the timeline module rather than being written here. A
        // track left on the 500ms default would be measured against a deadband
        // sized for 250ms and would be "corrected" on the difference.
        progressUpdateIntervalMillis: MUSIC_STATUS_INTERVAL_MS,
        // Deliberately silent on load. Audibility is decided by the correction
        // loop below, against the video's clock.
        shouldPlay: false,
        volume: musicPolicy.musicVolume,
        isMuted: muted
      }
    ).then((created) => {
      if (cancelled) return created.sound.unloadAsync().catch(() => undefined);
      attachedSoundRef.current = created.sound;
      created.sound.setOnPlaybackStatusUpdate((status) => {
        // The timestamp is taken here, at the moment the reading is true, and
        // not where it is consumed -- by then it is already old, which is the
        // entire problem this records.
        musicStatusRef.current = status.isLoaded
          ? {
              isLoaded: true,
              positionMillis: status.positionMillis || 0,
              isPlaying: Boolean(status.isPlaying),
              durationMillis: status.durationMillis ?? null,
              sampledAtMillis: Date.now()
            }
          : { isLoaded: false, positionMillis: 0, isPlaying: false, durationMillis: null, sampledAtMillis: Date.now() };
      });
      return undefined;
    }).catch(() => undefined);
    return () => {
      cancelled = true;
      const existing = attachedSoundRef.current;
      attachedSoundRef.current = null;
      musicStatusRef.current = null;
      if (existing) {
        existing.setOnPlaybackStatusUpdate(null);
        existing.unloadAsync().catch(() => undefined);
      }
    };
    // Depends on the track's VALUES, not on the policy object's identity.
    // `musicPolicy` is memoized on `reel.audio`, so any refetch or pagination
    // that rebuilds the reel objects yields an equal-but-new policy -- and
    // keying the effect on the object would tear down a playing track and
    // re-download the identical file, silencing the reel for a network round
    // trip in the middle of playback. The effect should re-run when the music
    // changes, which is what these values say and the object does not.
    //
    // `muted` is deliberately NOT a dependency. It is read above only as the
    // sound's initial `isMuted`; every later change is applied by the
    // correction loop's `setStatusAsync` and by the pause effect below. Listing
    // it here would make muting a reel unload the track and unmuting it
    // re-download the same file -- turning a local toggle into a network round
    // trip, on the one control the user expects to be instant.
  }, [
    drivesPlayback,
    musicPolicy.musicUrl,
    musicPolicy.isLooping,
    musicPolicy.musicStartMs,
    musicPolicy.musicVolume
  ]);

  /**
   * Silence the track the instant this card stops being entitled to be heard.
   *
   * The `active` edge is already handled by the ownership effect above, and
   * losing `ownsPlayback` is handled by the coordinator's own `pause` callback.
   * The edge this one exists for is MUTE: an active, still-playing reel that
   * the user mutes takes the `else if (active)` branch above, which pauses the
   * video's audio but says nothing about the attached track.
   *
   * Without this, the only thing that would stop the music is the correction
   * loop -- and that runs on the video's status callback, so the track stays
   * audible until the next tick. §11 says an explicit mute outranks autoplay,
   * and "outranks it within about 250ms" is not what that means. Tapping mute
   * has to be silent immediately.
   *
   * The other two conditions are kept in the guard even though they are covered
   * elsewhere: they make this effect's postcondition -- not entitled implies
   * not audible -- true on its own terms rather than true by coincidence of
   * what some other effect happens to do.
   */
  useEffect(() => {
    if (active && ownsPlayback && !muted) return;
    attachedSoundRef.current?.pauseAsync().catch(() => undefined);
  }, [active, ownsPlayback, muted]);

  useEffect(() => {
    if (!drivesPlayback || !active || !ownsPlayback) return;
    videoRef.current?.playAsync().catch(() => undefined);
    // The attached track is deliberately NOT started here. Starting it from an
    // ownership effect is what this mission removed: it sets `shouldPlay` with
    // no position, so the track begins wherever it was left rather than where
    // the picture is. Resuming it is the correction loop's job, which runs off
    // the video's next status tick and knows the position to land on.
  }, [active, muted, ownsPlayback, drivesPlayback]);

  const { onPress: handleTap } = useTapMuteLike({
    // A confirmed single tap keeps its existing product meaning (mute) and, in
    // full-bleed mode, additionally recovers a hidden navigator. It is deliberately
    // wired to the *confirmed* single tap rather than to the raw press, so the
    // first half of a double-tap-to-like does not flash the navigation.
    onToggleMuted: () => {
      onToggleMuted();
      onSurfaceTap?.();
    },
    onLike: () => onReact(reel, "like"),
    onSingleTapFeedback: () => muteGlyphRef.current?.trigger(!muted),
    onLikeFeedback: (x, y) => likeBurstRef.current?.trigger(x, y)
  });

  /**
   * Put the track where the video says it should be.
   *
   * Called from the video's own status callback so the video is literally the
   * clock: there is no interval, and no second timebase that could disagree
   * with the picture. Every transition the mission asks about -- start, seek,
   * pause, resume, rebuffer, loop -- arrives here as nothing more than a new
   * video position, which is why none of them has its own handler.
   */
  async function applyMusicCorrection(status: AVPlaybackStatusSuccess) {
    const sound = attachedSoundRef.current;
    const musicState = musicStatusRef.current;
    if (!sound || !musicState || correctingMusic.current) return;
    const plan = planMusicCorrection(
      {
        isLoaded: true,
        positionMillis: status.positionMillis || 0,
        // A card that does not own playback is not allowed to be audible, so it
        // is reported as not playing regardless of what the video element is
        // doing. This is what keeps a muted or superseded reel silent.
        isPlaying: Boolean(status.isPlaying) && ownsPlayback && !muted,
        isBuffering: Boolean(status.isBuffering)
      },
      musicState,
      musicPolicy,
      MUSIC_DRIFT_TOLERANCE_MS,
      // The video reading is current as of right now; the music reading is as
      // old as its own callback interval. Handing over the instant lets the
      // planner age the music sample up to this one instead of treating a
      // sampling gap as drift.
      Date.now(),
      previousDriftRef.current
    );
    // Recorded on EVERY tick, not just corrected ones. The persistence rule
    // needs the immediately preceding reading, and the readings that matter
    // most are the uncorrected ones -- a spike is, by definition, a tick on
    // which nothing was done. Anything that actuates the track clears the
    // history instead: the track has just been moved, so the previous reading
    // describes a position that no longer exists, and letting it confirm the
    // next one would seek on every tick again.
    previousDriftRef.current = plan.action === "none" ? plan.driftMillis ?? null : null;
    if (plan.action === "none") return;
    correctingMusic.current = true;
    try {
      if (plan.action === "pause") {
        await sound.pauseAsync();
      } else {
        // setStatusAsync applies position and play state in one call, so the
        // track cannot be briefly audible at the wrong position the way a
        // separate seek-then-play would allow.
        //
        // A null seek means the track is already within the deadband of where
        // it belongs, so the position is OMITTED rather than sent as its current
        // value: re-sending a position restarts the player's start-up sequence,
        // and doing that every tick while waiting for `isPlaying` is what held a
        // reel silent for 7.2 seconds on device.
        await sound.setStatusAsync({
          ...(plan.seekToMillis === null ? {} : { positionMillis: plan.seekToMillis }),
          shouldPlay: true,
          isMuted: muted,
          volume: musicPolicy.musicVolume
        });
        trackMediaEvent({
          name: "MEDIA_AUDIO_RESYNC",
          kind: "audio",
          surface: "reels",
          driftMs: plan.driftMillis
        });
      }
    } catch {
      // A correction that fails is not worth surfacing: the next status tick
      // recomputes from scratch, so a transient failure self-heals.
    } finally {
      correctingMusic.current = false;
    }
  }

  async function recoverPlaybackUrl() {
    if (!media || refreshingUrl || refreshAttempted.current) {
      setFailed(true);
      return;
    }
    refreshAttempted.current = true;
    setRefreshingUrl(true);
    try {
      const refreshed = await refreshCanonicalMediaAccess(media);
      const nextUrl = refreshed.url;
      if (!nextUrl) throw new Error("playback unavailable");
      setSource(nextUrl);
      setFailed(false);
    } catch {
      setFailed(true);
    } finally {
      setRefreshingUrl(false);
    }
  }

  return (
    <View style={[styles.card, fullBleed && styles.cardFullBleed]}>
      {poster ? <Image source={{ uri: poster }} style={styles.poster} resizeMode="cover" blurRadius={active ? 0 : 3} /> : null}
      {kind === "photo" ? (
        <ReelPhotoSurface reel={reel} />
      ) : kind === "carousel" ? (
        <ReelCarouselSurface reel={reel} active={active} muted={muted} muteOriginal={musicPolicy.muteOriginalAudio} />
      ) : kind === "livestream" ? (
        <ReelLiveViewerSurface reel={reel} active={active} muted={muted} poster={poster} />
      ) : contentState === "playable" && source ? (
        <Video
          ref={videoRef}
          source={{ uri: source }}
          style={styles.video}
          resizeMode={ResizeMode.COVER}
          shouldPlay={false}
          isLooping
          isMuted={muted || musicPolicy.muteOriginalAudio}
          progressUpdateIntervalMillis={MUSIC_STATUS_INTERVAL_MS}
          usePoster={Boolean(poster)}
          posterSource={poster ? { uri: poster } : undefined}
          onPlaybackStatusUpdate={(status) => {
            if (!status.isLoaded) {
              if (status.error) recoverPlaybackUrl().catch(() => undefined);
              setBuffering(false);
              return;
            }
            setBuffering(Boolean(status.isBuffering));
            if (status.durationMillis) setProgress(Math.min(1, status.positionMillis / status.durationMillis));
            applyMusicCorrection(status).catch(() => undefined);
          }}
          onError={() => recoverPlaybackUrl().catch(() => undefined)}
        />
      ) : (
        <ReelStateSurface state={refreshingUrl ? "refreshing" : contentState === "playable" ? "error" : contentState} onRetry={() => {
          refreshAttempted.current = false;
          setFailed(false);
          recoverPlaybackUrl().catch(() => undefined);
        }} onShare={() => sharePulseObject({
          kind: "reel",
          url: reelWebUrl(reel.id),
          title: reel.title || "PulseSoc Reel",
          description: reel.caption || reel.body,
          author: reel.author?.display_name || reel.author?.name || reel.author?.username,
          previewImageUrl: reel.poster_url
        }).catch(() => undefined)} />
      )}
      {isVideoKind && contentState === "playable" ? <Pressable accessibilityRole="button" accessibilityLabel={muted ? "Reel muted. Tap to unmute, double tap to like." : "Reel sound on. Tap to mute, double tap to like."} style={styles.tapLayer} onPress={handleTap} onLongPress={() => onOpenReactions(reel)} /> : null}
      <View style={styles.scrim} pointerEvents="none" />
      {fullBleed ? (
        // Contrast protection, not decoration. Full-bleed media puts white
        // caption text and the creator row directly over whatever the video
        // happens to be showing, and a bright reel makes both unreadable. Two
        // neutral black gradients — no brand color, no permanent opaque panel —
        // darken only the bands the overlays occupy and fade to nothing across
        // the middle of the frame, so the media itself is never covered.
        <>
          <LinearGradient
            colors={["rgba(0,0,0,0.58)", "rgba(0,0,0,0.22)", "rgba(0,0,0,0)"]}
            style={[styles.topScrim, { height: contentTop + 108 }]}
            pointerEvents="none"
          />
          <LinearGradient
            colors={["rgba(0,0,0,0)", "rgba(0,0,0,0.34)", "rgba(0,0,0,0.72)"]}
            style={[styles.bottomScrim, { height: contentBottom + 220 }]}
            pointerEvents="none"
          />
        </>
      ) : null}
      {buffering || refreshingUrl ? <View style={styles.buffering}><View style={styles.bufferingCore}><ActivityIndicator color="#36f0cf" /><Text style={styles.bufferingText}>{refreshingUrl ? "Refreshing Reel" : "Tuning signal"}</Text></View></View> : null}
      <LikeBurst ref={likeBurstRef} />
      <MuteGlyphPulse ref={muteGlyphRef} />

      <View style={[styles.top, fullBleed ? { top: contentTop } : null]}>
        <Pressable style={styles.author} onPress={() => onAuthorPress(reel)}>
          {author.avatar_url ? <Image source={{ uri: author.avatar_url }} style={styles.avatar} /> : <View style={styles.avatarFallback} />}
          <View style={styles.authorCopy}>
            <Text style={styles.authorName} numberOfLines={1}>{author.display_name || author.name || "PulseSoc creator"}</Text>
            <Text style={styles.authorMeta} numberOfLines={1}>{author.username ? `@${author.username}` : reel.category || "Reels"}</Text>
          </View>
        </Pressable>
        {isLive ? <View style={styles.followButton}><Text style={styles.followText}>LIVE</Text></View> : <Pressable style={[styles.followButton, reel.viewer_follows_author && styles.followButtonActive]} disabled={busy} onPress={() => onFollowCreator(reel)}><Text style={styles.followText}>{reel.viewer_follows_author ? "Following" : "Follow"}</Text></Pressable>}
      </View>

      {isLive ? <View style={[styles.liveBadge, fullBleed ? { top: contentTop + 58 } : null]}><View style={styles.liveDot} /><Text style={styles.liveText}>LIVE · {reel.live?.viewer_count || reel.view_count || 0}</Text></View> : null}

      <View style={[styles.actions, fullBleed ? { bottom: contentBottom + 56 } : null]}>
        <Action icon={reactionIcon(reel.viewer_reaction)} label={reel.viewer_reaction ? "Liked" : "Like"} value={reel.reactions_count || reel.reaction_counts?.like || reel.reaction_counts?.fire || 0} active={Boolean(reel.viewer_reaction)} disabled={reel.reactions_disabled} onPress={() => onReact(reel, reel.viewer_reaction || "like")} onLongPress={() => onOpenReactions(reel)} />
        <Action icon="◌" label="Comment" value={reel.comments_count || 0} onPress={() => onOpenComments(reel)} />
        <Action icon="➤" label="Share" value={reel.share_count || 0} onPress={() => onShare(reel)} />
        <Action
          icon={saveState.saved ? "◆" : "◇"}
          label={saveState.pending ? (saveState.saved ? "Saving" : "Removing") : saveState.saved ? "Saved" : "Save"}
          hint={saveState.saved ? "Removes this Reel from your Saved collection" : "Adds this Reel to your Saved collection"}
          busy={saveState.pending}
          onPress={() => onSave(reel)}
          active={saveState.saved}
        />
        <Action icon="•••" label="More" onPress={() => onOpenMore(reel)} />
      </View>

      <View style={[styles.caption, fullBleed ? { bottom: contentBottom } : null]}>
        <Text style={styles.title} numberOfLines={1}>{author.username ? `@${author.username}` : reel.title || "PulseSoc Reel"}</Text>
        {reel.caption || reel.body ? (
          <ContentTranslation
            contentType="reel"
            contentRef={reel.id}
            text={reel.caption || reel.body || ""}
            renderText={(value) => <RichCaption value={value} />}
          />
        ) : null}
        {isLive ? <Pressable accessibilityRole="button" accessibilityLabel="Join this Live" style={styles.joinLive} onPress={() => onJoinLive(reel)}><Text style={styles.joinLiveText}>Join Live</Text></Pressable> : null}
        <View style={styles.mediaMetaRow}>
          <Pressable accessibilityRole="button" accessibilityLabel={reel.audio?.title ? `Music: ${reel.audio.title}${reel.audio.artist ? ` by ${reel.audio.artist}` : ""}` : "Original audio"} style={styles.musicMicro} onPress={() => onOpenMusic(reel)}><View style={styles.musicOrb}><Text style={styles.musicNote}>♪</Text></View><Text style={styles.musicLabel} numberOfLines={1}>{reel.audio?.title || "Original audio"}{reel.audio?.artist ? ` · ${reel.audio.artist}` : ""}</Text></Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel={muted ? "Turn Reel sound on" : "Mute Reel"} style={styles.muteButton} onPress={onToggleMuted}><Text style={styles.muteButtonText}>{muted ? "⌁" : "◖))"}</Text></Pressable>
        </View>
      </View>

      {/* Non-interactive, so it may sit on the safe-area line rather than above
          it — but not *under* the home indicator, where it would be invisible. */}
      <View style={[styles.progressTrack, fullBleed ? { bottom: safeBottom } : null]}>
        <View style={[styles.progressBar, { width: `${Math.round(progress * 100)}%` }]} />
      </View>
    </View>
  );
}

type ReelContentState = "playable" | "offline" | "error" | "processing" | "removed" | "restricted" | "moderation";

function reelContentState(reel: PulseReel, offline: boolean, failed: boolean): ReelContentState {
  const availability = String(reel.availability || reel.visibility_state || "").toLowerCase();
  const moderation = String(reel.moderation_status || "").toLowerCase();
  const processing = String(reel.transcoding_status || reel.processing_status || "").toLowerCase();
  if (reel.is_removed || reel.deleted_at || ["removed", "deleted", "unavailable"].includes(availability)) return "removed";
  if (["restricted", "blocked", "private", "followers_only"].includes(availability)) return "restricted";
  if (["rejected", "blocked", "unavailable"].includes(moderation)) return "moderation";
  if (failed) return offline ? "offline" : "error";
  if (!reelIsPlayable(reel) || ["queued", "pending", "processing", "transcoding", "preparing"].includes(processing)) return "processing";
  return "playable";
}

function ReelStateSurface({ state, onRetry, onShare }: { state: Exclude<ReelContentState, "playable"> | "refreshing"; onRetry: () => void; onShare: () => void }) {
  const copy = {
    offline: { icon: "⌁", title: "Reel is offline", body: "Your saved cover stays visible. Reconnect and retry when the signal returns.", action: "Retry" },
    error: { icon: "◇", title: "Couldn’t play this Reel", body: "PulseSoc can refresh the secure playback link without changing this Reel.", action: "Try again" },
    processing: { icon: "◌", title: "Reel is preparing", body: "The original upload is still being processed. It will appear here when ready.", action: "Check again" },
    removed: { icon: "—", title: "Reel is no longer available", body: "This content was removed by its creator or is no longer available.", action: "Share link" },
    restricted: { icon: "◇", title: "Reel is restricted", body: "Your account or this Reel’s audience settings do not permit playback.", action: "Share link" },
    moderation: { icon: "!", title: "Reel unavailable", body: "This content cannot be shown while its availability is being reviewed.", action: "Share link" },
    refreshing: { icon: "◌", title: "Refreshing Reel", body: "Restoring secure playback without changing the Reel or its media identity.", action: "Please wait" }
  }[state];
  const retryable = ["offline", "error", "processing"].includes(state);
  return <View style={styles.fallback}><View style={styles.fallbackOrb}><Text style={styles.fallbackIcon}>{copy.icon}</Text></View><Text style={styles.fallbackTitle}>{copy.title}</Text><Text style={styles.fallbackText}>{copy.body}</Text>{state !== "refreshing" ? <Pressable accessibilityRole="button" style={styles.fallbackButton} onPress={retryable ? onRetry : onShare}><Text style={styles.fallbackButtonText}>{copy.action}</Text></Pressable> : null}</View>;
}

function RichCaption({ value }: { value: string }) {
  const tokens = String(value || "").split(/(#[\p{L}\p{N}_]+|@[\p{L}\p{N}_.]+)/gu);
  return <Text style={styles.body} numberOfLines={3}>{tokens.map((token, index) => token.startsWith("#") || token.startsWith("@") ? <Text key={`${token}-${index}`} style={styles.captionLink}>{token}</Text> : token)}</Text>;
}

function Action({ icon, label, value, active, disabled, busy, hint, onPress, onLongPress }: { icon: string; label: string; value?: number; active?: boolean; disabled?: boolean; busy?: boolean; hint?: string; onPress: () => void; onLongPress?: () => void }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`${label}${value ? `, ${value}` : ""}`} accessibilityHint={hint} accessibilityState={{ selected: active, disabled: disabled || busy, busy }} disabled={disabled || busy} style={({ pressed }) => [styles.action, active ? styles.actionActive : undefined, pressed && styles.actionPressed, (disabled || busy) && styles.actionDisabled]} onPress={onPress} onLongPress={onLongPress}>
      <Text style={[styles.actionIcon, active ? styles.actionTextActive : undefined]}>{icon}</Text>
      <Text style={styles.actionLabel}>{label}</Text>
      {value ? <Text style={styles.actionValue}>{value}</Text> : null}
    </Pressable>
  );
}

function reactionIcon(reaction?: string) {
  return ({ like: "♥", love: "♥", fire: "🔥", funny: "☺", wow: "✦", rocket: "🚀", clap: "👏", hundred: "💯", target: "◎", smart: "◇" } as Record<string, string>)[reaction || ""] || "♡";
}

const styles = createThemedStyles(() => ({
  action: {
    alignItems: "center",
    backgroundColor: "rgba(2, 9, 18, 0.62)",
    borderColor: "rgba(109,244,229,0.22)",
    borderRadius: 22,
    borderWidth: 1,
    minHeight: 52,
    justifyContent: "center",
    paddingHorizontal: 5,
    width: 60
  },
  actionActive: {
    backgroundColor: "rgba(37, 208, 167, 0.22)",
    borderColor: colors.accent
  },
  actionPressed: { transform: [{ scale: 0.9 }], backgroundColor: "rgba(97,234,246,0.18)" },
  actionDisabled: { opacity: 0.42 },
  actionIcon: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900"
  },
  actionLabel: { color: "rgba(244,247,251,0.82)", fontSize: 8, fontWeight: "800", marginTop: 2 },
  actionTextActive: {
    color: colors.accent
  },
  actionValue: {
    color: colors.muted,
    fontSize: 10,
    marginTop: 2
  },
  actions: {
    bottom: 112,
    gap: 8,
    position: "absolute",
    right: 12,
    zIndex: 5
  },
  author: {
    alignItems: "center",
    flex: 1,
    flexDirection: "row",
    gap: 10
  },
  authorCopy: {
    flex: 1
  },
  authorMeta: {
    color: "rgba(244,247,251,0.72)",
    fontSize: 12,
    marginTop: 2
  },
  authorName: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  avatar: {
    borderColor: "rgba(57,239,207,0.82)",
    borderRadius: 21,
    borderWidth: 1.5,
    height: 42,
    width: 42
  },
  avatarFallback: {
    backgroundColor: colors.accent,
    borderRadius: 18,
    height: 36,
    width: 36
  },
  body: {
    color: colors.text,
    fontSize: 13,
    lineHeight: 18,
    marginTop: 5,
    textShadowColor: "rgba(0,0,0,0.92)",
    textShadowOffset: { width: 0, height: 1 },
    textShadowRadius: 5
  },
  captionLink: { color: "#43efd4", fontWeight: "800" },
  buffering: {
    alignItems: "center",
    justifyContent: "center",
    left: 0,
    position: "absolute",
    right: 0,
    top: "45%",
    zIndex: 6
  },
  bufferingCore: { alignItems: "center", backgroundColor: "rgba(2,9,18,0.78)", borderColor: "rgba(65,239,211,0.28)", borderRadius: 20, borderWidth: 1, flexDirection: "row", gap: 8, paddingHorizontal: 14, paddingVertical: 9 },
  bufferingText: { color: "rgba(244,247,251,0.84)", fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  caption: {
    bottom: 24,
    left: 14,
    position: "absolute",
    right: 76,
    zIndex: 4
  },
  card: {
    backgroundColor: "#02050b",
    borderColor: "rgba(62,226,210,0.20)",
    borderRadius: 24,
    borderWidth: 1,
    flex: 1,
    marginBottom: 96,
    marginHorizontal: 8,
    marginTop: 112,
    overflow: "hidden"
  },
  /**
   * Edge-to-edge. Every inset is zeroed rather than omitted, and the border is
   * removed rather than made transparent: a 1pt transparent border still
   * reserves a pixel, which is exactly the kind of hairline gap at the screen
   * edge that only shows up on a device.
   */
  cardFullBleed: {
    borderRadius: 0,
    borderWidth: 0,
    marginBottom: 0,
    marginHorizontal: 0,
    marginTop: 0
  },
  fallback: {
    alignItems: "center",
    backgroundColor: "rgba(3,12,24,0.84)",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  fallbackOrb: { alignItems: "center", backgroundColor: "rgba(37,227,194,0.10)", borderColor: "rgba(67,239,212,0.42)", borderRadius: 28, borderWidth: 1, height: 56, justifyContent: "center", marginBottom: 12, width: 56 },
  fallbackIcon: { color: "#43efd4", fontSize: 24, fontWeight: "900" },
  fallbackButton: {
    backgroundColor: "#32dfbc",
    borderRadius: 18,
    marginTop: 14,
    minHeight: 42,
    justifyContent: "center",
    paddingHorizontal: 18
  },
  fallbackButtonText: {
    color: colors.background,
    fontWeight: "900"
  },
  fallbackText: {
    color: colors.muted,
    lineHeight: 20,
    marginTop: 6,
    textAlign: "center"
  },
  fallbackTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  followButton: {
    backgroundColor: "rgba(4,20,28,0.74)",
    borderColor: "rgba(60,239,208,0.55)",
    borderRadius: 16,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  followButtonActive: { backgroundColor: "rgba(53,223,189,0.15)", borderColor: "rgba(161,135,255,0.42)" },
  followText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900"
  },
  liveBadge: { alignItems: "center", backgroundColor: "rgba(255,35,88,0.22)", borderColor: "rgba(255,91,128,0.56)", borderRadius: 14, borderWidth: 1, flexDirection: "row", gap: 6, left: 14, paddingHorizontal: 9, paddingVertical: 6, position: "absolute", top: 72, zIndex: 5 },
  liveDot: { backgroundColor: "#ff3565", borderRadius: 4, height: 7, width: 7 },
  liveText: { color: "#fff", fontSize: 10, fontWeight: "900" },
  joinLive: { alignItems: "center", alignSelf: "flex-start", backgroundColor: "rgba(255,39,89,0.88)", borderRadius: 18, marginTop: 9, minHeight: 38, justifyContent: "center", paddingHorizontal: 16 },
  joinLiveText: { color: "#fff", fontSize: 12, fontWeight: "900" },
  mediaMetaRow: { alignItems: "center", flexDirection: "row", gap: 8, marginTop: 8 },
  musicMicro: { alignItems: "center", backgroundColor: "rgba(2,10,20,0.68)", borderColor: "rgba(87,229,220,0.20)", borderRadius: 16, borderWidth: 1, flexDirection: "row", flexShrink: 1, gap: 7, maxWidth: 210, paddingHorizontal: 7, paddingVertical: 5 },
  musicOrb: { alignItems: "center", backgroundColor: "rgba(129,94,245,0.22)", borderRadius: 10, height: 20, justifyContent: "center", width: 20 },
  musicNote: { color: "#68f3de", fontSize: 11, fontWeight: "900" },
  musicLabel: { color: "rgba(244,247,251,0.86)", flexShrink: 1, fontSize: 9, fontWeight: "700" },
  muteButton: { alignItems: "center", backgroundColor: "rgba(2,10,20,0.72)", borderColor: "rgba(98,235,226,0.24)", borderRadius: 15, borderWidth: 1, height: 30, justifyContent: "center", width: 34 },
  muteButtonText: { color: "#68f3de", fontSize: 10, fontWeight: "900" },
  poster: {
    ...StyleSheet.absoluteFillObject,
    opacity: 0.64
  },
  progressBar: {
    backgroundColor: colors.accent,
    height: "100%"
  },
  progressTrack: {
    backgroundColor: "rgba(255,255,255,0.18)",
    bottom: 0,
    height: 3,
    left: 0,
    position: "absolute",
    right: 0,
    zIndex: 6
  },
  scrim: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(0,0,0,0.10)",
    zIndex: 1
  },
  topScrim: {
    left: 0,
    position: "absolute",
    right: 0,
    top: 0,
    zIndex: 3
  },
  bottomScrim: {
    bottom: 0,
    left: 0,
    position: "absolute",
    right: 0,
    zIndex: 3
  },
  sound: {
    color: "rgba(244,247,251,0.78)",
    fontSize: 12,
    marginTop: 8
  },
  tapLayer: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 2
  },
  title: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900",
    lineHeight: 19,
    textShadowColor: "rgba(0,0,0,0.94)",
    textShadowOffset: { width: 0, height: 1 },
    textShadowRadius: 5
  },
  top: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    left: 12,
    position: "absolute",
    right: 12,
    top: 14,
    zIndex: 5
  },
  video: {
    ...StyleSheet.absoluteFillObject
  }
}));
