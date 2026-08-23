import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, FlatList, StyleSheet, Text, View, ViewToken } from "react-native";
import { deletePost, getPostDetail, PulsePost, pulsePostUrl, reactToPost, repostPost, savablePostId } from "../api/feed";
import { listPublicProfilePosts } from "../api/profile";
import { PostCard } from "../components/PostCard";
import { invalidateNativeSync } from "../core/eventSync";
import { RootStackParamList } from "../navigation/types";
import { sharePulseObject } from "../sharing/nativeShare";
import { actionKey, useSocialActionGuard } from "../social/actionGuard";
import { peekSaveState } from "../social/savedStore";
import { setSaved } from "../social/useSaveAction";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "ProfilePostViewer">;

export function ProfilePostViewerScreen({ route, navigation }: Props) {
  const [postIds, setPostIds] = useState(() => dedupe(route.params.postIds));
  const [posts, setPosts] = useState<Record<number, PulsePost>>({});
  const [message, setMessage] = useState("");
  const [activePostId, setActivePostId] = useState(route.params.postId);
  const [nextOffset, setNextOffset] = useState(() => Number(route.params.nextOffset ?? route.params.postIds.length));
  const [hasMore, setHasMore] = useState(Boolean(route.params.hasMore));
  const loading = useRef(new Set<number>());
  const loadingMore = useRef(false);
  const postsRef = useRef(posts);
  const postIdsRef = useRef(postIds);
  const nextOffsetRef = useRef(nextOffset);
  const hasMoreRef = useRef(hasMore);
  const displayPostIdsRef = useRef<number[]>([]);
  const listRef = useRef<FlatList<number>>(null);
  const guard = useSocialActionGuard();

  postsRef.current = posts;
  postIdsRef.current = postIds;
  nextOffsetRef.current = nextOffset;
  hasMoreRef.current = hasMore;

  // Start with the exact tapped post, continue toward older posts, then expose
  // the newer posts that preceded it. Every canonical id appears exactly once.
  const displayPostIds = useMemo(() => rotateFrom(postIds, route.params.postId), [postIds, route.params.postId]);
  displayPostIdsRef.current = displayPostIds;

  const loadPost = useCallback(async (postId: number) => {
    if (!postId || postsRef.current[postId] || loading.current.has(postId)) return;
    loading.current.add(postId);
    try {
      const detail = await getPostDetail(postId);
      if (detail.post) {
        setPosts((current) => ({ ...current, [postId]: detail.post as PulsePost }));
      }
      else setPostIds((current) => current.filter((id) => id !== postId));
    } catch {
      setMessage("A post could not load. Swipe again to retry.");
    } finally {
      loading.current.delete(postId);
    }
  }, []);

  const loadMore = useCallback(async () => {
    if (loadingMore.current || !hasMoreRef.current || !route.params.profileKey) return;
    loadingMore.current = true;
    try {
      const page = await listPublicProfilePosts(
        { userId: route.params.profileId, profileKey: route.params.profileKey },
        { limit: 20, offset: nextOffsetRef.current, mediaOnly: route.params.contentTab === "media" }
      );
      const pagePosts = (page.posts || []).filter((post) => route.params.contentTab !== "media" || Boolean(post.media?.length || post.media_assets?.length || post.attachments?.length));
      if (pagePosts.length) {
        setPosts((current) => {
          const next = { ...current };
          pagePosts.forEach((post) => { next[post.id] = post; });
          return next;
        });
        setPostIds((current) => dedupe(current.concat(pagePosts.map((post) => post.id))));
      }
      const offset = Number(page.next_offset ?? nextOffsetRef.current + (page.posts || []).length);
      nextOffsetRef.current = offset;
      hasMoreRef.current = Boolean(page.has_more);
      setNextOffset(offset);
      setHasMore(Boolean(page.has_more));
      setMessage("");
    } catch {
      setMessage("More posts could not load. Your current posts are preserved.");
    } finally {
      loadingMore.current = false;
    }
  }, [route.params.contentTab, route.params.profileKey]);

  useEffect(() => {
    displayPostIds.slice(0, 3).forEach((id) => loadPost(id));
    if (postIds.indexOf(route.params.postId) >= Math.max(0, postIds.length - 3)) loadMore().catch(() => undefined);
  }, []); // Route context is immutable for the lifetime of this viewer.

  const activeLivePost = posts[activePostId];
  const activeLiveId = Number(activeLivePost?.live?.live_session_id || 0);
  const activeLiveStatus = String(activeLivePost?.live?.status || "").toLowerCase();

  // Keep the visible Profile Live card on canonical server state. In
  // particular, replace "Replay processing" with the ready Mux Long Reel as
  // soon as its webhook finalizes the post; no pull-to-refresh is required.
  useEffect(() => {
    if (!activePostId || !activeLiveId || activeLiveStatus !== "processing") return undefined;
    let cancelled = false;
    const refresh = async () => {
      const detail = await getPostDetail(activePostId).catch(() => null);
      if (!cancelled && detail?.post) {
        setPosts((current) => ({ ...current, [activePostId]: detail.post as PulsePost }));
      }
    };
    const timer = setInterval(() => { refresh().catch(() => undefined); }, 6_000);
    return () => { cancelled = true; clearInterval(timer); };
  }, [activeLiveId, activeLiveStatus, activePostId]);

  const onViewableItemsChanged = useCallback(({ viewableItems }: { viewableItems: ViewToken<number>[] }) => {
    const visible = viewableItems.find((item) => item.isViewable && item.item);
    const postId = Number(visible?.item || 0);
    if (!postId) return;
    setActivePostId(postId);
    const ids = displayPostIdsRef.current;
    const index = ids.indexOf(postId);
    [ids[index - 1], ids[index], ids[index + 1], ids[index + 2]].forEach((id) => id && loadPost(id));
    const canonicalIndex = postIdsRef.current.indexOf(postId);
    if (canonicalIndex >= Math.max(0, postIdsRef.current.length - 4)) loadMore().catch(() => undefined);
  }, [loadMore, loadPost]);

  function patch(postId: number, values: Partial<PulsePost>) {
    setPosts((current) => current[postId] ? ({ ...current, [postId]: { ...current[postId], ...values } }) : current);
  }

  async function react(post: PulsePost, reactionType: string) {
    const previous = post.viewer_reaction || "";
    const removing = previous === reactionType;
    const counts = { ...(post.reaction_counts || {}) };
    if (previous) counts[previous] = Math.max(0, Number(counts[previous] || 0) - 1);
    if (!removing) counts[reactionType] = Number(counts[reactionType] || 0) + 1;
    await guard.run(actionKey("profile_viewer_react", post.id), () => reactToPost(post.id, reactionType), {
      supersede: true,
      optimistic: () => patch(post.id, { viewer_reaction: removing ? "" : reactionType, reaction_counts: counts }),
      onResult: (result) => patch(post.id, { viewer_reaction: result.removed ? "" : result.viewer_reaction || reactionType, reaction_counts: result.reaction_counts || counts }),
      onError: setMessage
    });
  }

  async function save(post: PulsePost) {
    const id = savablePostId(post);
    const saved = peekSaveState("post", id)?.saved ?? Boolean(post.saved ?? post.is_saved);
    const result = await setSaved({ type: "post", id }, !saved);
    patch(post.id, { saved: result.saved, is_saved: result.saved });
    if (!result.ok) setMessage(result.message || "Save failed.");
  }

  async function repost(post: PulsePost) {
    const undo = Boolean(post.reposted ?? post.is_reposted);
    const result = await repostPost(post.id, { undo });
    patch(post.id, { reposted: Boolean(result.reposted ?? !undo), repost_count: Number(result.repost_count ?? post.repost_count ?? 0) });
  }

  async function remove(post: PulsePost) {
    await deletePost(post.id);
    const remaining = postIds.filter((id) => id !== post.id);
    setPostIds(remaining);
    setPosts((current) => { const next = { ...current }; delete next[post.id]; return next; });
    if (activePostId === post.id && remaining.length) setActivePostId(remaining[0]);
    invalidateNativeSync(["activity", "notifications"], "profile_viewer_delete").catch(() => undefined);
    if (!remaining.length) navigation.goBack();
  }

  const viewabilityConfig = useMemo(() => ({ itemVisiblePercentThreshold: 50, minimumViewTime: 80 }), []);
  return (
    <View style={styles.root}>
      {message ? <Text accessibilityLiveRegion="polite" style={styles.message}>{message}</Text> : null}
      <FlatList
        ref={listRef}
        data={displayPostIds}
        keyExtractor={String}
        onViewableItemsChanged={onViewableItemsChanged}
        viewabilityConfig={viewabilityConfig}
        onEndReached={() => loadMore().catch(() => undefined)}
        onEndReachedThreshold={0.6}
        contentContainerStyle={styles.listContent}
        ItemSeparatorComponent={() => <View style={styles.separator} />}
        renderItem={({ item: postId }) => {
          const post = posts[postId];
          return <View style={styles.post}>{post ? <PostCard
            post={post}
            active={postId === activePostId}
            busy={guard.isItemBusy(post.id)}
            onOpen={() => undefined}
            onReact={react}
            onSave={save}
            onRepost={repost}
            onComment={(item) => navigation.navigate("PostDetail", { postId: item.id, title: "Comments" })}
            onShare={(item) => sharePulseObject({ kind: "post", url: pulsePostUrl(item.id), title: item.title || "PulseSoc post", description: item.body || item.text, previewImageUrl: item.thumbnail_url || item.image_url }).catch(() => undefined)}
            onDelete={route.params.owner ? remove : undefined}
          /> : <View style={styles.loading}><ActivityIndicator color={colors.accent} /><Text style={styles.loadingText}>Loading post…</Text></View>}</View>;
        }}
      />
    </View>
  );
}

function dedupe(ids: number[]) { return Array.from(new Set(ids.map(Number).filter((id) => id > 0))); }

function rotateFrom(ids: number[], selectedId: number) {
  const unique = dedupe(ids);
  const index = unique.indexOf(Number(selectedId));
  if (index <= 0) return unique;
  return unique.slice(index).concat(unique.slice(0, index));
}

const styles = createThemedStyles(() => ({
  root: { backgroundColor: "transparent", flex: 1 },
  listContent: { paddingBottom: 32, paddingTop: 12 },
  post: { backgroundColor: "transparent", minHeight: 240 },
  separator: { backgroundColor: colors.border, height: StyleSheet.hairlineWidth, marginVertical: 8 },
  loading: { alignItems: "center", minHeight: 240, justifyContent: "center" },
  loadingText: { color: colors.muted, marginTop: 10 },
  message: { backgroundColor: colors.signalSoft, color: colors.text, left: 0, paddingHorizontal: 14, paddingVertical: 8, position: "absolute", right: 0, top: 0, zIndex: 4 }
}));
