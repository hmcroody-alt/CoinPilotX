import { ResizeMode, Video } from "expo-av";
import { useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Image, Pressable, Share, StyleSheet, Text, View } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { PulseStatus, pulseStatusUrl, statusMediaKind, statusMediaUrl, statusMusicLabel, statusPosterUrl } from "../api/status";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";

type Props = {
  status: PulseStatus;
  active: boolean;
  muted: boolean;
  busy?: boolean;
  progress?: number;
  onPrevious: () => void;
  onNext: () => void;
  onToggleMuted: () => void;
  onReact: (status: PulseStatus, reactionType?: string) => void;
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
  const startedAt = useRef(0);
  const [buffering, setBuffering] = useState(false);
  const [failed, setFailed] = useState(false);
  const [paused, setPaused] = useState(false);
  const mediaUrl = useMemo(() => statusMediaUrl(status), [status]);
  const posterUrl = useMemo(() => statusPosterUrl(status), [status]);
  const kind = statusMediaKind(status);
  const author = status.author || {};
  const music = statusMusicLabel(status);

  useEffect(() => {
    if (active) {
      startedAt.current = Date.now();
      videoRef.current?.playAsync().catch(() => undefined);
    } else {
      if (startedAt.current) {
        onViewed?.(status, Date.now() - startedAt.current, false);
        startedAt.current = 0;
      }
      videoRef.current?.pauseAsync().catch(() => undefined);
    }
  }, [active, onViewed, status]);

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
          shouldPlay={active}
          isLooping={false}
          isMuted={muted}
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

      <Pressable accessibilityRole="button" accessibilityLabel="Previous Status" style={styles.leftTap} onPress={onPrevious} onPressIn={() => setPaused(true)} onPressOut={() => setPaused(false)} />
      <Pressable accessibilityRole="button" accessibilityLabel="Next Status" style={styles.rightTap} onPress={onNext} onPressIn={() => setPaused(true)} onPressOut={() => setPaused(false)} />
      <Pressable accessibilityRole="button" accessibilityLabel={muted ? "Unmute Status" : "Mute Status"} style={styles.soundTap} onPress={onToggleMuted} />
      <View style={styles.scrim} />

      {buffering ? (
        <View style={styles.buffering}>
          <ActivityIndicator color={colors.accent} />
        </View>
      ) : null}

      <View style={[styles.header, { top: insets.top + 14 }]}>
        <Pressable style={styles.author} onPress={() => onAuthorPress(status)}>
          {author.avatar_url ? <Image source={{ uri: author.avatar_url }} style={styles.avatar} /> : <View style={styles.avatarFallback} />}
          <View style={styles.authorCopy}>
            <Text style={styles.authorName} numberOfLines={1}>{author.display_name || "PulseSoc member"}</Text>
            <Text style={styles.authorMeta} numberOfLines={1}>{formatShortTime(status.created_at) || status.visibility || "Status"}</Text>
          </View>
        </Pressable>
        <Text style={styles.muteText}>{muted ? "Muted" : "Sound"}</Text>
        <Pressable accessibilityRole="button" accessibilityLabel="Status options" style={styles.moreButton} onPress={() => onMore(status)}><Text style={styles.moreText}>•••</Text></Pressable>
      </View>

      <View style={styles.actions}>
        <Action label="React" value={status.reaction_count || 0} disabled={busy} onPress={() => onReact(status, "fire")} />
        <Action label="Reply" value={status.reply_count || 0} disabled={busy} onPress={() => onReply(status)} />
        <Action label="Share" value={status.share_count || 0} disabled={busy} onPress={() => onShare(status)} />
      </View>

      <View style={styles.caption}>
        {status.body && kind !== "text" ? <Text style={styles.captionText} numberOfLines={4}>{status.body}</Text> : null}
        {music ? <Text style={styles.music} numberOfLines={1}>{music}</Text> : null}
        <Text style={styles.stats}>{status.view_count || 0} views</Text>
      </View>
    </View>
  );
}

function Action({ label, value, disabled, onPress }: { label: string; value?: number; disabled?: boolean; onPress: () => void }) {
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={`${label} to Status${value ? `, ${value}` : ""}`} style={[styles.action, disabled ? styles.disabled : undefined]} disabled={disabled} onPress={onPress}>
      <Text style={styles.actionText}>{label}</Text>
      {value ? <Text style={styles.actionValue}>{value}</Text> : null}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  action: {
    alignItems: "center",
    backgroundColor: "rgba(8, 15, 28, 0.62)",
    borderColor: "rgba(255,255,255,0.16)",
    borderRadius: 16,
    borderWidth: 1,
    minHeight: 50,
    justifyContent: "center",
    paddingHorizontal: 8,
    width: 66
  },
  actionText: {
    color: colors.text,
    fontSize: 11,
    fontWeight: "900"
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
    zIndex: 7
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
  disabled: {
    opacity: 0.45
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
