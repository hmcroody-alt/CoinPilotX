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

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { PanResponder, StyleSheet, View } from "react-native";
import Svg, { Circle, Line, Path } from "react-native-svg";
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
 * Same maths, drawn into a caller-sized box. There is deliberately no axis and
 * no grid: this shows the shape of the move, and the labelled numbers live in
 * the price header and the metrics grid above and below it.
 *
 * ## Scrubbing
 *
 * Passing `onScrub` turns the chart into a read of the series it was already
 * given. A drag maps the finger's x to the nearest index and reports that
 * point; releasing reports `null`. **No fetch happens on any of this** — the
 * whole series is in `values` before the first touch, so there is nothing left
 * to ask for, and a chart that re-requested data per drag frame would put a
 * provider call behind every finger movement.
 *
 * Without `onScrub` the chart is exactly what it was: a path, no responder, no
 * touch handling at all.
 */
export function AssetPriceChart({
  values,
  width,
  height = 180,
  color = colors.accent,
  onScrub
}: AssetSparklineProps & {
  width: number;
  /** Called with the index under the finger, or `null` when the touch ends. */
  onScrub?: (index: number | null) => void;
}) {
  const path = useMemo(() => buildPath(values, width, height), [values, width, height]);
  const [scrubX, setScrubX] = useState<number | null>(null);

  // Held in a ref so the responder below is not rebuilt on every parent render
  // just because the callback identity changed.
  const onScrubRef = useRef(onScrub);
  onScrubRef.current = onScrub;

  // A shorter series after a range change would otherwise leave the crosshair
  // pointing at an index that no longer exists.
  useEffect(() => {
    setScrubX(null);
    onScrubRef.current?.(null);
  }, [values.length]);

  const report = useCallback(
    (x: number | null) => {
      if (x === null || values.length < 2) {
        setScrubX(null);
        onScrubRef.current?.(null);
        return;
      }
      const stepX = width / (values.length - 1);
      const index = Math.min(values.length - 1, Math.max(0, Math.round(x / stepX)));
      setScrubX(index * stepX);
      onScrubRef.current?.(index);
    },
    [values.length, width]
  );

  const responder = useMemo(
    () =>
      onScrub
        ? PanResponder.create({
            onStartShouldSetPanResponder: () => true,
            onMoveShouldSetPanResponder: () => true,
            onPanResponderGrant: (event) => report(event.nativeEvent.locationX),
            onPanResponderMove: (event) => report(event.nativeEvent.locationX),
            onPanResponderRelease: () => report(null),
            onPanResponderTerminate: () => report(null)
          })
        : null,
    [onScrub, report]
  );

  if (!path) return <View style={[styles.chartPlaceholder, { width, height }]} />;

  const marker =
    scrubX === null || values.length < 2
      ? null
      : (() => {
          const index = Math.min(values.length - 1, Math.max(0, Math.round(scrubX / (width / (values.length - 1)))));
          const value = values[index];
          if (value === undefined) return null;
          const min = Math.min(...values);
          const max = Math.max(...values);
          const range = max - min;
          const usable = height - 2;
          const y = range === 0 ? 1 + usable / 2 : 1 + usable - ((value - min) / range) * usable;
          return { x: scrubX, y };
        })();

  return (
    <View {...(responder ? responder.panHandlers : {})} style={{ width, height }}>
      <Svg width={width} height={height}>
        <Path d={path} stroke={color} strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" fill="none" />
        {marker ? (
          <>
            <Line x1={marker.x} y1={0} x2={marker.x} y2={height} stroke={colors.border} strokeWidth={1} />
            <Circle cx={marker.x} cy={marker.y} r={4} fill={color} />
          </>
        ) : null}
      </Svg>
    </View>
  );
}

const styles = StyleSheet.create({
  chartPlaceholder: {
    backgroundColor: colors.surface,
    borderRadius: 10
  }
});
