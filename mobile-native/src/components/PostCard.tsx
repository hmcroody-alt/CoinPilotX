import { useMemo, useState } from "react";
import { Image, Pressable, Share, StyleSheet, Text, View } from "react-native";
import { mediaDisplayUrl, mediaKind, PulsePost, pulsePostUrl } from "../api/feed";
import { mediaViewerItemFromPulseMedia, NativeMediaViewer } from "./NativeMediaViewer";
import { colors } from "../theme/colors";
import { compactPreview, formatShortTime } from "../utils/format";

const REACTIONS = ["fire", "smart", "bullish", "funny"];

type PostCardProps = {
  post: PulsePost;
  detail?: boolean;
  busy?: boolean;
  onOpen?: (post: PulsePost) => void;
  onReact?: (post: PulsePost, reactionType: string) => void;
  onSave?: (post: PulsePost) => void;
  onRepost?: (post: PulsePost) => void;
  onPromote?: (post: PulsePost) => void;
  onShare?: (post: PulsePost) => void;
  onAuthorPress?: (post: PulsePost) => void;
};

export function PostCard({ post, detail, busy, onOpen, onReact, onSave, onRepost, onPromote, onShare, onAuthorPress }: PostCardProps) {
  const author = post.author || {};
  const displayName = author.display_name || author.name || post.author_name || "PulseSoc";
  const handle = author.username || author.handle || post.author_username || "";
  const body = detail ? post.body : compactPreview(post.body, "");
  const commentCount = Number(post.comment_count || 0);
  const reactionTotal = Object.values(post.reaction_counts || {}).reduce((sum, count) => sum + Number(count || 0), 0);

  return (
    <Pressable
      style={({ pressed }) => [styles.card, pressed && onOpen ? styles.cardPressed : undefined]}
      onPress={() => onOpen?.(post)}
      disabled={!onOpen}
    >
      <Pressable
        style={styles.authorRow}
        onPress={(event) => {
          event.stopPropagation();
          onAuthorPress?.(post);
        }}
        disabled={!onAuthorPress}
      >
        {author.avatar_url ? <Image source={{ uri: author.avatar_url }} style={styles.avatar} /> : <View style={styles.avatarFallback} />}
        <View style={styles.authorText}>
          <Text style={styles.authorName} numberOfLines={1}>
            {displayName}
          </Text>
          <Text style={styles.meta} numberOfLines={1}>
            {handle ? `@${handle} · ` : ""}
            {formatShortTime(post.created_at)}
          </Text>
        </View>
      </Pressable>

      {post.title ? <Text style={styles.title}>{post.title}</Text> : null}
      {body ? <Text style={styles.body}>{body}</Text> : null}
      <MediaStrip post={post} />

      <View style={styles.countRow}>
        <Text style={styles.meta}>{reactionTotal} reactions</Text>
        <Text style={styles.meta}>{commentCount} comments</Text>
        <Text style={styles.meta}>{post.repost_count || 0} reposts</Text>
      </View>

      <View style={styles.actionRow}>
        {REACTIONS.map((reaction) => (
          <Pressable
            key={reaction}
            style={[styles.actionButton, post.viewer_reaction === reaction ? styles.actionButtonActive : undefined]}
            disabled={busy}
            onPress={() => onReact?.(post, reaction)}
          >
            <Text style={[styles.actionText, post.viewer_reaction === reaction ? styles.actionTextActive : undefined]}>
              {reactionLabel(reaction, post.reaction_counts?.[reaction])}
            </Text>
          </Pressable>
        ))}
      </View>

      <View style={styles.utilityRow}>
        <Pressable style={styles.utilityButton} disabled={busy} onPress={() => onSave?.(post)}>
          <Text style={styles.utilityText}>{post.saved ? "Saved" : "Save"}</Text>
        </Pressable>
        <Pressable style={styles.utilityButton} disabled={busy} onPress={() => onRepost?.(post)}>
          <Text style={styles.utilityText}>{post.reposted ? "Reposted" : "Repost"}</Text>
        </Pressable>
        {onPromote ? (
          <Pressable style={styles.utilityButton} disabled={busy} onPress={() => onPromote(post)}>
            <Text style={styles.utilityText}>Promote</Text>
          </Pressable>
        ) : null}
        <Pressable style={styles.utilityButton} onPress={() => (onShare ? onShare(post) : Share.share({ message: pulsePostUrl(post.id) }))}>
          <Text style={styles.utilityText}>Share</Text>
        </Pressable>
      </View>

      {!detail && post.preview_comments?.length ? (
        <View style={styles.previewComments}>
          {post.preview_comments.slice(0, 2).map((comment) => (
            <Text key={`${comment.id}-${comment.created_at || ""}`} style={styles.previewComment} numberOfLines={2}>
              <Text style={styles.previewAuthor}>{comment.author?.display_name || comment.author?.username || "PulseSoc"}: </Text>
              {comment.body}
            </Text>
          ))}
        </View>
      ) : null}
    </Pressable>
  );
}

function MediaStrip({ post }: { post: PulsePost }) {
  const [viewerIndex, setViewerIndex] = useState<number | null>(null);
  const author = post.author || {};
  const viewerItems = useMemo(
    () =>
      (post.media || []).map((media) =>
        mediaViewerItemFromPulseMedia(media, {
          title: post.title || "PulseSoc post media",
          subtitle: post.body || "PulseSoc media",
          author,
          sourceUrl: pulsePostUrl(post.id)
        })
      ),
    [author, post.body, post.id, post.media, post.title]
  );
  if (!post.media?.length) return null;
  return (
    <View style={styles.mediaWrap}>
      {post.media.slice(0, 4).map((media, index) => {
        const url = mediaDisplayUrl(media);
        const kind = mediaKind(media);
        if (kind === "image") {
          return (
            <Pressable key={`${url}-${index}`} onPress={(event) => {
              event.stopPropagation();
              setViewerIndex(index);
            }}>
              <Image source={{ uri: url }} style={styles.mediaImage} resizeMode="cover" />
            </Pressable>
          );
        }
        return (
          <Pressable key={`${url}-${index}`} style={styles.mediaFallback} onPress={(event) => {
            event.stopPropagation();
            setViewerIndex(index);
          }}>
            <Text style={styles.mediaFallbackTitle}>{kind === "video" ? "Video" : "Attachment"}</Text>
            <Text style={styles.mediaFallbackText}>Open viewer</Text>
          </Pressable>
        );
      })}
      <NativeMediaViewer
        visible={viewerIndex !== null}
        items={viewerItems}
        initialIndex={viewerIndex || 0}
        title="Post media"
        onClose={() => setViewerIndex(null)}
        onShare={() => Share.share({ message: pulsePostUrl(post.id) }).catch(() => undefined)}
      />
    </View>
  );
}

function reactionLabel(reaction: string, count?: number) {
  const label = reaction.replace(/_/g, " ");
  const value = Number(count || 0);
  return value ? `${label} ${value}` : label;
}

const styles = StyleSheet.create({
  actionButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    minHeight: 34,
    paddingHorizontal: 10,
    paddingVertical: 8
  },
  actionButtonActive: {
    backgroundColor: "rgba(37, 208, 167, 0.14)",
    borderColor: colors.accent
  },
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8,
    marginTop: 12
  },
  actionText: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800",
    textTransform: "capitalize"
  },
  actionTextActive: {
    color: colors.accent
  },
  authorName: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "800"
  },
  authorRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 10
  },
  authorText: {
    flex: 1
  },
  avatar: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 18,
    height: 36,
    width: 36
  },
  avatarFallback: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 18,
    borderWidth: 1,
    height: 36,
    width: 36
  },
  body: {
    color: colors.text,
    fontSize: 15,
    lineHeight: 22,
    marginTop: 10
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginBottom: 12,
    padding: 14
  },
  cardPressed: {
    borderColor: colors.accent
  },
  countRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 14,
    marginTop: 12
  },
  mediaFallback: {
    alignItems: "center",
    aspectRatio: 16 / 10,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    overflow: "hidden",
    width: "100%"
  },
  mediaFallbackText: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 4
  },
  mediaFallbackTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "800"
  },
  mediaImage: {
    aspectRatio: 16 / 10,
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    width: "100%"
  },
  mediaWrap: {
    gap: 8,
    marginTop: 12
  },
  meta: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  previewAuthor: {
    color: colors.text,
    fontWeight: "800"
  },
  previewComment: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  previewComments: {
    borderTopColor: colors.border,
    borderTopWidth: 1,
    gap: 6,
    marginTop: 12,
    paddingTop: 10
  },
  title: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900",
    lineHeight: 23,
    marginTop: 12
  },
  utilityButton: {
    minHeight: 34,
    paddingVertical: 8
  },
  utilityRow: {
    flexDirection: "row",
    gap: 18,
    marginTop: 8
  },
  utilityText: {
    color: colors.accentStrong,
    fontSize: 13,
    fontWeight: "800"
  }
});
