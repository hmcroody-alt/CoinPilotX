import { Audio, ResizeMode, Video } from "expo-av";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Image, Pressable, Share, StyleSheet, Text, View } from "react-native";
import { PulseReel, reelIsPlayable, reelPosterUrl, reelVideoUrl, reelWebUrl } from "../api/reels";
import { colors } from "../theme/colors";

type ReelPlayerCardProps = {
  reel: PulseReel;
  active: boolean;
  muted: boolean;
  contentTop?: number;
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
  contentTop = 68,
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
  const lastTap = useRef(0);
  const watchStartedAt = useRef(0);
  const [buffering, setBuffering] = useState(false);
  const [progress, setProgress] = useState(0);
  const [failed, setFailed] = useState(false);
  const source = useMemo(() => reelVideoUrl(reel), [reel]);
  const poster = useMemo(() => reelPosterUrl(reel), [reel]);
  const playable = reelIsPlayable(reel);
  const author = reel.author || {};
  const isLive = String(reel.content_type || reel.post_type || "").toLowerCase() === "live" || Boolean(reel.live_session_id || reel.live?.live_session_id);
  const attachedAudio = reel.audio?.attached_audio_url || reel.audio?.audio_url || "";

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

  useEffect(() => {
    let cancelled = false;
    async function syncAttachedAudio() {
      if (!attachedAudio) return;
      if (!attachedSoundRef.current) {
        const created = await Audio.Sound.createAsync(
          { uri: attachedAudio },
          { isLooping: true, positionMillis: Math.max(0, Number(reel.audio?.audio_start_time || 0) * 1000), shouldPlay: active && !muted, volume: Math.max(0, Math.min(1, Number(reel.audio?.audio_volume ?? 1))) }
        );
        if (cancelled) return created.sound.unloadAsync();
        attachedSoundRef.current = created.sound;
      } else {
        await attachedSoundRef.current.setStatusAsync({ shouldPlay: active && !muted, isMuted: muted });
      }
    }
    syncAttachedAudio().catch(() => undefined);
    return () => { cancelled = true; };
  }, [active, attachedAudio, muted, reel.audio?.audio_start_time, reel.audio?.audio_volume]);

  useEffect(() => () => {
    attachedSoundRef.current?.unloadAsync().catch(() => undefined);
    attachedSoundRef.current = null;
  }, [attachedAudio]);

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
          isMuted={muted || Boolean(attachedAudio && reel.audio?.original_audio_muted !== false)}
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
      <Pressable accessibilityRole="button" accessibilityLabel={muted ? "Play Reel with sound" : "Mute Reel"} style={styles.tapLayer} onPress={handleTap} onLongPress={() => onOpenReactions(reel)} />
      <View style={styles.scrim} pointerEvents="none" />
      {buffering ? (
        <View style={styles.buffering}>
          <ActivityIndicator color={colors.accent} />
        </View>
      ) : null}

      <View style={[styles.top, { top: contentTop }]}>
        <Pressable style={styles.author} onPress={() => onAuthorPress(reel)}>
          {author.avatar_url ? <Image source={{ uri: author.avatar_url }} style={styles.avatar} /> : <View style={styles.avatarFallback} />}
          <View style={styles.authorCopy}>
            <Text style={styles.authorName} numberOfLines={1}>{author.display_name || author.name || "PulseSoc creator"}</Text>
            <Text style={styles.authorMeta} numberOfLines={1}>{author.username ? `@${author.username}` : reel.category || "Reels"}</Text>
          </View>
        </Pressable>
        {isLive ? <View style={styles.followButton}><Text style={styles.followText}>Live</Text></View> : <Pressable style={styles.followButton} disabled={busy} onPress={() => onFollowCreator(reel)}><Text style={styles.followText}>{reel.viewer_follows_author ? "Following" : "Follow"}</Text></Pressable>}
      </View>

      {isLive ? <View style={[styles.liveBadge, { top: contentTop + 48 }]}><View style={styles.liveDot} /><Text style={styles.liveText}>LIVE · {reel.live?.viewer_count || reel.view_count || 0}</Text></View> : null}

      <View style={styles.actions}>
        <Action icon={reactionIcon(reel.viewer_reaction)} label={reel.viewer_reaction ? "Reacted" : "React"} value={reel.reactions_count || reel.reaction_counts?.fire || 0} active={Boolean(reel.viewer_reaction)} disabled={reel.reactions_disabled} onPress={() => onReact(reel, reel.viewer_reaction || "fire")} onLongPress={() => onOpenReactions(reel)} />
        <Action icon="💬" label="Comment" value={reel.comments_count || 0} onPress={() => onOpenComments(reel)} />
        <Action icon="↗" label="Share" value={reel.share_count || 0} onPress={() => onShare(reel)} />
        <Action icon="🔖" label={reel.saved ? "Saved" : "Save"} onPress={() => onSave(reel)} active={Boolean(reel.saved)} />
        <Action icon="•••" label="More" onPress={() => onOpenMore(reel)} />
      </View>

      <View style={styles.caption}>
        <Text style={styles.title} numberOfLines={2}>{reel.title || "PulseSoc Reel"}</Text>
        {reel.caption ? <Text style={styles.body} numberOfLines={3}>{reel.caption}</Text> : null}
        {isLive ? <Pressable accessibilityRole="button" accessibilityLabel="Join this Live" style={styles.joinLive} onPress={() => onJoinLive(reel)}><Text style={styles.joinLiveText}>Join Live</Text></Pressable> : null}
        {reel.audio?.title ? <Pressable accessibilityRole="button" accessibilityLabel={`Music: ${reel.audio.title}${reel.audio.artist ? ` by ${reel.audio.artist}` : ""}`} style={styles.musicMicro} onPress={() => onOpenMusic(reel)}><Text style={styles.musicNote}>♪</Text><Text style={styles.musicLabel} numberOfLines={1}>{reel.audio.title}</Text></Pressable> : <Text style={styles.sound}>{muted ? "Muted" : "Sound on"}</Text>}
      </View>

      <View style={styles.progressTrack}>
        <View style={[styles.progressBar, { width: `${Math.round(progress * 100)}%` }]} />
      </View>
    </View>
  );
}

function Action({ icon, label, value, active, disabled, onPress, onLongPress }: { icon: string; label: string; value?: number; active?: boolean; disabled?: boolean; onPress: () => void; onLongPress?: () => void }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`${label}${value ? `, ${value}` : ""}`} accessibilityState={{ selected: active, disabled }} disabled={disabled} style={({ pressed }) => [styles.action, active ? styles.actionActive : undefined, pressed && styles.actionPressed, disabled && styles.actionDisabled]} onPress={onPress} onLongPress={onLongPress}>
      <Text style={[styles.actionIcon, active ? styles.actionTextActive : undefined]}>{icon}</Text>
      <Text style={styles.actionLabel}>{label}</Text>
      {value ? <Text style={styles.actionValue}>{value}</Text> : null}
    </Pressable>
  );
}

function reactionIcon(reaction?: string) {
  return ({ like: "♥", love: "♥", fire: "🔥", funny: "☺", wow: "✦", rocket: "🚀", clap: "👏", hundred: "💯", target: "◎", smart: "◇" } as Record<string, string>)[reaction || ""] || "♡";
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
  actionPressed: { transform: [{ scale: 0.9 }], backgroundColor: "rgba(97,234,246,0.18)" },
  actionDisabled: { opacity: 0.42 },
  actionIcon: {
    color: colors.text,
    fontSize: 21,
    fontWeight: "900"
  },
  actionLabel: { color: colors.text, fontSize: 8, fontWeight: "800", marginTop: 2 },
  actionTextActive: {
    color: colors.accent
  },
  actionValue: {
    color: colors.muted,
    fontSize: 10,
    marginTop: 2
  },
  actions: {
    gap: 9,
    position: "absolute",
    right: 10,
    top: "38%",
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
    bottom: 44,
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
  liveBadge: { alignItems: "center", backgroundColor: "rgba(255,35,88,0.22)", borderColor: "rgba(255,91,128,0.56)", borderRadius: 14, borderWidth: 1, flexDirection: "row", gap: 6, left: 14, paddingHorizontal: 9, paddingVertical: 6, position: "absolute", top: 72, zIndex: 5 },
  liveDot: { backgroundColor: "#ff3565", borderRadius: 4, height: 7, width: 7 },
  liveText: { color: "#fff", fontSize: 10, fontWeight: "900" },
  joinLive: { alignItems: "center", alignSelf: "flex-start", backgroundColor: "rgba(255,39,89,0.88)", borderRadius: 18, marginTop: 9, minHeight: 38, justifyContent: "center", paddingHorizontal: 16 },
  joinLiveText: { color: "#fff", fontSize: 12, fontWeight: "900" },
  musicMicro: { alignItems: "center", alignSelf: "flex-start", flexDirection: "row", gap: 5, marginTop: 8, maxWidth: 160, opacity: 0.58, paddingVertical: 3 },
  musicNote: { color: colors.accent, fontSize: 11 },
  musicLabel: { color: colors.text, flexShrink: 1, fontSize: 9 },
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
