import * as Battery from "expo-battery";
import { LinearGradient } from "expo-linear-gradient";
import { Fragment, memo, ReactNode, useEffect, useMemo, useRef, useState } from "react";
import { AccessibilityInfo, Animated, AppState, Easing, StyleSheet, View, ViewStyle } from "react-native";
import { useTheme } from "../theme/ThemeContext";
import {
  PULSE_BACKGROUND_GEOMETRY,
  PULSE_BACKGROUND_LINES,
  PULSE_BACKGROUND_NODES,
  PULSE_BACKGROUND_SURFACES,
  PULSE_BACKGROUND_VARIANTS,
  PULSE_BACKGROUND_VARIANT_CYCLES,
  PulseBackgroundIntensity,
  PulseBackgroundVariant,
  bottomGlowOpacity,
  haloOpacity,
  includesTier,
  lineOpacity,
  nodeOpacity,
  pulseBackgroundScale,
  pulseOpacityRange
} from "../theme/pulseBackground";

type Props = {
  variant?: PulseBackgroundVariant;
  intensity?: PulseBackgroundIntensity;
  style?: ViewStyle;
  testID?: string;
  /**
   * Optional. With children this is a wrapper; without them it is an
   * absolutely-positioned layer a screen drops in behind its own content.
   */
  children?: ReactNode;
};

/**
 * The default PulseSoc backdrop: a deep-space digital network.
 *
 * Sharp, dark, and almost still. There is no blur, no haze and no bloom here on
 * purpose — the backdrop this replaces went light-purple and soft, and every
 * one of those effects costs text contrast on top of costing a frame. What is
 * left is a multi-stop gradient, a hairline mesh, and fourteen small nodes, all
 * of them below the opacity ceilings in `theme/pulseBackground`.
 *
 * Three things about the structure are load-bearing:
 *
 *  - Every decorative layer is `pointerEvents="none"` and hidden from
 *    accessibility. A backdrop that swallows one gesture is worse than no
 *    backdrop, and the root only takes `"none"` when it has no children —
 *    `"none"` on a parent would block its children too.
 *
 *  - Three drivers, not thirty. One drift, one breath, one line translation,
 *    all native-driven; individual nodes only interpolate off the shared
 *    breath. Nothing is created per render.
 *
 *  - The active theme's `galacticBackground` profile decides globally. White
 *    renders nothing at all, Black dims the whole field, and light themes get
 *    the light surface rather than a dimmed dark one — a near-black field under
 *    a light palette is a contrast inversion, not a subtler backdrop.
 *
 * Motion stops for Reduce Motion, Low Power Mode, and whenever the app is not
 * foregrounded; the `static` variant never starts it in the first place. In all
 * of those cases the composition is drawn as plain values, so there is no
 * animated node left in the tree at all.
 */
export const PulseBackground = memo(function PulseBackground({
  variant = "default",
  intensity = "standard",
  style,
  testID = "pulse-background",
  children
}: Props) {
  const theme = useTheme();
  const profile = theme.galacticBackground;
  const [lowPower, setLowPower] = useState(false);
  const [systemReduceMotion, setSystemReduceMotion] = useState(false);
  const [foreground, setForeground] = useState(AppState.currentState === "active");
  const drift = useRef(new Animated.Value(PULSE_BACKGROUND_GEOMETRY.restingProgress)).current;
  const breath = useRef(new Animated.Value(PULSE_BACKGROUND_GEOMETRY.restingProgress)).current;
  const travel = useRef(new Animated.Value(PULSE_BACKGROUND_GEOMETRY.restingProgress)).current;

  useEffect(() => {
    Battery.isLowPowerModeEnabledAsync().then(setLowPower).catch(() => setLowPower(false));
    const battery = Battery.addLowPowerModeListener(({ lowPowerMode }) => setLowPower(lowPowerMode));
    AccessibilityInfo.isReduceMotionEnabled().then(setSystemReduceMotion).catch(() => setSystemReduceMotion(false));
    const motion = AccessibilityInfo.addEventListener("reduceMotionChanged", setSystemReduceMotion);
    const app = AppState.addEventListener("change", (state) => setForeground(state === "active"));
    return () => {
      battery?.remove?.();
      motion?.remove?.();
      app?.remove?.();
    };
  }, []);

  const animate =
    PULSE_BACKGROUND_VARIANTS[variant].animated &&
    profile.enabled &&
    !theme.reduceMotion &&
    !systemReduceMotion &&
    !lowPower &&
    foreground;

  useEffect(() => {
    drift.stopAnimation();
    breath.stopAnimation();
    travel.stopAnimation();
    if (!animate) {
      // Settle mid-cycle rather than at an endpoint so the still composition is
      // the same one the eye was already looking at.
      drift.setValue(PULSE_BACKGROUND_GEOMETRY.restingProgress);
      breath.setValue(PULSE_BACKGROUND_GEOMETRY.restingProgress);
      travel.setValue(PULSE_BACKGROUND_GEOMETRY.restingProgress);
      return;
    }
    const cycles = PULSE_BACKGROUND_VARIANT_CYCLES[variant];
    // Cycle times are full round trips, so each leg runs for half of one.
    const pingPong = (value: Animated.Value, cycle: number) =>
      Animated.loop(
        Animated.sequence([
          Animated.timing(value, {
            toValue: 1,
            duration: cycle / 2,
            easing: Easing.inOut(Easing.sin),
            useNativeDriver: true
          }),
          Animated.timing(value, {
            toValue: 0,
            duration: cycle / 2,
            easing: Easing.inOut(Easing.sin),
            useNativeDriver: true
          })
        ])
      );
    const loops = [pingPong(drift, cycles.drift), pingPong(breath, cycles.pulse), pingPong(travel, cycles.travel)];
    loops.forEach((loop) => loop.start());
    return () => loops.forEach((loop) => loop.stop());
  }, [animate, breath, drift, travel, variant]);

  const surfaceKey = profile.variant === "light" ? "light" : "dark";
  const surface = PULSE_BACKGROUND_SURFACES[surfaceKey];
  const scale = pulseBackgroundScale(variant, intensity, surfaceKey);
  // Reduce Transparency asks for opaque fills, so the two translucent flourishes
  // — node halos and the bottom lift — come off entirely rather than thicken.
  const decorated = !theme.reduceTransparency;

  const nodes = useMemo(
    () =>
      PULSE_BACKGROUND_NODES.filter((node) => includesTier(variant, node.tier)).map((node, index) => {
        const value = nodeOpacity(node.opacity, scale);
        const [dim, bright] = pulseOpacityRange(value);
        const color = surface.node[node.tone];
        const halo = node.size * PULSE_BACKGROUND_GEOMETRY.haloScale;
        // A Fragment, not a View: an intervening box would be the percentage
        // basis for these absolute positions and would collapse to zero size.
        return (
          <Fragment key={`${node.x}-${node.y}`}>
            {decorated ? (
              <View
                testID={`${testID}-halo-${index}`}
                pointerEvents="none"
                style={[
                  styles.dot,
                  {
                    backgroundColor: color,
                    borderRadius: halo,
                    height: halo,
                    left: `${node.x}%`,
                    marginLeft: -halo / 2,
                    marginTop: -halo / 2,
                    opacity: haloOpacity(value),
                    top: `${node.y}%`,
                    width: halo
                  }
                ]}
              />
            ) : null}
            <Animated.View
              testID={`${testID}-node-${index}`}
              pointerEvents="none"
              style={[
                styles.dot,
                {
                  backgroundColor: color,
                  borderRadius: node.size,
                  height: node.size,
                  left: `${node.x}%`,
                  marginLeft: -node.size / 2,
                  marginTop: -node.size / 2,
                  opacity:
                    animate && node.pulse
                      ? breath.interpolate({ inputRange: [0, 1], outputRange: [dim, bright] })
                      : value,
                  top: `${node.y}%`,
                  width: node.size
                }
              ]}
            />
          </Fragment>
        );
      }),
    [animate, breath, decorated, scale, surface, testID, variant]
  );

  const lines = useMemo(
    () =>
      PULSE_BACKGROUND_LINES.filter((line) => includesTier(variant, line.tier)).map((line, index) => (
        <View
          key={`${line.x}-${line.y}`}
          testID={`${testID}-line-${index}`}
          pointerEvents="none"
          style={[
            styles.line,
            {
              backgroundColor: surface.line,
              height: PULSE_BACKGROUND_GEOMETRY.lineThickness,
              left: `${line.x}%`,
              opacity: lineOpacity(line.opacity, scale),
              top: `${line.y}%`,
              transform: [{ rotate: `${line.angle}deg` }],
              width: `${line.length}%`
            }
          ]}
        />
      )),
    [scale, surface, testID, variant]
  );

  if (!profile.enabled) return null;

  const nodeTransform = animate
    ? [
        {
          translateX: drift.interpolate({
            inputRange: [0, 1],
            outputRange: [-PULSE_BACKGROUND_GEOMETRY.driftTranslate / 2, PULSE_BACKGROUND_GEOMETRY.driftTranslate / 2]
          })
        },
        {
          translateY: drift.interpolate({
            inputRange: [0, 1],
            outputRange: [PULSE_BACKGROUND_GEOMETRY.driftTranslate / 2, -PULSE_BACKGROUND_GEOMETRY.driftTranslate / 2]
          })
        }
      ]
    : undefined;
  const lineTransform = animate
    ? [
        {
          translateX: travel.interpolate({
            inputRange: [0, 1],
            outputRange: [-PULSE_BACKGROUND_GEOMETRY.lineTranslate / 2, PULSE_BACKGROUND_GEOMETRY.lineTranslate / 2]
          })
        }
      ]
    : undefined;

  return (
    <View
      testID={testID}
      // `none` on a parent blocks its children as well, so a wrapper has to use
      // `box-none`: it never takes a touch itself, its content still can.
      pointerEvents={children ? "box-none" : "none"}
      // Hiding the root from screen readers is only safe when nothing of the
      // caller's is inside it; otherwise the field below carries the flags.
      accessibilityElementsHidden={!children}
      importantForAccessibility={children ? "auto" : "no-hide-descendants"}
      style={[children ? styles.wrapper : styles.detached, style]}
    >
      <View
        testID={`${testID}-field`}
        pointerEvents="none"
        accessibilityElementsHidden
        importantForAccessibility="no-hide-descendants"
        style={[styles.field, { opacity: profile.intensity }]}
      >
        <LinearGradient
          testID={`${testID}-gradient`}
          pointerEvents="none"
          colors={surface.gradient.colors}
          locations={surface.gradient.locations}
          style={StyleSheet.absoluteFill}
        />
        <Animated.View
          testID={`${testID}-lines`}
          pointerEvents="none"
          style={[styles.layer, lineTransform ? { transform: lineTransform } : null]}
        >
          {lines}
        </Animated.View>
        <Animated.View
          testID={`${testID}-nodes`}
          pointerEvents="none"
          style={[styles.layer, nodeTransform ? { transform: nodeTransform } : null]}
        >
          {nodes}
        </Animated.View>
        {decorated ? (
          <LinearGradient
            testID={`${testID}-glow`}
            pointerEvents="none"
            colors={surface.bottomGlow.colors}
            locations={surface.bottomGlow.locations}
            style={[styles.bottomGlow, { opacity: bottomGlowOpacity(variant) }]}
          />
        ) : null}
      </View>
      {children ? (
        <View testID={`${testID}-content`} pointerEvents="box-none" style={styles.content}>
          {children}
        </View>
      ) : null}
    </View>
  );
});

const styles = StyleSheet.create({
  /** As a sibling layer it must claim no space, or it would shift the layout. */
  detached: { ...StyleSheet.absoluteFillObject },
  wrapper: { flex: 1 },
  /** Clipped so drifting nodes never spill past the surface they belong to. */
  field: { ...StyleSheet.absoluteFillObject, overflow: "hidden" },
  layer: { ...StyleSheet.absoluteFillObject },
  content: { flex: 1 },
  dot: { position: "absolute" },
  line: { position: "absolute" },
  bottomGlow: {
    bottom: 0,
    height: `${PULSE_BACKGROUND_GEOMETRY.bottomGlowHeight * 100}%`,
    left: 0,
    position: "absolute",
    right: 0
  }
});
