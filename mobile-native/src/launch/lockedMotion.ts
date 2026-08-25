/**
 * Motion for a locked launch card.
 *
 * A locked card has to read as *alive and coming*, not as *disabled*. Greying it
 * out would say "you cannot have this"; the gentle drift and the slow glow say
 * "this is being built". That is the whole reason the gate shows the card at all
 * instead of hiding it.
 *
 * Three properties this hook is responsible for, each of which is a way the
 * effect goes wrong if it is left to a screen to remember:
 *
 *   - It never drops a frame. Only `transform` and `opacity` are animated, and
 *     every animation runs with `useNativeDriver: true`, so the loop lives on the
 *     UI thread and a busy JS thread cannot stutter it.
 *   - It stops when nobody is looking. A `Animated.loop` with no off switch keeps
 *     the UI thread ticking behind whatever screen the user pushed on top. The
 *     `active` argument is wired to navigation focus by the caller.
 *   - It respects Reduce Motion. When motion is off the card keeps its full
 *     locked appearance — border, glow, badge — and simply stops moving. Nothing
 *     about the premium look is conditional on the animation.
 */

import { useEffect, useMemo, useRef } from "react";
import { Animated, Easing } from "react-native";

/** Vertical travel, in points. Deliberately small: a drift, not a bounce. */
export const LOCKED_FLOAT_DISTANCE = 4;

/** One half-cycle. Slow enough to read as breathing rather than blinking. */
export const LOCKED_FLOAT_DURATION_MS = 2600;

/**
 * Offset between neighbouring cards.
 *
 * Without it every card in the grid rises and falls in lockstep, which reads as
 * the whole panel wobbling. Staggering turns the same animation into individual
 * objects that happen to share a rhythm.
 */
export const LOCKED_STAGGER_MS = 220;

/** Glow opacity floor and ceiling. Never fully off — the card is always marked. */
export const LOCKED_GLOW_MIN = 0.35;
export const LOCKED_GLOW_MAX = 0.85;

/**
 * `cardStyle` goes on the card's `Animated.View`; `glowStyle` goes on the halo
 * layer behind its content. The return type is inferred rather than declared so
 * the animated-style shapes stay whatever React Native's own typings say they
 * are — restating them here is how a style silently stops type-checking against
 * `Animated.View` after an SDK bump.
 */
export function useLockedMotion(options: {
  /** Position in the grid. Drives the stagger. */
  index: number;
  /** False when the screen is not focused: the loop stops and resets. */
  active: boolean;
  /** False under Reduce Motion: nothing ever animates. */
  enabled: boolean;
}) {
  const { index, active, enabled } = options;

  // `useRef` rather than `useState`: these are animation drivers, and recreating
  // them on a re-render would restart the loop mid-cycle and un-stagger the grid.
  const drift = useRef(new Animated.Value(0)).current;
  const glow = useRef(new Animated.Value(0)).current;
  const scale = useRef(new Animated.Value(1)).current;
  const running = useRef<Animated.CompositeAnimation | null>(null);

  useEffect(() => {
    if (!enabled || !active) {
      // Stop and settle. Resetting to 0 matters: a card parked mid-drift while
      // the screen was blurred would otherwise sit visibly off its baseline for
      // as long as Reduce Motion stays on.
      running.current?.stop();
      running.current = null;
      drift.setValue(0);
      glow.setValue(0);
      return;
    }

    const halfCycle = (value: Animated.Value, toValue: number) =>
      Animated.timing(value, {
        toValue,
        duration: LOCKED_FLOAT_DURATION_MS,
        easing: Easing.inOut(Easing.quad),
        useNativeDriver: true
      });

    // The delay sits in front of an endless loop, so `sequence` starts the loop
    // once and then never advances — which is exactly the intent. Each card's
    // loop therefore begins `index * LOCKED_STAGGER_MS` after its neighbour's
    // and keeps that phase offset for the life of the screen.
    const animation = Animated.sequence([
      Animated.delay(index * LOCKED_STAGGER_MS),
      Animated.loop(
        Animated.parallel([
          Animated.sequence([halfCycle(drift, 1), halfCycle(drift, 0)]),
          Animated.sequence([halfCycle(glow, 1), halfCycle(glow, 0)])
        ])
      )
    ]);

    running.current = animation;
    animation.start();

    return () => {
      animation.stop();
      running.current = null;
    };
  }, [active, drift, enabled, glow, index]);

  const press = (toValue: number) => {
    if (!enabled) return;
    Animated.spring(scale, { toValue, useNativeDriver: true, speed: 40, bounciness: 0 }).start();
  };

  const cardStyle = useMemo(
    () => ({
      transform: [
        {
          translateY: drift.interpolate({
            inputRange: [0, 1],
            outputRange: [0, -LOCKED_FLOAT_DISTANCE]
          })
        },
        { scale }
      ]
    }),
    [drift, scale]
  );

  const glowStyle = useMemo(
    () =>
      enabled
        ? {
            opacity: glow.interpolate({
              inputRange: [0, 1],
              outputRange: [LOCKED_GLOW_MIN, LOCKED_GLOW_MAX]
            })
          }
        : // Reduce Motion keeps the halo, at a fixed mid strength. The card still
          // looks premium and still reads as locked; it just holds still.
          { opacity: (LOCKED_GLOW_MIN + LOCKED_GLOW_MAX) / 2 },
    [enabled, glow]
  );

  return {
    cardStyle,
    glowStyle,
    onPressIn: () => press(0.97),
    onPressOut: () => press(1)
  };
}
