import { Pressable, StyleSheet } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";

/**
 * Invisible safe-area strip shown only while the dock is immersively hidden.
 * A tap (or an upward edge swipe, which lands as a press on this strip) brings
 * the navigation back. Deliberately unstyled: it must not read as UI, only as
 * a recovery affordance sitting where the dock was.
 */
export function ImmersiveRevealStrip({ visible, onReveal }: { visible: boolean; onReveal: () => void }) {
  const insets = useSafeAreaInsets();
  if (!visible) return null;
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel="Show navigation"
      testID="immersive-reveal-strip"
      onPress={onReveal}
      style={[styles.strip, { height: Math.max(insets.bottom, 12) + 20 }]}
    />
  );
}

const styles = StyleSheet.create({
  strip: {
    position: "absolute",
    left: 0,
    right: 0,
    bottom: 0
  }
});
