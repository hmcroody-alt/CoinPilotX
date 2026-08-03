/**
 * A fulfilment-health ring: a track, an arc, a percentage and a name.
 *
 * Colour states the band — green excellent, blue on track, gold needs attention
 * — but colour is never the only signal. The percentage is written in the
 * middle, the band is written into the accessibility label in words ("96
 * percent, excellent"), and the metric's name sits under the ring.
 *
 * **A ring with no data shows an em dash, not a zero.** "0% on-time dispatch" is
 * an accusation; "—" with "not enough orders yet" is the truth. `value` is
 * therefore `number | null` and the null case is a first-class render, not a
 * fallback.
 *
 * *No ring ships in this release.* All three metrics the design specifies —
 * on-time dispatch, replies under the threshold, offers answered — have no
 * backend source (see `INSIGHTS_MOCK_DATA_GAPS`). The component is built, tested
 * and ready so that the day a source exists this is a wiring change; the screen
 * renders the whole module only when the server stops naming those gaps.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import Svg, { Circle } from "react-native-svg";
import { insightsLight, ringBand, ringBandLabel } from "../../theme/insightsLight";
import { useInsightsRing } from "../../theme/insightsMotion";
import { useStorePress } from "../../theme/storeMotion";

const AnimatedCircle = Animated.createAnimatedComponent(Circle);

const SIZE = 68;
const STROKE = 7;
const RADIUS = (SIZE - STROKE) / 2;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;

export type HealthRingProps = {
  /** e.g. "On-time dispatch" — already localized. */
  label: string;
  /** 0–100, or `null` when there is not enough data to state a rate. */
  value: number | null;
  /** e.g. "96%" — already formatted. Ignored when `value` is null. */
  formattedValue: string;
  /** Why there is no number, e.g. "Not enough orders yet". */
  emptyReason?: string;
  /** Where tapping goes, named for the label: "Opens Orders". */
  destinationHint?: string;
  onPress?: () => void;
  delay?: number;
  animationKey: unknown;
  reducedMotion: boolean;
};

export function HealthRing({
  label,
  value,
  formattedValue,
  emptyReason,
  destinationHint,
  onPress,
  delay = 0,
  animationKey,
  reducedMotion
}: HealthRingProps) {
  const progress = useInsightsRing(reducedMotion, animationKey, delay);
  const press = useStorePress(reducedMotion, 0.96);

  const known = value !== null && Number.isFinite(value);
  const clamped = known ? Math.max(0, Math.min(value as number, 100)) : 0;
  const band = known ? ringBand(clamped) : null;
  const color = band ? insightsLight.ring[band] : insightsLight.ring.track;

  const accessibilityLabel = known
    ? `${label}. ${formattedValue}, ${ringBandLabel(clamped)}.${destinationHint ? ` ${destinationHint}.` : ""}`
    : `${label}. No figure yet.${emptyReason ? ` ${emptyReason}.` : ""}`;

  const body = (
    <View style={styles.item}>
      <View style={styles.ringBox} accessibilityElementsHidden importantForAccessibility="no">
        <Svg width={SIZE} height={SIZE}>
          <Circle
            cx={SIZE / 2}
            cy={SIZE / 2}
            r={RADIUS}
            stroke={insightsLight.ring.track}
            strokeWidth={STROKE}
            fill="none"
          />
          {known ? (
            <AnimatedCircle
              cx={SIZE / 2}
              cy={SIZE / 2}
              r={RADIUS}
              stroke={color}
              strokeWidth={STROKE}
              strokeLinecap="round"
              fill="none"
              strokeDasharray={CIRCUMFERENCE}
              strokeDashoffset={progress.interpolate({
                inputRange: [0, 1],
                outputRange: [CIRCUMFERENCE, CIRCUMFERENCE * (1 - clamped / 100)]
              })}
              // Start the arc at twelve o'clock rather than three.
              transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}
            />
          ) : null}
        </Svg>
        <View style={styles.center} pointerEvents="none">
          <Text style={[styles.value, known ? { color: color } : styles.valueEmpty]}>
            {known ? formattedValue : "—"}
          </Text>
        </View>
      </View>
      <Text style={styles.label} numberOfLines={2}>
        {label}
      </Text>
      {!known && emptyReason ? (
        <Text style={styles.reason} numberOfLines={2}>
          {emptyReason}
        </Text>
      ) : null}
    </View>
  );

  if (!onPress) {
    return (
      <View accessible accessibilityRole="text" accessibilityLabel={accessibilityLabel}>
        {body}
      </View>
    );
  }

  return (
    <Animated.View style={press.style}>
      <Pressable
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        accessibilityRole="button"
        accessibilityLabel={accessibilityLabel}
        hitSlop={6}
      >
        {body}
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  item: { alignItems: "center", gap: 6, minWidth: 92, minHeight: insightsLight.size.tapTarget },
  ringBox: { width: SIZE, height: SIZE, alignItems: "center", justifyContent: "center" },
  center: { ...StyleSheet.absoluteFillObject, alignItems: "center", justifyContent: "center" },
  value: { fontSize: 16, fontWeight: "800" },
  valueEmpty: { color: insightsLight.text.muted },
  label: {
    fontSize: 11,
    fontWeight: "700",
    color: insightsLight.text.primary,
    textAlign: "center"
  },
  reason: { fontSize: 10, color: insightsLight.text.muted, textAlign: "center" }
});
