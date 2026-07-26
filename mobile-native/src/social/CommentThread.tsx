import { Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import type { PulseComment } from "../api/feed";
import { ContentTranslation } from "../components/ContentTranslation";
import { colors } from "../theme/colors";
import { formatShortTime } from "../utils/format";
import { segmentMentions } from "./mentions";

// The recursive comment renderer, promoted out of ReelsScreen.tsx:736 so every
// content type gets the same thread UI instead of Reels getting nesting and feed
// posts getting a flat list. It is deliberately presentational: it owns no
// network calls and no state beyond what it is handed, so a screen can drive it
// with optimistic updates from commentTree.ts and the same component serves
// posts, reels and statuses.

/** Deepest visual indentation. Beyond this, replies still render — they just stop indenting. */
export const COMMENT_MAX_INDENT_DEPTH = 4;

export type CommentEditSession = {
  comment: PulseComment | null;
  body: string;
  busy: boolean;
  onChangeBody: (value: string) => void;
  onSubmit: () => void;
  onCancel: () => void;
};

export type CommentThreadHandlers = {
  onReply?: (comment: PulseComment) => void;
  onReact?: (comment: PulseComment) => void;
  onBeginEdit?: (comment: PulseComment) => void;
  onDelete?: (comment: PulseComment) => void;
  onReport?: (comment: PulseComment) => void;
  onToggleReplies?: (comment: PulseComment) => void;
  onAuthorPress?: (comment: PulseComment) => void;
  onMentionPress?: (username: string) => void;
};

export type CommentThreadProps = {
  comment: PulseComment;
  currentUserId?: number;
  /** Ids whose replies are currently shown. Absent id means collapsed. */
  expandedIds?: Set<number>;
  /** Ids with a request in flight, from useSocialActionGuard. */
  busyIds?: Set<number>;
  edit?: CommentEditSession;
  handlers?: CommentThreadHandlers;
  depth?: number;
};

/**
 * Whether to render an Edit control.
 *
 * The server is authoritative and now says so explicitly: `list_comments`
 * returns viewer-scoped `can_edit`/`can_delete`
 * (services/pulse_feed_engine.py:1426). Reels' original inline version used
 * `can_edit || user_id === currentUserId`, which is an OR — so a comment the
 * server had already refused would still render an Edit button, and the user
 * discovered the refusal only after typing. When the server has stated a verdict
 * we honor it; the local ownership comparison is a fallback for the older
 * endpoints that omit the field entirely.
 */
export function viewerMayEditComment(comment: PulseComment, currentUserId = 0): boolean {
  if (comment.can_edit !== undefined) return Boolean(comment.can_edit);
  return commentAuthorId(comment) > 0 && commentAuthorId(comment) === Number(currentUserId || 0);
}

/** Same precedence rule as `viewerMayEditComment`, for the Delete control. */
export function viewerMayDeleteComment(comment: PulseComment, currentUserId = 0): boolean {
  if (comment.can_delete !== undefined) return Boolean(comment.can_delete);
  if (comment.can_moderate) return true;
  return commentAuthorId(comment) > 0 && commentAuthorId(comment) === Number(currentUserId || 0);
}

export function commentAuthorId(comment: PulseComment): number {
  return Number(comment.user_id || comment.author?.user_id || comment.author?.id || 0);
}

export function commentAuthorLabel(comment: PulseComment): string {
  const author = comment.author || comment.user || {};
  return author.display_name || author.name || author.username || "PulseSoc";
}

/** "View 1 reply" / "View 3 replies" — pluralization is a visible detail worth pinning. */
export function replyToggleLabel(count: number, expanded: boolean): string {
  if (expanded) return "Hide replies";
  return `View ${count} ${count === 1 ? "reply" : "replies"}`;
}

export function CommentThread({
  comment,
  currentUserId = 0,
  expandedIds,
  busyIds,
  edit,
  handlers = {},
  depth = 0
}: CommentThreadProps) {
  const replies = comment.replies || [];
  const expanded = expandedIds ? expandedIds.has(comment.id) : false;
  const busy = busyIds ? busyIds.has(comment.id) : false;
  const editing = Boolean(edit?.comment && edit.comment.id === comment.id);
  const mayEdit = viewerMayEditComment(comment, currentUserId);
  const mayDelete = viewerMayDeleteComment(comment, currentUserId);
  const indent = Math.min(depth, COMMENT_MAX_INDENT_DEPTH);
  const authorLabel = commentAuthorLabel(comment);

  return (
    <View
      testID={`comment-${comment.id}`}
      style={[styles.comment, indent > 0 && styles.reply, indent > 0 && { marginLeft: indent * 12 }]}
    >
      <Pressable
        accessibilityRole={handlers.onAuthorPress ? "button" : "text"}
        accessibilityLabel={handlers.onAuthorPress ? `Open ${authorLabel}'s profile` : undefined}
        disabled={!handlers.onAuthorPress}
        onPress={() => handlers.onAuthorPress?.(comment)}
      >
        <Text style={styles.author}>{authorLabel}</Text>
      </Pressable>

      {editing && edit ? (
        <View style={styles.editComposer}>
          <TextInput
            accessibilityLabel="Edit comment"
            testID={`comment-edit-input-${comment.id}`}
            style={styles.editInput}
            value={edit.body}
            onChangeText={edit.onChangeBody}
            multiline
          />
          <View style={styles.actions}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Save comment edit"
              accessibilityState={{ disabled: !edit.body.trim() || edit.busy, busy: edit.busy }}
              testID={`comment-edit-save-${comment.id}`}
              disabled={!edit.body.trim() || edit.busy}
              onPress={edit.onSubmit}
            >
              <Text style={styles.action}>{edit.busy ? "Saving" : "Save edit"}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityLabel="Cancel comment edit" onPress={edit.onCancel}>
              <Text style={styles.action}>Cancel</Text>
            </Pressable>
          </View>
        </View>
      ) : (
        <View>
          <CommentBody comment={comment} depth={depth} onMentionPress={handlers.onMentionPress} />
          {comment.edited_at ? <Text style={styles.edited}>Edited</Text> : null}
        </View>
      )}

      <View style={styles.actions}>
        <Text style={styles.time}>{formatShortTime(comment.created_at)}</Text>
        {handlers.onReply ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`Reply to ${authorLabel}`}
            testID={`comment-reply-${comment.id}`}
            onPress={() => handlers.onReply?.(comment)}
          >
            <Text style={styles.action}>Reply</Text>
          </Pressable>
        ) : null}
        {handlers.onReact ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={comment.viewer_reaction ? `Remove reaction from ${authorLabel}'s comment` : `Like ${authorLabel}'s comment`}
            accessibilityState={{ selected: Boolean(comment.viewer_reaction), busy, disabled: busy }}
            testID={`comment-react-${comment.id}`}
            disabled={busy}
            onPress={() => handlers.onReact?.(comment)}
          >
            <Text style={[styles.action, comment.viewer_reaction ? styles.actionActive : null]}>
              {comment.viewer_reaction ? "Liked" : "Like"}
              {commentReactionTotal(comment) ? ` ${commentReactionTotal(comment)}` : ""}
            </Text>
          </Pressable>
        ) : null}
        {mayEdit && handlers.onBeginEdit ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Edit comment"
            testID={`comment-edit-${comment.id}`}
            onPress={() => handlers.onBeginEdit?.(comment)}
          >
            <Text style={styles.action}>Edit</Text>
          </Pressable>
        ) : null}
        {mayDelete && handlers.onDelete ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Delete comment"
            accessibilityState={{ busy, disabled: busy }}
            testID={`comment-delete-${comment.id}`}
            disabled={busy}
            onPress={() => handlers.onDelete?.(comment)}
          >
            <Text style={styles.danger}>Delete</Text>
          </Pressable>
        ) : null}
        {!mayDelete && handlers.onReport ? (
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Report comment"
            testID={`comment-report-${comment.id}`}
            onPress={() => handlers.onReport?.(comment)}
          >
            <Text style={styles.action}>Report</Text>
          </Pressable>
        ) : null}
      </View>

      {replies.length ? (
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ expanded }}
          testID={`comment-toggle-replies-${comment.id}`}
          onPress={() => handlers.onToggleReplies?.(comment)}
        >
          <Text style={styles.replyToggle}>{replyToggleLabel(replies.length, expanded)}</Text>
        </Pressable>
      ) : null}

      {expanded
        ? replies.map((reply) => (
          <CommentThread
            key={reply.id}
            comment={reply}
            currentUserId={currentUserId}
            expandedIds={expandedIds}
            busyIds={busyIds}
            edit={edit}
            handlers={handlers}
            depth={depth + 1}
          />
        ))
        : null}
    </View>
  );
}

export function commentReactionTotal(comment: PulseComment): number {
  const counts = comment.reaction_counts || {};
  const keys = Object.keys(counts);
  if (keys.length) return keys.reduce((total, key) => total + Number(counts[key] || 0), 0);
  return Number(comment.like_count || 0);
}

/**
 * Comment body with @mentions rendered as distinct, tappable spans.
 *
 * When no mention handler is supplied the body is passed to ContentTranslation
 * whole, which keeps the existing translate-a-comment behavior intact. Splitting
 * a translated string into spans would break translation, so mentions and
 * translation are deliberately exclusive rather than layered.
 */
function CommentBody({ comment, depth, onMentionPress }: { comment: PulseComment; depth: number; onMentionPress?: (username: string) => void }) {
  const contentType = depth > 0 || comment.parent_comment_id ? "reply" : "comment";
  if (!onMentionPress) {
    return (
      <ContentTranslation
        contentType={contentType}
        contentRef={comment.id || comment.comment_id}
        text={comment.body}
        textStyle={styles.body}
      />
    );
  }
  const segments = segmentMentions(comment.body);
  return (
    <Text style={styles.body}>
      {segments.map((segment, index) => (segment.username ? (
        <Text
          key={`mention-${index}`}
          accessibilityRole="link"
          accessibilityLabel={`Open @${segment.username} profile`}
          testID={`comment-mention-${comment.id}-${segment.username}`}
          style={styles.mention}
          onPress={() => onMentionPress(segment.username || "")}
        >
          {segment.text}
        </Text>
      ) : (
        <Text key={`text-${index}`}>{segment.text}</Text>
      )))}
    </Text>
  );
}

const styles = StyleSheet.create({
  action: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800"
  },
  actionActive: {
    color: colors.text
  },
  actions: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 14,
    marginTop: 8
  },
  author: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "900"
  },
  body: {
    color: colors.text,
    fontSize: 14,
    lineHeight: 21,
    marginTop: 5
  },
  comment: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginBottom: 10,
    padding: 12
  },
  danger: {
    color: colors.danger,
    fontSize: 12,
    fontWeight: "800"
  },
  edited: {
    color: colors.muted,
    fontSize: 11,
    marginTop: 4
  },
  editComposer: {
    gap: 8,
    marginTop: 6
  },
  editInput: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    fontSize: 14,
    minHeight: 60,
    padding: 10,
    textAlignVertical: "top"
  },
  mention: {
    color: colors.accent,
    fontWeight: "800"
  },
  reply: {
    borderLeftColor: colors.accent,
    borderLeftWidth: 2
  },
  replyToggle: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "800",
    marginTop: 8
  },
  time: {
    color: colors.muted,
    fontSize: 11
  }
});
