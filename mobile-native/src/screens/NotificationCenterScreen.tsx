import { useNavigation } from "@react-navigation/native";
import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import { ActivityIndicator, Alert, AppState, FlatList, Pressable, RefreshControl, StyleSheet, Text, View } from "react-native";
import {
  deleteNotification,
  getNotificationBadgeCounts,
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  PulseNotification,
  resolveNotificationTarget,
  unreadCount
} from "../api/notifications";
import { RootStackParamList } from "../navigation/types";
import { routeNotificationTarget } from "../navigation/notificationRouting";
import { colors } from "../theme/colors";
import { compactPreview, formatShortTime } from "../utils/format";

export function NotificationCenterScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const [notifications, setNotifications] = useState<PulseNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(async ({ refresh = false } = {}) => {
    if (refresh) setRefreshing(true);
    else setLoading(true);
    setError("");
    try {
      const [list, counts] = await Promise.all([listNotifications({ limit: 80 }), getNotificationBadgeCounts()]);
      setNotifications(list.notifications || []);
      setUnread(unreadCount(counts));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "Notifications could not load.");
    } finally {
      setRefreshing(false);
      setLoading(false);
    }
  }, []);

  const openNotification = useCallback(async (notification: PulseNotification) => {
    try {
      const resolved = await resolveNotificationTarget(notification.id);
      setNotifications((current) => current.map((item) => (item.id === notification.id ? { ...item, read: true } : item)));
      setUnread((current) => Math.max(0, current - (notification.read ? 0 : 1)));
      await routeNotificationTarget(resolved.target_url || notification.target_url || notification.deep_link || "/pulse/notifications");
    } catch (openError) {
      Alert.alert("Notification unavailable", openError instanceof Error ? openError.message : "That notification could not be opened.");
    }
  }, []);

  const markRead = useCallback(async (notification: PulseNotification) => {
    try {
      const result = await markNotificationRead(notification.id);
      setNotifications((current) => current.map((item) => (item.id === notification.id ? { ...item, read: true } : item)));
      setUnread(unreadCount(result.badge_counts || result));
    } catch (readError) {
      Alert.alert("Could not mark read", readError instanceof Error ? readError.message : "Notification was not updated.");
    }
  }, []);

  const markAllRead = useCallback(async () => {
    try {
      const result = await markAllNotificationsRead();
      setNotifications((current) => current.map((item) => ({ ...item, read: true })));
      setUnread(unreadCount(result.badge_counts || result));
    } catch (readError) {
      Alert.alert("Could not mark all read", readError instanceof Error ? readError.message : "Notifications were not updated.");
    }
  }, []);

  const removeNotification = useCallback(async (notification: PulseNotification) => {
    const previous = notifications;
    setNotifications((current) => current.filter((item) => item.id !== notification.id));
    try {
      const result = await deleteNotification(notification.id);
      setUnread(unreadCount(result.badge_counts || result));
    } catch (deleteError) {
      setNotifications(previous);
      Alert.alert("Could not delete", deleteError instanceof Error ? deleteError.message : "Notification was not deleted.");
    }
  }, [notifications]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") load({ refresh: true }).catch(() => undefined);
    });
    return () => subscription.remove();
  }, [load]);

  return (
    <View style={styles.root}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>Notifications</Text>
          <Text style={styles.subtitle}>{unread ? `${unread} unread` : "All caught up"}</Text>
        </View>
        <View style={styles.headerActions}>
          <Pressable style={styles.secondaryButton} onPress={() => navigation.navigate("NotificationPreferences")}>
            <Text style={styles.secondaryText}>Prefs</Text>
          </Pressable>
          <Pressable style={styles.button} onPress={markAllRead}>
            <Text style={styles.buttonText}>Read all</Text>
          </Pressable>
        </View>
      </View>
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {loading && notifications.length === 0 ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.accent} />
        </View>
      ) : (
        <FlatList
          data={notifications}
          keyExtractor={(item) => String(item.id)}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load({ refresh: true })} />}
          ListEmptyComponent={<Text style={styles.empty}>No notifications yet.</Text>}
          renderItem={({ item }) => (
            <NotificationRow notification={item} onOpen={() => openNotification(item)} onRead={() => markRead(item)} onDelete={() => removeNotification(item)} />
          )}
        />
      )}
    </View>
  );
}

function NotificationRow({
  notification,
  onOpen,
  onRead,
  onDelete
}: {
  notification: PulseNotification;
  onOpen: () => void;
  onRead: () => void;
  onDelete: () => void;
}) {
  return (
    <Pressable style={({ pressed }) => [styles.card, !notification.read && styles.unreadCard, pressed && styles.pressed]} onPress={onOpen}>
      <View style={styles.rowTop}>
        <Text style={styles.category}>{notification.category || notification.type || "PulseSoc"}</Text>
        <Text style={styles.time}>{formatShortTime(notification.created_at)}</Text>
      </View>
      <Text style={styles.cardTitle} numberOfLines={2}>{notification.title}</Text>
      <Text style={styles.body} numberOfLines={3}>{compactPreview(notification.body, "Open notification")}</Text>
      <View style={styles.actions}>
        <Pressable style={styles.smallButton} onPress={onOpen}>
          <Text style={styles.smallButtonText}>Open</Text>
        </Pressable>
        <Pressable style={styles.smallButton} onPress={onRead}>
          <Text style={styles.smallButtonText}>{notification.read ? "Read" : "Mark read"}</Text>
        </Pressable>
        <Pressable style={styles.deleteButton} onPress={onDelete}>
          <Text style={styles.deleteText}>Delete</Text>
        </Pressable>
      </View>
    </Pressable>
  );
}

const styles = StyleSheet.create({
  root: {
    backgroundColor: colors.background,
    flex: 1
  },
  header: {
    alignItems: "center",
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 12,
    justifyContent: "space-between",
    padding: 16
  },
  title: {
    color: colors.text,
    fontSize: 26,
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 13,
    marginTop: 3
  },
  headerActions: {
    flexDirection: "row",
    gap: 8
  },
  button: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    minHeight: 38,
    paddingHorizontal: 12
  },
  buttonText: {
    color: "#08110f",
    fontWeight: "900"
  },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    justifyContent: "center",
    minHeight: 38,
    paddingHorizontal: 12
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "800"
  },
  error: {
    color: colors.warning,
    padding: 12
  },
  center: {
    alignItems: "center",
    flex: 1,
    justifyContent: "center"
  },
  list: {
    gap: 10,
    padding: 12
  },
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 8,
    padding: 12
  },
  unreadCard: {
    borderColor: colors.accent
  },
  pressed: {
    backgroundColor: colors.surfaceRaised
  },
  rowTop: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    justifyContent: "space-between"
  },
  category: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  time: {
    color: colors.muted,
    fontSize: 12
  },
  cardTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900",
    lineHeight: 22
  },
  body: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  actions: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  smallButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 10,
    paddingVertical: 7
  },
  smallButtonText: {
    color: colors.text,
    fontSize: 12,
    fontWeight: "800"
  },
  deleteButton: {
    borderColor: colors.danger,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    paddingHorizontal: 10,
    paddingVertical: 7
  },
  deleteText: {
    color: colors.danger,
    fontSize: 12,
    fontWeight: "800"
  },
  empty: {
    color: colors.muted,
    padding: 20,
    textAlign: "center"
  }
});
