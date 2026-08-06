import { useEffect, useRef } from "react";
import { Animated, Easing, Image, StyleSheet, Text, View } from "react-native";
import { colors } from "../../theme/colors";
import { logiNexus } from "../../theme/logiNexus";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";
import { createThemedStyles } from "../../theme/themedStyles";

// Official PulseSoc brand lockup (real asset, symbol + wordmark), pre-processed to
// a transparent background so it blends into the login environment with no image
// boundary. Source: src/assets/brand/pulsesoc-mark.png.
const PULSESOC_LOGO = require("../../assets/brand/pulsesoc-mark.png");
const LOGO_ASPECT = 512 / 362;

const RING_COUNT = 3;
const RING_DURATION = 2600;
const RING_STAGGER = 850;

const LOGO_WIDTH = 244;
const LOGO_HEIGHT = LOGO_WIDTH / LOGO_ASPECT;
// The pulse symbol sits above the wordmark, so shift the ambient glow and signal
// rings up from the lockup's center onto the symbol — the light then reads as
// emanating from the mark rather than the text.
const SYMBOL_OFFSET_Y = LOGO_HEIGHT * 0.4 - LOGO_HEIGHT / 2;
const RING_SIZE = 148;

/**
 * PulseSoc brand mark for the login screen. Renders the real transparent logo
 * asset centered over slow pulse rings and tiny particles drawn in the logo's
 * own colors. The logo itself is never scaled, cropped, blurred, or backed by
 * a filled disc.
 */
export function PulseSocBrandHeader({ compact = false }: { compact?: boolean }) {
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

  const glowOpacity = breathe.interpolate({ inputRange: [0, 1], outputRange: [0.28, 0.6] });

  return (
    <View
      style={styles.root}
      accessible
      accessibilityRole="header"
      accessibilityLabel="PulseSoc. Connected."
      testID="pulse-gate-brand"
    >
      <View style={[styles.markWrap, compact && styles.markWrapCompact]}>
        <View style={styles.decorLayer} pointerEvents="none">
          {rings.map((value, index) => {
            const scale = value.interpolate({ inputRange: [0, 1], outputRange: [0.7, 1.7] });
            const opacity = value.interpolate({ inputRange: [0, 1], outputRange: [0.4, 0] });
            return (
              <Animated.View
                key={index}
                style={[
                  styles.ring,
                  {
                    borderColor: index % 2 === 0 ? colors.accent : colors.accentStrong,
                    opacity: reducedMotion ? 0 : opacity,
                    transform: [{ translateY: SYMBOL_OFFSET_Y }, { scale: reducedMotion ? 1 : scale }]
                  }
                ]}
              />
            );
          })}
          <Animated.View style={[styles.particle, styles.particleA, { opacity: reducedMotion ? 0.18 : glowOpacity }]} />
          <Animated.View style={[styles.particle, styles.particleB, { opacity: reducedMotion ? 0.12 : glowOpacity }]} />
          <Animated.View style={[styles.particle, styles.particleC, { opacity: reducedMotion ? 0.16 : glowOpacity }]} />
        </View>

        <Image
          source={PULSESOC_LOGO}
          style={[styles.logo, compact && styles.logoCompact]}
          resizeMode="contain"
          fadeDuration={0}
          accessible={false}
        />
      </View>

      <View style={styles.connection}>
        <View style={styles.connectionDot} />
        <Text style={styles.connectionLabel} maxFontSizeMultiplier={1.5}>Connected</Text>
      </View>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  root: {
    alignItems: "center",
    gap: logiNexus.spacing.xs
  },
  markWrap: {
    alignItems: "center",
    height: LOGO_HEIGHT,
    justifyContent: "center",
    marginBottom: logiNexus.spacing.sm,
    width: LOGO_WIDTH
  },
  markWrapCompact: {
    height: LOGO_HEIGHT * 0.82,
    width: LOGO_WIDTH * 0.82
  },
  decorLayer: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center"
  },
  ring: {
    borderRadius: logiNexus.radius.circular,
    borderWidth: 1.5,
    height: RING_SIZE,
    position: "absolute",
    width: RING_SIZE
  },
  logo: {
    height: LOGO_HEIGHT,
    width: LOGO_WIDTH
  },
  logoCompact: {
    height: LOGO_HEIGHT * 0.82,
    width: LOGO_WIDTH * 0.82
  },
  particle: {
    backgroundColor: colors.accentStrong,
    borderRadius: 3,
    height: 3,
    position: "absolute",
    shadowColor: colors.accentStrong,
    shadowOpacity: 0.7,
    shadowRadius: 5,
    width: 3
  },
  particleA: { left: 34, top: 22 },
  particleB: { right: 28, top: 70 },
  particleC: { bottom: 22, left: 70 },
  connection: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    marginTop: -2
  },
  connectionDot: {
    backgroundColor: colors.accent,
    borderRadius: 5,
    height: 9,
    shadowColor: colors.accent,
    shadowOpacity: 0.9,
    shadowRadius: 7,
    width: 9
  },
  connectionLabel: {
    color: colors.accent,
    fontSize: 14,
    fontWeight: "700",
    letterSpacing: 0.2
  }
}));
