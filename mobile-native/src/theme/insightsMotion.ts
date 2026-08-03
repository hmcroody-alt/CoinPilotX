/**
 * Motion for the Insights screen.
 *
 * The screen's entrance stagger, presses, ambient loops and app-foreground
 * gating all come from `storeMotion` unchanged — this module only adds the three
 * shapes analytics needs and a dashboard of lists does not: a long line draw, a
 * horizontal fill, and a ring sweep.
 *
 * Every hook here follows the house rules. `reducedMotion` is a parameter, not a
 * lookup, and it resolves to the *finished* state via `setValue` rather than
 * running a zero-duration animation — a line arrives drawn, a fill arrives at
 * full width, a ring arrives at its value. Ambient loops pause when the app
 * leaves the foreground. And `useNativeDriver` is true everywhere except where
 * the target is an SVG attribute the native driver cannot write, which is
 * exactly two places, both marked.
 */

import { useEffect, useRef } from "react";
import { Animated } from "react-native";
import { logiNexusMotion } from "./logiNexusMotion";
import { useAppForegrounded } from "./storeMotion";

export const INSIGHTS_MOTION = {
  /** The revenue line's draw. Long enough to read as drawing, not as sweeping. */
  lineDrawMs: 1600,
  /**
   * The orders line starts this far behind revenue, so the eye follows the
   * primary series first and reads the second as commentary on it.
   */
  lineStaggerMs: 200,
  /** The "you are here" dot appears only once its line has arrived under it. */
  latestDotMs: 260,
  /** Source bars grow from the left. */
  fillMs: 1000,
  /** Rings sweep from twelve o'clock. */
  ringMs: 1400,
  /** A period switch crossfades; it does not replay the entrance cascade. */
  periodFadeMs: 250,
  /** The tip's shimmer, while it is on screen. */
  tipShimmerMs: 7000
} as const;

/**
 * One-shot 0 → 1 for a chart line, with a per-series delay.
 *
 * `useNativeDriver: false` is required and unavoidable: the consumer maps this
 * onto an SVG `strokeDashoffset`, which the native driver cannot write. It runs
 * once per `key` change — so the lines redraw when the period changes and real
 * data replaces the skeleton, rather than replaying on every render.
 */
export function useInsightsDraw(
  reducedMotion: boolean,
  key: unknown,
  delay = 0,
  duration = INSIGHTS_MOTION.lineDrawMs
): Animated.Value {
  const progress = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (reducedMotion) {
      progress.setValue(1);
      return;
    }
    progress.setValue(0);
    const animation = Animated.timing(progress, {
      toValue: 1,
      duration,
      delay,
      easing: logiNexusMotion.easing.exit,
      useNativeDriver: false
    });
    animation.start();
    return () => animation.stop();
  }, [delay, duration, key, progress, reducedMotion]);

  return progress;
}

/**
 * The latest-point dot. Fades in after its line has reached it, so it reads as
 * the line arriving rather than as a marker that was always there.
 */
export function useInsightsLatestDot(reducedMotion: boolean, key: unknown): Animated.Value {
  const opacity = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (reducedMotion) {
      opacity.setValue(1);
      return;
    }
    opacity.setValue(0);
    const animation = Animated.timing(opacity, {
      toValue: 1,
      duration: INSIGHTS_MOTION.latestDotMs,
      delay: INSIGHTS_MOTION.lineDrawMs,
      easing: logiNexusMotion.easing.exit,
      useNativeDriver: true
    });
    animation.start();
    return () => animation.stop();
  }, [key, opacity, reducedMotion]);

  return opacity;
}

/**
 * A source bar growing from the left.
 *
 * Returns 0 → 1 to be used as a `scaleX` with `transformOrigin` faked by a left
 * anchor, which keeps it on the native driver. Scaling the *width* would be a
 * layout animation on every frame; this is one composited transform.
 */
export function useInsightsFill(reducedMotion: boolean, key: unknown, delay = 0): Animated.Value {
  const value = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (reducedMotion) {
      value.setValue(1);
      return;
    }
    value.setValue(0);
    const animation = Animated.timing(value, {
      toValue: 1,
      duration: INSIGHTS_MOTION.fillMs,
      delay,
      easing: logiNexusMotion.easing.exit,
      useNativeDriver: true
    });
    animation.start();
    return () => animation.stop();
  }, [delay, key, reducedMotion, value]);

  return value;
}

/**
 * A ring sweeping to its value.
 *
 * `useNativeDriver: false` for the same reason as the line: the target is an SVG
 * `strokeDashoffset`.
 */
export function useInsightsRing(
  reducedMotion: boolean,
  key: unknown,
  delay = 0
): Animated.Value {
  const value = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (reducedMotion) {
      value.setValue(1);
      return;
    }
    value.setValue(0);
    const animation = Animated.timing(value, {
      toValue: 1,
      duration: INSIGHTS_MOTION.ringMs,
      delay,
      easing: logiNexusMotion.easing.exit,
      useNativeDriver: false
    });
    animation.start();
    return () => animation.stop();
  }, [delay, key, reducedMotion, value]);

  return value;
}

/**
 * The crossfade a period switch uses.
 *
 * Switching from 7d to 30d must not replay the whole entrance cascade — the
 * seller is comparing two views of the same screen, and re-staggering six
 * modules would make that comparison feel like a navigation. Content dips to
 * 0.35 and returns, ≤250ms each way, and the layout never moves.
 */
export function useInsightsPeriodFade(reducedMotion: boolean, key: unknown): Animated.Value {
  const opacity = useRef(new Animated.Value(1)).current;
  const first = useRef(true);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (reducedMotion) {
      opacity.setValue(1);
      return;
    }
    opacity.setValue(0.35);
    const animation = Animated.timing(opacity, {
      toValue: 1,
      duration: INSIGHTS_MOTION.periodFadeMs,
      easing: logiNexusMotion.easing.exit,
      useNativeDriver: true
    });
    animation.start();
    return () => animation.stop();
  }, [key, opacity, reducedMotion]);

  return opacity;
}

/**
 * The tip card's slow shimmer.
 *
 * Ambient, so it is the first thing to go under reduce-motion, and it stops when
 * the app is backgrounded — a loop running behind a locked screen is battery
 * spent on nobody.
 */
export function useInsightsTipShimmer(reducedMotion: boolean, visible: boolean): Animated.Value {
  const value = useRef(new Animated.Value(0)).current;
  const foregrounded = useAppForegrounded();

  useEffect(() => {
    if (reducedMotion || !visible || !foregrounded) {
      value.setValue(0);
      return;
    }
    value.setValue(0);
    const animation = Animated.loop(
      Animated.timing(value, {
        toValue: 1,
        duration: INSIGHTS_MOTION.tipShimmerMs,
        easing: logiNexusMotion.easing.standard,
        useNativeDriver: true
      })
    );
    animation.start();
    return () => animation.stop();
  }, [foregrounded, reducedMotion, value, visible]);

  return value;
}
