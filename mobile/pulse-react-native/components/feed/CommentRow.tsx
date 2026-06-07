import React from "react";
import { Image, Text, View } from "react-native";
import { PulseComment } from "../../services/feed/types";
import { colors, screenStyles } from "../theme";

export function CommentRow({ comment }: { comment: PulseComment }) {
  const isReply = Number(comment.parent_comment_id || 0) > 0;
  return (
    <View style={[screenStyles.card, { marginLeft: isReply ? 24 : 0, marginBottom: 8 }]}>
      <View style={{ flexDirection: "row", alignItems: "center", gap: 8 }}>
        {comment.author?.avatar_url ? <Image source={{ uri: comment.author.avatar_url }} style={{ width: 30, height: 30, borderRadius: 15 }} /> : <View style={{ width: 30, height: 30, borderRadius: 15, backgroundColor: colors.surfaceSoft }} />}
        <Text style={screenStyles.cardTitle}>{comment.author?.display_name || "PulseSoc member"}</Text>
      </View>
      <Text style={[screenStyles.muted, { marginTop: 8 }]}>{comment.body || ""}</Text>
    </View>
  );
}
