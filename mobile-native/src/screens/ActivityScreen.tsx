/**
 * Activity — the unified seller notification feed reached from the header bell.
 *
 * This is NOT the Events manager (hosted events) and NOT the legacy category
 * inbox (`ActivityInbox`). It renders the notifications source through the pure
 * `activityFeed` derivation and the shared Activity components:
 *
 *   • Header — back / "Activity" / Mark all read + filter chips (All · Social ·
 *     Marketplace · Orders · System) each with its own unread count.
 *   • Grouped rows — New (unread since last visit) / Today / Yesterday / dated,
 *     each a `NotificationRow` that lays out the derivation's plain-language
 *     sentence, unread treatment, live urgency and (max two) inline actions.
 *   • Tapping a row marks it read and deep-links to its subject; a deleted
 *     subject lands gracefully instead of a dead screen.
 *
 * The screen computes no feed facts: classification, aggregation, day grouping,
 * relative time, filter counts and inline actions all come from `activityFeed`.
 * The bell number is the SAME `UnreadCountStore` number every seller header
 * shows — this screen refreshes and optimistically clears that one store, so the
 * feed and every bell never diverge.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import AsyncStorage from "@react-native-async-storage/async-storage";
import {
  type FeedFilter,
  type FeedNotification,
  type InlineAction,
  aggregateFeed,
  filterUnreadCounts,
  groupFeedByDay,
  rowMatchesFilter,
  toFeedNotification
} from "../api/activityFeed";
import {
  listNotifications,
  markAllNotificationsRead,
  markNotificationRead
} from "../api/notifications";
import { applyOptimisticRead, refreshUnreadCounts, setUnreadCounts } from "../core/unreadCounts";
import { invalidateNativeSync, registerSyncInvalidation } from "../core/eventSync";
import { ActivityHeader, NotificationRow } from "../components/activity";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { eventsLight } from "../theme/eventsLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";

const FILTER_KEY = "pulsesoc.native.activity.filter";
const LAST_VISIT_KEY = "pulsesoc.native.activity.lastVisit";
const VALID_FILTERS: FeedFilter[] = ["all", "social", "marketplace", "orders", "system"];

type Props = {
  route?: { params?: RootStackParamList["BusinessOsActivity"] };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function ActivityScreen({ route, navigation }: Props) {
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();

  const [rows, setRows] = useState<FeedNotification[]>([]);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [filter, setFilter] = useState<FeedFilter>(route?.params?.filter || "all");
  const [now, setNow] = useState(() => Date.now());

  // The "New" divider is everything unread since the seller last opened Activity.
  // Captured once on mount so it doesn't move as rows are marked read this visit.
  const lastVisitRef = useRef<number>(0);

  /* -------------------------------------------------------------- *
   * Persisted filter + last-visit boundary
   * -------------------------------------------------------------- */
  useEffect(() => {
    AsyncStorage.getItem(FILTER_KEY)
      .then((value) => {
        if (!route?.params?.filter && value && (VALID_FILTERS as string[]).includes(value)) {
          setFilter(value as FeedFilter);
        }
      })
      .catch(() => undefined);
    AsyncStorage.getItem(LAST_VISIT_KEY)
      .then((value) => {
        const parsed = Number(value || 0);
        lastVisitRef.current = Number.isFinite(parsed) ? parsed : 0;
      })
      .catch(() => undefined);
    // Stamp this visit so the NEXT open's "New" divider starts here.
    AsyncStorage.setItem(LAST_VISIT_KEY, String(Date.now())).catch(() => undefined);
  }, [route?.params?.filter]);

  const changeFilter = useCallback((next: FeedFilter) => {
    setFilter(next);
    AsyncStorage.setItem(FILTER_KEY, next).catch(() => undefined);
  }, []);

  /* -------------------------------------------------------------- *
   * Load + refresh
   * -------------------------------------------------------------- */
  const load = useCallback(async (kind: "initial" | "refresh" = "initial") => {
    if (kind === "initial") setLoading(true);
    const nowMs = Date.now();
    try {
      const response = await listNotifications({ limit: 100 });
      const feed = (response.notifications || []).map((n) => toFeedNotification(n, nowMs));
      setRows(feed);
      setOffline(false);
      // Same authoritative badge numbers the bell store holds; publish them so
      // every header bell reflects what this feed just loaded.
      if (response.badge_counts) setUnreadCounts(response.badge_counts, nowMs);
      else void refreshUnreadCounts();
    } catch {
      setOffline(true);
    } finally {
      setNow(nowMs);
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  useEffect(() => {
    // A push arriving, a mark-read elsewhere, or any commerce event fires
    // "notifications"/"activity"; refresh the feed on the same signal the bells use.
    const refresh = () => load("refresh").catch(() => undefined);
    const unregister = [
      registerSyncInvalidation("notifications", refresh),
      registerSyncInvalidation("activity", refresh)
    ];
    return () => unregister.forEach((fn) => fn());
  }, [load]);

  // Re-tick relative timestamps ("3h ago") once a minute.
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 60 * 1000);
    return () => clearInterval(id);
  }, []);

  /* -------------------------------------------------------------- *
   * Derived — all via activityFeed, nothing invented here
   * -------------------------------------------------------------- */
  const feed = useMemo(() => aggregateFeed(rows), [rows]);
  const counts = useMemo(() => filterUnreadCounts(feed), [feed]);
  const filtered = useMemo(() => feed.filter((r) => rowMatchesFilter(r, filter)), [feed, filter]);
  const sections = useMemo(
    () => groupFeedByDay(filtered, now, { lastVisitMs: lastVisitRef.current }),
    [filtered, now]
  );

  /* -------------------------------------------------------------- *
   * Read-state mutations — optimistic locally + shared bell store
   * -------------------------------------------------------------- */
  const markRead = useCallback((id: number) => {
    setRows((prev) => prev.map((r) => (r.id === id ? { ...r, unread: false } : r)));
    markNotificationRead(id)
      .then((res) => {
        if (res?.badge_counts) setUnreadCounts(res.badge_counts);
        else void refreshUnreadCounts();
      })
      .catch(() => void refreshUnreadCounts());
    void invalidateNativeSync(["notifications", "activity"], "activity:mark_read");
  }, []);

  const markAllRead = useCallback(() => {
    setRows((prev) => prev.map((r) => (r.unread ? { ...r, unread: false } : r)));
    applyOptimisticRead("notifications"); // instant bell zero; reconciled below
    markAllNotificationsRead()
      .then((res) => {
        if (res?.badge_counts) setUnreadCounts(res.badge_counts);
        else void refreshUnreadCounts();
      })
      .catch(() => void refreshUnreadCounts());
    void invalidateNativeSync(["notifications", "activity"], "activity:mark_all_read");
  }, []);

  /* -------------------------------------------------------------- *
   * Navigation — tapping a row marks it read then deep-links to the subject.
   * Every destination is an existing route; an unmappable/deleted target still
   * marks read rather than pushing a dead screen.
   * -------------------------------------------------------------- */
  const navigateToTarget = useCallback(
    (target?: string) => {
      if (!target || !navigation) return;
      const path = target.toLowerCase();
      const conv = /\/messages\/(\d+)/.exec(path);
      if (conv) {
        navigation.navigate("Chat", { conversationId: Number(conv[1]) });
        return;
      }
      if (/\/orders?\b/.test(path)) {
        navigation.navigate("BusinessOsOrders");
        return;
      }
      if (/marketplace|offer|listing/.test(path)) {
        navigation.navigate("Marketplace");
        return;
      }
      if (/\/live\b|livestream/.test(path)) {
        navigation.navigate("Live");
        return;
      }
      // Unmappable path: stay put. The row is already marked read.
    },
    [navigation]
  );

  const onRowPress = useCallback(
    (row: FeedNotification) => {
      if (row.unread) markRead(row.id);
      navigateToTarget(row.target);
    },
    [markRead, navigateToTarget]
  );

  const onRowAction = useCallback(
    (action: InlineAction, row: FeedNotification) => {
      if (row.unread) markRead(row.id);
      navigateToTarget(action.target || row.target);
    },
    [markRead, navigateToTarget]
  );

  /* -------------------------------------------------------------- *
   * Body
   * -------------------------------------------------------------- */
  const isEmpty = !loading && sections.length === 0;

  return (
    <View style={styles.root}>
      <ActivityHeader
        title={route?.params?.title || "Activity"}
        filter={filter}
        counts={counts}
        onChangeFilter={changeFilter}
        onBack={() => navigation?.goBack?.()}
        onMarkAllRead={markAllRead}
      />

      <ScrollView
        contentContainerStyle={[styles.content, { paddingBottom: bottomPad(insets.bottom) }]}
        showsVerticalScrollIndicator={false}
      >
        {offline ? (
          <View style={styles.notice}>
            <Ionicons name="cloud-offline-outline" size={16} color={eventsLight.text.muted} />
            <Text style={styles.noticeText}>You&apos;re offline. Showing the last synced activity.</Text>
          </View>
        ) : null}

        {loading ? (
          <Text style={styles.state}>Loading activity…</Text>
        ) : isEmpty ? (
          <EmptyFeed filter={filter} />
        ) : (
          sections.map((section) => (
            <View key={section.key} style={styles.section}>
              <Text style={styles.sectionTitle}>{section.title}</Text>
              <View style={styles.card}>
                {section.items.map((row, index) => (
                  <View key={row.id}>
                    {index > 0 ? <View style={styles.divider} /> : null}
                    <NotificationRow
                      row={row}
                      now={now}
                      reducedMotion={reducedMotion}
                      onPress={onRowPress}
                      onAction={onRowAction}
                    />
                  </View>
                ))}
              </View>
            </View>
          ))
        )}
      </ScrollView>
    </View>
  );
}

function EmptyFeed({ filter }: { filter: FeedFilter }) {
  const copy =
    filter === "all"
      ? "You're all caught up. New activity shows up here."
      : `Nothing in ${LABELS[filter]} right now.`;
  return (
    <View style={styles.empty}>
      <Ionicons name="notifications-outline" size={28} color={eventsLight.text.muted} />
      <Text style={styles.emptyText}>{copy}</Text>
    </View>
  );
}

const LABELS: Record<FeedFilter, string> = {
  all: "Activity",
  social: "Social",
  marketplace: "Marketplace",
  orders: "Orders",
  system: "System"
};

function bottomPad(inset: number) {
  return Math.max(inset, 16) + BOTTOM_NAV_CONTENT_CLEARANCE + 24;
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: eventsLight.bg.page },
  content: { paddingTop: 12, gap: 14 },
  section: { gap: 6 },
  sectionTitle: {
    paddingHorizontal: eventsLight.space.card,
    fontSize: 13,
    fontWeight: "800",
    color: eventsLight.text.muted,
    textTransform: "uppercase",
    letterSpacing: 0.4
  },
  card: {
    marginHorizontal: eventsLight.space.card,
    borderRadius: eventsLight.radius.card,
    backgroundColor: eventsLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: eventsLight.border.hairline,
    overflow: "hidden"
  },
  divider: {
    height: StyleSheet.hairlineWidth,
    backgroundColor: eventsLight.border.hairline,
    marginLeft: eventsLight.space.card + 52
  },
  notice: {
    flexDirection: "row",
    alignItems: "center",
    gap: 8,
    marginHorizontal: eventsLight.space.card,
    paddingHorizontal: 12,
    paddingVertical: 10,
    borderRadius: eventsLight.radius.control,
    backgroundColor: eventsLight.bg.strip
  },
  noticeText: { flex: 1, fontSize: 12, color: eventsLight.text.muted },
  state: { paddingVertical: 24, textAlign: "center", fontSize: 13, color: eventsLight.text.muted },
  empty: { alignItems: "center", gap: 10, paddingVertical: 48, paddingHorizontal: 24 },
  emptyText: { fontSize: 13, color: eventsLight.text.muted, textAlign: "center", lineHeight: 19 }
});

export default ActivityScreen;
