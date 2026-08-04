import * as Battery from "expo-battery";
import { LinearGradient } from "expo-linear-gradient";
import { memo, useEffect, useRef, useState } from "react";
import { AccessibilityInfo, Animated, AppState, Easing, StyleSheet, View, ViewStyle } from "react-native";

export type GalacticAtmosphereVariant = "feed" | "profile" | "messages" | "marketplace" | "business" | "advertising" | "music" | "live" | "undx";

type Props = {
  variant?: GalacticAtmosphereVariant;
  style?: ViewStyle;
  /** A scroll-position driver may be supplied by long surfaces for restrained parallax. */
  scrollY?: Animated.Value;
  testID?: string;
};

const STARS = [
  [7, 8, 1, 0.14], [19, 17, 1, 0.1], [31, 6, 2, 0.13], [43, 24, 1, 0.16], [58, 11, 1, 0.1],
  [71, 29, 1, 0.12], [84, 8, 2, 0.11], [94, 21, 1, 0.15], [11, 39, 1, 0.12], [25, 52, 1, 0.09],
  [39, 43, 1, 0.14], [53, 61, 2, 0.1], [66, 48, 1, 0.16], [79, 68, 1, 0.09], [91, 51, 1, 0.13],
  [5, 75, 1, 0.1], [18, 88, 2, 0.09], [34, 79, 1, 0.15], [49, 93, 1, 0.11], [63, 82, 1, 0.13],
  [76, 95, 1, 0.1], [88, 84, 2, 0.09], [97, 72, 1, 0.12]
] as const;

const ACCENTS: Record<GalacticAtmosphereVariant, { a: string; b: string; planet: string }> = {
  feed: { a: "rgba(25,105,156,0.07)", b: "rgba(45,82,142,0.05)", planet: "rgba(34,94,134,0.09)" },
  profile: { a: "rgba(37,176,183,0.075)", b: "rgba(87,72,155,0.05)", planet: "rgba(52,112,144,0.09)" },
  messages: { a: "rgba(34,93,135,0.045)", b: "rgba(31,108,115,0.035)", planet: "rgba(30,79,108,0.055)" },
  marketplace: { a: "rgba(153,116,45,0.055)", b: "rgba(29,125,112,0.045)", planet: "rgba(130,97,40,0.07)" },
  business: { a: "rgba(31,85,147,0.06)", b: "rgba(22,101,126,0.035)", planet: "rgba(38,83,132,0.07)" },
  advertising: { a: "rgba(84,56,143,0.05)", b: "rgba(25,127,124,0.04)", planet: "rgba(75,54,125,0.07)" },
  music: { a: "rgba(48,112,153,0.06)", b: "rgba(92,51,143,0.045)", planet: "rgba(51,93,133,0.07)" },
  live: { a: "rgba(39,126,139,0.065)", b: "rgba(82,48,133,0.045)", planet: "rgba(44,96,128,0.075)" },
  undx: { a: "rgba(28,132,156,0.055)", b: "rgba(26,91,136,0.04)", planet: "rgba(32,105,139,0.065)" }
};

/**
 * Content-supporting space only: no foreground objects, sharp streaks, rapid
 * sparkles, or looping geometry. All motion is native-driven and pauses for
 * Reduce Motion, Low Power Mode, and inactive app state.
 */
export const GalacticAtmosphere = memo(function GalacticAtmosphere({
  variant = "feed",
  style,
  scrollY,
  testID = "galactic-atmosphere"
}: Props) {
  const lowPower = Battery.useLowPowerMode();
  const [reduceMotion, setReduceMotion] = useState(false);
  const [foreground, setForeground] = useState(AppState.currentState === "active");
  const drift = useRef(new Animated.Value(0)).current;
  const breathe = useRef(new Animated.Value(0)).current;
  const accents = ACCENTS[variant];

  useEffect(() => {
    AccessibilityInfo.isReduceMotionEnabled().then(setReduceMotion).catch(() => setReduceMotion(false));
    const motion = AccessibilityInfo.addEventListener("reduceMotionChanged", setReduceMotion);
    const app = AppState.addEventListener("change", (state) => setForeground(state === "active"));
    return () => { motion.remove(); app.remove(); };
  }, []);

  useEffect(() => {
    drift.stopAnimation();
    breathe.stopAnimation();
    if (reduceMotion || lowPower || !foreground) {
      drift.setValue(0.35);
      breathe.setValue(0.35);
      return;
    }
    const driftLoop = Animated.loop(Animated.sequence([
      Animated.timing(drift, { toValue: 1, duration: 180000, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
      Animated.timing(drift, { toValue: 0, duration: 180000, easing: Easing.inOut(Easing.sin), useNativeDriver: true })
    ]));
    const breatheLoop = Animated.loop(Animated.sequence([
      Animated.timing(breathe, { toValue: 1, duration: 120000, easing: Easing.inOut(Easing.sin), useNativeDriver: true }),
      Animated.timing(breathe, { toValue: 0, duration: 120000, easing: Easing.inOut(Easing.sin), useNativeDriver: true })
    ]));
    driftLoop.start(); breatheLoop.start();
    return () => { driftLoop.stop(); breatheLoop.stop(); };
  }, [breathe, drift, foreground, lowPower, reduceMotion]);

  const parallax = scrollY?.interpolate({ inputRange: [0, 1200], outputRange: [0, 24], extrapolate: "clamp" }) || 0;
  return (
    <View testID={testID} pointerEvents="none" accessibilityElementsHidden importantForAccessibility="no-hide-descendants" style={[styles.root, style]}>
      <LinearGradient colors={["#02050A", "#040A14", "#06101C"]} locations={[0, 0.48, 1]} style={StyleSheet.absoluteFill} />
      <Animated.View style={[styles.depth, { transform: [{ translateY: parallax }] }]}>
        {STARS.map(([left, top, size, opacity], index) => (
          <Animated.View key={`${left}-${top}`} style={[styles.star, { left: `${left}%`, top: `${top}%`, width: size, height: size, borderRadius: size, opacity: index % 11 === 0 ? breathe.interpolate({ inputRange: [0, 1], outputRange: [opacity * 0.65, opacity] }) : opacity }]} />
        ))}
        <Animated.View style={[styles.nebula, styles.nebulaA, { backgroundColor: accents.a, opacity: breathe.interpolate({ inputRange: [0, 1], outputRange: [0.55, 0.8] }), transform: [{ translateX: drift.interpolate({ inputRange: [0, 1], outputRange: [-8, 12] }) }, { scale: breathe.interpolate({ inputRange: [0, 1], outputRange: [0.98, 1.025] }) }] }]} />
        <Animated.View style={[styles.nebula, styles.nebulaB, { backgroundColor: accents.b, transform: [{ translateY: drift.interpolate({ inputRange: [0, 1], outputRange: [6, -10] }) }, { scale: breathe.interpolate({ inputRange: [0, 1], outputRange: [1.02, 0.985] }) }] }]} />
        <Animated.View style={[styles.planet, { backgroundColor: accents.planet, transform: [{ translateX: drift.interpolate({ inputRange: [0, 1], outputRange: [0, -8] }) }] }]} />
        <View style={styles.galaxy} />
        <View style={styles.dustA} /><View style={styles.dustB} /><View style={styles.dustC} />
      </Animated.View>
      <LinearGradient colors={["rgba(4,10,18,0.02)", "rgba(4,10,18,0.13)"]} style={StyleSheet.absoluteFill} />
    </View>
  );
});

const styles = StyleSheet.create({
  root: { ...StyleSheet.absoluteFillObject, overflow: "hidden" },
  depth: { ...StyleSheet.absoluteFillObject },
  star: { position: "absolute", backgroundColor: "#CDEBFA" },
  nebula: { position: "absolute", borderRadius: 999 },
  nebulaA: { height: 430, left: -230, top: "8%", width: 520 },
  nebulaB: { bottom: "4%", height: 520, right: -310, width: 600 },
  planet: { borderRadius: 190, height: 380, position: "absolute", right: -315, top: "15%", width: 380 },
  galaxy: { backgroundColor: "rgba(73,108,144,0.022)", borderRadius: 260, height: 120, left: "22%", position: "absolute", top: "67%", transform: [{ rotate: "-18deg" }], width: 520 },
  dustA: { backgroundColor: "rgba(123,187,202,0.035)", borderRadius: 2, height: 2, left: "37%", position: "absolute", top: "34%", width: 2 },
  dustB: { backgroundColor: "rgba(120,147,199,0.03)", borderRadius: 1, height: 1, left: "68%", position: "absolute", top: "58%", width: 1 },
  dustC: { backgroundColor: "rgba(175,145,95,0.025)", borderRadius: 2, height: 2, left: "14%", position: "absolute", top: "82%", width: 2 }
});
