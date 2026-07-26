import { useEffect, useRef } from "react";
import { Animated, Easing, Image, StyleSheet, Text, View } from "react-native";
import { useTranslation } from "../../i18n";
import { colors } from "../../theme/colors";
import { logiNexus } from "../../theme/logiNexus";
import { useLogiNexusReducedMotion } from "../../theme/logiNexusMotion";

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
const GLOW_SIZE = 96;

/**
 * PulseSoc brand mark for the login screen. Renders the real transparent logo
 * asset centered over a restrained breathing glow and slow pulse rings drawn in
 * the logo's own colors, so the mark feels lit by the interface rather than
 * pasted on top of it. The logo itself is never scaled, cropped, or blurred.
 */
export function PulseSocBrandHeader() {
  const { t } = useTranslation();
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
      accessibilityLabel={t("auth:brand.loginLogoA11y")}
    >
      <View style={styles.markWrap}>
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
          <Animated.View style={[styles.glow, { opacity: reducedMotion ? 0.28 : glowOpacity }]} />
        </View>

        <Image
          source={PULSESOC_LOGO}
          style={styles.logo}
          resizeMode="contain"
          fadeDuration={0}
          accessible={false}
        />
      </View>

      <Text style={styles.eyebrow} maxFontSizeMultiplier={1.8}>
        {t("auth:brand.eyebrow")}
      </Text>
      <Text style={styles.tagline} maxFontSizeMultiplier={1.8}>
        {t("auth:brand.tagline")}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
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
  glow: {
    backgroundColor: colors.accent,
    borderRadius: logiNexus.radius.circular,
    height: GLOW_SIZE,
    position: "absolute",
    shadowColor: colors.accent,
    shadowOffset: { width: 0, height: 0 },
    shadowOpacity: 1,
    shadowRadius: 40,
    transform: [{ translateY: SYMBOL_OFFSET_Y }],
    width: GLOW_SIZE
  },
  logo: {
    height: LOGO_HEIGHT,
    width: LOGO_WIDTH
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
