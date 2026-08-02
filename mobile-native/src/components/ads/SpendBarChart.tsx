/**
 * Seven-day spend bar chart (analytics = blue).
 *
 * Bars grow up from the axis on data arrival, staggered left to right, then hold
 * still — motion is for arrival, not decoration. "Today" is the last bar and it
 * breaks the blue for gold, because today's column is live money and the money
 * colour is reserved for exactly that.
 *
 * Accessibility and honesty are the two non-negotiables:
 *
 *   • The bars are hidden from assistive tech and a single text `summary`
 *     carries the whole chart — a screen reader hears "Spend last 7 days: $x
 *     total, highest on…" rather than seven unlabeled rectangles.
 *
 *   • Per-day spend is MOCK-DATA (the analytics endpoint gives a total, not a
 *     daily breakdown). When `mock` is true the chart wears a visible "Preview"
 *     badge; when there is no per-day data at all (`empty`) it does not draw
 *     invented bars — it shows the real total and says the daily view isn't
 *     available yet.
 */

import { useMemo } from "react";
import { Animated, StyleSheet, Text, View } from "react-native";
import { adsLight } from "../../theme/adsLight";
import { useAdsBarCascade, useAdsTodayShine } from "../../theme/adsMotion";

export type SpendBarChartProps = {
  /** Per-day magnitudes (cents), oldest first. Last entry is "today". */
  values: number[];
  /** Short axis labels, e.g. ["M","T","W",…]. Same length as values. */
  dayLabels: string[];
  /** One-line text equivalent of the whole chart, for the caption + a11y. */
  summary: string;
  /** True when the per-day series is a preview, not real daily data. */
  mock: boolean;
  /** True when there is no per-day data to draw at all. */
  empty: boolean;
  /** Real, always-trustworthy total, already formatted. Shown when empty. */
  totalLabel: string;
  reducedMotion: boolean;
  /** Changes when the series changes, to re-trigger the grow-in. */
  seriesKey?: unknown;
};

const CHART_HEIGHT = 96;
const MIN_BAR = 4;

export function SpendBarChart({
  values,
  dayLabels,
  summary,
  mock,
  empty,
  totalLabel,
  reducedMotion,
  seriesKey = 0
}: SpendBarChartProps) {
  const cascade = useAdsBarCascade(values.length || 1, reducedMotion, seriesKey);
  const shine = useAdsTodayShine(reducedMotion, !empty && values.length > 0);

  const heights = useMemo(() => {
    const max = Math.max(1, ...values);
    return values.map((v) => MIN_BAR + (Math.max(0, v) / max) * (CHART_HEIGHT - MIN_BAR));
  }, [values]);

  if (empty || values.length === 0) {
    return (
      <View style={styles.card} accessible accessibilityLabel={summary}>
        <View style={styles.headRow}>
          <Text style={styles.title}>Spend · last 7 days</Text>
        </View>
        <View style={styles.emptyBox}>
          <Text style={styles.emptyTotal}>{totalLabel}</Text>
          <Text style={styles.emptyNote}>
            Total spend to date. A day-by-day view isn’t available yet.
          </Text>
        </View>
      </View>
    );
  }

  const todayIndex = values.length - 1;

  return (
    <View style={styles.card}>
      <View style={styles.headRow}>
        <Text style={styles.title}>Spend · last 7 days</Text>
        {mock ? (
          <View style={styles.previewBadge}>
            <Text style={styles.previewText}>Preview</Text>
          </View>
        ) : null}
      </View>

      {/* The plot is decorative to assistive tech; the caption below is the
          accessible equivalent. */}
      <View style={styles.plot} accessibilityElementsHidden importantForAccessibility="no">
        {values.map((_, index) => {
          const barHeight = heights[index];
          const isToday = index === todayIndex;
          const progress = cascade.progressFor(index);
          return (
            <View key={index} style={styles.column}>
              <View style={styles.barSlot}>
                <Animated.View
                  style={[
                    styles.bar,
                    {
                      height: barHeight,
                      backgroundColor: isToday ? adsLight.money.todayTo : adsLight.chart.barTo,
                      // Bottom-anchored grow: collapse to the axis at 0, full at 1.
                      transform: [
                        {
                          translateY: progress.interpolate({
                            inputRange: [0, 1],
                            outputRange: [barHeight / 2, 0]
                          })
                        },
                        { scaleY: progress }
                      ]
                    }
                  ]}
                >
                  {isToday ? (
                    <Animated.View
                      pointerEvents="none"
                      style={[
                        styles.shine,
                        {
                          opacity: shine.interpolate({
                            inputRange: [0, 0.4, 0.5, 0.6, 1],
                            outputRange: [0, 0, 0.5, 0, 0]
                          }),
                          transform: [
                            {
                              translateY: shine.interpolate({
                                inputRange: [0, 1],
                                outputRange: [barHeight, -barHeight]
                              })
                            }
                          ]
                        }
                      ]}
                    />
                  ) : null}
                </Animated.View>
              </View>
              <Text style={[styles.dayLabel, isToday ? styles.todayLabel : null]}>
                {dayLabels[index] ?? ""}
              </Text>
            </View>
          );
        })}
      </View>

      <Text style={styles.caption} accessibilityLabel={summary}>
        {summary}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: adsLight.bg.card,
    borderRadius: adsLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: adsLight.border.hairline,
    padding: adsLight.space.card,
    gap: 10
  },
  headRow: { flexDirection: "row", alignItems: "center", justifyContent: "space-between" },
  title: { fontSize: 14, fontWeight: "700", color: adsLight.text.primary },
  previewBadge: {
    paddingHorizontal: 8,
    paddingVertical: 2,
    borderRadius: adsLight.radius.pill,
    backgroundColor: adsLight.post.tint
  },
  previewText: { fontSize: 10, fontWeight: "800", color: adsLight.post.base },
  plot: {
    flexDirection: "row",
    alignItems: "flex-end",
    height: CHART_HEIGHT + 18,
    gap: 6
  },
  column: { flex: 1, alignItems: "center", gap: 4 },
  barSlot: { height: CHART_HEIGHT, width: "100%", justifyContent: "flex-end" },
  bar: {
    width: "100%",
    borderTopLeftRadius: adsLight.radius.bar,
    borderTopRightRadius: adsLight.radius.bar,
    overflow: "hidden"
  },
  shine: {
    position: "absolute",
    left: 0,
    right: 0,
    height: 18,
    backgroundColor: "#FFFFFF"
  },
  dayLabel: { fontSize: 10, color: adsLight.text.muted, fontWeight: "600" },
  todayLabel: { color: adsLight.money.budget, fontWeight: "800" },
  caption: { fontSize: 12, color: adsLight.text.muted, lineHeight: 17 },
  emptyBox: {
    paddingVertical: 14,
    gap: 4,
    alignItems: "flex-start"
  },
  emptyTotal: { fontSize: 22, fontWeight: "800", color: adsLight.text.primary },
  emptyNote: { fontSize: 12, color: adsLight.text.muted, lineHeight: 17 }
});
