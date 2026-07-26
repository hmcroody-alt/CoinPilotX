import { RouteProp, useNavigation, useRoute } from "@react-navigation/native";
import { NativeStackNavigationProp } from "@react-navigation/native-stack";
import { useCallback, useEffect, useRef, useState } from "react";
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
import { registerSyncInvalidation } from "../core/eventSync";
import { useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { routeNotificationTarget } from "../navigation/notificationRouting";
import { colors } from "../theme/colors";
import { compactPreview, formatShortTime } from "../utils/format";

export function NotificationCenterScreen() {
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const route = useRoute<RouteProp<RootStackParamList, "NotificationCenter">>();
  const { t } = useTranslation();
  const openedDeepLinkId = useRef<number | null>(null);
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
      setError(loadError instanceof Error ? loadError.message : t("discovery:notifications.loadError"));
    } finally {
      setRefreshing(false);
      setLoading(false);
    }
  }, [t]);

  const openNotificationById = useCallback(async (notificationId: number, wasRead = false) => {
    try {
      const resolved = await resolveNotificationTarget(notificationId);
      const notification = notifications.find(item => item.id === notificationId);
      setNotifications((current) => current.map((item) => (item.id === notificationId ? { ...item, read: true } : item)));
      setUnread((current) => Math.max(0, current - (wasRead ? 0 : 1)));
      await routeNotificationTarget(
        resolved.target_url || notification?.target_url || notification?.deep_link || "/pulse/notifications"
      );
    } catch (openError) {
      Alert.alert(
        t("discovery:notifications.openFailedTitle"),
        openError instanceof Error ? openError.message : t("discovery:notifications.openFailedBody")
      );
    }
  }, [notifications, t]);

  const openNotification = useCallback(async (notification: PulseNotification) => {
    await openNotificationById(notification.id, notification.read);
  }, [openNotificationById]);

  const markRead = useCallback(async (notification: PulseNotification) => {
    try {
      const result = await markNotificationRead(notification.id);
      setNotifications((current) => current.map((item) => (item.id === notification.id ? { ...item, read: true } : item)));
      setUnread(unreadCount(result.badge_counts || result));
    } catch (readError) {
      Alert.alert(
        t("discovery:notifications.markReadFailedTitle"),
        readError instanceof Error ? readError.message : t("discovery:notifications.markReadFailedBody")
      );
    }
  }, [t]);

  const markAllRead = useCallback(async () => {
    try {
      const result = await markAllNotificationsRead();
      setNotifications((current) => current.map((item) => ({ ...item, read: true })));
      setUnread(unreadCount(result.badge_counts || result));
    } catch (readError) {
      Alert.alert(
        t("discovery:notifications.markAllReadFailedTitle"),
        readError instanceof Error ? readError.message : t("discovery:notifications.markAllReadFailedBody")
      );
    }
  }, [t]);

  const removeNotification = useCallback(async (notification: PulseNotification) => {
    const previous = notifications;
    setNotifications((current) => current.filter((item) => item.id !== notification.id));
    try {
      const result = await deleteNotification(notification.id);
      setUnread(unreadCount(result.badge_counts || result));
    } catch (deleteError) {
      setNotifications(previous);
      Alert.alert(
        t("discovery:notifications.deleteFailedTitle"),
        deleteError instanceof Error ? deleteError.message : t("discovery:notifications.deleteFailedBody")
      );
    }
  }, [notifications, t]);

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
    const unregisterNotifications = registerSyncInvalidation("notifications", () => load({ refresh: true }));
    return unregisterNotifications;
  }, [load]);

  useEffect(() => {
    const notificationId = route.params?.notificationId;
    if (!notificationId || loading || openedDeepLinkId.current === notificationId) return;
    openedDeepLinkId.current = notificationId;
    const notification = notifications.find(item => item.id === notificationId);
    openNotificationById(notificationId, notification?.read === true).catch(() => undefined);
  }, [loading, notifications, openNotificationById, route.params?.notificationId]);

  return (
    <View style={styles.root}>
      <View style={styles.header}>
        <View>
          <Text style={styles.title}>{t("discovery:notifications.title")}</Text>
          <Text style={styles.subtitle}>
            {unread ? t("discovery:notifications.unreadCount", { count: unread }) : t("discovery:notifications.caughtUp")}
          </Text>
        </View>
        <View style={styles.headerActions}>
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("NotificationPreferences")}>
            <Text style={styles.secondaryText}>{t("discovery:notifications.prefs")}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" style={styles.button} onPress={markAllRead}>
            <Text style={styles.buttonText}>{t("discovery:notifications.readAll")}</Text>
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
          ListEmptyComponent={<Text style={styles.empty}>{t("discovery:notifications.empty")}</Text>}
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
  const { t } = useTranslation();
  return (
    <Pressable accessibilityRole="button" style={({ pressed }) => [styles.card, !notification.read && styles.unreadCard, pressed && styles.pressed]} onPress={onOpen}>
      <View style={styles.rowTop}>
        <Text style={styles.category}>{notification.category || notification.type || "PulseSoc"}</Text>
        <Text style={styles.time}>{formatShortTime(notification.created_at)}</Text>
      </View>
      <Text style={styles.cardTitle} numberOfLines={2}>{notification.title}</Text>
      <Text style={styles.body} numberOfLines={3}>{compactPreview(notification.body, t("discovery:notifications.bodyFallback"))}</Text>
      <View style={styles.actions}>
        <Pressable accessibilityRole="button" style={styles.smallButton} onPress={onOpen}>
          <Text style={styles.smallButtonText}>{t("common:actions.open")}</Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.smallButton} onPress={onRead}>
          <Text style={styles.smallButtonText}>
            {notification.read ? t("discovery:notifications.read") : t("discovery:notifications.markRead")}
          </Text>
        </Pressable>
        <Pressable accessibilityRole="button" style={styles.deleteButton} onPress={onDelete}>
          <Text style={styles.deleteText}>{t("common:actions.delete")}</Text>
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
