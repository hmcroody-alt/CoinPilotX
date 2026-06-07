import React, { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, FlatList, RefreshControl, Text, View } from "react-native";
import { pulseApi } from "../services/apiClient";
import { screenStyles, colors } from "./theme";

type ApiPreviewProps = {
  endpoint: string;
  listKeys: string[];
  emptyLabel: string;
};

export function ApiPreview({ endpoint, listKeys, emptyLabel }: ApiPreviewProps) {
  const [items, setItems] = useState<unknown[]>([]);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setError("");
    const data = await pulseApi<Record<string, unknown>>(endpoint);
    const list = pickList(data, listKeys);
    setItems(list);
  }, [endpoint, listKeys]);

  useEffect(() => {
    let active = true;
    load()
      .catch(errorValue => active && setError(readError(errorValue)))
      .finally(() => active && setLoading(false));
    return () => {
      active = false;
    };
  }, [load]);

  async function refresh() {
    setRefreshing(true);
    load()
      .catch(errorValue => setError(readError(errorValue)))
      .finally(() => setRefreshing(false));
  }

  if (loading) {
    return (
      <View style={screenStyles.card}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  if (error) {
    return (
      <View style={screenStyles.card}>
        <Text style={screenStyles.error}>{error}</Text>
      </View>
    );
  }

  return (
    <FlatList
      scrollEnabled={false}
      data={items}
      keyExtractor={(item, index) => itemKey(item, index)}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={refresh} tintColor={colors.accent} />}
      ListEmptyComponent={<Text style={screenStyles.muted}>{emptyLabel}</Text>}
      renderItem={({ item }) => <PreviewCard item={item} />}
    />
  );
}

function PreviewCard({ item }: { item: unknown }) {
  const record = isRecord(item) ? item : {};
  const title = String(record.title || record.body || record.message || record.name || record.username || "PulseSoc item");
  const subtitle = String(record.description || record.preview_text || record.created_at || record.handle || "");
  return (
    <View style={screenStyles.card}>
      <Text style={screenStyles.cardTitle}>{title}</Text>
      {subtitle ? <Text style={screenStyles.muted}>{subtitle}</Text> : null}
    </View>
  );
}

function pickList(data: Record<string, unknown>, keys: string[]) {
  for (const key of keys) {
    const value = data[key];
    if (Array.isArray(value)) return value;
  }
  if (Array.isArray(data.items)) return data.items;
  return [];
}

function itemKey(item: unknown, index: number) {
  if (!isRecord(item)) return String(index);
  return String(item.id || item.post_id || item.reel_id || item.video_id || item.notification_id || item.conversation_id || index);
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function readError(error: unknown) {
  return error instanceof Error ? error.message : "PulseSoc API request failed.";
}
