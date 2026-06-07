import React, { useCallback, useEffect, useState } from "react";
import { FlatList, Image, Text, TouchableOpacity, View } from "react-native";
import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { PostCard } from "../../../components/feed/PostCard";
import { colors, screenStyles } from "../../../components/theme";
import { MainStackParamList } from "../../../navigation/types";
import { loadFeed } from "../../../services/feed/feedApi";
import { PulsePost } from "../../../services/feed/types";
import { trackMobileEvent } from "../../../services/analytics";

type Props = NativeStackScreenProps<MainStackParamList, "ProfileDetail">;

export function ProfileDetailScreen({ navigation, route }: Props) {
  const [posts, setPosts] = useState<PulsePost[]>([]);
  const [message, setMessage] = useState("");
  const username = route.params.username;

  const load = useCallback(async () => {
    if (!username) {
      setMessage("Profile id is not available for this post.");
      return;
    }
    try {
      const result = await loadFeed(0, 20, username);
      setPosts(result.posts || []);
      trackMobileEvent("mobile_profile_view", { username });
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Profile could not load.");
    }
  }, [username]);

  useEffect(() => {
    load();
  }, [load]);

  const firstAuthor = posts[0]?.author;
  const displayName = route.params.displayName || firstAuthor?.display_name || username || "PulseSoc profile";
  const avatarUrl = route.params.avatarUrl || firstAuthor?.avatar_url || "";

  return (
    <View style={screenStyles.screen}>
      <FlatList
        data={posts}
        keyExtractor={item => String(item.id)}
        ListHeaderComponent={(
          <View>
            <TouchableOpacity style={screenStyles.secondaryButton} onPress={() => navigation.goBack()}>
              <Text style={screenStyles.secondaryButtonText}>Back</Text>
            </TouchableOpacity>
            <View style={screenStyles.card}>
              {avatarUrl ? <Image source={{ uri: avatarUrl }} style={{ width: 84, height: 84, borderRadius: 42, backgroundColor: colors.surfaceSoft }} /> : <View style={{ width: 84, height: 84, borderRadius: 42, backgroundColor: colors.surfaceSoft }} />}
              <Text style={[screenStyles.title, { marginTop: 12 }]}>{displayName}</Text>
              <Text style={screenStyles.subtitle}>@{username || "pulse"}</Text>
              <Text style={screenStyles.muted}>Posts: {posts.length}</Text>
              <Text style={screenStyles.muted}>Follower and following counts need a public profile JSON endpoint; this screen uses the existing profile feed API.</Text>
            </View>
            {message ? <Text style={screenStyles.error}>{message}</Text> : null}
          </View>
        )}
        renderItem={({ item }) => (
          <PostCard
            post={item}
            onOpenPost={post => navigation.navigate("PostDetail", { postId: post.id })}
            onOpenProfile={() => undefined}
            onDeleted={postId => setPosts(current => current.filter(post => post.id !== postId))}
            onChanged={next => setPosts(current => current.map(post => post.id === next.id ? next : post))}
          />
        )}
        ListEmptyComponent={<Text style={screenStyles.muted}>No recent public posts.</Text>}
        refreshing={false}
        onRefresh={load}
      />
    </View>
  );
}
