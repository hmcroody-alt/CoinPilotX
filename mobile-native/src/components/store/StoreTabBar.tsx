/**
 * Listing filter tabs: All / Active / Low stock / Out / Drafts.
 *
 * The underline slides between tabs rather than cutting, which is the one place
 * on this screen where motion carries meaning: it shows *which* tab you came
 * from, so a mis-tap is obvious.
 *
 * Counts are part of each tab's accessibility label rather than a separate
 * element, because "Low stock, 3" read as one phrase is the information; read
 * as two nodes it is a puzzle. Counts on the problem tabs are coloured, and the
 * colour is redundant — the number is there either way.
 */

import { useCallback, useRef, useState } from "react";
import { Animated, Pressable, ScrollView, StyleSheet, Text, View, type LayoutChangeEvent } from "react-native";
import { storeLight } from "../../theme/storeLight";
import { useStoreTabIndicator } from "../../theme/storeMotion";
import type { StoreTab, StoreTabKey } from "../../api/storeDashboard";

const TAB_LABELS: Record<StoreTabKey, string> = {
  all: "All",
  active: "Active",
  low: "Low stock",
  out: "Out",
  drafts: "Drafts"
};

export type StoreTabBarProps = {
  tabs: StoreTab[];
  active: StoreTabKey;
  onChange: (key: StoreTabKey) => void;
  reducedMotion: boolean;
};

export function StoreTabBar({ tabs, active, onChange, reducedMotion }: StoreTabBarProps) {
  const layouts = useRef<Partial<Record<StoreTabKey, { x: number; width: number }>>>({});
  const [target, setTarget] = useState({ x: 0, width: 0 });
  const indicator = useStoreTabIndicator(target, reducedMotion);

  const onTabLayout = useCallback(
    (key: StoreTabKey) => (event: LayoutChangeEvent) => {
      const { x, width } = event.nativeEvent.layout;
      layouts.current[key] = { x, width };
      // Only move the bar for the tab that is actually selected. Measuring the
      // others is what lets the bar be there on first paint instead of sliding
      // in from zero.
      if (key === active) setTarget({ x, width });
    },
    [active]
  );

  const select = useCallback(
    (key: StoreTabKey) => {
      const layout = layouts.current[key];
      if (layout) setTarget(layout);
      onChange(key);
    },
    [onChange]
  );

  return (
    <View style={styles.wrap}>
      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.row}
      >
        {tabs.map((tab) => {
          const selected = tab.key === active;
          return (
            <Pressable
              key={tab.key}
              onLayout={onTabLayout(tab.key)}
              onPress={() => select(tab.key)}
              style={styles.tab}
              accessibilityRole="tab"
              accessibilityState={{ selected }}
              accessibilityLabel={`${TAB_LABELS[tab.key]}, ${tab.count}`}
            >
              <Text style={[styles.label, selected && styles.labelActive]}>
                {TAB_LABELS[tab.key]}{" "}
                <Text style={tab.needsAttention ? styles.countAlert : styles.count}>{tab.count}</Text>
              </Text>
            </Pressable>
          );
        })}
        <Animated.View
          pointerEvents="none"
          style={[styles.underline, { left: indicator.x, width: indicator.width }]}
        />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    backgroundColor: storeLight.bg.card,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: storeLight.border.hairline
  },
  row: { flexDirection: "row", paddingHorizontal: storeLight.space.card, position: "relative" },
  tab: {
    minHeight: storeLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  label: { fontSize: 13, color: storeLight.text.muted, fontWeight: "600" },
  labelActive: { color: storeLight.text.primary },
  count: { color: storeLight.text.muted },
  countAlert: { color: storeLight.status.warning, fontWeight: "700" },
  underline: {
    position: "absolute",
    bottom: 0,
    height: 2,
    backgroundColor: storeLight.accent.orange,
    borderRadius: 1
  }
});
