/**
 * The 7-day price line on a watchlist row.
 *
 * ## Why not `StoreSparkline`
 *
 * The store one animates its draw with `strokeDashoffset`, which has no native
 * driver and therefore runs on the JS thread. That is fine for the four KPI
 * cards it was built for and wrong for a watchlist, where fifty rows would put
 * fifty JS-driven animations on the same thread the user is scrolling with. It
 * is also bound to the store's light theme. This is the same twenty lines of
 * path maths without either property.
 *
 * ## Why an empty series renders nothing rather than a flat line
 *
 * A horizontal line is a claim: "this price did not move". An asset the
 * provider could not price has made no such claim, and the row already says
 * "--" in the price column. Drawing a line beside that would contradict it.
 */

import { useMemo } from "react";
import { StyleSheet, View } from "react-native";
import Svg, { Path } from "react-native-svg";
import { colors } from "../../theme/colors";

export type AssetSparklineProps = {
  /** Prices oldest-first. The server caps the length; any length renders. */
  values: number[];
  width?: number;
  height?: number;
  /** Usually derived from the 24h change, so the line agrees with the number. */
  color?: string;
};

function buildPath(values: number[], width: number, height: number): string {
  if (values.length < 2) return "";
  const min = Math.min(...values);
  const max = Math.max(...values);
  const range = max - min;
  const stepX = width / (values.length - 1);
  // 1px inset top and bottom so the stroke is not clipped at the extremes.
  const usable = height - 2;

  return values
    .map((value, index) => {
      const x = index * stepX;
      // A genuinely flat series sits at mid-height. Scaling it to fill the box
      // would turn "did not move" into a dramatic shape.
      const y = range === 0 ? 1 + usable / 2 : 1 + usable - ((value - min) / range) * usable;
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

export function AssetSparkline({ values, width = 64, height = 24, color = colors.accent }: AssetSparklineProps) {
  const path = useMemo(() => buildPath(values, width, height), [values, width, height]);

  if (!path) return <View style={{ width, height }} testID="asset-sparkline-empty" />;

  return (
    <Svg width={width} height={height} accessibilityElementsHidden importantForAccessibility="no">
      <Path d={path} stroke={color} strokeWidth={1.5} strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </Svg>
  );
}

/**
 * The larger line on the asset detail screen.
 *
 * Same maths, drawn into a caller-sized box. There is deliberately no axis, no
 * grid and no tooltip: this build shows the shape of the move, and the exact
 * numbers live in the price header and the metrics grid above and below it,
 * where they are labelled.
 */
export function AssetPriceChart({
  values,
  width,
  height = 180,
  color = colors.accent
}: AssetSparklineProps & { width: number }) {
  const path = useMemo(() => buildPath(values, width, height), [values, width, height]);

  if (!path) return <View style={[styles.chartPlaceholder, { width, height }]} />;

  return (
    <Svg width={width} height={height}>
      <Path d={path} stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" fill="none" />
    </Svg>
  );
}

const styles = StyleSheet.create({
  chartPlaceholder: {
    backgroundColor: colors.surface,
    borderRadius: 10
  }
});
