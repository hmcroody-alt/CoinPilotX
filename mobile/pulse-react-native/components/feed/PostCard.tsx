import React, { useMemo, useState } from "react";
import { Alert, Image, Text, TextInput, TouchableOpacity, View } from "react-native";
import { PulsePost } from "../../services/feed/types";
import { deletePost, editPost, reactToPost, repostPost, sharePost } from "../../services/feed/feedApi";
import { emitFeedHook } from "../../services/feed/events";
import { trackMobileEvent } from "../../services/analytics";
import { colors, screenStyles } from "../theme";
import { FeedMedia } from "./FeedMedia";
import { compactNumber, formatRelativeTime, initials } from "../../utils/format";

type PostCardProps = {
  post: PulsePost;
  onOpenPost: (post: PulsePost) => void;
  onOpenProfile: (post: PulsePost) => void;
  onDeleted: (postId: number) => void;
  onChanged: (post: PulsePost) => void;
};

export function PostCard({ post, onOpenPost, onOpenProfile, onDeleted, onChanged }: PostCardProps) {
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(post.body || "");
  const author = post.author || {};
  const reactionTotal = useMemo(() => Object.values(post.reaction_counts || {}).reduce((total, value) => total + Number(value || 0), 0) || Number(post.reactions_count || 0), [post]);
  const liked = !!post.viewer_reaction;
  const authorName = author.display_name || post.author_public_name || "PulseSoc creator";

  async function like() {
    setBusy(true);
    try {
      const result = await reactToPost(post.id, liked ? post.viewer_reaction || "fire" : "fire");
      onChanged({ ...post, viewer_reaction: result.removed ? "" : result.reaction_type, reaction_counts: result.reaction_counts, reactions_count: result.reactions_count });
      emitFeedHook("like", { post_id: post.id, removed: !!result.removed });
    } catch (error) {
      Alert.alert("Like failed", error instanceof Error ? error.message : "Could not update reaction.");
    } finally {
      setBusy(false);
    }
  }

  async function repost() {
    setBusy(true);
    try {
      await repostPost(post.id);
      emitFeedHook("repost", { post_id: post.id });
      Alert.alert("Reposted", "Reposted to PulseSoc.");
    } catch (error) {
      Alert.alert("Repost failed", error instanceof Error ? error.message : "Could not repost.");
    } finally {
      setBusy(false);
    }
  }

  async function saveEdit() {
    setBusy(true);
    try {
      await editPost(post.id, draft);
      onChanged({ ...post, body: draft });
      setEditing(false);
    } catch (error) {
      Alert.alert("Edit failed", error instanceof Error ? error.message : "Could not edit this post.");
    } finally {
      setBusy(false);
    }
  }

  async function remove() {
    Alert.alert("Delete post", "Delete this PulseSoc post?", [
      { text: "Cancel", style: "cancel" },
      {
        text: "Delete",
        style: "destructive",
        onPress: async () => {
          setBusy(true);
          try {
            await deletePost(post.id);
            onDeleted(post.id);
          } catch (error) {
            Alert.alert("Delete failed", error instanceof Error ? error.message : "Could not delete this post.");
          } finally {
            setBusy(false);
          }
        }
      }
    ]);
  }

  return (
    <View style={screenStyles.card}>
      <TouchableOpacity style={{ flexDirection: "row", gap: 10, alignItems: "center" }} onPress={() => onOpenProfile(post)}>
        {author.avatar_url ? (
          <Image source={{ uri: author.avatar_url }} style={{ width: 42, height: 42, borderRadius: 21, backgroundColor: colors.surfaceSoft }} />
        ) : (
          <View style={{ width: 42, height: 42, borderRadius: 21, backgroundColor: colors.surfaceSoft, alignItems: "center", justifyContent: "center" }}>
            <Text style={{ color: colors.accent, fontWeight: "900" }}>{initials(authorName)}</Text>
          </View>
        )}
        <View style={{ flex: 1 }}>
          <Text style={screenStyles.cardTitle}>{authorName}</Text>
          <Text style={screenStyles.muted}>@{author.public_player_id || post.author_public_player_id || "pulsesoc"} · {formatRelativeTime(post.created_at)}</Text>
        </View>
      </TouchableOpacity>

      {editing ? (
        <TextInput style={[screenStyles.input, { minHeight: 96, marginTop: 12, textAlignVertical: "top" }]} value={draft} onChangeText={setDraft} multiline placeholderTextColor="#7890a8" />
      ) : (
        <TouchableOpacity onPress={() => { trackMobileEvent("mobile_post_view", { post_id: post.id }); onOpenPost(post); }}>
          {post.title ? <Text style={[screenStyles.cardTitle, { marginTop: 12 }]}>{post.title}</Text> : null}
          {post.body ? <Text style={[screenStyles.subtitle, { marginTop: 10, marginBottom: 0 }]}>{post.body}</Text> : null}
        </TouchableOpacity>
      )}

      {post.repost?.original ? <RepostPreview original={post.repost.original} /> : null}
      <FeedMedia media={post.media || []} />

      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
        {(post.tags || []).slice(0, 4).map(tag => <Text key={tag} style={screenStyles.muted}>#{tag}</Text>)}
      </View>

      <View style={{ flexDirection: "row", flexWrap: "wrap", gap: 8, marginTop: 12 }}>
        <Action label={`${liked ? "Unlike" : "Like"} ${compactNumber(reactionTotal)}`} onPress={like} disabled={busy} />
        <Action label={`Comment ${compactNumber(post.comments_count || post.comment_count)}`} onPress={() => onOpenPost(post)} />
        <Action label="Repost" onPress={repost} disabled={busy} />
        <Action label="Share" onPress={() => sharePost(post)} />
        {post.can_delete && !editing ? <Action label="Edit" onPress={() => setEditing(true)} /> : null}
        {post.can_delete && !editing ? <Action label="Delete" onPress={remove} disabled={busy} danger /> : null}
        {editing ? <Action label="Save" onPress={saveEdit} disabled={busy} /> : null}
        {editing ? <Action label="Cancel" onPress={() => { setEditing(false); setDraft(post.body || ""); }} /> : null}
      </View>
    </View>
  );
}

function Action({ label, onPress, disabled, danger }: { label: string; onPress: () => void; disabled?: boolean; danger?: boolean }) {
  return (
    <TouchableOpacity onPress={onPress} disabled={disabled} style={[screenStyles.secondaryButton, { marginTop: 0, minHeight: 38, opacity: disabled ? 0.6 : 1, borderColor: danger ? colors.danger : colors.border }]}>
      <Text style={[screenStyles.secondaryButtonText, danger && { color: colors.danger }]}>{label}</Text>
    </TouchableOpacity>
  );
}

function RepostPreview({ original }: { original: PulsePost }) {
  return (
    <View style={[screenStyles.card, { marginTop: 12, marginBottom: 0, backgroundColor: colors.surfaceSoft }]}>
      <Text style={screenStyles.cardTitle}>{original.author?.display_name || "Reposted PulseSoc"}</Text>
      <Text style={screenStyles.muted}>{original.body || original.title || "Original post"}</Text>
    </View>
  );
}
