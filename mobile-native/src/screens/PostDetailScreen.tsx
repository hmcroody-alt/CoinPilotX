import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useState } from "react";
import {
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import {
  addPostComment,
  deletePost,
  getPostDetail,
  listPostComments,
  loadCachedPostDetail,
  POST_COMMENT_PAGE_SIZE,
  PulseComment,
  PulsePost,
  pulsePostUrl,
  reactToPost,
  repostPost,
  savablePostId
} from "../api/feed";
import { isContentOwner } from "../api/contentOwnership";
import { describeDeleteError } from "../api/deleteErrors";
import { profileTargetFromPost } from "../api/profile";
import { profileNavigationParams, resolveProfileTarget } from "../api/profileTarget";
import { PostCard } from "../components/PostCard";
import { peekSaveState } from "../social/savedStore";
import { setSaved } from "../social/useSaveAction";
import { LogiNexusScreenShell, LogiNexusStatePanel } from "../components/Screen";
import { invalidateNativeSync } from "../core/eventSync";
import { RootStackParamList } from "../navigation/types";
import { useAuth } from "../session/auth";
import { colors } from "../theme/colors";
import { sharePulseObject } from "../sharing/nativeShare";
import { actionKey, useSocialActionGuard } from "../social/actionGuard";
import { CommentThread, commentAuthorLabel } from "../social/CommentThread";
import { buildCommentTree, countCommentTree, flattenCommentTree, mergeFlatComments, toggleSetValue } from "../social/commentTree";
import { authorHandle, seedReplyDraft } from "../social/mentions";

type Props = NativeStackScreenProps<RootStackParamList, "PostDetail">;

export function PostDetailScreen({ route, navigation }: Props) {
  const postId = route.params.postId;
  const { authState } = useAuth();
  const currentUserId = Number(authState.user?.user_id || 0);
  const [post, setPost] = useState<PulsePost | null>(null);
  // Comments are held FLAT, in server order, and nested only for rendering.
  // Page 2 can carry a reply whose parent arrived on page 1, so re-parenting has
  // to happen over the whole accumulation rather than per page. Merging two
  // already-nested pages at the root level would strand that reply as a
  // top-level comment, which looks exactly like the bug this screen shipped with.
  const [flatComments, setFlatComments] = useState<PulseComment[]>([]);
  const [commentTotal, setCommentTotal] = useState(0);
  const [hasMoreComments, setHasMoreComments] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [expandedReplies, setExpandedReplies] = useState<Set<number>>(new Set());
  const [replyTo, setReplyTo] = useState<PulseComment | null>(null);
  const [commentBody, setCommentBody] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [posting, setPosting] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);
  // Replaces a `busy` scalar that was written on every action and read by
  // nothing, so it prevented no duplicate request at all. The guard is keyed by
  // action+id and holds its lock in a ref, so a second tap is dropped
  // synchronously rather than one render too late.
  const guard = useSocialActionGuard();
  const busy = guard.anyBusy() || deleting;

  const comments = useMemo(() => buildCommentTree(flatComments), [flatComments]);
  const loadedCommentCount = flatComments.length;

  async function load(mode: "initial" | "refresh" = "initial") {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      // The post and its first comment page are independent reads, so they run
      // together. The comment page is the one that carries total/has_more, which
      // the post detail endpoint does not return.
      const [detailResult, pageResult] = await Promise.allSettled([
        getPostDetail(postId),
        listPostComments(postId, { limit: POST_COMMENT_PAGE_SIZE, offset: 0 })
      ]);
      if (detailResult.status === "rejected") throw detailResult.reason;
      const detail = detailResult.value;
      setPost(detail.post || null);
      if (pageResult.status === "fulfilled") {
        setFlatComments(pageResult.value.flat);
        setCommentTotal(pageResult.value.total);
        setHasMoreComments(pageResult.value.hasMore);
      } else {
        // The post loaded; only the pager failed. Show the comments the detail
        // response already included rather than an empty thread, and disable
        // "load more" because without a total we would be guessing.
        setFlatComments(flattenCommentTree(detail.comments || []));
        setCommentTotal(Number(detail.post?.comment_count || countCommentTree(detail.comments || [])));
        setHasMoreComments(false);
      }
      setExpandedReplies(new Set());
      setReplyTo(null);
    } catch (err) {
      const cached = await loadCachedPostDetail(postId);
      if (cached?.post) {
        setPost(cached.post);
        setFlatComments(flattenCommentTree(cached.comments || []));
        setCommentTotal(countCommentTree(cached.comments || []));
        setHasMoreComments(false);
        setOffline(true);
      } else {
        setError(err instanceof Error ? err.message : "Post unavailable.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  async function loadMoreComments() {
    if (!hasMoreComments || loadingMore || offline) return;
    setLoadingMore(true);
    try {
      // The offset is rows already consumed, not roots rendered. Counting roots
      // would re-request every reply on every page.
      const page = await listPostComments(postId, { limit: POST_COMMENT_PAGE_SIZE, offset: loadedCommentCount });
      setFlatComments((current) => mergeFlatComments(current, page.flat));
      setCommentTotal(page.total);
      setHasMoreComments(page.hasMore);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load more comments.");
    } finally {
      setLoadingMore(false);
    }
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [postId]);

  async function handleReact(nextPost: PulsePost, reactionType: string) {
    if (!post) return;
    const previousReaction = post.viewer_reaction || "";
    const previousCounts = post.reaction_counts || {};
    const removing = previousReaction === reactionType;
    const counts = { ...previousCounts };
    if (previousReaction) counts[previousReaction] = Math.max(0, Number(counts[previousReaction] || 0) - 1);
    if (!removing) counts[reactionType] = Number(counts[reactionType] || 0) + 1;
    await guard.run(actionKey("post_react", nextPost.id), () => reactToPost(nextPost.id, reactionType), {
      // A user changing their mind mid-flight is legitimate, so the second tap
      // is allowed through and the sequence guard makes the LATER tap win.
      // Without that, the slower first response lands last and the button
      // silently reverts to the reaction the user just abandoned.
      supersede: true,
      optimistic: () => setPost((current) => (current ? { ...current, viewer_reaction: removing ? "" : reactionType, reaction_counts: counts } : current)),
      onResult: (result) => setPost((current) => (current
        ? {
          ...current,
          viewer_reaction: result.removed ? "" : result.viewer_reaction || result.reaction_type || reactionType,
          reaction_counts: result.reaction_counts || counts
        }
        : current)),
      onRollback: () => setPost((current) => (current ? { ...current, viewer_reaction: previousReaction, reaction_counts: previousCounts } : current)),
      onError: setError
    });
  }

  // Shared store rather than this screen's guard — the feed or profile that
  // pushed this screen is still mounted behind it holding its own copy of the
  // same post. See `social/useSaveAction.ts`.
  async function handleSave(nextPost: PulsePost) {
    if (!post) return;
    const savableId = savablePostId(post) || Number(nextPost.id || 0);
    const previousSaved = peekSaveState("post", savableId)?.saved ?? Boolean(post.saved ?? post.is_saved);
    const outcome = await setSaved({ type: "post", id: savableId }, !previousSaved);
    setPost((current) => (current ? { ...current, saved: outcome.saved, is_saved: outcome.saved } : current));
    if (!outcome.ok && outcome.message) setError(outcome.message);
  }

  async function handleRepost(nextPost: PulsePost) {
    if (!post) return;
    const previousReposted = Boolean(post.reposted);
    const previousCount = Number(post.repost_count || 0);
    const undo = previousReposted;
    // A real toggle now that the route has a DELETE branch. It was one-way while
    // the server returned {ok, post_id, next_url} with no `reposted` flag, no
    // count and no undo path, because flipping the button off would have claimed
    // an un-repost that never happened. The server's count is authoritative in
    // onResult since it includes reposts by people this screen cannot see.
    await guard.run(actionKey("post_repost", nextPost.id), () => repostPost(nextPost.id, { undo }), {
      optimistic: () => setPost((current) => (current
        ? { ...current, reposted: !undo, repost_count: undo ? Math.max(0, previousCount - 1) : previousCount + 1 }
        : current)),
      onResult: (result) => setPost((current) => (current
        ? {
          ...current,
          reposted: Boolean(result.reposted ?? result.is_reposted ?? !undo),
          repost_count: typeof result.repost_count === "number"
            ? result.repost_count
            : undo ? Math.max(0, previousCount - 1) : previousCount + 1
        }
        : current)),
      onRollback: () => setPost((current) => (current ? { ...current, reposted: previousReposted, repost_count: previousCount } : current)),
      onError: setError
    });
  }

  async function handleComment() {
    const body = commentBody.trim();
    if (!body || posting) return;
    const parent = replyTo;
    const parentId = Number(parent?.id || 0);
    setPosting(true);
    setCommentBody("");
    setReplyTo(null);
    try {
      const result = await addPostComment(postId, body, parentId);
      if (result.comment) {
        const created = result.comment;
        // Append rather than prepend: the server orders comments created_at ASC,
        // so a new comment belongs at the end. Prepending it — which is what this
        // screen used to do — put it above comments older than it, and the next
        // refresh silently moved it, which reads as the comment jumping.
        setFlatComments((current) => mergeFlatComments(current, [created]));
        setCommentTotal((current) => current + 1);
        setPost((current) => (current ? { ...current, comment_count: Number(current.comment_count || 0) + 1 } : current));
        // A reply the user cannot see is indistinguishable from a reply that
        // failed, so open the thread it landed in.
        if (parentId) setExpandedReplies((current) => new Set(current).add(parentId));
      } else {
        await load("refresh");
      }
      invalidateNativeSync(["activity", "notifications"], "post_detail_comment").catch(() => undefined);
    } catch (err) {
      setCommentBody(body);
      setReplyTo(parent);
      setError(err instanceof Error ? err.message : "Comment failed.");
    } finally {
      setPosting(false);
    }
  }

  function handleReply(comment: PulseComment) {
    setReplyTo(comment);
    // Seeding the handle is idempotent, so tapping Reply twice cannot stack
    // "@name @name" into the draft.
    setCommentBody((current) => seedReplyDraft(current, comment.author || comment.user));
  }

  function handleMentionPress(username: string) {
    const handle = String(username || "").trim();
    if (!handle) return;
    // A mention carries only a handle, so the target is resolved from the handle
    // itself rather than assembled by hand — resolveProfileTarget is what
    // normalizes and sanitizes it, and skipping it is how a mention ends up
    // navigating to a profileKey the detail screen cannot look up.
    const params = profileNavigationParams(resolveProfileTarget(handle), `@${handle}`);
    if (params) navigation.navigate("ProfileDetail", params);
  }

  function handleCommentAuthorPress(comment: PulseComment) {
    const handle = authorHandle(comment.author || comment.user);
    if (handle) handleMentionPress(handle);
  }

  async function handleDelete(target: PulsePost) {
    if (deleting) return;
    setDeleting(true);
    try {
      await deletePost(target.id);
      invalidateNativeSync(["activity", "notifications"], "post_detail_delete", [
        {
          event_type: "pulse_post_deleted",
          entity_type: "post",
          entity_id: target.id,
          invalidates: ["activity", "notifications"],
          metadata: { source: "native_post_detail" }
        }
      ]).catch(() => undefined);
      navigation.goBack();
    } catch (err) {
      setError(describeDeleteError(err, "Post"));
      setDeleting(false);
    }
  }

  if (loading && !post) {
    return (
      <LogiNexusScreenShell>
        <LogiNexusStatePanel state="loading" title="Loading post" body="Opening the latest server-authoritative signal." loading />
      </LogiNexusScreenShell>
    );
  }

  if (!post) {
    return (
      <LogiNexusScreenShell>
        <LogiNexusStatePanel state="error" title="Post unavailable" body={error || "PulseSoc could not load this post."} />
      </LogiNexusScreenShell>
    );
  }

  return (
    <KeyboardAvoidingView style={styles.keyboard} behavior={Platform.OS === "ios" ? "padding" : undefined}>
      <FlatList
        style={styles.list}
        contentContainerStyle={styles.content}
        data={comments}
        keyExtractor={(item, index) => `${item.id}-${index}`}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
        ListHeaderComponent={
          <View>
            {offline ? <Text style={styles.offline}>Showing saved post</Text> : null}
            {error ? <Text style={styles.error}>{error}</Text> : null}
            <PostCard
              post={post}
              detail
              busy={busy}
              onReact={handleReact}
              onSave={handleSave}
              onRepost={handleRepost}
              onPromote={(item) => navigation.navigate("GrowthCenter", { contentType: "post", contentId: item.id, title: "Promote Post" })}
              onShare={(item) => sharePulseObject({
                kind: "post",
                url: pulsePostUrl(item.id),
                title: item.title || "PulseSoc post",
                description: item.body || item.text || item.content,
                author: item.author?.display_name || item.author?.name || item.author?.username || item.author_name,
                previewImageUrl: item.thumbnail_url || item.image_url
              }).catch(() => undefined)}
              onDelete={isContentOwner(post, currentUserId) ? handleDelete : undefined}
              onAuthorPress={(item) => {
                const params = profileNavigationParams(profileTargetFromPost(item), item.author?.display_name || "Profile");
                if (params) navigation.navigate("ProfileDetail", params);
              }}
            />
            <View style={styles.commentComposer}>
              {replyTo ? (
                <View style={styles.replyBanner}>
                  <Text style={styles.replyBannerText} numberOfLines={1}>
                    {`Replying to ${commentAuthorLabel(replyTo)}`}
                  </Text>
                  <Pressable
                    accessibilityRole="button"
                    accessibilityLabel="Cancel reply"
                    testID="post-detail-cancel-reply"
                    onPress={() => setReplyTo(null)}
                  >
                    <Text style={styles.replyBannerCancel}>Cancel</Text>
                  </Pressable>
                </View>
              ) : null}
              <TextInput
                accessibilityLabel="Comment text"
                testID="post-detail-comment-input"
                style={styles.input}
                value={commentBody}
                onChangeText={setCommentBody}
                onSubmitEditing={handleComment}
                placeholder="Add a comment"
                placeholderTextColor={colors.muted}
                multiline
              />
              <Pressable
                accessibilityRole="button"
                accessibilityLabel="Submit comment"
                accessibilityState={{ disabled: !commentBody.trim() || posting, busy: posting }}
                testID="post-detail-submit-comment"
                style={[styles.commentButton, (!commentBody.trim() || posting) && styles.commentButtonDisabled]}
                onPress={handleComment}
                disabled={!commentBody.trim() || posting}
              >
                <Text style={styles.commentButtonText}>{posting ? "Sending" : replyTo ? "Reply" : "Post"}</Text>
              </Pressable>
            </View>
            <Text style={styles.sectionTitle} testID="post-detail-comments-title">
              {commentTotal ? `Comments (${commentTotal})` : "Comments"}
            </Text>
          </View>
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyText}>No comments yet.</Text>
          </View>
        }
        ListFooterComponent={
          hasMoreComments ? (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Load more comments"
              accessibilityState={{ busy: loadingMore, disabled: loadingMore }}
              testID="post-detail-load-more-comments"
              style={styles.loadMore}
              disabled={loadingMore}
              onPress={() => loadMoreComments().catch(() => undefined)}
            >
              <Text style={styles.loadMoreText}>
                {loadingMore ? "Loading comments" : `Load more comments (${Math.max(0, commentTotal - loadedCommentCount)} left)`}
              </Text>
            </Pressable>
          ) : null
        }
        renderItem={({ item }) => (
          <CommentThread
            comment={item}
            currentUserId={currentUserId}
            expandedIds={expandedReplies}
            handlers={{
              onReply: handleReply,
              onToggleReplies: (comment) => setExpandedReplies((current) => toggleSetValue(current, comment.id)),
              onAuthorPress: handleCommentAuthorPress,
              onMentionPress: handleMentionPress
            }}
          />
        )}
      />
    </KeyboardAvoidingView>
  );
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
    marginTop: 10,
    textAlign: "center"
  },
  commentButton: {
    alignItems: "center",
    alignSelf: "flex-end",
    backgroundColor: colors.accent,
    borderRadius: 8,
    minWidth: 76,
    paddingHorizontal: 14,
    paddingVertical: 10
  },
  commentButtonDisabled: {
    opacity: 0.5
  },
  commentButtonText: {
    color: colors.background,
    fontSize: 13,
    fontWeight: "900"
  },
  commentComposer: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    gap: 10,
    marginBottom: 14,
    padding: 12
  },
  content: {
    padding: 16,
    paddingBottom: 32
  },
  empty: {
    padding: 16
  },
  emptyText: {
    color: colors.muted,
    textAlign: "center"
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    marginBottom: 10
  },
  errorTitle: {
    color: colors.text,
    fontSize: 20,
    fontWeight: "900"
  },
  input: {
    color: colors.text,
    fontSize: 15,
    lineHeight: 21,
    maxHeight: 130,
    minHeight: 44
  },
  keyboard: {
    backgroundColor: colors.background,
    flex: 1
  },
  list: {
    backgroundColor: colors.background,
    flex: 1
  },
  loadMore: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginTop: 4,
    paddingVertical: 12
  },
  loadMoreText: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "800"
  },
  offline: {
    color: colors.warning,
    fontSize: 13,
    marginBottom: 10
  },
  replyBanner: {
    alignItems: "center",
    backgroundColor: colors.background,
    borderRadius: 8,
    flexDirection: "row",
    gap: 12,
    justifyContent: "space-between",
    paddingHorizontal: 10,
    paddingVertical: 8
  },
  replyBannerCancel: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800"
  },
  replyBannerText: {
    color: colors.muted,
    flexShrink: 1,
    fontSize: 12,
    fontWeight: "700"
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900",
    marginBottom: 10
  }
});
