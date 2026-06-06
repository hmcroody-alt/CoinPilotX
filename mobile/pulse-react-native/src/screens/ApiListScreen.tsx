import React, { useEffect, useState } from "react";
import { ActivityIndicator, FlatList, Text, View } from "react-native";
import { pulseApi } from "../api/client";
import { styles } from "../styles";

type Props = {
  title: string;
  endpoint: string;
  listKey: string;
};

export function ApiListScreen({ title, endpoint, listKey }: Props) {
  const [items, setItems] = useState<any[]>([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    pulseApi<any>(endpoint)
      .then(data => {
        if (!alive) return;
        const value = data[listKey] || data.items || data.posts || data.videos || data.notifications || [];
        setItems(Array.isArray(value) ? value : []);
      })
      .catch(err => alive && setError(err.message))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [endpoint, listKey]);

  if (loading) return <View style={styles.center}><ActivityIndicator /><Text style={styles.muted}>Loading {title}</Text></View>;
  if (error) return <View style={styles.center}><Text style={styles.error}>{error}</Text></View>;

  return (
    <View style={styles.screen}>
      <Text style={styles.title}>{title}</Text>
      <FlatList
        data={items}
        keyExtractor={(item, index) => String(item.id || item.post_id || item.video_id || index)}
        renderItem={({ item }) => (
          <View style={styles.card}>
            <Text style={styles.cardTitle}>{item.title || item.body || item.message || item.owner_name || "Pulse item"}</Text>
            <Text style={styles.muted}>{item.description || item.preview_text || item.created_at || ""}</Text>
          </View>
        )}
      />
    </View>
  );
}
