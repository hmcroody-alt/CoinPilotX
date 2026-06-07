import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, FlatList, RefreshControl, Text, View } from "react-native";
import { pulseApi } from "../api/client";
import { colors, screenStyles } from "../styles/theme";

type ApiListScreenProps = {
  title: string;
  endpoint: string;
  listKey: string;
  empty?: string;
};

type ItemRecord = Record<string, unknown>;

export function ApiListScreen({ title, endpoint, listKey, empty = "Nothing here yet." }: ApiListScreenProps) {
  const [items, setItems] = useState<ItemRecord[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    const data = await pulseApi<Record<string, unknown>>(endpoint);
    const value = data[listKey] || data.items || data.posts || data.videos || data.notifications || [];
    setItems(Array.isArray(value) ? value.filter(isRecord) : []);
  }, [endpoint, listKey]);

  useEffect(() => {
    load()
      .catch(errorValue => setError(errorValue instanceof Error ? errorValue.message : "PulseSoc could not load this screen."))
      .finally(() => setLoading(false));
  }, [load]);

  async function refresh() {
    setRefreshing(true);
    load()
      .catch(errorValue => setError(errorValue instanceof Error ? errorValue.message : "PulseSoc could not refresh."))
      .finally(() => setRefreshing(false));
  }

  if (loading) {
    return (
      <View style={screenStyles.centered}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <FlatList
      style={screenStyles.screen}
      data={items}
      keyExtractor={(item, index) => String(item.id || item.post_id || item.video_id || item.reel_id || item.notification_id || item.conversation_id || index)}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.accent} />}
      ListHeaderComponent={
        <View>
          <Text style={screenStyles.title}>{title}</Text>
          {error ? <Text style={screenStyles.error}>{error}</Text> : null}
        </View>
      }
      ListEmptyComponent={<Text style={screenStyles.muted}>{empty}</Text>}
      renderItem={({ item }) => <PulseItemCard item={item} />}
    />
  );
}

function PulseItemCard({ item }: { item: ItemRecord }) {
  const title = String(item.title || item.body || item.message || item.name || item.username || item.display_name || "PulseSoc item");
  const subtitle = String(item.description || item.preview_text || item.created_at || item.author_name || item.handle || item.type || "");
  return (
    <View style={screenStyles.card}>
      <Text style={screenStyles.cardTitle}>{title}</Text>
      {subtitle ? <Text style={screenStyles.muted}>{subtitle}</Text> : null}
    </View>
  );
}

function isRecord(value: unknown): value is ItemRecord {
  return typeof value === "object" && value !== null;
}
