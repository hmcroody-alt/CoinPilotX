import React, { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Dimensions, FlatList, Image, RefreshControl, Text, TouchableOpacity, View, ViewToken } from "react-native";
import { useVideoPlayer, VideoView } from "expo-video";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { pulseApi } from "../../services/apiClient";
import { colors, screenStyles } from "../../components/theme";
import { PulsePill } from "../../components/PulseChrome";
import { compactNumber, formatRelativeTime } from "../../utils/format";
import { PulseVideoItem, isFailed, isProcessing, readCreator, readDescription, readPlaybackUrl, readPoster, readTitle } from "../../services/pulseMedia";

type ReelsResponse = {
  ok?: boolean;
  reels?: PulseVideoItem[];
  items?: PulseVideoItem[];
};

const height = Dimensions.get("window").height;

export function ReelsScreen() {
  const [reels, setReels] = useState<PulseVideoItem[]>([]);
  const [activeId, setActiveId] = useState<string>("");
  const [refreshing, setRefreshing] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    const data = await pulseApi<ReelsResponse>("/api/pulse/reels/feed");
    const next = data.reels || data.items || [];
    setReels(next);
    setActiveId(current => current || String(next[0]?.id || ""));
  }, []);

  useEffect(() => {
    load()
      .catch(value => setError(value instanceof Error ? value.message : "Reels could not load."))
      .finally(() => setLoading(false));
  }, [load]);

  async function refresh() {
    setRefreshing(true);
    await load().finally(() => setRefreshing(false));
  }

  const onViewableItemsChanged = useRef(({ viewableItems }: { viewableItems: ViewToken[] }) => {
    const visible = viewableItems.find(item => item.isViewable)?.item as PulseVideoItem | undefined;
    if (visible) setActiveId(String(visible.id));
  }).current;

  if (loading) {
    return <Loading />;
  }

  return (
    <FlatList
      data={reels}
      keyExtractor={(item, index) => String(item.id || index)}
      pagingEnabled
      decelerationRate="fast"
      snapToInterval={height}
      showsVerticalScrollIndicator={false}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.accent} />}
      viewabilityConfig={{ itemVisiblePercentThreshold: 75 }}
      onViewableItemsChanged={onViewableItemsChanged}
      ListEmptyComponent={<EmptyState error={error} />}
      renderItem={({ item }) => <ReelCard reel={item} active={String(item.id) === activeId} />}
      style={{ flex: 1, backgroundColor: colors.background }}
    />
  );
}

function ReelCard({ reel, active }: { reel: PulseVideoItem; active: boolean }) {
  const insets = useSafeAreaInsets();
  const [muted, setMuted] = useState(true);
  const playbackUrl = readPlaybackUrl(reel);
  const poster = readPoster(reel);
  const creator = readCreator(reel);
  const player = useVideoPlayer(playbackUrl, next => {
    next.loop = true;
    next.muted = true;
  });

  useEffect(() => {
    player.muted = muted;
    if (active && playbackUrl && !isProcessing(reel) && !isFailed(reel)) {
      player.play();
    } else {
      player.pause();
    }
  }, [active, muted, playbackUrl, player, reel]);

  return (
    <View style={{ height, backgroundColor: "#020812" }}>
      {playbackUrl && !isProcessing(reel) && !isFailed(reel) ? (
        <VideoView player={player} nativeControls={false} contentFit="cover" style={{ flex: 1 }} />
      ) : poster ? (
        <Image source={{ uri: poster }} resizeMode="cover" style={{ flex: 1 }} />
      ) : (
        <View style={{ flex: 1, alignItems: "center", justifyContent: "center", padding: 24 }}>
          <MaterialCommunityIcons name={isFailed(reel) ? "alert-circle" : "movie-open-play"} color={colors.accent} size={56} />
          <Text style={[screenStyles.cardTitle, { marginTop: 12, textAlign: "center" }]}>{isFailed(reel) ? "Reel processing failed" : "Preparing reel..."}</Text>
        </View>
      )}

      <View style={{ position: "absolute", top: insets.top + 12, left: 16, right: 16, flexDirection: "row", alignItems: "center", justifyContent: "space-between" }}>
        <PulsePill icon="star-four-points" label="For You" accent={colors.accent} />
        <View style={{ flexDirection: "row", gap: 8, alignItems: "center" }}>
          <TouchableOpacity style={{ width: 38, height: 38, borderRadius: 19, borderWidth: 1, borderColor: colors.border, backgroundColor: "rgba(5,11,20,0.72)", alignItems: "center", justifyContent: "center" }}>
            <MaterialCommunityIcons name="magnify" color={colors.text} size={21} />
          </TouchableOpacity>
          <TouchableOpacity onPress={() => setMuted(value => !value)}>
            <PulsePill icon={muted ? "volume-mute" : "volume-high"} label={muted ? "Muted" : "Sound On"} accent={muted ? colors.muted : colors.accent} />
          </TouchableOpacity>
        </View>
      </View>

      <View style={{ position: "absolute", left: 16, right: 86, bottom: 34 }}>
        <Text style={{ color: colors.text, fontSize: 18, fontWeight: "900" }}>{creator.display_name || creator.username || "PulseSoc creator"}</Text>
        <Text style={{ color: colors.muted, marginTop: 4 }}>@{creator.public_player_id || creator.username || "pulsesoc"} · {formatRelativeTime(reel.created_at)}</Text>
        <Text style={{ color: colors.text, marginTop: 10, fontSize: 15, lineHeight: 21 }}>{readTitle(reel, "PulseSoc reel")}</Text>
        {readDescription(reel) ? <Text style={{ color: colors.muted, marginTop: 6, lineHeight: 20 }}>{readDescription(reel)}</Text> : null}
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 10 }}>
          <Text style={{ color: colors.accentAlt, fontWeight: "900" }}>#PulseSoc</Text>
          <Text style={{ color: colors.accentAlt, fontWeight: "900" }}>#Reels</Text>
        </View>
        <View style={{ flexDirection: "row", alignItems: "center", gap: 7, marginTop: 10 }}>
          <MaterialCommunityIcons name="music-note" color={colors.text} size={16} />
          <Text style={{ color: colors.text, fontWeight: "800" }}>Original PulseSoc sound</Text>
        </View>
      </View>

      <View style={{ position: "absolute", right: 12, bottom: 42, gap: 16, alignItems: "center" }}>
        <ReelAction icon="heart" label={compactNumber(reel.likes_count || reel.reactions_count)} />
        <ReelAction icon="comment" label={compactNumber(reel.comments_count)} />
        <ReelAction icon="bookmark" label={compactNumber(reel.saves_count)} />
        <ReelAction icon="share" label={compactNumber(reel.shares_count)} />
        <ReelAction icon="dots-horizontal-circle" label="More" />
      </View>
    </View>
  );
}

function ReelAction({ icon, label }: { icon: React.ComponentProps<typeof MaterialCommunityIcons>["name"]; label: string }) {
  return (
    <TouchableOpacity style={{ alignItems: "center" }}>
      <MaterialCommunityIcons name={icon} color={colors.text} size={30} />
      <Text style={{ color: colors.text, fontSize: 12, fontWeight: "800", marginTop: 2 }}>{label}</Text>
    </TouchableOpacity>
  );
}

function Loading() {
  return (
    <View style={screenStyles.centered}>
      <ActivityIndicator color={colors.accent} />
    </View>
  );
}

function EmptyState({ error }: { error: string }) {
  return (
    <View style={[screenStyles.screen, { minHeight: height, justifyContent: "center" }]}>
      <Text style={screenStyles.title}>Reels</Text>
      <Text style={screenStyles.subtitle}>{error || "No reels yet. New PulseSoc reels will appear here full-screen."}</Text>
    </View>
  );
}
