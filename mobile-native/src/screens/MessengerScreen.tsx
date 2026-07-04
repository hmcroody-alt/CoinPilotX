import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useNavigation } from "@react-navigation/native";
import { useEffect, useState } from "react";
import { ActivityIndicator, FlatList, Pressable, RefreshControl, StyleSheet, Text, TextInput, View } from "react-native";
import { loadCachedConversations, listConversations, MessengerConversation, searchMessenger } from "../api/messenger";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { compactPreview, formatShortTime } from "../utils/format";

export function MessengerScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [conversations, setConversations] = useState<MessengerConversation[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  async function load({ refresh = false } = {}) {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const data = query.trim() ? (await searchMessenger(query.trim())).conversations : await listConversations();
      setConversations(data);
    } catch (loadError) {
      const cached = await loadCachedConversations();
      setConversations(cached);
      setError(loadError instanceof Error ? loadError.message : "Messenger could not load.");
    } finally {
      setRefreshing(false);
      setLoading(false);
    }
  }

  useEffect(() => {
    loadCachedConversations().then((cached) => {
      if (cached.length) setConversations(cached);
    });
    load().catch(() => undefined);
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => {
      load().catch(() => undefined);
    }, 350);
    return () => clearTimeout(timer);
  }, [query]);

  return (
    <View style={styles.root}>
      <View style={styles.header}>
        <Text style={styles.screenTitle}>Messenger</Text>
        <TextInput
          autoCapitalize="none"
          placeholder="Search messages"
          placeholderTextColor={colors.muted}
          style={styles.search}
          value={query}
          onChangeText={setQuery}
        />
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </View>
      {loading && conversations.length === 0 ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.accent} />
        </View>
      ) : (
        <FlatList
          data={conversations}
          keyExtractor={(item) => String(item.id)}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load({ refresh: true })} />}
          ListEmptyComponent={<Text style={styles.empty}>{query ? "No matching conversations." : "No conversations loaded yet."}</Text>}
          renderItem={({ item }) => (
            <Pressable
              style={({ pressed }) => [styles.row, pressed && styles.rowPressed]}
              onPress={() => navigation.navigate("Chat", { conversationId: item.id, title: item.title || item.name })}
            >
              <View style={styles.avatar}>
                <Text style={styles.avatarText}>{(item.title || item.name || "P").slice(0, 1).toUpperCase()}</Text>
              </View>
              <View style={styles.rowBody}>
                <View style={styles.rowTop}>
                  <Text style={styles.title} numberOfLines={1}>{item.title || item.name || `Conversation ${item.id}`}</Text>
                  <Text style={styles.time}>{formatShortTime(item.last_activity_at || item.updated_at)}</Text>
                </View>
                <Text style={styles.muted} numberOfLines={1}>
                  {compactPreview(item.latest_message || item.last_message_preview, "Open chat")}
                </Text>
              </View>
              {item.other_public_player_id || item.public_player_id ? (
                <Pressable
                  style={styles.profileButton}
                  onPress={() =>
                    navigation.navigate("ProfileDetail", {
                      profileKey: item.other_public_player_id || item.public_player_id,
                      title: item.title || "Profile"
                    })
                  }
                >
                  <Text style={styles.profileButtonText}>Profile</Text>
                </Pressable>
              ) : null}
              {Number(item.unread_count || 0) > 0 ? (
                <View style={styles.badge}>
                  <Text style={styles.badgeText}>{item.unread_count}</Text>
                </View>
              ) : null}
            </Pressable>
          )}
        />
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  header: {
    backgroundColor: colors.background,
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 12,
    padding: 16
  },
  screenTitle: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  },
  search: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: colors.text,
    minHeight: 44,
    paddingHorizontal: 12
  },
  list: {
    padding: 12,
    gap: 8
  },
  center: {
    alignItems: "center",
    flex: 1,
    justifyContent: "center"
  },
  row: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 12,
    minHeight: 74,
    padding: 12
  },
  rowPressed: {
    backgroundColor: colors.surfaceRaised
  },
  avatar: {
    alignItems: "center",
    backgroundColor: colors.accentStrong,
    borderRadius: 22,
    height: 44,
    justifyContent: "center",
    width: 44
  },
  avatarText: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  rowBody: {
    flex: 1,
    gap: 4
  },
  rowTop: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8
  },
  title: {
    color: colors.text,
    flex: 1,
    fontSize: 17,
    fontWeight: "800"
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  profileButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 8
  },
  profileButtonText: {
    color: colors.accentStrong,
    fontSize: 12,
    fontWeight: "900"
  },
  time: {
    color: colors.muted,
    fontSize: 12
  },
  badge: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 11,
    minWidth: 22,
    paddingHorizontal: 6,
    paddingVertical: 3
  },
  badgeText: {
    color: "#08110f",
    fontSize: 12,
    fontWeight: "900"
  },
  empty: {
    color: colors.muted,
    padding: 20,
    textAlign: "center"
  },
  error: {
    color: colors.warning,
    fontSize: 13
  }
});
