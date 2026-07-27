import { useEffect, useRef } from "react";
import { Animated, StyleSheet, Text, View } from "react-native";
import { colors } from "../../../theme/colors";
import { logiNexus } from "../../../theme/logiNexus";
import { useLogiNexusReducedMotion } from "../../../theme/logiNexusMotion";

/**
 * Compact segmented progress line for the multi-step signup. Each segment fills
 * with the PulseSoc accent as the user advances. Announces progress to screen
 * readers ("Step 2 of 3, Secure your account") and animates width changes
 * without shifting surrounding layout.
 */
export function SignupProgress({ steps, currentIndex }: { steps: string[]; currentIndex: number }) {
  const total = steps.length;
  const clamped = Math.max(0, Math.min(currentIndex, total - 1));
  const label = steps[clamped] ?? "";

  return (
    <View
      style={styles.root}
      accessibilityRole="progressbar"
      accessibilityLabel={`Step ${clamped + 1} of ${total}, ${label}`}
      accessibilityValue={{ min: 1, max: total, now: clamped + 1 }}
    >
      <View style={styles.track}>
        {steps.map((_, index) => (
          <Segment key={index} active={index <= clamped} />
        ))}
      </View>
      <View style={styles.labelRow}>
        <Text style={styles.step} maxFontSizeMultiplier={1.4}>
          Step {clamped + 1} of {total}
        </Text>
        <Text style={styles.current} numberOfLines={1} maxFontSizeMultiplier={1.4}>
          {label}
        </Text>
      </View>
    </View>
  );
}

function Segment({ active }: { active: boolean }) {
  const reducedMotion = useLogiNexusReducedMotion();
  const fill = useRef(new Animated.Value(active ? 1 : 0)).current;

  useEffect(() => {
    if (reducedMotion) {
      fill.setValue(active ? 1 : 0);
      return;
    }
    Animated.timing(fill, {
      toValue: active ? 1 : 0,
      duration: logiNexus.motion.standard,
      useNativeDriver: false
    }).start();
  }, [active, fill, reducedMotion]);

  const width = fill.interpolate({ inputRange: [0, 1], outputRange: ["0%", "100%"] });
  return (
    <View style={styles.segment}>
      <Animated.View style={[styles.segmentFill, { width }]} />
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    gap: logiNexus.spacing.xs,
    width: "100%"
  },
  track: {
    flexDirection: "row",
    gap: 6
  },
  segment: {
    backgroundColor: colors.border,
    borderRadius: 999,
    flex: 1,
    height: 4,
    overflow: "hidden"
  },
  segmentFill: {
    backgroundColor: colors.accent,
    borderRadius: 999,
    height: "100%"
  },
  labelRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between"
  },
  step: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800"
  },
  current: {
    color: colors.accentStrong,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0.4
  }
});
