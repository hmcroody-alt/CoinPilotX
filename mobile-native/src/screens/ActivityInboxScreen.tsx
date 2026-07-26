import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { RouteProp, useNavigation, useRoute } from "@react-navigation/native";
import { useCallback, useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Alert, AppState, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import {
  ActivityCategory,
  ActivityInboxItem,
  activityCategories,
  activityCategoryLabel,
  deleteActivityItem,
  filterActivityItems,
  loadActivityInboxState,
  markActivityCategoryRead,
  markActivityItemRead,
  resolveActivityItemTarget
} from "../api/activity";
import { registerSyncInvalidation } from "../core/eventSync";
import { useBottomNavSurface } from "../navigation/BottomNavVisibility";
import { routeNotificationTarget } from "../navigation/notificationRouting";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { compactPreview, formatShortTime } from "../utils/format";

function unreadCountsByCategory(items: ActivityInboxItem[]) {
  return items.reduce<Record<string, number>>((counts, item) => {
    if (!item.unread) return counts;
    counts[item.category] = Number(counts[item.category] || 0) + 1;
    return counts;
  }, {});
}

export function ActivityInboxScreen() {
  // Bottom-dock coupling: drives hide-on-scroll-down / reveal-on-scroll-up and
  // reserves the matching clearance so the last row never sits under the dock.
  const dock = useBottomNavSurface();
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const route = useRoute<RouteProp<RootStackParamList, "ActivityInbox">>();
  const [items, setItems] = useState<ActivityInboxItem[]>([]);
  const [unreadTotal, setUnreadTotal] = useState(0);
  const [category, setCategory] = useState<ActivityCategory>(normalizeRouteCategory(route.params?.category));
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");

  const visibleItems = useMemo(() => filterActivityItems(items, category), [category, items]);
  const categoryCounts = useMemo(() => unreadCountsByCategory(items), [items]);

  const load = useCallback(async ({ refresh = false } = {}) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const state = await loadActivityInboxState({ limit: 100 });
      setItems(state.items);
      setUnreadTotal(state.unreadTotal);
      setOffline(Boolean(state.loadedFromCache));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Activity could not load.");
    } finally {
      setRefreshing(false);
      setLoading(false);
    }
  }, []);

  const openItem = useCallback(async (item: ActivityInboxItem) => {
    try {
      const target = await resolveActivityItemTarget(item);
      if (item.unread) {
        setItems((current) => current.map((entry) => (entry.id === item.id ? { ...entry, unread: false } : entry)));
      }
      await routeNotificationTarget(target);
    } catch (openError) {
      Alert.alert("Activity unavailable", openError instanceof Error ? openError.message : "That activity could not be opened.");
    }
  }, []);

  const markRead = useCallback(async (item: ActivityInboxItem) => {
    if (!item.notificationId) {
      setItems((current) => current.map((entry) => (entry.id === item.id ? { ...entry, unread: false } : entry)));
      return;
    }
    try {
      const result = await markActivityItemRead(item);
      setItems((current) => current.map((entry) => (entry.id === item.id ? { ...entry, unread: false } : entry)));
      setUnreadTotal(Number(result?.badge_counts?.total_unread_count ?? result?.total_unread_count ?? unreadTotal));
    } catch (readError) {
      Alert.alert("Could not mark read", readError instanceof Error ? readError.message : "Activity was not updated.");
    }
  }, [unreadTotal]);

  const markCategoryRead = useCallback(async () => {
    try {
      const result = await markActivityCategoryRead(category);
      setItems((current) => current.map((entry) => (category === "all" || entry.category === category ? { ...entry, unread: false } : entry)));
      setUnreadTotal(Number(result.badge_counts?.total_unread_count ?? result.total_unread_count ?? 0));
    } catch (readError) {
      Alert.alert("Could not mark read", readError instanceof Error ? readError.message : "Activity was not updated.");
    }
  }, [category]);

  const deleteItem = useCallback(async (item: ActivityInboxItem) => {
    if (!item.notificationId) {
      setItems((current) => current.filter((entry) => entry.id !== item.id));
      return;
    }
    const previous = items;
    setItems((current) => current.filter((entry) => entry.id !== item.id));
    try {
      const result = await deleteActivityItem(item);
      setUnreadTotal(Number(result?.badge_counts?.total_unread_count ?? result?.total_unread_count ?? unreadTotal));
    } catch (deleteError) {
      setItems(previous);
      Alert.alert("Could not delete", deleteError instanceof Error ? deleteError.message : "Activity was not deleted.");
    }
  }, [items, unreadTotal]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") load({ refresh: true }).catch(() => undefined);
    });
    return () => subscription.remove();
  }, [load]);

  useEffect(() => {
    const refreshActivity = () => load({ refresh: true });
    const unregisterActivity = registerSyncInvalidation("activity", refreshActivity);
    const unregisterNotifications = registerSyncInvalidation("notifications", refreshActivity);
    return () => {
      unregisterActivity();
      unregisterNotifications();
    };
  }, [load]);

  return (
    <View style={styles.root}>
      <View style={styles.header}>
        <Text style={styles.kicker}>Unified signal layer</Text>
        <Text style={styles.title}>Activity Inbox</Text>
        <Text style={styles.subtitle}>
          {offline ? "Showing cached activity. " : ""}{unreadTotal ? `${unreadTotal} unread signals across PulseSoc.` : "All current signals are read."}
        </Text>
        <View style={styles.headerActions}>
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("NotificationPreferences")}>
            <Text style={styles.secondaryText}>Preferences</Text>
          </Pressable>
          <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={markCategoryRead}>
            <Text style={styles.primaryText}>Mark read</Text>
          </Pressable>
        </View>
      </View>

      <View style={styles.categoryRail}>
        <FlatList
          data={activityCategories}
          horizontal
          showsHorizontalScrollIndicator={false}
          keyExtractor={(item) => item.key}
          contentContainerStyle={styles.categoryList}
          renderItem={({ item }) => {
            const active = category === item.key;
            const count = item.key === "all" ? unreadTotal : categoryCounts[item.key] || 0;
            return (
              <Pressable accessibilityRole="button" style={[styles.categoryChip, active && styles.categoryChipActive]} onPress={() => setCategory(item.key)}>
                <Text style={[styles.categoryText, active && styles.categoryTextActive]}>{item.label}</Text>
                {count ? <Text style={styles.categoryCount}>{count}</Text> : null}
              </Pressable>
            );
          }}
        />
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      {loading && !items.length ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.accent} />
          <Text style={styles.loadingText}>Synchronizing activity graph</Text>
        </View>
      ) : (
        <FlatList
          data={visibleItems}
          keyExtractor={(item) => item.id}
          refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load({ refresh: true })} />}
          {...dock.handlers}
          contentContainerStyle={[styles.list, dock.contentPadding]}
          ListEmptyComponent={<EmptyState category={category} />}
          renderItem={({ item }) => (
            <ActivityRow item={item} onOpen={() => openItem(item)} onRead={() => markRead(item)} onDelete={() => deleteItem(item)} />
          )}
        />
      )}
    </View>
  );
}

function ActivityRow({
  item,
  onOpen,
  onRead,
  onDelete
}: {
  item: ActivityInboxItem;
  onOpen: () => void;
  onRead: () => void;
  onDelete: () => void;
}) {
  return (
    <Pressable style={({ pressed }) => [styles.card, item.unread && styles.unreadCard, pressed && styles.pressedCard]} onPress={onOpen}>
      <View style={styles.cardTop}>
        <View style={[styles.signalDot, item.unread && styles.signalDotUnread]} />
        <Text style={styles.cardCategory}>{activityCategoryLabel(item.category)}</Text>
        <Text style={styles.cardTime}>{formatShortTime(item.createdAt)}</Text>
      </View>
      <Text style={styles.cardTitle} numberOfLines={2}>{item.title}</Text>
      <Text style={styles.cardBody} numberOfLines={3}>{compactPreview(item.body, "Open activity")}</Text>
      <View style={styles.actionRow}>
        <Pressable accessibilityRole="button" style={styles.rowPrimaryButton} onPress={onOpen}>
          <Text style={styles.rowPrimaryText}>Open</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.rowSecondaryButton} onPress={onRead}>
          <Text style={styles.rowSecondaryText}>{item.unread ? "Mark read" : "Read"}</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.rowDangerButton} onPress={onDelete}>
          <Text style={styles.rowDangerText}>{item.notificationId ? "Delete" : "Clear"}</Text>
        </Pressable>
      </View>
    </Pressable>
  );
}

function EmptyState({ category }: { category: ActivityCategory }) {
  return (
    <View style={styles.emptyState}>
      <View style={styles.emptyOrb} />
      <Text style={styles.emptyTitle}>No {activityCategoryLabel(category).toLowerCase()} activity</Text>
      <Text style={styles.emptyBody}>When PulseSoc has new signals for this lane, they will appear here with the same server-authoritative routing used by notifications.</Text>
    </View>
  );
}

function normalizeRouteCategory(category?: ActivityCategory): ActivityCategory {
  return activityCategories.some((item) => item.key === category) ? category || "all" : "all";
}

const styles = StyleSheet.create({
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  header: {
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 8,
    padding: 18
  },
  kicker: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0,
    textTransform: "uppercase"
  },
  title: {
    color: colors.text,
    fontSize: 30,
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  headerActions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    paddingTop: 4
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    minHeight: 40,
    paddingHorizontal: 14
  },
  primaryText: {
    color: "#08110f",
    fontWeight: "900"
  },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    justifyContent: "center",
    minHeight: 40,
    paddingHorizontal: 14
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "800"
  },
  categoryRail: {
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth
  },
  categoryList: {
    gap: 8,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  categoryChip: {
    alignItems: "center",
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 7,
    minHeight: 38,
    paddingHorizontal: 12
  },
  categoryChipActive: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.accent
  },
  categoryText: {
    color: colors.muted,
    fontSize: 13,
    fontWeight: "800"
  },
  categoryTextActive: {
    color: colors.text
  },
  categoryCount: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900"
  },
  error: {
    color: colors.warning,
    padding: 14
  },
  center: {
    alignItems: "center",
    flex: 1,
    gap: 10,
    justifyContent: "center"
  },
  loadingText: {
    color: colors.muted,
    fontWeight: "700"
  },
  list: {
    gap: 10,
    padding: 12,
    paddingBottom: 24
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 9,
    overflow: "hidden",
    padding: 13
  },
  unreadCard: {
    borderColor: colors.accent,
    shadowColor: colors.accent,
    shadowOpacity: 0.12,
    shadowRadius: 14
  },
  pressedCard: {
    backgroundColor: colors.surfaceRaised
  },
  cardTop: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8
  },
  signalDot: {
    backgroundColor: colors.border,
    borderRadius: 5,
    height: 10,
    width: 10
  },
  signalDotUnread: {
    backgroundColor: colors.accent
  },
  cardCategory: {
    color: colors.accent,
    flex: 1,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  cardTime: {
    color: colors.muted,
    fontSize: 12
  },
  cardTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900",
    lineHeight: 23
  },
  cardBody: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  rowPrimaryButton: {
    backgroundColor: colors.accent,
    borderRadius: 8,
    minHeight: 34,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  rowPrimaryText: {
    color: "#08110f",
    fontWeight: "900"
  },
  rowSecondaryButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 34,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  rowSecondaryText: {
    color: colors.text,
    fontWeight: "800"
  },
  rowDangerButton: {
    borderColor: colors.danger,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    minHeight: 34,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  rowDangerText: {
    color: colors.danger,
    fontWeight: "800"
  },
  emptyState: {
    alignItems: "center",
    gap: 10,
    padding: 28
  },
  emptyOrb: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.accent,
    borderRadius: 26,
    borderWidth: StyleSheet.hairlineWidth,
    height: 52,
    shadowColor: colors.accent,
    shadowOpacity: 0.18,
    shadowRadius: 18,
    width: 52
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900",
    textAlign: "center"
  },
  emptyBody: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20,
    textAlign: "center"
  }
});
