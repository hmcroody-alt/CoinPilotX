import { Ionicons } from "@expo/vector-icons";
import { useEffect, useRef } from "react";
import { Animated, Easing, Image, Pressable, StyleSheet, Text, View } from "react-native";
import { colors } from "../../../theme/colors";
import { logiNexus } from "../../../theme/logiNexus";
import { useLogiNexusReducedMotion } from "../../../theme/logiNexusMotion";
import { createThemedStyles } from "../../../theme/themedStyles";

// Same official brand asset the login screen uses (transparent, no image
// boundary). Documented path: src/assets/brand/pulsesoc-mark.png.
const PULSESOC_LOGO = require("../../../assets/brand/pulsesoc-mark.png");
const LOGO_ASPECT = 512 / 362;

// Deliberately smaller than the login lockup (login uses 244) so the form is the
// primary focus while the brand still anchors the screen in PulseSoc auth.
const LOGO_WIDTH = 150;
const LOGO_HEIGHT = LOGO_WIDTH / LOGO_ASPECT;
const SYMBOL_OFFSET_Y = LOGO_HEIGHT * 0.4 - LOGO_HEIGHT / 2;
const RING_SIZE = 104;
const GLOW_SIZE = 68;

/**
 * Compact PulseSoc brand mark for the account-creation flow. Renders the real
 * logo over a restrained breathing glow + slow pulse rings drawn in the brand
 * colors, matching the login header's motion language at a smaller scale. Also
 * hosts the back-navigation control so the treatment matches login continuity.
 */
export function SignupBrandHeader({ onBack }: { onBack?: () => void }) {
  const reducedMotion = useLogiNexusReducedMotion();
  const rings = useRef([new Animated.Value(0), new Animated.Value(0)]).current;
  const breathe = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (reducedMotion) return;
    const ringLoop = (value: Animated.Value, delay: number) =>
      Animated.loop(
        Animated.sequence([
          Animated.delay(delay),
          Animated.timing(value, { toValue: 1, duration: 2600, easing: Easing.out(Easing.quad), useNativeDriver: true }),
          Animated.timing(value, { toValue: 0, duration: 0, useNativeDriver: true })
        ])
      );
    const breatheLoop = Animated.loop(
      Animated.sequence([
        Animated.timing(breathe, { toValue: 1, duration: logiNexus.motion.ambient, easing: Easing.inOut(Easing.quad), useNativeDriver: true }),
        Animated.timing(breathe, { toValue: 0, duration: logiNexus.motion.ambient, easing: Easing.inOut(Easing.quad), useNativeDriver: true })
      ])
    );
    const running = [ringLoop(rings[0], 0), ringLoop(rings[1], 900), breatheLoop];
    running.forEach((animation) => animation.start());
    return () => running.forEach((animation) => animation.stop());
  }, [reducedMotion, rings, breathe]);

  const glowOpacity = breathe.interpolate({ inputRange: [0, 1], outputRange: [0.24, 0.5] });

  return (
    <View style={styles.root}>
      {onBack ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Go back"
          testID="signup-back"
          hitSlop={12}
          onPress={onBack}
          style={({ pressed }) => [styles.back, { opacity: pressed ? 0.6 : 1 }]}
        >
          <Ionicons name="chevron-back" size={22} color={colors.text} />
        </Pressable>
      ) : null}

      <View style={styles.markWrap} accessible accessibilityRole="header" accessibilityLabel="PulseSoc. Native access.">
        <View style={styles.decorLayer} pointerEvents="none">
          {rings.map((value, index) => {
            const scale = value.interpolate({ inputRange: [0, 1], outputRange: [0.7, 1.7] });
            const opacity = value.interpolate({ inputRange: [0, 1], outputRange: [0.36, 0] });
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
          <Animated.View style={[styles.glow, { opacity: reducedMotion ? 0.24 : glowOpacity }]} />
        </View>
        <Image source={PULSESOC_LOGO} style={styles.logo} resizeMode="contain" fadeDuration={0} accessible={false} />
      </View>

      <Text style={styles.eyebrow} maxFontSizeMultiplier={1.8}>
        Native Access
      </Text>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  root: {
    alignItems: "center"
  },
  back: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: logiNexus.radius.circular,
    borderWidth: 1,
    height: 40,
    justifyContent: "center",
    left: 0,
    position: "absolute",
    top: 0,
    width: 40
  },
  markWrap: {
    alignItems: "center",
    height: LOGO_HEIGHT,
    justifyContent: "center",
    width: LOGO_WIDTH
  },
  decorLayer: {
    ...StyleSheet.absoluteFillObject,
    alignItems: "center",
    justifyContent: "center"
  },
  ring: {
    borderRadius: logiNexus.radius.circular,
    borderWidth: 1.25,
    height: RING_SIZE,
    position: "absolute",
    width: RING_SIZE
  },
  glow: {
    backgroundColor: colors.accent,
    borderRadius: logiNexus.radius.circular,
    height: GLOW_SIZE,
    position: "absolute",
    shadowColor: colors.accent,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 1,
    shadowRadius: 28,
    transform: [{ translateY: SYMBOL_OFFSET_Y }],
    width: GLOW_SIZE
  },
  logo: {
    height: LOGO_HEIGHT,
    width: LOGO_WIDTH
  },
  eyebrow: {
    color: colors.accentStrong,
    fontSize: 11,
    fontWeight: "900",
    letterSpacing: 3,
    marginTop: logiNexus.spacing.xs,
    textTransform: "uppercase"
  }
}));
