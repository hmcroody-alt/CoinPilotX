/**
 * The triage rail, each chip with a live count. The Unread count turns hot-orange
 * when nonzero to pull the eye to the one control that means "money-relevant
 * threads are waiting" — every other count stays quiet.
 *
 * Which chips appear depends on `inboxFilterRail()`. With the Tier 0.4 split on
 * the rail is All / Unread / Marketplace / Store support / Orders / Returns /
 * Disputes; with it off the pre-0.4 rail is unchanged. This rail is the commerce
 * side's own control and is never shared with the Messenger tab — the two inboxes
 * do not sit behind one list component.
 *
 * Selection persistence is the screen's job (it writes the chosen filter to
 * storage); this component is controlled — it renders `active` and reports taps.
 */

import { ScrollView, StyleSheet, Text, View } from "react-native";
import { Pressable } from "react-native";
import { messagesLight } from "../../theme/messagesLight";
import { FilterCounts, InboxFilter, inboxFilterRail } from "../../api/commerceInbox";

export const FILTER_LABELS: Record<InboxFilter, string> = {
  all: "All",
  unread: "Unread",
  marketplace: "Marketplace",
  store_support: "Store support",
  orders: "Orders",
  returns: "Returns",
  disputes: "Disputes",
  offers: "Offers",
  starred: "Starred",
  archived: "Archived"
};

export function FilterChips({
  active,
  counts,
  onChange
}: {
  active: InboxFilter;
  counts: FilterCounts;
  onChange: (filter: InboxFilter) => void;
}) {
  return (
    <ScrollView
      horizontal
      showsHorizontalScrollIndicator={false}
      contentContainerStyle={styles.rail}
      accessibilityRole="tablist"
    >
      {inboxFilterRail().map((key) => {
        const label = FILTER_LABELS[key];
        const isActive = active === key;
        const count = counts[key] ?? 0;
        const hot = key === "unread" && count > 0;
        return (
          <Pressable
            key={key}
            onPress={() => onChange(key)}
            style={[styles.chip, isActive && styles.chipActive]}
            accessibilityRole="tab"
            accessibilityState={{ selected: isActive }}
            accessibilityLabel={`${label}${count ? `, ${count}` : ""}`}
          >
            <Text style={[styles.label, isActive && styles.labelActive]}>{label}</Text>
            {count > 0 ? (
              <View style={[styles.countPill, isActive && styles.countPillActive]}>
                <Text
                  style={[
                    styles.count,
                    isActive && styles.countActive,
                    hot && !isActive && styles.countHot
                  ]}
                >
                  {count > 99 ? "99+" : count}
                </Text>
              </View>
            ) : null}
          </Pressable>
        );
      })}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  rail: {
    gap: 8,
    paddingHorizontal: messagesLight.space.card,
    paddingVertical: 10
  },
  chip: {
    flexDirection: "row",
    alignItems: "center",
    gap: 6,
    paddingHorizontal: 12,
    height: 34,
    borderRadius: messagesLight.radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: messagesLight.border.secondaryButton,
    backgroundColor: messagesLight.bg.card
  },
  chipActive: {
    backgroundColor: messagesLight.text.primary,
    borderColor: messagesLight.text.primary
  },
  label: { fontSize: 13, fontWeight: "700", color: messagesLight.text.primary },
  labelActive: { color: messagesLight.text.onDark },
  countPill: {
    minWidth: 18,
    paddingHorizontal: 5,
    height: 18,
    borderRadius: 9,
    backgroundColor: messagesLight.bg.strip,
    alignItems: "center",
    justifyContent: "center"
  },
  countPillActive: { backgroundColor: "rgba(255,255,255,0.22)" },
  count: { fontSize: 11, fontWeight: "800", color: messagesLight.text.onDark },
  countActive: { color: messagesLight.text.onDark },
  countHot: { color: messagesLight.filterHot }
});
