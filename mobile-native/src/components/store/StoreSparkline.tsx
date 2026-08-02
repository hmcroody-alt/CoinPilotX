/**
 * Seven-day sales sparkline for the KPI card.
 *
 * Draws left to right once, using SVG `strokeDashoffset`. That property has no
 * native-driver equivalent, so this is the one JS-driven animation on the
 * screen — which is why it runs once on data arrival rather than looping.
 * Under reduce-motion the line is simply drawn complete.
 *
 * A flat series is drawn as a flat line at mid-height rather than being scaled
 * to fill the box: a store with the same sales every day should not look like a
 * store with wild swings, and dividing by a zero range would do exactly that.
 */

import { useMemo } from "react";
import { StyleSheet, View } from "react-native";
import { Animated } from "react-native";
import Svg, { Path } from "react-native-svg";
import { storeLight } from "../../theme/storeLight";
import { useStoreDraw } from "../../theme/storeMotion";

const AnimatedPath = Animated.createAnimatedComponent(Path);

export type StoreSparklineProps = {
  /** Daily totals, oldest first. Any length; 7 is what the KPI card passes. */
  values: number[];
  width?: number;
  height?: number;
  color?: string;
  reducedMotion: boolean;
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
      const y = range === 0 ? 1 + usable / 2 : 1 + usable - ((value - min) / range) * usable;
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
}

export function StoreSparkline({
  values,
  width = 76,
  height = 24,
  color = storeLight.status.success,
  reducedMotion
}: StoreSparklineProps) {
  const path = useMemo(() => buildPath(values, width, height), [height, values, width]);
  // Redraw when the series itself changes — so the line animates when real data
  // replaces the skeleton, and not on every unrelated re-render.
  const progress = useStoreDraw(reducedMotion, path);

  if (!path) return <View style={[styles.placeholder, { width, height }]} />;

  // Generous over-estimate of the path length. Exact length would need
  // `getTotalLength`, which is not available on the RN SVG node; overshooting
  // only means the line starts fully hidden, which is what we want.
  const length = width * values.length;

  return (
    <Svg width={width} height={height} accessibilityElementsHidden importantForAccessibility="no">
      <AnimatedPath
        d={path}
        stroke={color}
        strokeWidth={1.75}
        strokeLinecap="round"
        strokeLinejoin="round"
        fill="none"
        strokeDasharray={length}
        strokeDashoffset={progress.interpolate({
          inputRange: [0, 1],
          outputRange: [length, 0]
        })}
      />
    </Svg>
  );
}

const styles = StyleSheet.create({
  placeholder: { backgroundColor: storeLight.bg.skeleton, borderRadius: 4 }
});
