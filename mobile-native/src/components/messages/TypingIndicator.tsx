/**
 * The typing indicator that replaces a row's snippet while the buyer is typing.
 * Three dots pulse on a 1.2s cycle, staggered 150ms apart, in the same green as
 * presence and reply speed ("someone is here, now").
 *
 * Under reduce-motion the animation is not scheduled and the row shows a static
 * "typing…" label instead — the fact is still conveyed in text, never by motion
 * or colour alone (assistive tech reads `accessibilityLabel="typing"` either way).
 */

import { useEffect, useRef } from "react";
import { Animated, StyleSheet, Text, View } from "react-native";
import { messagesLight } from "../../theme/messagesLight";
import { useAppForegrounded } from "../../theme/storeMotion";

const CYCLE_MS = 1200;
const STAGGER_MS = 150;
const DOTS = [0, 1, 2];

export function TypingIndicator({ reducedMotion }: { reducedMotion: boolean }) {
  const values = useRef(DOTS.map(() => new Animated.Value(0))).current;
  const foreground = useAppForegrounded();

  useEffect(() => {
    if (reducedMotion || !foreground) {
      values.forEach((v) => v.setValue(0));
      return;
    }
    const loops = values.map((value, i) =>
      Animated.loop(
        Animated.sequence([
          Animated.delay(i * STAGGER_MS),
          Animated.timing(value, { toValue: 1, duration: CYCLE_MS / 2, useNativeDriver: true }),
          Animated.timing(value, { toValue: 0, duration: CYCLE_MS / 2, useNativeDriver: true })
        ])
      )
    );
    loops.forEach((l) => l.start());
    return () => loops.forEach((l) => l.stop());
  }, [values, reducedMotion, foreground]);

  if (reducedMotion) {
    return (
      <Text style={styles.static} accessibilityLabel="typing">
        typing…
      </Text>
    );
  }

  return (
    <View style={styles.row} accessibilityLabel="typing">
      {values.map((value, i) => (
        <Animated.View
          key={i}
          style={[
            styles.dot,
            {
              opacity: value.interpolate({ inputRange: [0, 1], outputRange: [0.35, 1] }),
              transform: [
                { translateY: value.interpolate({ inputRange: [0, 1], outputRange: [0, -2] }) }
              ]
            }
          ]}
        />
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", alignItems: "center", gap: 4, height: 18 },
  dot: {
    width: 6,
    height: 6,
    borderRadius: 3,
    backgroundColor: messagesLight.typing.dot
  },
  static: {
    fontSize: 13,
    fontStyle: "italic",
    color: messagesLight.typing.dot,
    fontWeight: "600"
  }
});
