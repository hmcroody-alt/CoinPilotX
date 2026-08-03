/**
 * One row of "where sales came from": a label, a bar, an amount and a count.
 *
 * The bar's colour is the source's colour app-wide — blue for Store, violet for
 * Marketplace, gold reserved for ads — so the row is recognisable before it is
 * read. Colour is never the only signal: the source name is written out, the
 * share is stated as a percentage in the accessible label, and the amount is on
 * the row.
 *
 * The fill animates by `scaleX` from a left anchor. Animating `width` would
 * relayout the row on every frame; a transform is composited and stays on the
 * native driver.
 */

import { Animated, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { insightsLight, sourceColors, type InsightsSourceKey } from "../../theme/insightsLight";
import { useInsightsFill } from "../../theme/insightsMotion";

export type SourceBreakdownRowProps = {
  source: InsightsSourceKey;
  /** e.g. "Your store" — already localized by the caller. */
  label: string;
  /** e.g. "$1,240.50" — already formatted by the caller. */
  amount: string;
  /** e.g. "12 orders" — already formatted and pluralized by the caller. */
  orders: string;
  /** e.g. "62%" — already formatted by the caller. */
  sharePercent: string;
  /** 0–1. Drives the bar width. */
  share: number;
  /** Redraw key: changing it replays the fill, e.g. on a period switch. */
  animationKey: unknown;
  delay?: number;
  reducedMotion: boolean;
};

export function SourceBreakdownRow({
  source,
  label,
  amount,
  orders,
  sharePercent,
  share,
  animationKey,
  delay = 0,
  reducedMotion
}: SourceBreakdownRowProps) {
  const fill = useInsightsFill(reducedMotion, animationKey, delay);
  const colors = sourceColors(source);
  // A source with revenue always shows a sliver, so "small but present" never
  // renders as an empty track that reads as zero.
  const width = `${Math.max(share * 100, share > 0 ? 2 : 0)}%` as const;

  return (
    <View
      style={styles.row}
      accessible
      accessibilityRole="text"
      accessibilityLabel={`${label}. ${amount}, ${orders}, ${sharePercent} of revenue.`}
    >
      <View style={styles.head}>
        <Text style={styles.label} numberOfLines={1}>
          {label}
        </Text>
        <Text style={styles.amount} numberOfLines={1}>
          {amount}
        </Text>
      </View>

      <View style={styles.track} accessibilityElementsHidden importantForAccessibility="no">
        <Animated.View
          style={[
            styles.fillHolder,
            { width },
            // `transformOrigin: "left center"` on the style below is what makes
            // this grow from the left rather than out from the middle. It is a
            // style prop, not a transform, so the scale stays on the native driver.
            { transform: [{ scaleX: fill }] }
          ]}
        >
          <LinearGradient
            colors={[colors.from, colors.to]}
            start={{ x: 0, y: 0 }}
            end={{ x: 1, y: 0 }}
            style={styles.fill}
          />
        </Animated.View>
      </View>

      <Text style={styles.meta} numberOfLines={1}>
        {orders} · {sharePercent}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { gap: 6, paddingVertical: 8 },
  head: { flexDirection: "row", alignItems: "baseline", gap: 8 },
  label: { flex: 1, fontSize: 13, fontWeight: "700", color: insightsLight.text.primary },
  amount: { fontSize: 13, fontWeight: "800", color: insightsLight.text.primary },
  track: {
    height: 10,
    borderRadius: 5,
    backgroundColor: insightsLight.source.track,
    overflow: "hidden"
  },
  // The holder carries the width; the gradient fills it. Separating them keeps
  // the animated transform off the gradient node, which is cheaper to composite.
  fillHolder: { height: 10, transformOrigin: "left center" },
  fill: { flex: 1, borderRadius: 5 },
  meta: { fontSize: 11, color: insightsLight.text.muted, fontWeight: "600" }
});
