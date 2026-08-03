/**
 * The Activity center header: the navy band (back / "Activity" / Mark all read)
 * plus the filter chips (All · Social · Marketplace · Orders · System) each with
 * its unread count. The chips are controlled — the screen owns the active filter
 * and its persistence; this only reflects and reports. "Mark all read" disables
 * itself when nothing is unread so it never fires a no-op mutation.
 */

import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { eventsLight } from "../../theme/eventsLight";
import type { FeedFilter, FilterCounts } from "../../api/activityFeed";

const CHIPS: Array<{ key: FeedFilter; label: string }> = [
  { key: "all", label: "All" },
  { key: "social", label: "Social" },
  { key: "marketplace", label: "Marketplace" },
  { key: "orders", label: "Orders" },
  { key: "system", label: "System" }
];

export function ActivityHeader({
  title = "Activity",
  filter,
  counts,
  onChangeFilter,
  onBack,
  onMarkAllRead
}: {
  title?: string;
  filter: FeedFilter;
  counts: FilterCounts;
  onChangeFilter: (next: FeedFilter) => void;
  onBack: () => void;
  onMarkAllRead?: () => void;
}) {
  const insets = useSafeAreaInsets();
  const anyUnread = counts.all > 0;

  return (
    <View>
      <LinearGradient colors={[eventsLight.bg.headerFrom, eventsLight.bg.headerTo]} style={[styles.header, { paddingTop: insets.top + 8 }]}>
        <View style={styles.topRow}>
          <Pressable onPress={onBack} style={styles.iconButton} accessibilityRole="button" accessibilityLabel="Go back" hitSlop={6}>
            <Ionicons name="chevron-back" size={24} color={eventsLight.text.onDark} />
          </Pressable>
          <Text style={styles.title} numberOfLines={1} accessibilityRole="header">
            {title}
          </Text>
          <Pressable
            onPress={anyUnread ? onMarkAllRead : undefined}
            disabled={!anyUnread}
            style={styles.markAll}
            accessibilityRole="button"
            accessibilityState={{ disabled: !anyUnread }}
            accessibilityLabel="Mark all read"
            hitSlop={6}
          >
            <Text style={[styles.markAllText, !anyUnread ? styles.markAllDisabled : null]}>Mark all read</Text>
          </Pressable>
        </View>
      </LinearGradient>

      <View style={styles.chipStrip}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.rail} accessibilityRole="tablist">
          {CHIPS.map(({ key, label }) => {
            const active = filter === key;
            const count = counts[key] ?? 0;
            return (
              <Pressable
                key={key}
                onPress={() => onChangeFilter(key)}
                style={[styles.chip, active ? styles.chipActive : null]}
                accessibilityRole="tab"
                accessibilityState={{ selected: active }}
                accessibilityLabel={`${label}${count ? `, ${count} unread` : ""}`}
              >
                <Text style={[styles.chipLabel, active ? styles.chipLabelActive : null]}>{label}</Text>
                {count > 0 ? (
                  <View style={[styles.countPill, active ? styles.countPillActive : null]}>
                    <Text style={[styles.count, active ? styles.countActive : null]}>{count > 99 ? "99+" : count}</Text>
                  </View>
                ) : null}
              </Pressable>
            );
          })}
        </ScrollView>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  header: { paddingHorizontal: eventsLight.space.card, paddingBottom: 12, overflow: "hidden" },
  topRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  iconButton: {
    minWidth: eventsLight.size.tapTarget,
    minHeight: eventsLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  title: { flex: 1, fontSize: 20, fontWeight: "700", color: eventsLight.text.onDark },
  markAll: { minHeight: eventsLight.size.tapTarget, justifyContent: "center", paddingHorizontal: 4 },
  markAllText: { fontSize: 13, fontWeight: "700", color: eventsLight.text.onDark },
  markAllDisabled: { color: eventsLight.text.onDarkMuted, opacity: 0.6 },
  chipStrip: {
    backgroundColor: eventsLight.bg.card,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: eventsLight.border.hairline
  },
  rail: { gap: 8, paddingHorizontal: eventsLight.space.card, paddingVertical: 10 },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 12,
    height: 34,
    borderRadius: eventsLight.radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: eventsLight.border.secondaryButton,
    backgroundColor: eventsLight.bg.card
  },
  chipActive: { backgroundColor: eventsLight.text.primary, borderColor: eventsLight.text.primary },
  chipLabel: { fontSize: 13, fontWeight: "700", color: eventsLight.text.primary },
  chipLabelActive: { color: eventsLight.text.onDark },
  countPill: {
    minWidth: 18,
    paddingHorizontal: 5,
    height: 18,
    borderRadius: 9,
    backgroundColor: eventsLight.border.unreadEdge,
    alignItems: "center",
    justifyContent: "center"
  },
  countPillActive: { backgroundColor: "rgba(255,255,255,0.22)" },
  count: { fontSize: 11, fontWeight: "800", color: "#FFFFFF" },
  countActive: { color: eventsLight.text.onDark }
});
