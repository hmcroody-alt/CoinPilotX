/**
 * The campaign / promotion tab bar.
 *
 * `StoreTabBar` does the same job on the Store surface but its key type is the
 * Store's own `StoreTabKey` union and its labels are baked into the module, so
 * reusing it here would mean widening a Store type to carry advertising
 * concepts. This is the same control with the key left open, and it shares the
 * underline animation (`useStoreTabIndicator`) rather than reimplementing it.
 *
 * Counts are announced to assistive tech as part of the tab's label — "Active,
 * 3" — so a screen reader user does not have to enter a tab to find out whether
 * there is anything in it.
 */

import { useCallback, useRef, useState } from "react";
import {
  Animated,
  type LayoutChangeEvent,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";
import { adsLight } from "../../theme/adsLight";
import { useStoreTabIndicator } from "../../theme/storeMotion";

export type AdsTab = {
  key: string;
  label: string;
  count: number;
  /** Draws the count in the warning colour, e.g. a blocked campaign. */
  needsAttention?: boolean;
};

export type AdsTabBarProps = {
  tabs: AdsTab[];
  active: string;
  onChange: (key: string) => void;
  reducedMotion: boolean;
  /** Underline colour, so Post mode can run violet and Marketplace gold. */
  accent?: string;
};

export function AdsTabBar({
  tabs,
  active,
  onChange,
  reducedMotion,
  accent = adsLight.money.budget
}: AdsTabBarProps) {
  const layouts = useRef<Record<string, { x: number; width: number }>>({});
  const [target, setTarget] = useState({ x: 0, width: 0 });
  const indicator = useStoreTabIndicator(target, reducedMotion);

  const onTabLayout = useCallback(
    (key: string) => (event: LayoutChangeEvent) => {
      const { x, width } = event.nativeEvent.layout;
      layouts.current[key] = { x, width };
      // Measure every tab, but only move the underline for the selected one, so
      // it is already in place on first paint instead of sliding in from zero.
      if (key === active) setTarget({ x, width });
    },
    [active]
  );

  const select = useCallback(
    (key: string) => {
      const layout = layouts.current[key];
      if (layout) setTarget(layout);
      onChange(key);
    },
    [onChange]
  );

  return (
    <View style={styles.wrap}>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.row}>
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
              accessibilityLabel={`${tab.label}, ${tab.count}`}
            >
              <Text style={[styles.label, selected && styles.labelActive]}>
                {tab.label}{" "}
                <Text style={tab.needsAttention ? styles.countAlert : styles.count}>{tab.count}</Text>
              </Text>
            </Pressable>
          );
        })}
        <Animated.View
          pointerEvents="none"
          style={[styles.underline, { left: indicator.x, width: indicator.width, backgroundColor: accent }]}
        />
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    backgroundColor: adsLight.bg.card,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: adsLight.border.hairline
  },
  row: { flexDirection: "row", paddingHorizontal: adsLight.space.card, position: "relative" },
  tab: { minHeight: adsLight.size.tapTarget, justifyContent: "center", paddingHorizontal: 12 },
  label: { fontSize: 13, color: adsLight.text.muted, fontWeight: "600" },
  labelActive: { color: adsLight.text.primary },
  count: { color: adsLight.text.muted },
  countAlert: { color: adsLight.status.warning, fontWeight: "700" },
  underline: { position: "absolute", bottom: 0, height: 2, borderRadius: 1 }
});
