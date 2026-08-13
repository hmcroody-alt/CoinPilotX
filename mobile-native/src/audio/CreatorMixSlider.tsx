import { useCallback, useMemo, useRef, useState } from "react";
import { I18nManager, LayoutChangeEvent, PanResponder, StyleSheet, Text, View } from "react-native";

import { colors } from "../theme/colors";

/**
 * A fader.
 *
 * Built on PanResponder against React Native core rather than pulling in
 * `@react-native-community/slider`: the app does not ship that dependency, and
 * adding a native module for one control would force a rebuild of both dev
 * clients and put a new package inside the audio dependency watch.
 *
 * Emits continuously while dragging. That is required here rather than merely
 * nice — the music preview follows the fader in real time, and a control that
 * only reported on release would make a creator set a level they never heard.
 */
export function CreatorMixSlider({
  label,
  value,
  minimumValue,
  maximumValue,
  step,
  onChange,
  onSlidingComplete,
  formatValue,
  disabled = false,
  tint = colors.accent,
  testID
}: {
  label: string;
  value: number;
  minimumValue: number;
  maximumValue: number;
  step: number;
  onChange: (next: number) => void;
  onSlidingComplete?: (next: number) => void;
  formatValue?: (value: number) => string;
  disabled?: boolean;
  tint?: string;
  testID?: string;
}) {
  const [trackWidth, setTrackWidth] = useState(0);
  const widthRef = useRef(0);
  const valueRef = useRef(value);
  const lastEmitted = useRef(value);
  valueRef.current = value;

  const range = Math.max(maximumValue - minimumValue, 0.0001);
  const ratio = Math.min(1, Math.max(0, (value - minimumValue) / range));

  const quantize = useCallback(
    (raw: number) => {
      const clamped = Math.min(maximumValue, Math.max(minimumValue, raw));
      const steps = Math.round((clamped - minimumValue) / step);
      return Number((minimumValue + steps * step).toFixed(4));
    },
    [maximumValue, minimumValue, step]
  );

  const emit = useCallback(
    (raw: number, final: boolean) => {
      const next = quantize(raw);
      if (next !== lastEmitted.current) {
        lastEmitted.current = next;
        onChange(next);
      }
      if (final) onSlidingComplete?.(next);
    },
    [onChange, onSlidingComplete, quantize]
  );

  const positionToValue = useCallback(
    (x: number) => {
      const width = widthRef.current;
      if (width <= 0) return valueRef.current;
      const clampedX = Math.min(width, Math.max(0, I18nManager.isRTL ? width - x : x));
      return minimumValue + (clampedX / width) * range;
    },
    [minimumValue, range]
  );

  const panResponder = useMemo(
    () =>
      PanResponder.create({
        onStartShouldSetPanResponder: () => !disabled,
        onMoveShouldSetPanResponder: () => !disabled,
        // Hold the gesture: this control lives inside a scrolling sheet, and a
        // vertical wobble mid-drag would otherwise hand the touch to the scroll
        // view and leave the fader wherever it happened to be.
        onPanResponderTerminationRequest: () => false,
        onPanResponderGrant: (event) => emit(positionToValue(event.nativeEvent.locationX), false),
        onPanResponderMove: (event, gesture) => {
          const width = widthRef.current;
          if (width <= 0) return;
          const startX = (I18nManager.isRTL ? 1 - ratio : ratio) * width;
          emit(positionToValue(startX + gesture.dx), false);
        },
        onPanResponderRelease: () => emit(valueRef.current, true),
        onPanResponderTerminate: () => emit(valueRef.current, true)
      }),
    [disabled, emit, positionToValue, ratio]
  );

  const onLayout = useCallback((event: LayoutChangeEvent) => {
    const width = event.nativeEvent.layout.width;
    widthRef.current = width;
    setTrackWidth(width);
  }, []);

  const display = formatValue ? formatValue(value) : String(value);

  return (
    <View style={styles.container}>
      <View style={styles.header}>
        <Text style={styles.label}>{label}</Text>
        <Text style={[styles.readout, { color: disabled ? colors.disabled : tint }]}>{display}</Text>
      </View>
      <View
        testID={testID}
        accessible
        accessibilityRole="adjustable"
        accessibilityLabel={label}
        accessibilityValue={{ min: minimumValue, max: maximumValue, now: value, text: display }}
        accessibilityState={{ disabled }}
        accessibilityActions={[{ name: "increment" }, { name: "decrement" }]}
        onAccessibilityAction={(event) => {
          if (disabled) return;
          emit(value + (event.nativeEvent.actionName === "increment" ? step : -step), true);
        }}
        onLayout={onLayout}
        style={styles.touchArea}
        {...panResponder.panHandlers}
      >
        <View style={[styles.track, disabled && styles.trackDisabled]}>
          <View style={[styles.fill, { width: Math.max(0, ratio * trackWidth), backgroundColor: disabled ? colors.disabled : tint }]} />
        </View>
        <View
          pointerEvents="none"
          style={[styles.thumb, { left: Math.max(0, ratio * trackWidth - 11), borderColor: disabled ? colors.disabled : tint }]}
        />
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    paddingVertical: 8
  },
  fill: {
    borderRadius: 3,
    height: 6
  },
  header: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 8
  },
  label: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "600"
  },
  readout: {
    fontSize: 14,
    fontVariant: ["tabular-nums"],
    fontWeight: "700"
  },
  thumb: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 11,
    borderWidth: 3,
    height: 22,
    position: "absolute",
    top: 6,
    width: 22
  },
  touchArea: {
    justifyContent: "center",
    minHeight: 34
  },
  track: {
    backgroundColor: colors.border,
    borderRadius: 3,
    height: 6,
    overflow: "hidden"
  },
  trackDisabled: {
    opacity: 0.5
  }
});
