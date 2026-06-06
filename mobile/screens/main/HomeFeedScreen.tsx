import React, { useCallback, useEffect, useState } from "react";
import { FlatList, RefreshControl, Text, TouchableOpacity, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { colors, screenStyles } from "../../components/theme";
import { FeedSkeleton } from "../../components/feed/FeedSkeleton";
import { PostCard } from "../../components/feed/PostCard";
import { MainStackParamList } from "../../navigation/types";
import { loadFeed } from "../../services/feed/feedApi";
import { PulsePost } from "../../services/feed/types";
import { trackMobileEvent } from "../../services/analytics";

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
      <View style={{ flexDirection: "row", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
        <View style={{ flex: 1 }}>
          <Text style={screenStyles.title}>Pulse</Text>
          <Text style={screenStyles.subtitle}>Live timeline</Text>
        </View>
        <TouchableOpacity style={[screenStyles.button, { minWidth: 112 }]} onPress={onCreate}>
          <Text style={screenStyles.buttonText}>Create</Text>
        </TouchableOpacity>
      </View>
      {error ? <Text style={screenStyles.error}>{error}</Text> : null}
    </View>
  );
}

function EmptyFeed({ onCreate }: { onCreate: () => void }) {
  return (
    <View style={screenStyles.card}>
      <Text style={screenStyles.cardTitle}>No posts yet</Text>
      <Text style={screenStyles.muted}>Create the first Pulse or pull to refresh.</Text>
      <TouchableOpacity style={screenStyles.button} onPress={onCreate}>
        <Text style={screenStyles.buttonText}>Create Pulse</Text>
      </TouchableOpacity>
    </View>
  );
}

function mergePosts(current: PulsePost[], incoming: PulsePost[]) {
  const seen = new Set(current.map(post => post.id));
  return [...current, ...incoming.filter(post => !seen.has(post.id))];
}
