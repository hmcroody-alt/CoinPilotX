/**
 * The border that makes a suggestion card look alive — §7, §8, §9.
 *
 * ## How the motion is made
 *
 * One oversized gradient square rotates behind the card; an opaque inner layer
 * sits on top of everything except a hairline at the edge. What the eye sees is
 * light travelling around the border, but what the system animates is a single
 * `rotate` transform, so it stays on the native driver and costs one animation
 * rather than one per edge. This is the same construction as
 * `ShimmerBorderCard`, deliberately: a second technique for the same effect is a
 * second thing to keep in sync.
 *
 * ## Why `energy` is three states and not a boolean
 *
 * §8 asks for a border that reacts to what the card is doing, and the three
 * things a card can be doing are meaningfully different: sitting off to the side
 * of the carousel, being the card the user is looking at, and actively playing a
 * preview. Collapsing them to on/off would either animate every card in the row
 * — which §8 forbids outright, and which is what makes a carousel expensive —
 * or animate none of them.
 *
 * ## What "stops" means
 *
 * An idle card does not run a paused animation; it runs no animation. Both the
 * gradient layer and the animation are removed from the tree, which is why the
 * `-sweep` testID is the honest signal for "is this card animating" — a style
 * assertion would still pass for a looping animation whose output happens to be
 * invisible. Reduce Motion takes the same path for the travelling layer while
 * keeping the static gradient ring, so the hierarchy §9 asks to preserve
 * survives without any motion at all.
 */
import { LinearGradient } from "expo-linear-gradient";
import { ReactNode } from "react";
import { Animated, StyleSheet, View, ViewStyle } from "react-native";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { createThemedStyles } from "../theme/themedStyles";
import { useBusinessLiveAmbient } from "../theme/businessLiveMotion";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";

/** Sitting in the carousel, being looked at, or playing a preview. */
export type DiscoveryCardEnergy = "idle" | "focused" | "playing";

/** Border thickness. A hairline reads as premium; anything thicker reads as a selection state. */
const RING_WIDTH = 1.5;

/**
 * How far past the card the rotating square extends.
 *
 * A rotating square must be at least √2 × the diagonal to cover the corners
 * throughout a full turn; 1.5 clears that with margin so no corner ever goes
 * dark mid-rotation.
 */
const SWEEP_OVERSCAN = 1.5;

/** Teal → cyan → violet: the PulseSoc accents, with transparent gaps so the light travels. */
const SWEEP_COLORS = ["transparent", colors.accent, colors.accentStrong, colors.intelligence, "transparent"] as const;

/** The still ring under Reduce Motion, and under the resting state of a focused card. */
const STILL_COLORS = [colors.accent, colors.accentStrong, colors.intelligence] as const;

export function DiscoveryCardFrame({
  energy,
  width,
  height,
  radius,
  testID,
  style,
  children
}: {
  energy: DiscoveryCardEnergy;
  width: number;
  height: number;
  radius: number;
  testID?: string;
  style?: ViewStyle;
  children: ReactNode;
}) {
  const reducedMotion = useLogiNexusReducedMotion();
  const lit = energy !== "idle";
  // Passing `true` for the hook's reduce-motion argument is how the shared
  // ambient helper parks a loop, so an idle card and a Reduce Motion user reach
  // the same stopped state through the same path.
  const animated = lit && !reducedMotion;
  const sweep = useBusinessLiveAmbient(logiNexus.motion.borderShimmer, !animated, { resetTo: 0 });

  const outerRadius = radius + RING_WIDTH;
  const sweepSize = Math.round(Math.max(width, height) * SWEEP_OVERSCAN);

  return (
    <View
      testID={testID}
      style={[
        styles.frame,
        {
          width,
          borderRadius: outerRadius,
          borderColor: lit ? colors.accent : colors.border,
          // Playing is the strongest state: the glow reads before the motion does.
          shadowOpacity: energy === "playing" ? 0.45 : lit ? 0.2 : 0,
          shadowRadius: energy === "playing" ? 18 : 10
        },
        style
      ]}
    >
      {lit ? (
        <View
          testID={testID ? `${testID}-ring` : undefined}
          pointerEvents="none"
          style={[StyleSheet.absoluteFill, { borderRadius: outerRadius, overflow: "hidden" }]}
        >
          {animated ? (
            <Animated.View
              testID={testID ? `${testID}-sweep` : undefined}
              style={{
                position: "absolute",
                width: sweepSize,
                height: sweepSize,
                left: (width - sweepSize) / 2,
                top: (height - sweepSize) / 2,
                opacity: energy === "playing" ? 0.95 : 0.55,
                transform: [
                  { rotate: sweep.interpolate({ inputRange: [0, 1], outputRange: ["0deg", "360deg"] }) }
                ]
              }}
            >
              <LinearGradient
                colors={[...SWEEP_COLORS]}
                start={{ x: 0, y: 0 }}
                end={{ x: 1, y: 1 }}
                style={StyleSheet.absoluteFill}
              />
            </Animated.View>
          ) : (
            <LinearGradient
              colors={[...STILL_COLORS]}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 1 }}
              style={[StyleSheet.absoluteFill, { opacity: energy === "playing" ? 0.7 : 0.4 }]}
            />
          )}
        </View>
      ) : null}
      <View style={[styles.inner, { borderRadius: radius }]}>{children}</View>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  frame: {
    padding: RING_WIDTH,
    borderWidth: StyleSheet.hairlineWidth,
    backgroundColor: colors.surface,
    shadowColor: colors.accentStrong,
    shadowOffset: { width: 0, height: 0 }
  },
  inner: { overflow: "hidden", backgroundColor: colors.surface }
}));
