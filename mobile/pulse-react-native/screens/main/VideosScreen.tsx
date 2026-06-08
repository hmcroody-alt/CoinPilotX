import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, FlatList, Image, RefreshControl, Text, TouchableOpacity, View } from "react-native";
import { useVideoPlayer, VideoView } from "expo-video";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { PulseApiError, pulseApi } from "../../services/apiClient";
import { colors, screenStyles } from "../../components/theme";
import { PulseHeroCard, PulseTopBar } from "../../components/PulseChrome";
import { compactNumber, formatDuration, formatRelativeTime } from "../../utils/format";
import { PulseVideoItem, isFailed, isProcessing, readCreator, readDescription, readPlaybackUrl, readPoster, readTitle } from "../../services/pulseMedia";
import { useAuthStore } from "../../store/authStore";

type VideosResponse = {
  ok?: boolean;
  videos?: PulseVideoItem[];
  items?: PulseVideoItem[];
};

export function VideosScreen() {
  const logout = useAuthStore(state => state.logout);
  const [videos, setVideos] = useState<PulseVideoItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    const data = await pulseApi<VideosResponse>("/api/pulse/videos");
    setVideos(data.videos || data.items || []);
  }, []);

  useEffect(() => {
    load()
      .catch(value => {
        if (value instanceof PulseApiError && value.status === 401) {
          logout().catch(() => undefined);
          return;
        }
        setError(value instanceof Error ? value.message : "Videos could not load.");
      })
      .finally(() => setLoading(false));
  }, [load, logout]);

  async function refresh() {
    setRefreshing(true);
    await load().finally(() => setRefreshing(false));
  }

  if (loading) {
    return (
      <View style={screenStyles.centered}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <FlatList
      style={screenStyles.screen}
      data={videos}
      keyExtractor={(item, index) => String(item.id || index)}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.accent} />}
      ListHeaderComponent={
        <View>
          <PulseTopBar subtitle="Videos" safeTop />
          <PulseHeroCard
            eyebrow="PulseSoc Videos"
            title="Videos"
            body="Long-form PulseSoc videos with creator info, Mux playback, duration, reactions, comments, saves, and shares."
          />
          {error ? <Text style={screenStyles.error}>{error}</Text> : null}
        </View>
      }
      ListEmptyComponent={<Text style={screenStyles.muted}>No videos yet.</Text>}
      renderItem={({ item }) => <VideoCard video={item} />}
      contentContainerStyle={{ paddingBottom: 28 }}
    />
  );
}

function VideoCard({ video }: { video: PulseVideoItem }) {
  const [playing, setPlaying] = useState(false);
  const playbackUrl = readPlaybackUrl(video);
  const poster = readPoster(video);
  const creator = readCreator(video);
  const player = useVideoPlayer(playbackUrl, next => {
    next.loop = false;
    next.muted = false;
  });

  useEffect(() => {
    if (playing && playbackUrl && !isProcessing(video) && !isFailed(video)) player.play();
    else player.pause();
  }, [playing, playbackUrl, player, video]);

  return (
    <View style={[screenStyles.card, { padding: 10 }]}>
      <View style={{ minHeight: 430, maxHeight: 560, borderRadius: 18, overflow: "hidden", backgroundColor: "#020812", alignItems: "center", justifyContent: "center" }}>
        {playing && playbackUrl ? (
          <VideoView player={player} nativeControls contentFit="contain" style={{ width: "100%", height: 430 }} />
        ) : poster ? (
          <Image source={{ uri: poster }} resizeMode="contain" style={{ width: "100%", height: 430 }} />
        ) : (
          <MaterialCommunityIcons name={isFailed(video) ? "alert-circle" : "video"} color={colors.accent} size={48} />
        )}
        {formatDuration(video.duration || video.duration_seconds) ? (
          <Text style={{ position: "absolute", right: 10, bottom: 10, color: colors.text, backgroundColor: "rgba(0,0,0,0.72)", paddingHorizontal: 8, paddingVertical: 4, borderRadius: 6, overflow: "hidden" }}>
            {formatDuration(video.duration || video.duration_seconds)}
          </Text>
        ) : null}
        {!playing && playbackUrl && !isProcessing(video) && !isFailed(video) ? (
          <TouchableOpacity onPress={() => setPlaying(true)} style={{ position: "absolute", alignSelf: "center", backgroundColor: "rgba(54,229,143,0.94)", borderRadius: 999, padding: 12 }}>
            <MaterialCommunityIcons name="play" color={colors.background} size={34} />
          </TouchableOpacity>
        ) : null}
      </View>
      <Text style={[screenStyles.cardTitle, { marginTop: 12 }]}>{readTitle(video, "PulseSoc video")}</Text>
      {readDescription(video) ? <Text style={screenStyles.muted}>{readDescription(video)}</Text> : null}
      <Text style={[screenStyles.muted, { marginTop: 8 }]}>
        {creator.display_name || creator.username || "PulseSoc creator"} · {formatRelativeTime(video.created_at)}
      </Text>
      {isProcessing(video) ? <Badge label="Preparing video..." /> : null}
      {isFailed(video) ? <Badge label="Video processing failed" danger /> : null}
      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
        <Chip icon="heart" label={compactNumber(video.likes_count || video.reactions_count)} />
        <Chip icon="comment" label={compactNumber(video.comments_count)} />
        <Chip icon="bookmark" label={compactNumber(video.saves_count)} />
        <Chip icon="share" label={compactNumber(video.shares_count)} />
      </View>
    </View>
  );
}

function Badge({ label, danger }: { label: string; danger?: boolean }) {
  return (
    <Text style={{ alignSelf: "flex-start", color: danger ? colors.danger : colors.accent, borderColor: danger ? colors.danger : colors.accent, borderWidth: 1, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 4, marginTop: 10 }}>
      {label}
    </Text>
  );
}

function Chip({ icon, label }: { icon: React.ComponentProps<typeof MaterialCommunityIcons>["name"]; label: string }) {
  return (
    <View style={{ flexDirection: "row", alignItems: "center", gap: 5, borderWidth: 1, borderColor: colors.border, borderRadius: 999, paddingHorizontal: 10, paddingVertical: 6 }}>
      <MaterialCommunityIcons name={icon} color={colors.accent} size={16} />
      <Text style={screenStyles.muted}>{label}</Text>
    </View>
  );
}
