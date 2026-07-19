import { Audio, ResizeMode, Video } from "expo-av";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Image, Pressable, Share, StyleSheet, Text, View } from "react-native";
import { PulseReel, reelIsPlayable, reelPosterUrl, reelVideoUrl, reelWebUrl } from "../api/reels";
import { claimMediaPlayback, releaseMediaPlayback } from "../core/mediaPlaybackCoordinator";
import { refreshCanonicalMediaAccess } from "../media/mediaAccess";
import { colors } from "../theme/colors";

type ReelPlayerCardProps = {
  reel: PulseReel;
  active: boolean;
  muted: boolean;
  offline?: boolean;
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
  offline = false,
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
  const singleTapTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const refreshAttempted = useRef(false);
  const watchStartedAt = useRef(0);
  const [buffering, setBuffering] = useState(false);
  const [progress, setProgress] = useState(0);
  const [failed, setFailed] = useState(false);
  const [ownsPlayback, setOwnsPlayback] = useState(false);
  const [userPaused, setUserPaused] = useState(false);
  const [refreshingUrl, setRefreshingUrl] = useState(false);
  const [source, setSource] = useState(() => reelVideoUrl(reel));
  const initialSource = useMemo(() => reelVideoUrl(reel), [reel]);
  const poster = useMemo(() => reelPosterUrl(reel), [reel]);
  const author = reel.author || {};
  const isLive = String(reel.content_type || reel.post_type || "").toLowerCase() === "live" || Boolean(reel.live_session_id || reel.live?.live_session_id);
  const attachedAudio = reel.audio?.attached_audio_url || reel.audio?.audio_url || "";
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
    if (active) {
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
        setOwnsPlayback(granted);
        return granted ? videoRef.current?.playAsync() : undefined;
      }).catch(() => setOwnsPlayback(false));
    } else {
      setOwnsPlayback(false);
      if (watchStartedAt.current) {
        onViewable?.(reel, Date.now() - watchStartedAt.current);
        watchStartedAt.current = 0;
      }
      videoRef.current?.pauseAsync().catch(() => undefined);
      releaseMediaPlayback(playbackOwnerId).catch(() => undefined);
    }
    return () => { releaseMediaPlayback(playbackOwnerId).catch(() => undefined); };
  }, [active, onViewable, playbackOwnerId, reel]);

  useEffect(() => {
    let cancelled = false;
    async function syncAttachedAudio() {
      if (!active || !ownsPlayback || !attachedAudio) {
        const existing = attachedSoundRef.current;
        attachedSoundRef.current = null;
        if (existing) await existing.unloadAsync().catch(() => undefined);
        return;
      }
      if (!attachedSoundRef.current) {
        const created = await Audio.Sound.createAsync(
          { uri: attachedAudio },
          { isLooping: true, positionMillis: Math.max(0, Number(reel.audio?.audio_start_time || 0) * 1000), shouldPlay: ownsPlayback && !muted && !userPaused, volume: Math.max(0, Math.min(1, Number(reel.audio?.audio_volume ?? 1))) }
        );
        if (cancelled) return created.sound.unloadAsync();
        attachedSoundRef.current = created.sound;
      } else {
        await attachedSoundRef.current.setStatusAsync({ shouldPlay: ownsPlayback && !muted && !userPaused, isMuted: muted });
      }
    }
    syncAttachedAudio().catch(() => undefined);
    return () => { cancelled = true; };
  }, [active, attachedAudio, muted, ownsPlayback, reel.audio?.audio_start_time, reel.audio?.audio_volume, userPaused]);

  useEffect(() => () => {
    attachedSoundRef.current?.unloadAsync().catch(() => undefined);
    attachedSoundRef.current = null;
  }, [attachedAudio]);

  useEffect(() => () => {
    if (singleTapTimer.current) clearTimeout(singleTapTimer.current);
  }, []);

  useEffect(() => {
    if (!active || !ownsPlayback) return;
    if (userPaused) {
      videoRef.current?.pauseAsync().catch(() => undefined);
      attachedSoundRef.current?.pauseAsync().catch(() => undefined);
    } else {
      videoRef.current?.playAsync().catch(() => undefined);
      attachedSoundRef.current?.setStatusAsync({ shouldPlay: !muted, isMuted: muted }).catch(() => undefined);
    }
  }, [active, muted, ownsPlayback, userPaused]);

  function handleTap() {
    const now = Date.now();
    if (now - lastTap.current < 280) {
      if (singleTapTimer.current) clearTimeout(singleTapTimer.current);
      singleTapTimer.current = null;
      onReact(reel, "like");
    } else {
      singleTapTimer.current = setTimeout(() => {
        setUserPaused((current) => !current);
        singleTapTimer.current = null;
      }, 280);
    }
    lastTap.current = now;
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
    <View style={styles.card}>
      {poster ? <Image source={{ uri: poster }} style={styles.poster} resizeMode="cover" blurRadius={active ? 0 : 3} /> : null}
      {contentState === "playable" && source ? (
        <Video
          ref={videoRef}
          source={{ uri: source }}
          style={styles.video}
          resizeMode={ResizeMode.COVER}
          shouldPlay={false}
          isLooping
          isMuted={muted || Boolean(attachedAudio && reel.audio?.original_audio_muted !== false)}
          progressUpdateIntervalMillis={250}
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
          }}
          onError={() => recoverPlaybackUrl().catch(() => undefined)}
        />
      ) : (
        <ReelStateSurface state={refreshingUrl ? "refreshing" : contentState === "playable" ? "error" : contentState} onRetry={() => {
          refreshAttempted.current = false;
          setFailed(false);
          recoverPlaybackUrl().catch(() => undefined);
        }} onShare={() => Share.share({ message: reelWebUrl(reel.id) }).catch(() => undefined)} />
      )}
      {contentState === "playable" ? <Pressable accessibilityRole="button" accessibilityLabel={userPaused ? "Play Reel" : "Pause Reel"} style={styles.tapLayer} onPress={handleTap} onLongPress={() => onOpenReactions(reel)} /> : null}
      <View style={styles.scrim} pointerEvents="none" />
      {buffering || refreshingUrl ? <View style={styles.buffering}><View style={styles.bufferingCore}><ActivityIndicator color="#36f0cf" /><Text style={styles.bufferingText}>{refreshingUrl ? "Refreshing Reel" : "Tuning signal"}</Text></View></View> : null}
      {userPaused && contentState === "playable" && !buffering ? <View pointerEvents="none" style={styles.pausedGlyph}><Text style={styles.pausedGlyphText}>▶</Text></View> : null}

      <View style={styles.top}>
        <Pressable style={styles.author} onPress={() => onAuthorPress(reel)}>
          {author.avatar_url ? <Image source={{ uri: author.avatar_url }} style={styles.avatar} /> : <View style={styles.avatarFallback} />}
          <View style={styles.authorCopy}>
            <Text style={styles.authorName} numberOfLines={1}>{author.display_name || author.name || "PulseSoc creator"}</Text>
            <Text style={styles.authorMeta} numberOfLines={1}>{author.username ? `@${author.username}` : reel.category || "Reels"}</Text>
          </View>
        </Pressable>
        {isLive ? <View style={styles.followButton}><Text style={styles.followText}>LIVE</Text></View> : <Pressable style={[styles.followButton, reel.viewer_follows_author && styles.followButtonActive]} disabled={busy} onPress={() => onFollowCreator(reel)}><Text style={styles.followText}>{reel.viewer_follows_author ? "Following" : "Follow"}</Text></Pressable>}
      </View>

      {isLive ? <View style={styles.liveBadge}><View style={styles.liveDot} /><Text style={styles.liveText}>LIVE · {reel.live?.viewer_count || reel.view_count || 0}</Text></View> : null}

      <View style={styles.actions}>
        <Action icon={reactionIcon(reel.viewer_reaction)} label={reel.viewer_reaction ? "Liked" : "Like"} value={reel.reactions_count || reel.reaction_counts?.like || reel.reaction_counts?.fire || 0} active={Boolean(reel.viewer_reaction)} disabled={reel.reactions_disabled} onPress={() => onReact(reel, reel.viewer_reaction || "like")} onLongPress={() => onOpenReactions(reel)} />
        <Action icon="◌" label="Comment" value={reel.comments_count || 0} onPress={() => onOpenComments(reel)} />
        <Action icon="➤" label="Share" value={reel.share_count || 0} onPress={() => onShare(reel)} />
        <Action icon={reel.saved ? "◆" : "◇"} label={reel.saved ? "Saved" : "Save"} onPress={() => onSave(reel)} active={Boolean(reel.saved)} />
        <Action icon="•••" label="More" onPress={() => onOpenMore(reel)} />
      </View>

      <View style={styles.caption}>
        <Text style={styles.title} numberOfLines={1}>{author.username ? `@${author.username}` : reel.title || "PulseSoc Reel"}</Text>
        {reel.caption ? <RichCaption value={reel.caption} /> : reel.body ? <RichCaption value={reel.body} /> : null}
        {isLive ? <Pressable accessibilityRole="button" accessibilityLabel="Join this Live" style={styles.joinLive} onPress={() => onJoinLive(reel)}><Text style={styles.joinLiveText}>Join Live</Text></Pressable> : null}
        <View style={styles.mediaMetaRow}>
          <Pressable accessibilityRole="button" accessibilityLabel={reel.audio?.title ? `Music: ${reel.audio.title}${reel.audio.artist ? ` by ${reel.audio.artist}` : ""}` : "Original audio"} style={styles.musicMicro} onPress={() => onOpenMusic(reel)}><View style={styles.musicOrb}><Text style={styles.musicNote}>♪</Text></View><Text style={styles.musicLabel} numberOfLines={1}>{reel.audio?.title || "Original audio"}{reel.audio?.artist ? ` · ${reel.audio.artist}` : ""}</Text></Pressable>
          <Pressable accessibilityRole="button" accessibilityLabel={muted ? "Turn Reel sound on" : "Mute Reel"} style={styles.muteButton} onPress={onToggleMuted}><Text style={styles.muteButtonText}>{muted ? "⌁" : "◖))"}</Text></Pressable>
        </View>
      </View>

      <View style={styles.progressTrack}>
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
  pausedGlyph: { alignItems: "center", backgroundColor: "rgba(1,8,16,0.66)", borderColor: "rgba(67,239,212,0.48)", borderRadius: 32, borderWidth: 1, height: 64, justifyContent: "center", left: "50%", marginLeft: -32, marginTop: -32, position: "absolute", top: "50%", width: 64, zIndex: 6 },
  pausedGlyphText: { color: "#51efd5", fontSize: 25, marginLeft: 3 },
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
});
