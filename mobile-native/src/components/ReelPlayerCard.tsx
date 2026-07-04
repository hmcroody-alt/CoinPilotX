import { ResizeMode, Video } from "expo-av";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Image, Pressable, Share, StyleSheet, Text, View } from "react-native";
import { PulseReel, reelIsPlayable, reelPosterUrl, reelVideoUrl, reelWebUrl } from "../api/reels";
import { colors } from "../theme/colors";

type ReelPlayerCardProps = {
  reel: PulseReel;
  active: boolean;
  muted: boolean;
  busy?: boolean;
  onToggleMuted: () => void;
  onReact: (reel: PulseReel, reactionType?: string) => void;
  onOpenComments: (reel: PulseReel) => void;
  onSave: (reel: PulseReel) => void;
  onRepost: (reel: PulseReel) => void;
  onPromote?: (reel: PulseReel) => void;
  onShare: (reel: PulseReel) => void;
  onNotInterested: (reel: PulseReel) => void;
  onReport: (reel: PulseReel) => void;
  onFollowCreator: (reel: PulseReel) => void;
  onAuthorPress: (reel: PulseReel) => void;
  onViewable?: (reel: PulseReel, watchMs: number) => void;
};

export function ReelPlayerCard({
  reel,
  active,
  muted,
  busy,
  onToggleMuted,
  onReact,
  onOpenComments,
  onSave,
  onRepost,
  onPromote,
  onShare,
  onNotInterested,
  onReport,
  onFollowCreator,
  onAuthorPress,
  onViewable
}: ReelPlayerCardProps) {
  const videoRef = useRef<Video>(null);
  const lastTap = useRef(0);
  const watchStartedAt = useRef(0);
  const [buffering, setBuffering] = useState(false);
  const [progress, setProgress] = useState(0);
  const [failed, setFailed] = useState(false);
  const source = useMemo(() => reelVideoUrl(reel), [reel]);
  const poster = useMemo(() => reelPosterUrl(reel), [reel]);
  const playable = reelIsPlayable(reel);
  const author = reel.author || {};

  useEffect(() => {
    if (active) {
      watchStartedAt.current = Date.now();
      videoRef.current?.playAsync().catch(() => undefined);
    } else {
      if (watchStartedAt.current) {
        onViewable?.(reel, Date.now() - watchStartedAt.current);
        watchStartedAt.current = 0;
      }
      videoRef.current?.pauseAsync().catch(() => undefined);
    }
  }, [active, onViewable, reel]);

  function handleTap() {
    const now = Date.now();
    if (now - lastTap.current < 280) {
      onReact(reel, "fire");
    } else {
      onToggleMuted();
    }
    lastTap.current = now;
  }

  return (
    <View style={styles.card}>
      {poster ? <Image source={{ uri: poster }} style={styles.poster} resizeMode="cover" blurRadius={active ? 0 : 3} /> : null}
      {playable && source && !failed ? (
        <Video
          ref={videoRef}
          source={{ uri: source }}
          style={styles.video}
          resizeMode={ResizeMode.COVER}
          shouldPlay={active}
          isLooping
          isMuted={muted}
          progressUpdateIntervalMillis={250}
          usePoster={Boolean(poster)}
          posterSource={poster ? { uri: poster } : undefined}
          onPlaybackStatusUpdate={(status) => {
            if (!status.isLoaded) {
              setFailed(Boolean(status.error));
              setBuffering(false);
              return;
            }
            setBuffering(Boolean(status.isBuffering));
            if (status.durationMillis) setProgress(Math.min(1, status.positionMillis / status.durationMillis));
          }}
          onError={() => setFailed(true)}
        />
      ) : (
        <View style={styles.fallback}>
          <Text style={styles.fallbackTitle}>{failed ? "Playback unavailable" : "Processing Reel"}</Text>
          <Text style={styles.fallbackText}>{failed ? "Open this Reel in PulseSoc web while native playback support catches up." : "PulseSoc is still processing this media."}</Text>
          <Pressable style={styles.fallbackButton} onPress={() => Share.share({ message: reelWebUrl(reel.id) }).catch(() => undefined)}>
            <Text style={styles.fallbackButtonText}>Share Link</Text>
          </Pressable>
        </View>
      )}
      <Pressable style={styles.tapLayer} onPress={handleTap} onLongPress={() => onReact(reel, "smart")} />
      <View style={styles.scrim} pointerEvents="none" />
      {buffering ? (
        <View style={styles.buffering}>
          <ActivityIndicator color={colors.accent} />
        </View>
      ) : null}

      <View style={styles.top}>
        <Pressable style={styles.author} onPress={() => onAuthorPress(reel)}>
          {author.avatar_url ? <Image source={{ uri: author.avatar_url }} style={styles.avatar} /> : <View style={styles.avatarFallback} />}
          <View style={styles.authorCopy}>
            <Text style={styles.authorName} numberOfLines={1}>{author.display_name || author.name || "PulseSoc creator"}</Text>
            <Text style={styles.authorMeta} numberOfLines={1}>{author.username ? `@${author.username}` : reel.category || "Reels"}</Text>
          </View>
        </Pressable>
        <Pressable style={styles.followButton} disabled={busy} onPress={() => onFollowCreator(reel)}>
          <Text style={styles.followText}>Follow</Text>
        </Pressable>
      </View>

      <View style={styles.actions}>
        <Action label="Fire" value={reel.reactions_count || reel.reaction_counts?.fire || 0} active={reel.viewer_reaction === "fire"} onPress={() => onReact(reel, "fire")} />
        <Action label="Comments" value={reel.comments_count || 0} onPress={() => onOpenComments(reel)} />
        <Action label={reel.saved ? "Saved" : "Save"} onPress={() => onSave(reel)} active={Boolean(reel.saved)} />
        <Action label="Repost" onPress={() => onRepost(reel)} active={Boolean(reel.reposted)} />
        {onPromote ? <Action label="Promote" onPress={() => onPromote(reel)} /> : null}
        <Action label="Share" value={reel.share_count || 0} onPress={() => onShare(reel)} />
        <Action label="Less" onPress={() => onNotInterested(reel)} />
        <Action label="Report" onPress={() => onReport(reel)} />
      </View>

      <View style={styles.caption}>
        <Text style={styles.title} numberOfLines={2}>{reel.title || "PulseSoc Reel"}</Text>
        {reel.caption ? <Text style={styles.body} numberOfLines={3}>{reel.caption}</Text> : null}
        <Text style={styles.sound} numberOfLines={1}>
          {muted ? "Muted" : "Sound on"} {reel.audio?.title ? `· ${reel.audio.title}` : ""}
        </Text>
      </View>

      <View style={styles.progressTrack}>
        <View style={[styles.progressBar, { width: `${Math.round(progress * 100)}%` }]} />
      </View>
    </View>
  );
}

function Action({ label, value, active, onPress }: { label: string; value?: number; active?: boolean; onPress: () => void }) {
  return (
    <Pressable style={[styles.action, active ? styles.actionActive : undefined]} onPress={onPress}>
      <Text style={[styles.actionText, active ? styles.actionTextActive : undefined]}>{label}</Text>
      {value ? <Text style={styles.actionValue}>{value}</Text> : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  action: {
    alignItems: "center",
    backgroundColor: "rgba(8, 15, 28, 0.58)",
    borderColor: "rgba(255,255,255,0.16)",
    borderRadius: 16,
    borderWidth: 1,
    minHeight: 48,
    justifyContent: "center",
    paddingHorizontal: 8,
    width: 66
  },
  actionActive: {
    backgroundColor: "rgba(37, 208, 167, 0.22)",
    borderColor: colors.accent
  },
  actionText: {
    color: colors.text,
    fontSize: 11,
    fontWeight: "900"
  },
  actionTextActive: {
    color: colors.accent
  },
  actionValue: {
    color: colors.muted,
    fontSize: 10,
    marginTop: 2
  },
  actions: {
    gap: 8,
    position: "absolute",
    right: 10,
    top: "34%",
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
  body: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 20,
    marginTop: 6
  },
  buffering: {
    left: 0,
    position: "absolute",
    right: 0,
    top: "45%",
    zIndex: 6
  },
  caption: {
    bottom: 34,
    left: 14,
    position: "absolute",
    right: 86,
    zIndex: 4
  },
  card: {
    backgroundColor: "#02050b",
    flex: 1,
    overflow: "hidden"
  },
  fallback: {
    alignItems: "center",
    backgroundColor: colors.background,
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  fallbackButton: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    marginTop: 14,
    paddingHorizontal: 14,
    paddingVertical: 10
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
    backgroundColor: "rgba(255,255,255,0.14)",
    borderRadius: 16,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  followText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "900"
  },
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
    backgroundColor: "rgba(0,0,0,0.18)",
    zIndex: 1
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
    fontSize: 22,
    fontWeight: "900",
    lineHeight: 27
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
});
