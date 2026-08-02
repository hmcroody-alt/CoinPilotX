/**
 * One tile of the 2×2 KPI grid.
 *
 * Deliberately dumb: it takes an already-formatted string. Formatting happens
 * in the screen through `useFormatters`, so this component contains no currency
 * symbol, no percent sign and no locale assumption, and Orders and Insights can
 * reuse it with their own numbers.
 *
 * The value arrives a beat after the card so the tile reads as a container that
 * then fills, rather than as a finished block that faded in.
 */

import type { ReactNode } from "react";
import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { storeLight } from "../../theme/storeLight";
import {
  STORE_AMBIENT,
  useStoreAmbient,
  useStorePress,
  useStoreValueArrival
} from "../../theme/storeMotion";

export type StoreKpiTrend = {
  /** Signed ratio. 0.12 renders as an up arrow; -0.03 as a down arrow. */
  direction: "up" | "down";
  /** Already formatted, e.g. "12%". */
  label: string;
};

export type StoreKpiCardProps = {
  label: string;
  /** Already formatted for the active locale and currency. */
  value: string;
  /** Second line under the value, e.g. "3 ship today". Omitted when absent. */
  caption?: string | null;
  trend?: StoreKpiTrend | null;
  /** Sparkline or any other small graphic. */
  visual?: ReactNode;
  onPress?: () => void;
  /** Where this tile deep-links to, for the screen-reader hint. */
  destinationHint?: string;
  reducedMotion: boolean;
  /** Entrance slot, so the value offset holds wherever the card sits. */
  delay?: number;
};

export function StoreKpiCard({
  label,
  value,
  caption,
  trend,
  visual,
  onPress,
  destinationHint,
  reducedMotion,
  delay = 0
}: StoreKpiCardProps) {
  const press = useStorePress(reducedMotion, 0.985);
  const arrival = useStoreValueArrival(reducedMotion, delay);
  const bob = useStoreAmbient(STORE_AMBIENT.trendBob, reducedMotion, {
    enabled: Boolean(trend),
    resetTo: 0,
    pingPong: true
  });

  const valueStyle = {
    opacity: arrival,
    transform: [
      { translateY: arrival.interpolate({ inputRange: [0, 1], outputRange: [6, 0] }) }
    ]
  };

  const trendColor =
    trend?.direction === "up" ? storeLight.status.success : storeLight.status.error;

  return (
    <Animated.View style={[styles.wrap, press.style]}>
      <Pressable
        style={styles.card}
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        disabled={!onPress}
        accessibilityRole={onPress ? "button" : undefined}
        // One label for the whole tile, so a screen reader reads
        // "Today's sales, $412.90, up 12%" rather than three disconnected
        // fragments in an order that depends on layout.
        accessibilityLabel={[label, value, caption, trend ? `${trend.direction === "up" ? "up" : "down"} ${trend.label}` : null]
          .filter(Boolean)
          .join(", ")}
        accessibilityHint={onPress && destinationHint ? `Opens ${destinationHint}` : undefined}
      >
        <Text style={styles.label} numberOfLines={1}>
          {label}
        </Text>
        <Animated.View style={valueStyle}>
          <Text style={styles.value} numberOfLines={1} adjustsFontSizeToFit minimumFontScale={0.75}>
            {value}
          </Text>
        </Animated.View>
        <View style={styles.footer}>
          {trend ? (
            <Animated.View
              style={[
                styles.trend,
                {
                  transform: [
                    {
                      translateY: bob.interpolate({
                        inputRange: [0, 1],
                        outputRange: [0, trend.direction === "up" ? -2 : 2]
                      })
                    }
                  ]
                }
              ]}
            >
              <Text style={[styles.trendText, { color: trendColor }]}>
                {trend.direction === "up" ? "▲" : "▼"} {trend.label}
              </Text>
            </Animated.View>
          ) : null}
          {caption ? (
            <Text style={styles.caption} numberOfLines={1}>
              {caption}
            </Text>
          ) : null}
          {visual ? <View style={styles.visual}>{visual}</View> : null}
        </View>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1 },
  card: {
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    padding: storeLight.space.card,
    minHeight: 96,
    justifyContent: "space-between"
  },
  label: { fontSize: 12, color: storeLight.text.muted, fontWeight: "600" },
  value: { fontSize: 22, color: storeLight.text.primary, fontWeight: "700", marginTop: 4 },
  footer: { flexDirection: "row", alignItems: "center", gap: 8, marginTop: 6 },
  trend: {},
  trendText: { fontSize: 12, fontWeight: "700" },
  caption: { fontSize: 12, color: storeLight.text.muted, flexShrink: 1 },
  visual: { marginLeft: "auto" }
});
