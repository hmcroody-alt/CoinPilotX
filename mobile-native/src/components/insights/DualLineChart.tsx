/**
 * Revenue and orders over the selected period, drawn natively.
 *
 * Two polylines, three gridlines and a dot. That is a shape `react-native-svg`
 * already draws elsewhere in this app (the Store sparkline, the Business live
 * parts), so no chart library is added — a charting dependency here would be
 * several hundred kilobytes and a second animation system to make one figure
 * that eleven lines of path arithmetic already make.
 *
 * **The two series do not share a y-axis, and the chart says so.** Revenue is in
 * cents and orders are in ones; on a shared axis the orders line would be a flat
 * trace along the floor and would tell the seller nothing. Each series is
 * normalized to its own range across the plot height, so what the chart shows is
 * *the shape of two trends over the same days* — whether they move together and
 * where each peaked. It deliberately does not show their relative magnitude,
 * because that comparison is meaningless between a currency and a count. The
 * legend states the units and the accessible summary gives the real numbers, so
 * nobody has to read a value off the pixels.
 *
 * A flat series is drawn flat at mid-height rather than scaled to fill the box:
 * a week of identical days must not look like a week of wild swings, and
 * dividing by a zero range would do exactly that.
 */

import { useMemo } from "react";
import { Animated, StyleSheet, Text, View } from "react-native";
import Svg, { Circle, Line, Path } from "react-native-svg";
import { insightsLight } from "../../theme/insightsLight";
import { INSIGHTS_MOTION, useInsightsDraw, useInsightsLatestDot } from "../../theme/insightsMotion";
import type { InsightsBucket, InsightsBucketLabel } from "../../api/insightsDashboard";

const AnimatedPath = Animated.createAnimatedComponent(Path);
const AnimatedCircle = Animated.createAnimatedComponent(Circle);

/** Vertical inset so a stroke at the extreme is not clipped by the viewport. */
const INSET = 3;

export type DualLineChartProps = {
  series: InsightsBucket[];
  bucket: InsightsBucketLabel;
  /** Pre-formatted axis labels, one per bucket, from the caller's localization. */
  xLabels: string[];
  /** Pre-formatted, already localized. `[top, middle, bottom]` for the revenue axis. */
  gridLabels: [string, string, string];
  /** e.g. "Revenue in US dollars". Stated because the axes are not shared. */
  revenueLegend: string;
  ordersLegend: string;
  /** Full sentence alternative; see `chartSummary`. */
  accessibilitySummary: string;
  height?: number;
  width: number;
  reducedMotion: boolean;
  /** Shown instead of the lines when the seller has no data at all. */
  emptyMessage?: string;
};

type Scaled = { points: { x: number; y: number }[]; path: string };

function scale(values: number[], width: number, height: number): Scaled {
  if (values.length === 0) return { points: [], path: "" };
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min;
  const usable = height - INSET * 2;
  // A single bucket has no width to travel across, so it sits in the middle
  // rather than at x=0 where it would look like the start of a missing line.
  const stepX = values.length > 1 ? width / (values.length - 1) : 0;

  const points = values.map((value, index) => ({
    x: values.length > 1 ? index * stepX : width / 2,
    y: range === 0 ? INSET + usable / 2 : INSET + usable - ((value - min) / range) * usable
  }));

  const path = points
    .map((point, index) => `${index === 0 ? "M" : "L"}${point.x.toFixed(2)},${point.y.toFixed(2)}`)
    .join(" ");

  return { points, path: points.length > 1 ? path : "" };
}

export function DualLineChart({
  series,
  bucket,
  xLabels,
  gridLabels,
  revenueLegend,
  ordersLegend,
  accessibilitySummary,
  height = 168,
  width,
  reducedMotion,
  emptyMessage
}: DualLineChartProps) {
  const plotWidth = Math.max(width, 1);

  const revenue = useMemo(
    () => scale(series.map((point) => point.revenue_minor), plotWidth, height),
    [height, plotWidth, series]
  );
  const orders = useMemo(
    () => scale(series.map((point) => point.orders), plotWidth, height),
    [height, plotWidth, series]
  );

  // Keyed on the actual data, so the lines redraw when the period changes and
  // not on every unrelated re-render of the parent.
  const drawKey = `${bucket}:${series.length}:${series[0]?.date || ""}:${series[series.length - 1]?.date || ""}`;
  const revenueDraw = useInsightsDraw(reducedMotion, drawKey, 0);
  const ordersDraw = useInsightsDraw(reducedMotion, drawKey, INSIGHTS_MOTION.lineStaggerMs);
  const dotOpacity = useInsightsLatestDot(reducedMotion, drawKey);

  const hasAnything = series.some((point) => point.revenue_minor > 0 || point.orders > 0);
  const latest = revenue.points[revenue.points.length - 1];

  // Generous over-estimate of path length. `getTotalLength` is not available on
  // the RN SVG node; overshooting only means the line starts fully hidden.
  const dashLength = plotWidth * Math.max(series.length, 2);

  const gridY = [INSET, height / 2, height - INSET];

  return (
    <View style={styles.wrapper}>
      <View style={styles.plotRow}>
        <View style={styles.gridLabels} accessibilityElementsHidden importantForAccessibility="no">
          {gridLabels.map((label, index) => (
            <Text key={`${label}-${index}`} style={styles.gridLabel} numberOfLines={1}>
              {label}
            </Text>
          ))}
        </View>

        <View
          style={{ width: plotWidth, height }}
          // The drawing is decorative: everything it conveys is in the summary
          // below, which is what a screen reader reads instead.
          accessible
          accessibilityRole="image"
          accessibilityLabel={accessibilitySummary}
        >
          <Svg width={plotWidth} height={height}>
            {gridY.map((y) => (
              <Line
                key={y}
                x1={0}
                y1={y}
                x2={plotWidth}
                y2={y}
                stroke={insightsLight.chart.gridline}
                strokeWidth={1}
              />
            ))}

            {hasAnything && orders.path ? (
              <AnimatedPath
                d={orders.path}
                stroke={insightsLight.chart.orders}
                strokeOpacity={insightsLight.chart.ordersOpacity}
                strokeWidth={2}
                strokeLinecap="round"
                strokeLinejoin="round"
                fill="none"
                strokeDasharray={dashLength}
                strokeDashoffset={ordersDraw.interpolate({
                  inputRange: [0, 1],
                  outputRange: [dashLength, 0]
                })}
              />
            ) : null}

            {hasAnything && revenue.path ? (
              <AnimatedPath
                d={revenue.path}
                stroke={insightsLight.chart.revenue}
                strokeWidth={2.5}
                strokeLinecap="round"
                strokeLinejoin="round"
                fill="none"
                strokeDasharray={dashLength}
                strokeDashoffset={revenueDraw.interpolate({
                  inputRange: [0, 1],
                  outputRange: [dashLength, 0]
                })}
              />
            ) : null}

            {/* An empty period still draws a line — flat, on the baseline — so the
                frame reads as "nothing yet" rather than as a chart that failed. */}
            {!hasAnything ? (
              <Line
                x1={0}
                y1={height - INSET}
                x2={plotWidth}
                y2={height - INSET}
                stroke={insightsLight.chart.revenue}
                strokeOpacity={0.35}
                strokeWidth={2}
                strokeLinecap="round"
              />
            ) : null}

            {hasAnything && latest ? (
              <AnimatedCircle
                cx={latest.x}
                cy={latest.y}
                r={4}
                fill={insightsLight.chart.latest}
                opacity={dotOpacity}
              />
            ) : null}
          </Svg>
        </View>
      </View>

      <View style={styles.xAxis} accessibilityElementsHidden importantForAccessibility="no">
        {xLabels.map((label, index) => (
          <Text key={`${label}-${index}`} style={styles.xLabel} numberOfLines={1}>
            {label}
          </Text>
        ))}
      </View>

      {!hasAnything && emptyMessage ? (
        <Text style={styles.empty}>{emptyMessage}</Text>
      ) : null}

      <View style={styles.legend}>
        <View style={styles.legendItem}>
          <View style={[styles.swatch, { backgroundColor: insightsLight.chart.revenue }]} />
          <Text style={styles.legendText}>{revenueLegend}</Text>
        </View>
        <View style={styles.legendItem}>
          <View
            style={[
              styles.swatch,
              {
                backgroundColor: insightsLight.chart.orders,
                opacity: insightsLight.chart.ordersOpacity
              }
            ]}
          />
          <Text style={styles.legendText}>{ordersLegend}</Text>
        </View>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  wrapper: { gap: 8 },
  plotRow: { flexDirection: "row", gap: 8 },
  gridLabels: { width: 54, justifyContent: "space-between", paddingVertical: 0 },
  gridLabel: { fontSize: 10, color: insightsLight.chart.axisLabel, textAlign: "right" },
  xAxis: { flexDirection: "row", justifyContent: "space-between", paddingLeft: 62 },
  xLabel: { fontSize: 10, color: insightsLight.chart.axisLabel, flexShrink: 1 },
  empty: { fontSize: 13, color: insightsLight.text.muted, paddingLeft: 62 },
  legend: { flexDirection: "row", gap: 16, paddingLeft: 62, flexWrap: "wrap" },
  legendItem: { flexDirection: "row", alignItems: "center", gap: 6 },
  swatch: { width: 10, height: 3, borderRadius: 2 },
  legendText: { fontSize: 11, color: insightsLight.text.muted, fontWeight: "600" }
});
