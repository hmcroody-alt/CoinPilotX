import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  RefreshControl,
  Share,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import {
  addPostComment,
  getPostDetail,
  loadCachedPostDetail,
  PulseComment,
  PulsePost,
  pulsePostUrl,
  reactToPost,
  repostPost,
  savePost
} from "../api/feed";
import { PostCard } from "../components/PostCard";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";

type Props = NativeStackScreenProps<RootStackParamList, "PostDetail">;

export function PostDetailScreen({ route, navigation }: Props) {
  const postId = route.params.postId;
  const [post, setPost] = useState<PulsePost | null>(null);
  const [comments, setComments] = useState<PulseComment[]>([]);
  const [commentBody, setCommentBody] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [posting, setPosting] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function load(mode: "initial" | "refresh" = "initial") {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const data = await getPostDetail(postId);
      setPost(data.post || null);
      setComments(data.comments || []);
    } catch (err) {
      const cached = await loadCachedPostDetail(postId);
      if (cached?.post) {
        setPost(cached.post);
        setComments(cached.comments || []);
        setOffline(true);
      } else {
        setError(err instanceof Error ? err.message : "Post unavailable.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [postId]);

  async function handleReact(nextPost: PulsePost, reactionType: string) {
    if (!post) return;
    setBusy(true);
    const previous = post.viewer_reaction || "";
    const counts = { ...(post.reaction_counts || {}) };
    if (previous) counts[previous] = Math.max(0, Number(counts[previous] || 0) - 1);
    counts[reactionType] = Number(counts[reactionType] || 0) + 1;
    setPost({ ...post, viewer_reaction: reactionType, reaction_counts: counts });
    try {
      const result = await reactToPost(nextPost.id, reactionType);
      setPost((current) =>
        current ? { ...current, viewer_reaction: result.viewer_reaction || reactionType, reaction_counts: result.reaction_counts || counts } : current
      );
    } catch {
      setPost((current) => (current ? { ...current, viewer_reaction: previous, reaction_counts: post.reaction_counts || {} } : current));
    } finally {
      setBusy(false);
    }
  }

  async function handleSave(nextPost: PulsePost) {
    if (!post) return;
    setBusy(true);
    setPost({ ...post, saved: !post.saved });
    try {
      const result = await savePost(nextPost.id);
      setPost((current) => (current ? { ...current, saved: Boolean(result.saved ?? result.is_saved ?? !post.saved) } : current));
    } catch {
      setPost((current) => (current ? { ...current, saved: post.saved } : current));
    } finally {
      setBusy(false);
    }
  }

  async function handleRepost(nextPost: PulsePost) {
    if (!post) return;
    setBusy(true);
    setPost({ ...post, reposted: true, repost_count: Number(post.repost_count || 0) + (post.reposted ? 0 : 1) });
    try {
      const result = await repostPost(nextPost.id);
      setPost((current) => (current ? { ...current, reposted: Boolean(result.reposted ?? result.is_reposted ?? true) } : current));
    } catch {
      setPost((current) => (current ? { ...current, reposted: post.reposted, repost_count: post.repost_count || 0 } : current));
    } finally {
      setBusy(false);
    }
  }

  async function handleComment() {
    const body = commentBody.trim();
    if (!body || posting) return;
    setPosting(true);
    setCommentBody("");
    try {
      const result = await addPostComment(postId, body);
      if (result.comment) {
        setComments((current) => [result.comment as PulseComment, ...current]);
        setPost((current) => (current ? { ...current, comment_count: Number(current.comment_count || 0) + 1 } : current));
      } else {
        await load("refresh");
      }
    } catch (err) {
      setCommentBody(body);
      setError(err instanceof Error ? err.message : "Comment failed.");
    } finally {
      setPosting(false);
    }
  }

  if (loading && !post) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading post</Text>
      </View>
    );
  }

  if (!post) {
    return (
      <View style={styles.center}>
        <Text style={styles.errorTitle}>Post unavailable</Text>
        <Text style={styles.centerText}>{error || "PulseSoc could not load this post."}</Text>
      </View>
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
              onShare={(item) => Share.share({ message: pulsePostUrl(item.id) }).catch(() => undefined)}
              onAuthorPress={(item) => {
                const key = item.author?.public_player_id || item.author?.username || "";
                if (key) navigation.navigate("ProfileDetail", { profileKey: key, title: item.author?.display_name || "Profile" });
              }}
            />
            <View style={styles.commentComposer}>
              <TextInput
                style={styles.input}
                value={commentBody}
                onChangeText={setCommentBody}
                placeholder="Add a comment"
                placeholderTextColor={colors.muted}
                multiline
              />
              <Pressable style={[styles.commentButton, (!commentBody.trim() || posting) && styles.commentButtonDisabled]} onPress={handleComment} disabled={!commentBody.trim() || posting}>
                <Text style={styles.commentButtonText}>{posting ? "Sending" : "Post"}</Text>
              </Pressable>
            </View>
            <Text style={styles.sectionTitle}>Comments</Text>
          </View>
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyText}>No comments yet.</Text>
          </View>
        }
        renderItem={({ item }) => <CommentRow comment={item} />}
      />
    </KeyboardAvoidingView>
  );
}

function CommentRow({ comment }: { comment: PulseComment }) {
  const author = comment.author || comment.user || {};
  return (
    <View style={styles.comment}>
      <Text style={styles.commentAuthor}>{author.display_name || author.username || "PulseSoc"}</Text>
      <Text style={styles.commentBody}>{comment.body}</Text>
      <Text style={styles.commentMeta}>{formatShortTime(comment.created_at)}</Text>
    </View>
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
  comment: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginBottom: 10,
    padding: 12
  },
  commentAuthor: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  commentBody: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 21,
    marginTop: 5
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
  commentMeta: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 7
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
  offline: {
    color: colors.warning,
    fontSize: 13,
    marginBottom: 10
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900",
    marginBottom: 10
  }
});
