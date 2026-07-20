import { forwardRef, useCallback, useImperativeHandle, useRef, useState } from "react";
import { Animated, StyleSheet, Text, View } from "react-native";
import { colors } from "../theme/colors";

export type LikeBurstHandle = {
  trigger: (x: number, y: number) => void;
};

type BurstInstance = {
  id: number;
  x: number;
  y: number;
  progress: Animated.Value;
};

let burstSeq = 0;

/**
 * Floating heart burst rendered wherever the user double-taps to like a
 * media surface (Reels, feed video, statuses, mixed-media posts). Pure
 * React Native `Animated` API with `useNativeDriver: true` — no reanimated,
 * so it runs on the native thread without requiring the New Architecture.
 * Mount once per media card/viewer, overlaying the tap layer, and drive it
 * imperatively via ref so rapid double-taps don't thrash component state.
 */
export const LikeBurst = forwardRef<LikeBurstHandle>(function LikeBurst(_props, ref) {
  const [bursts, setBursts] = useState<BurstInstance[]>([]);

  const trigger = useCallback((x: number, y: number) => {
    const id = burstSeq++;
    const progress = new Animated.Value(0);
    setBursts((current) => [...current, { id, x, y, progress }]);
    Animated.timing(progress, {
      toValue: 1,
      duration: 620,
      useNativeDriver: true
    }).start(() => {
      setBursts((current) => current.filter((burst) => burst.id !== id));
    });
  }, []);

  useImperativeHandle(ref, () => ({ trigger }), [trigger]);

  if (!bursts.length) return null;

  return (
    <View style={styles.overlay} pointerEvents="none">
      {bursts.map((burst) => {
        const scale = burst.progress.interpolate({ inputRange: [0, 0.28, 1], outputRange: [0.3, 1.35, 1.05] });
        const opacity = burst.progress.interpolate({ inputRange: [0, 0.1, 0.7, 1], outputRange: [0, 1, 1, 0] });
        const translateY = burst.progress.interpolate({ inputRange: [0, 1], outputRange: [0, -46] });
        return (
          <Animated.View
            key={burst.id}
            pointerEvents="none"
            style={[styles.burst, { left: burst.x - 34, top: burst.y - 34, opacity, transform: [{ scale }, { translateY }] }]}
          >
            <Text style={styles.burstGlyph}>♥</Text>
          </Animated.View>
        );
      })}
    </View>
  );
});

export type MuteGlyphPulseHandle = {
  trigger: (muted: boolean) => void;
};

/**
 * Center-screen glyph pulse shown on every single-tap mute/unmute toggle —
 * gives an immediate, premium confirmation of the new audio state without
 * blocking playback or requiring the user to look away from the content.
 */
export const MuteGlyphPulse = forwardRef<MuteGlyphPulseHandle>(function MuteGlyphPulse(_props, ref) {
  const [visible, setVisible] = useState(false);
  const [muted, setMuted] = useState(false);
  const scale = useRef(new Animated.Value(0.6)).current;
  const opacity = useRef(new Animated.Value(0)).current;
  const hideTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const trigger = useCallback((nextMuted: boolean) => {
    if (hideTimer.current) clearTimeout(hideTimer.current);
    setMuted(nextMuted);
    setVisible(true);
    scale.setValue(0.6);
    opacity.setValue(0);
    Animated.parallel([
      Animated.spring(scale, { toValue: 1, useNativeDriver: true, friction: 6, tension: 140 }),
      Animated.timing(opacity, { toValue: 1, duration: 120, useNativeDriver: true })
    ]).start(() => {
      hideTimer.current = setTimeout(() => {
        Animated.timing(opacity, { toValue: 0, duration: 220, useNativeDriver: true }).start(() => setVisible(false));
      }, 360);
    });
  }, [opacity, scale]);

  useImperativeHandle(ref, () => ({ trigger }), [trigger]);

  if (!visible) return null;

  return (
    <View style={styles.overlay} pointerEvents="none">
      <Animated.View style={[styles.glyphOrb, { opacity, transform: [{ scale }] }]}>
        <Text style={styles.glyphText}>{muted ? "⌁" : "◖))"}</Text>
      </Animated.View>
    </View>
  );
});

const styles = StyleSheet.create({
  burst: {
    alignItems: "center",
    height: 68,
    justifyContent: "center",
    position: "absolute",
    width: 68
  },
  burstGlyph: {
    color: colors.danger,
    fontSize: 46,
    textShadowColor: "rgba(0,0,0,0.55)",
    textShadowOffset: { width: 0, height: 2 },
    textShadowRadius: 8
  },
  glyphOrb: {
    alignItems: "center",
    alignSelf: "center",
    backgroundColor: "rgba(2,9,18,0.72)",
    borderColor: "rgba(97,216,255,0.42)",
    borderRadius: 40,
    borderWidth: 1,
    height: 80,
    justifyContent: "center",
    marginTop: "42%",
    width: 80
  },
  glyphText: {
    color: colors.accentStrong,
    fontSize: 30,
    fontWeight: "900"
  },
  overlay: {
    ...StyleSheet.absoluteFillObject,
    zIndex: 9
  }
});
