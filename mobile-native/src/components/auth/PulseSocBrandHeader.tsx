import { useEffect, useRef } from "react";
import { Animated, Easing, StyleSheet, Text, View } from "react-native";
import { colors } from "../../theme/colors";
import { logiNexus } from "../../theme/logiNexus";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";

const pulseSocLogo = require("../../assets/brand/pulsesoc-logo.png");

const RING_COUNT = 3;
const RING_DURATION = 2400;
const RING_STAGGER = 800;

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

  const logoScale = breathe.interpolate({ inputRange: [0, 1], outputRange: [1, 1.045] });

  return (
    <View style={styles.root} accessible accessibilityRole="header" accessibilityLabel="PulseSoc, Native Access. Your network is ready.">
      <View style={styles.markWrap}>
        {rings.map((value, index) => {
          const scale = value.interpolate({ inputRange: [0, 1], outputRange: [0.8, 1.6] });
          const opacity = value.interpolate({ inputRange: [0, 1], outputRange: [0.55, 0] });
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
        <Animated.Image
          source={pulseSocLogo}
          style={[styles.mark, { transform: [{ scale: reducedMotion ? 1 : logoScale }] }]}
          resizeMode="contain"
          accessibilityIgnoresInvertColors
        />
      </View>
      <Text style={styles.eyebrow} maxFontSizeMultiplier={1.8}>
        Native Access
      </Text>
      <Text style={styles.tagline} maxFontSizeMultiplier={1.8}>
        Your network is ready.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    alignItems: "center",
    gap: logiNexus.spacing.sm
  },
  markWrap: {
    alignItems: "center",
    height: 168,
    justifyContent: "center",
    marginBottom: logiNexus.spacing.xs,
    width: 168
  },
  ring: {
    borderRadius: 999,
    borderWidth: 1.5,
    height: 168,
    position: "absolute",
    width: 168
  },
  mark: {
    borderRadius: logiNexus.radius.large,
    height: 168,
    shadowColor: colors.accentStrong,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 0.35,
    shadowRadius: 20,
    width: 168
  },
  eyebrow: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 3,
    textTransform: "uppercase"
  },
  tagline: {
    color: colors.muted,
    fontSize: 14,
    fontWeight: "600"
  }
});
