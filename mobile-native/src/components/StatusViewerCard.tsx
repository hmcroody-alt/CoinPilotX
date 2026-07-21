import { Audio, ResizeMode, Video } from "expo-av";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Image, Pressable, Share, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { DEFAULT_STATUS_REACTION, PulseStatus, pulseStatusUrl, StatusReactionType, statusMediaKind, statusMediaUrl, statusMusicLabel, statusPosterUrl } from "../api/status";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";
import { claimMediaPlayback, releaseMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { resolveStatusMusicPolicy } from "../core/attachedMusicAudioPolicy";
import { LikeBurst, LikeBurstHandle } from "../media/MediaGestureFeedback";
import { StatusActionRail } from "./StatusActionRail";

type Props = {
  status: PulseStatus;
  active: boolean;
  muted: boolean;
  busy?: boolean;
  reactionPending?: boolean;
  reactionError?: string;
  progress?: number;
  onPrevious: () => void;
  onNext: () => void;
  onToggleMuted: () => void;
  onReact: (status: PulseStatus, reactionType?: StatusReactionType) => void;
  onReply: (status: PulseStatus) => void;
  onShare: (status: PulseStatus) => void;
  onMore: (status: PulseStatus) => void;
  onAuthorPress: (status: PulseStatus) => void;
  onViewed?: (status: PulseStatus, watchMs: number, completed?: boolean) => void;
};

export function StatusViewerCard({
  status,
  active,
  muted,
  busy,
  reactionPending,
  reactionError,
  progress = 0,
  onPrevious,
  onNext,
  onToggleMuted,
  onReact,
  onReply,
  onShare,
  onMore,
  onAuthorPress,
  onViewed
}: Props) {
  const insets = useSafeAreaInsets();
  const videoRef = useRef<Video>(null);
  const attachedSoundRef = useRef<Audio.Sound | null>(null);
  const startedAt = useRef(0);
  const likeBurstRef = useRef<LikeBurstHandle>(null);
  const lastZoneTap = useRef<{ time: number; side: "left" | "right" }>({ time: 0, side: "left" });
  const [buffering, setBuffering] = useState(false);
  const [failed, setFailed] = useState(false);
  const [paused, setPaused] = useState(false);
  const mediaUrl = useMemo(() => statusMediaUrl(status), [status]);
  const posterUrl = useMemo(() => statusPosterUrl(status), [status]);
  const kind = statusMediaKind(status);
  const author = status.author || {};
  const music = statusMusicLabel(status);
  const musicPolicy = useMemo(() => resolveStatusMusicPolicy(status.music), [status.music]);
  const playbackOwnerId = `status:${status.id}`;
  const drivesPlayback = kind === "video" || musicPolicy.hasAttachedMusic;

  useEffect(() => {
    if (active && drivesPlayback && !muted) {
      startedAt.current = Date.now();
      claimMediaPlayback({
        id: playbackOwnerId,
        kind: "status",
        pause: () => Promise.all([
          videoRef.current?.pauseAsync().catch(() => undefined),
          attachedSoundRef.current?.pauseAsync().catch(() => undefined)
        ]).then(() => undefined),
        stop: () => Promise.all([
          videoRef.current?.stopAsync().catch(() => undefined),
          attachedSoundRef.current?.stopAsync().catch(() => undefined)
        ]).then(() => undefined)
      }).then((granted) => granted && kind === "video" ? videoRef.current?.playAsync() : undefined).catch(() => undefined);
    } else if (active) {
      startedAt.current = Date.now();
      releaseMediaPlayback(playbackOwnerId).catch(() => undefined);
      if (kind === "video") videoRef.current?.playAsync().catch(() => undefined);
    } else {
      if (startedAt.current) {
        onViewed?.(status, Date.now() - startedAt.current, false);
        startedAt.current = 0;
      }
      videoRef.current?.pauseAsync().catch(() => undefined);
      releaseMediaPlayback(playbackOwnerId).catch(() => undefined);
    }
    return () => { releaseMediaPlayback(playbackOwnerId).catch(() => undefined); };
  }, [active, drivesPlayback, kind, muted, onViewed, playbackOwnerId, status]);

  useEffect(() => {
    let cancelled = false;
    const shouldPlayMusic = active && !paused && !muted;
    async function syncAttachedMusic() {
      if (!active || !musicPolicy.hasAttachedMusic) {
        const existing = attachedSoundRef.current;
        attachedSoundRef.current = null;
        if (existing) await existing.unloadAsync().catch(() => undefined);
        return;
      }
      if (!attachedSoundRef.current) {
        const created = await Audio.Sound.createAsync(
          { uri: musicPolicy.musicUrl! },
          { isLooping: musicPolicy.isLooping, positionMillis: musicPolicy.musicStartMs, shouldPlay: shouldPlayMusic, volume: musicPolicy.musicVolume }
        );
        if (cancelled) return created.sound.unloadAsync();
        attachedSoundRef.current = created.sound;
      } else {
        await attachedSoundRef.current.setStatusAsync({ shouldPlay: shouldPlayMusic });
      }
    }
    syncAttachedMusic().catch(() => undefined);
    return () => { cancelled = true; };
  }, [active, muted, paused, musicPolicy]);

  useEffect(() => () => {
    attachedSoundRef.current?.unloadAsync().catch(() => undefined);
    attachedSoundRef.current = null;
  }, [musicPolicy.musicUrl]);

  useEffect(() => {
    if (!active || paused || kind === "video") return;
    const timer = setTimeout(() => { markComplete(); onNext(); }, 6000);
    return () => clearTimeout(timer);
  }, [active, kind, onNext, paused, status.id]);

  useEffect(() => {
    if (paused) videoRef.current?.pauseAsync().catch(() => undefined);
    else if (active) videoRef.current?.playAsync().catch(() => undefined);
  }, [active, paused]);

  function markComplete() {
    if (!startedAt.current) return;
    onViewed?.(status, Date.now() - startedAt.current, true);
    startedAt.current = Date.now();
  }

  /**
   * Double tap to like, layered on top of the existing prev/next nav zones.
   * Nav stays instant (undelayed) so story flick-through never feels
   * laggy — only a second same-side tap inside the double-tap window is
   * reinterpreted as a like instead of a repeat navigation.
   */
  function handleZoneTap(side: "left" | "right", nav: () => void, event?: { nativeEvent?: { locationX?: number; locationY?: number } }) {
    const now = Date.now();
    const isDoubleTap = side === lastZoneTap.current.side && now - lastZoneTap.current.time < 280;
    lastZoneTap.current = { time: isDoubleTap ? 0 : now, side };
    if (isDoubleTap) {
      const x = event?.nativeEvent?.locationX ?? (side === "left" ? 90 : 260);
      const y = event?.nativeEvent?.locationY ?? 320;
      likeBurstRef.current?.trigger(x, y);
      onReact(status, DEFAULT_STATUS_REACTION);
      return;
    }
    nav();
  }

  return (
    <View style={styles.card}>
      <View style={[styles.progressTrack, { top: insets.top + 4 }]}>
        <View style={[styles.progressBar, { width: `${Math.max(3, Math.min(100, Math.round(progress * 100)))}%` }]} />
      </View>

      {kind === "video" && mediaUrl && !failed ? (
        <Video
          ref={videoRef}
          source={{ uri: mediaUrl }}
          style={styles.media}
          resizeMode={ResizeMode.COVER}
          shouldPlay={false}
          isLooping={false}
          isMuted={muted || musicPolicy.muteOriginalAudio}
          usePoster={Boolean(posterUrl)}
          posterSource={posterUrl ? { uri: posterUrl } : undefined}
          onPlaybackStatusUpdate={(playbackStatus) => {
            if (!playbackStatus.isLoaded) {
              setFailed(Boolean(playbackStatus.error));
              setBuffering(false);
              return;
            }
            setBuffering(Boolean(playbackStatus.isBuffering));
            if (playbackStatus.didJustFinish) {
              markComplete();
              onNext();
            }
          }}
          onError={() => setFailed(true)}
        />
      ) : kind === "image" && mediaUrl ? (
        <Image source={{ uri: mediaUrl }} style={styles.media} resizeMode="cover" />
      ) : (
        <View style={styles.textStatus}>
          <Text style={styles.textStatusBody}>{status.body || (failed ? "Status media is unavailable." : "PulseSoc Status")}</Text>
          {failed ? (
            <Pressable style={styles.webButton} onPress={() => Share.share({ message: pulseStatusUrl(status.id) }).catch(() => undefined)}>
              <Text style={styles.webButtonText}>Share Status Link</Text>
            </Pressable>
          ) : null}
        </View>
      )}

      <Pressable accessibilityRole="button" accessibilityLabel="Previous Status. Double tap to like." style={styles.leftTap} onPress={(event) => handleZoneTap("left", onPrevious, event)} onPressIn={() => setPaused(true)} onPressOut={() => setPaused(false)} />
      <Pressable accessibilityRole="button" accessibilityLabel="Next Status. Double tap to like." style={styles.rightTap} onPress={(event) => handleZoneTap("right", onNext, event)} onPressIn={() => setPaused(true)} onPressOut={() => setPaused(false)} />
      <Pressable accessibilityRole="button" accessibilityLabel={muted ? "Unmute Status" : "Mute Status"} style={styles.soundTap} onPress={onToggleMuted} />
      <View style={styles.scrim} />
      <LikeBurst ref={likeBurstRef} />

      {buffering ? (
        <View style={styles.buffering}>
          <ActivityIndicator color={colors.accent} />
        </View>
      ) : null}

      <View style={[styles.header, { top: insets.top + 14 }]}>
        <Pressable accessibilityRole="button" accessibilityLabel={`Open ${author.display_name || "member"} profile, Status posted ${formatShortTime(status.created_at) || "recently"}`} style={styles.author} onPress={() => onAuthorPress(status)}>
          {author.avatar_url ? <Image source={{ uri: author.avatar_url }} style={styles.avatar} /> : <View style={styles.avatarFallback} />}
          <View style={styles.authorCopy}>
            <Text style={styles.authorName} numberOfLines={1}>{author.display_name || "PulseSoc member"}</Text>
            <Text style={styles.authorMeta} numberOfLines={1}>{formatShortTime(status.created_at) || status.visibility || "Status"}</Text>
          </View>
        </Pressable>
        <Text style={styles.muteText}>{muted ? "Muted" : "Sound"}</Text>
        <Pressable accessibilityRole="button" accessibilityLabel="Status options" style={styles.moreButton} onPress={() => onMore(status)}><Text style={styles.moreText}>•••</Text></Pressable>
      </View>

      <StatusActionRail
        reactionCount={status.reaction_count || 0}
        selectedReaction={status.viewer_reaction}
        reactionPending={reactionPending}
        shareBusy={busy}
        onReact={(reactionType) => onReact(status, reactionType)}
        onReply={() => onReply(status)}
        onShare={() => onShare(status)}
      />
      {reactionError ? (
        <Text accessibilityLiveRegion="polite" style={styles.reactionError}>{reactionError}</Text>
      ) : null}

      <View style={styles.caption}>
        {status.body && kind !== "text" ? <Text accessibilityLabel={`Status caption, ${status.body}`} style={styles.captionText} numberOfLines={4}>{status.body}</Text> : null}
        {music ? <Text accessibilityLabel={`Status music, ${music}`} style={styles.music} numberOfLines={1}>{music}</Text> : null}
        <Text style={styles.stats}>{status.view_count || 0} views</Text>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
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
    top: "45%",
    zIndex: 8
  },
  caption: {
    bottom: 32,
    left: 14,
    position: "absolute",
    right: 88,
    zIndex: 7
  },
  captionText: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "800",
    lineHeight: 23
  },
  card: {
    backgroundColor: "#02050b",
    flex: 1,
    overflow: "hidden"
  },
  header: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10,
    left: 12,
    position: "absolute",
    right: 12,
    top: 16,
    zIndex: 7
  },
  leftTap: {
    bottom: 0,
    left: 0,
    position: "absolute",
    top: 64,
    width: "34%",
    zIndex: 4
  },
  media: {
    ...StyleSheet.absoluteFillObject
  },
  music: {
    color: "rgba(244,247,251,0.8)",
    fontSize: 12,
    marginTop: 8
  },
  muteText: {
    color: "rgba(244,247,251,0.72)",
    fontSize: 12,
    fontWeight: "800"
  },
  moreButton: { alignItems: "center", height: 40, justifyContent: "center", width: 44 },
  moreText: { color: colors.text, fontSize: 18, fontWeight: "900" },
  progressBar: {
    backgroundColor: colors.accent,
    height: "100%"
  },
  progressTrack: {
    backgroundColor: "rgba(255,255,255,0.18)",
    height: 3,
    left: 10,
    position: "absolute",
    right: 10,
    top: 8,
    zIndex: 8
  },
  reactionError: {
    color: colors.danger,
    fontSize: 11,
    fontWeight: "800",
    maxWidth: 150,
    position: "absolute",
    right: 10,
    textAlign: "right",
    top: "58%",
    zIndex: 7
  },
  rightTap: {
    bottom: 0,
    position: "absolute",
    right: 0,
    top: 64,
    width: "66%",
    zIndex: 4
  },
  scrim: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: "rgba(0,0,0,0.16)",
    pointerEvents: "none",
    zIndex: 2
  },
  soundTap: {
    height: 64,
    position: "absolute",
    right: 0,
    top: 0,
    width: 120,
    zIndex: 5
  },
  stats: {
    color: "rgba(244,247,251,0.66)",
    fontSize: 12,
    marginTop: 7
  },
  textStatus: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    flex: 1,
    justifyContent: "center",
    padding: 26
  },
  textStatusBody: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900",
    lineHeight: 36,
    textAlign: "center"
  },
  webButton: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    marginTop: 18,
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  webButtonText: {
    color: colors.background,
    fontWeight: "900"
  }
});
