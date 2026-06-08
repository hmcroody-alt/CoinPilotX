import React, { useCallback, useEffect, useState } from "react";
import { FlatList, Linking, RefreshControl, Text, TouchableOpacity, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { MaterialCommunityIcons } from "@expo/vector-icons";
import { colors, screenStyles } from "../../components/theme";
import { PulseActionChip, PulseHeroCard, PulseSectionCard, PulseTopBar } from "../../components/PulseChrome";
import { FeedSkeleton } from "../../components/feed/FeedSkeleton";
import { PostCard } from "../../components/feed/PostCard";
import { MainStackParamList } from "../../navigation/types";
import { loadFeed } from "../../services/feed/feedApi";
import { PulsePost } from "../../services/feed/types";
import { trackMobileEvent } from "../../services/analytics";
import { PulseLiveItem, PulseStatusRailItem, loadLiveNow, loadStatusRail, readLiveHost, readLiveTitle, readStatusOwner, readStatusTitle } from "../../services/pulseDiscovery";
import { formatRelativeTime } from "../../utils/format";

type Props = NativeStackScreenProps<MainStackParamList, "HomeFeed">;

const PAGE_SIZE = 12;

export function HomeFeedScreen({ navigation }: Props) {
  const [posts, setPosts] = useState<PulsePost[]>([]);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(true);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async (mode: "initial" | "refresh" | "more" = "initial") => {
    if (mode === "more" && (!hasMore || loadingMore)) return;
    if (mode === "more") setLoadingMore(true);
    if (mode === "refresh") setRefreshing(true);
    if (mode === "initial") setLoading(true);
    setError("");
    try {
      const nextOffset = mode === "more" ? offset : 0;
      const result = await loadFeed(nextOffset, PAGE_SIZE);
      setPosts(current => mode === "more" ? mergePosts(current, result.posts || []) : result.posts || []);
      setOffset(result.next_offset || nextOffset + (result.posts || []).length);
      setHasMore(!!result.has_more);
      trackMobileEvent("mobile_feed_load", { mode, count: (result.posts || []).length });
    } catch (err) {
      setError(err instanceof Error ? err.message : "Feed could not load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
      setLoadingMore(false);
    }
  }, [hasMore, loadingMore, offset]);

  useEffect(() => {
    load("initial");
  }, []);

  function updatePost(next: PulsePost) {
    setPosts(current => current.map(post => post.id === next.id ? next : post));
  }

  function removePost(postId: number) {
    setPosts(current => current.filter(post => post.id !== postId));
  }

  if (loading) {
    return (
      <View style={screenStyles.screen}>
        <FeedHeader onCreate={() => navigation.navigate("CreatePulse")} />
        <FeedSkeleton />
      </View>
    );
  }

  return (
    <View style={screenStyles.screen}>
      <FlatList
        data={posts}
        keyExtractor={item => String(item.id)}
        ListHeaderComponent={<FeedHeader onCreate={() => navigation.navigate("CreatePulse")} error={error} />}
        ListEmptyComponent={<EmptyFeed onCreate={() => navigation.navigate("CreatePulse")} />}
        renderItem={({ item }) => (
          <PostCard
            post={item}
            onOpenPost={post => navigation.navigate("PostDetail", { postId: post.id })}
            onOpenProfile={post => navigation.navigate("ProfileDetail", {
              username: post.author?.public_player_id || post.author_public_player_id || "",
              displayName: post.author?.display_name || post.author_public_name,
              avatarUrl: post.author?.avatar_url || post.author_avatar
            })}
            onDeleted={removePost}
            onChanged={updatePost}
          />
        )}
        refreshControl={<RefreshControl refreshing={refreshing} onRefresh={() => load("refresh")} tintColor={colors.accent} />}
        onEndReached={() => load("more")}
        onEndReachedThreshold={0.45}
        ListFooterComponent={loadingMore ? <Text style={[screenStyles.muted, { textAlign: "center", padding: 16 }]}>Loading more...</Text> : !hasMore && posts.length ? <Text style={[screenStyles.muted, { textAlign: "center", padding: 16 }]}>You are caught up.</Text> : null}
        initialNumToRender={6}
        maxToRenderPerBatch={8}
        windowSize={8}
        removeClippedSubviews
      />
    </View>
  );
}

function FeedHeader({ onCreate, error }: { onCreate: () => void; error?: string }) {
  return (
    <View style={{ marginBottom: 12 }}>
      <PulseTopBar subtitle="PulseSoc.com" safeTop />
      <PulseHeroCard
        eyebrow="Global Pulse Feed"
        title="Global Pulse Feed"
        body="Post questions, scam warnings, ideas, and creator updates. New approved posts appear immediately."
        actionLabel="Create Post"
        actionIcon="plus"
        onAction={onCreate}
      />
      <StatusAndLiveRail onCreate={onCreate} />
      {error ? <Text style={screenStyles.error}>{error}</Text> : null}
    </View>
  );
}

function EmptyFeed({ onCreate }: { onCreate: () => void }) {
  return (
    <View style={screenStyles.card}>
      <Text style={screenStyles.cardTitle}>No posts yet</Text>
      <Text style={screenStyles.muted}>Create the first PulseSoc post or pull to refresh.</Text>
      <TouchableOpacity style={screenStyles.button} onPress={onCreate}>
        <Text style={screenStyles.buttonText}>Create Post</Text>
      </TouchableOpacity>
    </View>
  );
}

function StatusAndLiveRail({ onCreate }: { onCreate: () => void }) {
  const [statuses, setStatuses] = useState<PulseStatusRailItem[]>([]);
  const [live, setLive] = useState<PulseLiveItem[]>([]);

  useEffect(() => {
    let mounted = true;
    Promise.all([loadStatusRail(), loadLiveNow()])
      .then(([statusItems, liveItems]) => {
        if (!mounted) return;
        setStatuses(statusItems.slice(0, 3));
        setLive(liveItems.slice(0, 2));
      })
      .catch(() => undefined);
    return () => {
      mounted = false;
    };
  }, []);

  return (
    <View>
      <PulseSectionCard
        eyebrow="Pulse Status"
        title="Share quick stories from your PulseSoc world."
        icon="progress-clock"
        accent={colors.accent}
      >
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8 }}>
          <PulseActionChip icon="plus-circle" label="Create Status" onPress={() => Linking.openURL("https://pulsesoc.com/pulse/status/create")} />
          <PulseActionChip icon="history" label="View Status" onPress={() => Linking.openURL("https://pulsesoc.com/pulse/status")} />
          <PulseActionChip icon="fire" label="Trending" onPress={() => Linking.openURL("https://pulsesoc.com/pulse/status?lane=trending")} />
        </View>
        {statuses.length ? (
          <View style={{ marginTop: 12, gap: 8 }}>
            {statuses.map((status, index) => <StatusTile key={String(status.id || status.status_id || index)} status={status} />)}
          </View>
        ) : (
          <Text style={[screenStyles.muted, { marginTop: 12 }]}>No active statuses right now. Create one from PulseSoc or refresh in a moment.</Text>
        )}
      </PulseSectionCard>

      <PulseSectionCard
        eyebrow="Live Now"
        title="Realtime PulseSoc discovery"
        icon="broadcast"
        accent={colors.accent}
      >
        {live.length ? (
          <View style={{ gap: 8 }}>
            {live.map((item, index) => <LiveTile key={String(item.id || item.live_id || index)} live={item} />)}
          </View>
        ) : (
          <Text style={screenStyles.muted}>No live sessions are running right now.</Text>
        )}
        <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
          <PulseActionChip icon="play-circle" label="Watch Live" onPress={() => Linking.openURL("https://pulsesoc.com/pulse/live")} />
          <PulseActionChip icon="pencil" label="Post Update" onPress={onCreate} />
        </View>
      </PulseSectionCard>
    </View>
  );
}

function StatusTile({ status }: { status: PulseStatusRailItem }) {
  return (
    <View style={{ borderWidth: 1, borderColor: colors.borderSoft, borderRadius: 16, padding: 12, backgroundColor: colors.surface }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <MaterialCommunityIcons name="account-circle" color={colors.accentAlt} size={20} />
        <Text style={{ color: colors.text, fontWeight: "900", flex: 1 }} numberOfLines={1}>{readStatusOwner(status)}</Text>
        <Text style={screenStyles.muted}>{formatRelativeTime(status.created_at)}</Text>
      </View>
      <Text style={{ color: colors.text, marginTop: 8, lineHeight: 20 }} numberOfLines={3}>{readStatusTitle(status)}</Text>
    </View>
  );
}

function LiveTile({ live }: { live: PulseLiveItem }) {
  return (
    <View style={{ borderWidth: 1, borderColor: colors.borderSoft, borderRadius: 16, padding: 12, backgroundColor: colors.surface }}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        <View style={{ width: 8, height: 8, borderRadius: 4, backgroundColor: colors.accent }} />
        <Text style={{ color: colors.text, fontWeight: "900", flex: 1 }} numberOfLines={1}>{readLiveTitle(live)}</Text>
        <Text style={screenStyles.muted}>{Number(live.viewers_count || live.viewer_count || 0)} watching</Text>
      </View>
      <Text style={[screenStyles.muted, { marginTop: 6 }]}>{readLiveHost(live)} · {formatRelativeTime(live.started_at)}</Text>
    </View>
  );
}

function mergePosts(current: PulsePost[], incoming: PulsePost[]) {
  const seen = new Set(current.map(post => post.id));
  return [...current, ...incoming.filter(post => !seen.has(post.id))];
}
