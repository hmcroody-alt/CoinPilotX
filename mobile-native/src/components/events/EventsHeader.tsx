/**
 * The navy header for the seller Events manager. It carries the back affordance,
 * the "Events" title, a Calendar pill, and the segmented Upcoming / Past / Drafts
 * tabs. The tab selection governs the whole list beneath it, so it lives in the
 * fixed header rather than the scroll view — it must not scroll away. Selection
 * persistence is the screen's job; this component only reflects and reports it.
 */

import { type ReactNode } from "react";
import { Pressable, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import { eventsLight } from "../../theme/eventsLight";
import type { EventTab } from "../../api/eventsManager";

const TABS: Array<{ key: EventTab; label: string }> = [
  { key: "upcoming", label: "Upcoming" },
  { key: "past", label: "Past" },
  { key: "drafts", label: "Drafts" }
];

export function EventsHeader({
  title = "Events",
  tab,
  onChangeTab,
  onBack,
  onCalendar,
  below
}: {
  title?: string;
  tab: EventTab;
  onChangeTab: (next: EventTab) => void;
  onBack: () => void;
  onCalendar?: () => void;
  below?: ReactNode;
}) {
  const insets = useSafeAreaInsets();
  return (
    <LinearGradient
      colors={[eventsLight.bg.headerFrom, eventsLight.bg.headerTo]}
      style={[styles.header, { paddingTop: insets.top + 8 }]}
    >
      <View style={styles.topRow}>
        <Pressable onPress={onBack} style={styles.iconButton} accessibilityRole="button" accessibilityLabel="Go back" hitSlop={6}>
          <Ionicons name="chevron-back" size={24} color={eventsLight.text.onDark} />
        </Pressable>
        <Text style={styles.title} numberOfLines={1} accessibilityRole="header">
          {title}
        </Text>
        <Pressable
          onPress={onCalendar}
          style={styles.calendarPill}
          accessibilityRole="button"
          accessibilityLabel="Calendar view"
          hitSlop={6}
        >
          <Ionicons name="calendar-outline" size={15} color={eventsLight.text.onDark} />
          <Text style={styles.calendarText}>Calendar</Text>
        </Pressable>
      </View>

      <View style={styles.tabs} accessibilityRole="tablist">
        {TABS.map((t) => {
          const active = tab === t.key;
          return (
            <Pressable
              key={t.key}
              style={[styles.tabCell, active ? styles.tabCellActive : null]}
              onPress={() => onChangeTab(t.key)}
              accessibilityRole="tab"
              accessibilityState={{ selected: active }}
              accessibilityLabel={t.label}
            >
              <Text style={[styles.tabText, active ? styles.tabTextActive : null]}>{t.label}</Text>
            </Pressable>
          );
        })}
      </View>

      {below}
    </LinearGradient>
  );
}

const styles = StyleSheet.create({
  header: { paddingHorizontal: eventsLight.space.card, paddingBottom: 12, gap: 12, overflow: "hidden" },
  topRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  iconButton: {
    minWidth: eventsLight.size.tapTarget,
    minHeight: eventsLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  title: { flex: 1, fontSize: 20, fontWeight: "700", color: eventsLight.text.onDark },
  calendarPill: {
    flexDirection: "row",
    alignItems: "center",
    gap: 5,
    minHeight: 34,
    paddingHorizontal: 12,
    borderRadius: eventsLight.radius.pill,
    backgroundColor: "rgba(255,255,255,0.12)"
  },
  calendarText: { color: eventsLight.text.onDark, fontSize: 13, fontWeight: "700" },
  tabs: { flexDirection: "row", backgroundColor: "rgba(255,255,255,0.10)", borderRadius: eventsLight.radius.control, padding: 3 },
  tabCell: {
    flex: 1,
    minHeight: 38,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: eventsLight.radius.control - 2
  },
  tabCellActive: { backgroundColor: "#FFFFFF" },
  tabText: { fontSize: 14, fontWeight: "800", color: eventsLight.text.onDarkMuted },
  tabTextActive: { color: eventsLight.text.primary }
});
