import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, FlatList, LayoutChangeEvent, ScrollView, StyleSheet, Text, useWindowDimensions, View, ViewToken } from "react-native";
import { deletePost, getPostDetail, PulsePost, pulsePostUrl, reactToPost, repostPost, savablePostId } from "../api/feed";
import { PostCard } from "../components/PostCard";
import { invalidateNativeSync } from "../core/eventSync";
import { RootStackParamList } from "../navigation/types";
import { sharePulseObject } from "../sharing/nativeShare";
import { actionKey, useSocialActionGuard } from "../social/actionGuard";
import { peekSaveState } from "../social/savedStore";
import { setSaved } from "../social/useSaveAction";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "ProfilePostViewer">;

export function ProfilePostViewerScreen({ route, navigation }: Props) {
  const { height: windowHeight } = useWindowDimensions();
  const [postIds, setPostIds] = useState(() => dedupe(route.params.postIds));
  const [posts, setPosts] = useState<Record<number, PulsePost>>({});
  const [message, setMessage] = useState("");
  const [viewportHeight, setViewportHeight] = useState(() => Math.max(1, Math.round(windowHeight)));
  const [activeIndex, setActiveIndex] = useState(() => Math.max(0, dedupe(route.params.postIds).indexOf(route.params.postId)));
  const loading = useRef(new Set<number>());
  const listRef = useRef<FlatList<number>>(null);
  const guard = useSocialActionGuard();
  const initialIndex = Math.max(0, postIds.indexOf(route.params.postId));

  const loadPost = useCallback(async (postId: number) => {
    if (!postId || posts[postId] || loading.current.has(postId)) return;
    loading.current.add(postId);
    try {
      const detail = await getPostDetail(postId);
      if (detail.post) setPosts((current) => ({ ...current, [postId]: detail.post as PulsePost }));
      else setPostIds((current) => current.filter((id) => id !== postId));
    } catch {
      setMessage("A post could not load. Swipe again to retry.");
    } finally {
      loading.current.delete(postId);
    }
  }, [posts]);

  useEffect(() => {
    [postIds[initialIndex - 1], postIds[initialIndex], postIds[initialIndex + 1]].forEach((id) => id && loadPost(id));
  }, []);

  const onViewableItemsChanged = useCallback(({ viewableItems }: { viewableItems: ViewToken<number>[] }) => {
    const centered = viewableItems.find((item) => item.isViewable && item.index != null);
    const index = Number(centered?.index || 0);
    setActiveIndex(index);
    [postIds[index - 1], postIds[index], postIds[index + 1]].forEach((id) => id && loadPost(id));
  }, [loadPost, postIds]);

  const measureViewport = useCallback((event: LayoutChangeEvent) => {
    const next = Math.round(event.nativeEvent.layout.height);
    if (next > 0) setViewportHeight((current) => current === next ? current : next);
  }, []);

  useEffect(() => {
    if (!viewportHeight) return;
    requestAnimationFrame(() => listRef.current?.scrollToIndex({ index: Math.min(activeIndex, Math.max(0, postIds.length - 1)), animated: false }));
  }, [activeIndex, postIds.length, viewportHeight]);

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
    invalidateNativeSync(["activity", "notifications"], "profile_viewer_delete").catch(() => undefined);
    if (!remaining.length) navigation.goBack();
  }

  const layoutHeight = viewportHeight;
  const viewabilityConfig = useMemo(() => ({ itemVisiblePercentThreshold: 80, minimumViewTime: 80 }), []);
  return (
    <View style={styles.root} onLayout={measureViewport}>
      {message ? <Text accessibilityLiveRegion="polite" style={styles.message}>{message}</Text> : null}
      {layoutHeight > 0 ? <FlatList
        ref={listRef}
        data={postIds}
        keyExtractor={String}
        pagingEnabled
        disableIntervalMomentum
        decelerationRate="fast"
        initialScrollIndex={initialIndex}
        getItemLayout={(_, index) => ({ length: layoutHeight, offset: layoutHeight * index, index })}
        onViewableItemsChanged={onViewableItemsChanged}
        viewabilityConfig={viewabilityConfig}
        showsVerticalScrollIndicator={false}
        renderItem={({ item: postId }) => {
          const post = posts[postId];
          return <View style={[styles.page, { height: layoutHeight }]}>{post ? <ScrollView contentContainerStyle={styles.pageContent} showsVerticalScrollIndicator={false}><PostCard
            post={post}
            active={postId === postIds[activeIndex]}
            busy={guard.isItemBusy(post.id)}
            onOpen={() => undefined}
            onReact={react}
            onSave={save}
            onRepost={repost}
            onComment={(item) => navigation.navigate("PostDetail", { postId: item.id, title: "Comments" })}
            onShare={(item) => sharePulseObject({ kind: "post", url: pulsePostUrl(item.id), title: item.title || "PulseSoc post", description: item.body || item.text, previewImageUrl: item.thumbnail_url || item.image_url }).catch(() => undefined)}
            onDelete={route.params.owner ? remove : undefined}
          /></ScrollView> : <View style={styles.loading}><ActivityIndicator color={colors.accent} /><Text style={styles.loadingText}>Loading post…</Text></View>}</View>;
        }}
      /> : <View style={styles.loading}><ActivityIndicator color={colors.accent} /></View>}
    </View>
  );
}

function dedupe(ids: number[]) { return Array.from(new Set(ids.map(Number).filter((id) => id > 0))); }

const styles = StyleSheet.create({
  root: { backgroundColor: colors.background, flex: 1 },
  page: { backgroundColor: colors.background },
  pageContent: { flexGrow: 1, justifyContent: "center", paddingBottom: 18, paddingTop: 18 },
  loading: { alignItems: "center", flex: 1, justifyContent: "center" },
  loadingText: { color: colors.muted, marginTop: 10 },
  message: { backgroundColor: colors.signalSoft, color: colors.text, left: 0, paddingHorizontal: 14, paddingVertical: 8, position: "absolute", right: 0, top: 0, zIndex: 4 }
});
