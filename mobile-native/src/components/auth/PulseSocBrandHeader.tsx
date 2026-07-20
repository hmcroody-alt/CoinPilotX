import { useEffect, useRef } from "react";
import { Animated, Easing, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { colors } from "../../theme/colors";
import { logiNexus } from "../../theme/logiNexus";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";

const RING_COUNT = 3;
const RING_DURATION = 2400;
const RING_STAGGER = 800;

/**
 * PulseSoc brand mark, drawn entirely in code so it renders transparent, stays
 * razor-sharp at every iPhone size, and blends into the login environment via
 * glow rather than sitting on an opaque tile. No baked-in website text or
 * slogan raster — only the recognizable pulse symbol and the PulseSoc wordmark.
 */
export function PulseSocBrandHeader() {
  const reducedMotion = useLogiNexusReducedMotion();
  const rings = useRef(Array.from({ length: RING_COUNT }, () => new Animated.Value(0))).current;
  const breathe = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (reducedMotion) return;

    const ringLoop = (value: Animated.Value, delay: number) =>
      Animated.loop(
        Animated.sequence([
          Animated.delay(delay),
          Animated.timing(value, {
            toValue: 1,
            duration: RING_DURATION,
            easing: Easing.out(Easing.quad),
            useNativeDriver: true
          }),
          Animated.timing(value, { toValue: 0, duration: 0, useNativeDriver: true })
        ])
      );

    const breatheLoop = Animated.loop(
      Animated.sequence([
        Animated.timing(breathe, {
          toValue: 1,
          duration: logiNexus.motion.ambient,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true
        }),
        Animated.timing(breathe, {
          toValue: 0,
          duration: logiNexus.motion.ambient,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true
        })
      ])
    );

    const running = [...rings.map((value, index) => ringLoop(value, index * RING_STAGGER)), breatheLoop];
    running.forEach((animation) => animation.start());
    return () => running.forEach((animation) => animation.stop());
  }, [reducedMotion, rings, breathe]);

  const glyphScale = breathe.interpolate({ inputRange: [0, 1], outputRange: [1, 1.08] });
  const glowOpacity = breathe.interpolate({ inputRange: [0, 1], outputRange: [0.25, 0.6] });

  return (
    <View style={styles.root} accessible accessibilityRole="header" accessibilityLabel="PulseSoc. Native Access. Your network is ready.">
      <View style={styles.markWrap}>
        {rings.map((value, index) => {
          const scale = value.interpolate({ inputRange: [0, 1], outputRange: [0.75, 1.65] });
          const opacity = value.interpolate({ inputRange: [0, 1], outputRange: [0.5, 0] });
          return (
            <Animated.View
              key={index}
              pointerEvents="none"
              style={[
                styles.ring,
                {
                  borderColor: index % 2 === 0 ? colors.accent : colors.accentStrong,
                  opacity: reducedMotion ? 0 : opacity,
                  transform: [{ scale: reducedMotion ? 1 : scale }]
                }
              ]}
            />
          );
        })}
        <Animated.View pointerEvents="none" style={[styles.glow, { opacity: reducedMotion ? 0.3 : glowOpacity }]} />
        <View style={styles.coreDisc}>
          <Animated.View style={{ transform: [{ scale: reducedMotion ? 1 : glyphScale }] }}>
            <Ionicons name="pulse" size={54} color={colors.accent} style={styles.glyph} />
          </Animated.View>
        </View>
      </View>

      <Text style={styles.wordmark} maxFontSizeMultiplier={1.4} allowFontScaling>
        <Text style={styles.wordmarkPrimary}>Pulse</Text>
        <Text style={styles.wordmarkAccent}>Soc</Text>
      </Text>
      <Text style={styles.eyebrow} maxFontSizeMultiplier={1.8}>
        Native Access
      </Text>
      <Text style={styles.tagline} maxFontSizeMultiplier={1.8}>
        Your network is ready.
      </Text>
    </View>
  );
}

const MARK_SIZE = 148;

const styles = StyleSheet.create({
  root: {
    alignItems: "center",
    gap: logiNexus.spacing.xs
  },
  markWrap: {
    alignItems: "center",
    height: MARK_SIZE,
    justifyContent: "center",
    marginBottom: logiNexus.spacing.sm,
    width: MARK_SIZE
  },
  ring: {
    borderRadius: logiNexus.radius.circular,
    borderWidth: 1.5,
    height: MARK_SIZE,
    position: "absolute",
    width: MARK_SIZE
  },
  glow: {
    backgroundColor: colors.accent,
    borderRadius: logiNexus.radius.circular,
    height: 96,
    position: "absolute",
    shadowColor: colors.accent,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 1,
    shadowRadius: 34,
    width: 96
  },
  coreDisc: {
    alignItems: "center",
    borderColor: colors.accentStrong,
    borderRadius: logiNexus.radius.circular,
    borderWidth: StyleSheet.hairlineWidth,
    height: 92,
    justifyContent: "center",
    width: 92
  },
  glyph: {
    shadowColor: colors.accent,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.8,
    shadowRadius: 12
  },
  wordmark: {
    ...logiNexus.typography.display,
    letterSpacing: 0.5,
    textAlign: "center"
  },
  wordmarkPrimary: {
    color: colors.text
  },
  wordmarkAccent: {
    color: colors.accent
  },
  eyebrow: {
    color: colors.accentStrong,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 3,
    marginTop: logiNexus.spacing.xs,
    textTransform: "uppercase"
  },
  tagline: {
    color: colors.muted,
    fontSize: 14,
    fontWeight: "600"
  }
});
