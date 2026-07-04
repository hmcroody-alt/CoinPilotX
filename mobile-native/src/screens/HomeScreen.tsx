import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { RouteProp, useNavigation, useRoute } from "@react-navigation/native";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, FlatList, Pressable, RefreshControl, Share, StyleSheet, Text, View } from "react-native";
import {
  listFeed,
  loadCachedFeed,
  PulsePost,
  pulsePostUrl,
  reactToPost,
  repostPost,
  savePost
} from "../api/feed";
import { FeedComposer } from "../components/FeedComposer";
import { PostCard } from "../components/PostCard";
import { AppTabParamList, RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

type HomeNavigation = NativeStackNavigationProp<RootStackParamList>;

const FEED_NAME = "for_you";

export function HomeScreen() {
  const navigation = useNavigation<HomeNavigation>();
  const route = useRoute<RouteProp<AppTabParamList, "Home">>();
  const [posts, setPosts] = useState<PulsePost[]>([]);
  const [offset, setOffset] = useState(0);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [busyPostId, setBusyPostId] = useState<number | null>(null);
  const [composerOpen, setComposerOpen] = useState(false);

  async function load(mode: "initial" | "refresh" | "more" = "initial") {
    if (mode === "more" && (!hasMore || loadingMore)) return;
    const nextOffset = mode === "more" ? offset : 0;
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    if (mode === "more") setLoadingMore(true);
    try {
      const data = await listFeed({ feed: FEED_NAME, offset: nextOffset, limit: 20 });
      setPosts((current) => (mode === "more" ? mergePosts(current, data.posts || []) : data.posts || []));
      setOffset(Number(data.next_offset || nextOffset + (data.posts?.length || 0)));
      setHasMore(Boolean(data.has_more));
    } catch (err) {
      const cached = await loadCachedFeed(FEED_NAME);
      if (cached.length && mode !== "more") {
        setPosts(cached);
        setOffline(true);
      } else {
        setError(err instanceof Error ? err.message : "PulseSoc feed is unavailable.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, []);

  useEffect(() => {
    if (route.params?.openComposer) setComposerOpen(true);
  }, [route.params?.openComposer]);

  const updatePost = useCallback((postId: number, next: Partial<PulsePost>) => {
    setPosts((current) => current.map((post) => (post.id === postId ? { ...post, ...next } : post)));
  }, []);

  async function handleReact(post: PulsePost, reactionType: string) {
    setBusyPostId(post.id);
    const previous = post.viewer_reaction || "";
    const counts = { ...(post.reaction_counts || {}) };
    if (previous) counts[previous] = Math.max(0, Number(counts[previous] || 0) - 1);
    counts[reactionType] = Number(counts[reactionType] || 0) + 1;
    updatePost(post.id, { viewer_reaction: reactionType, reaction_counts: counts });
    try {
      const result = await reactToPost(post.id, reactionType);
      updatePost(post.id, {
        viewer_reaction: result.viewer_reaction || reactionType,
        reaction_counts: result.reaction_counts || counts
      });
    } catch {
      updatePost(post.id, { viewer_reaction: previous, reaction_counts: post.reaction_counts || {} });
    } finally {
      setBusyPostId(null);
    }
  }

  async function handleSave(post: PulsePost) {
    setBusyPostId(post.id);
    updatePost(post.id, { saved: !post.saved });
    try {
      const result = await savePost(post.id);
      updatePost(post.id, { saved: Boolean(result.saved ?? result.is_saved ?? !post.saved) });
    } catch {
      updatePost(post.id, { saved: post.saved });
    } finally {
      setBusyPostId(null);
    }
  }

  async function handleRepost(post: PulsePost) {
    setBusyPostId(post.id);
    updatePost(post.id, { reposted: true, repost_count: Number(post.repost_count || 0) + (post.reposted ? 0 : 1) });
    try {
      const result = await repostPost(post.id);
      updatePost(post.id, { reposted: Boolean(result.reposted ?? result.is_reposted ?? true) });
    } catch {
      updatePost(post.id, { reposted: post.reposted, repost_count: post.repost_count || 0 });
    } finally {
      setBusyPostId(null);
    }
  }

  async function handleShare(post: PulsePost) {
    await Share.share({ message: pulsePostUrl(post.id) }).catch(() => undefined);
  }

  if (loading && !posts.length) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading PulseSoc</Text>
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <FlatList
        style={styles.list}
        contentContainerStyle={styles.content}
        data={posts}
        keyExtractor={(item) => String(item.id)}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
        ListHeaderComponent={
          <View style={styles.header}>
            <View style={styles.headerRow}>
              <View>
                <Text style={styles.title}>Home Feed</Text>
                <Text style={styles.subtitle}>{offline ? "Showing saved feed" : "PulseSoc native feed"}</Text>
              </View>
              <Pressable style={styles.composeButton} onPress={() => setComposerOpen(true)}>
                <Text style={styles.composeText}>Create</Text>
              </Pressable>
            </View>
          </View>
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>{error ? "Feed unavailable" : "No posts yet"}</Text>
            <Text style={styles.emptyText}>{error || "The first posts will appear here when the existing PulseSoc feed returns them."}</Text>
          </View>
        }
        renderItem={({ item }) => (
          <PostCard
            post={item}
            busy={busyPostId === item.id}
            onOpen={(post) => navigation.navigate("PostDetail", { postId: post.id, title: "Post" })}
            onReact={handleReact}
            onSave={handleSave}
            onRepost={handleRepost}
            onPromote={(post) => navigation.navigate("GrowthCenter", { contentType: "post", contentId: post.id, title: "Promote Post" })}
            onShare={handleShare}
            onAuthorPress={(post) => {
              const key = post.author?.public_player_id || post.author?.username || "";
              if (key) navigation.navigate("ProfileDetail", { profileKey: key, title: post.author?.display_name || "Profile" });
            }}
          />
        )}
        onEndReached={() => load("more").catch(() => undefined)}
        onEndReachedThreshold={0.35}
        ListFooterComponent={loadingMore ? <ActivityIndicator style={styles.footer} color={colors.accent} /> : null}
      />
      <FeedComposer
        visible={composerOpen}
        onClose={() => setComposerOpen(false)}
        onCreated={(post) => {
          if (post) setPosts((current) => [post, ...current.filter((item) => item.id !== post.id)]);
          load("refresh").catch(() => undefined);
        }}
      />
    </View>
  );
}

function mergePosts(current: PulsePost[], incoming: PulsePost[]) {
  const seen = new Set(current.map((post) => post.id));
  return [...current, ...incoming.filter((post) => !seen.has(post.id))];
}

const styles = StyleSheet.create({
  center: {
    alignItems: "center",
    backgroundColor: colors.background,
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.muted,
    marginTop: 12
  },
  composeButton: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  composeText: {
    color: colors.background,
    fontWeight: "900"
  },
  content: {
    padding: 16,
    paddingBottom: 32
  },
  empty: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    padding: 18
  },
  emptyText: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 21,
    marginTop: 6
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  footer: {
    padding: 18
  },
  header: {
    marginBottom: 14
  },
  headerRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between"
  },
  list: {
    backgroundColor: colors.background,
    flex: 1
  },
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  subtitle: {
    color: colors.muted,
    fontSize: 13,
    marginTop: 3
  },
  title: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900"
  }
});
