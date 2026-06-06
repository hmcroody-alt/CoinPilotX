import React, { useEffect, useState } from "react";
import { FlatList, Text, TextInput, TouchableOpacity, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { CommentRow } from "../../../components/feed/CommentRow";
import { PostCard } from "../../../components/feed/PostCard";
import { colors, screenStyles } from "../../../components/theme";
import { MainStackParamList } from "../../../navigation/types";
import { addComment, loadPost } from "../../../services/feed/feedApi";
import { emitFeedHook } from "../../../services/feed/events";
import { PulseComment, PulsePost } from "../../../services/feed/types";
import { trackMobileEvent } from "../../../services/analytics";

type Props = NativeStackScreenProps<MainStackParamList, "PostDetail">;

export function PostDetailScreen({ navigation, route }: Props) {
  const [post, setPost] = useState<PulsePost | null>(null);
  const [comments, setComments] = useState<PulseComment[]>([]);
  const [body, setBody] = useState("");
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState("");

  useEffect(() => {
    let active = true;
    loadPost(route.params.postId)
      .then(result => {
        if (!active) return;
        setPost(result.post);
        setComments(result.comments || []);
        trackMobileEvent("mobile_post_view", { post_id: route.params.postId });
      })
      .catch(error => active && setMessage(error instanceof Error ? error.message : "Post could not load."))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [route.params.postId]);

  async function submit() {
    if (!body.trim()) return;
    try {
      const result = await addComment(route.params.postId, body);
      if (result.comment) setComments(current => [...current, result.comment as PulseComment]);
      if (post) setPost({ ...post, comments_count: result.comments_count || (post.comments_count || 0) + 1 });
      setBody("");
      emitFeedHook("comment", { post_id: route.params.postId });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not comment.");
    }
  }

  if (loading) {
    return <View style={screenStyles.screen}><Text style={screenStyles.muted}>Loading post...</Text></View>;
  }

  if (!post) {
    return <View style={screenStyles.screen}><Text style={screenStyles.error}>{message || "Post not found."}</Text></View>;
  }

  return (
    <View style={screenStyles.screen}>
      <FlatList
        data={comments}
        keyExtractor={item => String(item.id)}
        ListHeaderComponent={(
          <>
            <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.goBack()}>
              <Text style={screenStyles.secondaryButtonText}>Back</Text>
            </TouchableOpacity>
            <PostCard
              post={post}
              onOpenPost={() => undefined}
              onOpenProfile={item => navigation.navigate("ProfileDetail", { username: item.author?.public_player_id || item.author_public_player_id || "", displayName: item.author?.display_name, avatarUrl: item.author?.avatar_url })}
              onDeleted={() => navigation.goBack()}
              onChanged={setPost}
            />
            <Text style={screenStyles.title}>Comments</Text>
          </>
        )}
        renderItem={({ item }) => <CommentRow comment={item} />}
        ListEmptyComponent={<Text style={screenStyles.muted}>No comments yet.</Text>}
        ListFooterComponent={(
          <View style={{ paddingBottom: 28 }}>
            <TextInput style={screenStyles.input} value={body} onChangeText={setBody} placeholder="Write a comment..." placeholderTextColor="#7890a8" />
            <TouchableOpacity style={screenStyles.button} onPress={submit}>
              <Text style={screenStyles.buttonText}>Comment</Text>
            </TouchableOpacity>
            {message ? <Text style={screenStyles.error}>{message}</Text> : null}
          </View>
        )}
      />
    </View>
  );
}
