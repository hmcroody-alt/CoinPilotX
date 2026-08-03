/**
 * The Today / 7 days / 30 days / 90 days segmented control under the header.
 *
 * It is the Advertising `ModeToggle` pattern widened to four segments: a
 * translucent light-on-dark track over the navy header, an absolutely positioned
 * thumb that slides under the active label, and a real tab group for assistive
 * technology. Reusing the pattern rather than inventing a fifth kind of
 * segmented control is the whole point — Reports will want this control next,
 * which is why it takes its options as a prop instead of hard-coding four.
 *
 * A disabled segment is the honest part. When a period cannot be served — no
 * cached copy while offline, or a backend that will not answer for that span —
 * the pill dims and carries the reason in its accessibility label rather than
 * silently returning the previous period's numbers under a new heading.
 */

import { useEffect, useRef } from "react";
import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { insightsLight } from "../../theme/insightsLight";
import { logiNexusMotion } from "../../theme/logiNexusMotion";
import type { InsightsPeriod } from "../../api/insightsDashboard";

export type PeriodOption = {
  key: InsightsPeriod;
  label: string;
  /** Present means unavailable; the text says why, out loud. */
  disabledReason?: string;
};

export type PeriodPickerProps = {
  options: PeriodOption[];
  value: InsightsPeriod;
  onChange: (next: InsightsPeriod) => void;
  reducedMotion: boolean;
};

export function PeriodPicker({ options, value, onChange, reducedMotion }: PeriodPickerProps) {
  const index = Math.max(0, options.findIndex((option) => option.key === value));
  const slide = useRef(new Animated.Value(index)).current;
  const count = Math.max(options.length, 1);

  useEffect(() => {
    if (reducedMotion) {
      slide.setValue(index);
      return;
    }
    Animated.timing(slide, {
      toValue: index,
      duration: 180,
      easing: logiNexusMotion.easing.standard,
      useNativeDriver: true
    }).start();
  }, [index, reducedMotion, slide]);

  return (
    <View style={styles.track} accessibilityRole="tablist">
      <Animated.View
        pointerEvents="none"
        style={[
          styles.thumb,
          {
            // Width is a fraction of the track, so the thumb travels exactly one
            // segment per step no matter how many segments Reports asks for.
            width: `${100 / count}%`,
            transform: [
              {
                translateX: slide.interpolate({
                  inputRange: options.map((_, position) => position),
                  outputRange: options.map((_, position) => `${position * 100}%`)
                })
              }
            ]
          }
        ]}
      />
      {options.map((option) => {
        const selected = option.key === value;
        const disabled = Boolean(option.disabledReason);
        return (
          <Pressable
            key={option.key}
            style={styles.segment}
            onPress={() => !disabled && onChange(option.key)}
            disabled={disabled}
            accessibilityRole="tab"
            accessibilityState={{ selected, disabled }}
            accessibilityLabel={
              disabled ? `${option.label}, unavailable. ${option.disabledReason}` : option.label
            }
          >
            <Text
              numberOfLines={1}
              style={[
                styles.label,
                selected ? styles.labelSelected : null,
                disabled ? styles.labelDisabled : null
              ]}
            >
              {option.label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  track: {
    flexDirection: "row",
    height: 40,
    borderRadius: insightsLight.radius.pill,
    backgroundColor: "rgba(255,255,255,0.10)",
    padding: 3,
    position: "relative"
  },
  thumb: {
    position: "absolute",
    top: 3,
    left: 3,
    bottom: 3,
    borderRadius: insightsLight.radius.pill,
    backgroundColor: insightsLight.bg.card
  },
  segment: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    // 40pt of track plus the header padding above and below clears 44pt of
    // touchable height; the row is the tap target, not the pill's glyph.
    minHeight: 34
  },
  label: { fontSize: 13, fontWeight: "700", color: insightsLight.text.onDarkMuted },
  labelSelected: { fontWeight: "800", color: insightsLight.text.primary },
  labelDisabled: { opacity: 0.35 }
});
