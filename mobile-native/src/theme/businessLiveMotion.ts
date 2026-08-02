/**
 * Motion primitives for the "live" Business Profile surface.
 *
 * Built on React Native's core `Animated` and the existing `logiNexusMotion`
 * helpers on purpose. This project does not depend on react-native-reanimated,
 * and the brief is explicit that a capable system already in the tree should be
 * used rather than a new library added.
 *
 * Two rules run through every hook here:
 *
 * 1. Reduce-motion is a first-class input, not an afterthought. Each hook takes
 *    `reducedMotion` and, when it is on, settles its value at the *final* state
 *    and never starts a loop. That means no entrance fade, no ticker scroll, no
 *    shimmer, and a progress ring that is simply drawn at its real value. The
 *    screen never has to branch on the flag itself.
 * 2. Everything animates `transform` or `opacity` so it can run on the native
 *    driver, off the JS thread. The one exception is the progress ring, which
 *    animates an SVG `strokeDashoffset` — that property has no native-driver
 *    equivalent, so it is explicitly opted out and is deliberately the only
 *    JS-driven animation on the screen, and it runs once rather than looping.
 */

import { useEffect, useMemo, useRef } from "react";
import { Animated, Easing } from "react-native";
import { logiNexus } from "./logiNexus";
import { logiNexusMotion } from "./logiNexusMotion";

/** Distance, in px, an entering section travels upward as it fades in. */
const ENTRANCE_TRAVEL = 18;

/**
 * Ticker speed in device-independent pixels per second. Expressed as a speed
 * rather than a fixed duration so that a longer stat list scrolls at the same
 * pace as a short one instead of racing to keep the cycle time constant.
 */
const TICKER_SPEED_PX_PER_SEC = 34;

export type BusinessLiveEntrance = {
  /** Style for the section at `index`, ready to spread onto an Animated.View. */
  styleFor: (index: number) => {
    opacity: Animated.AnimatedInterpolation<number> | number;
    transform: { translateY: Animated.AnimatedInterpolation<number> | number }[];
  };
};

/**
 * Staggered entrance: each section fades and slides up over `motion.entrance`,
 * `motion.stagger` after the one above it.
 *
 * One `Animated.Value` per section rather than a single shared clock, because a
 * shared clock would force every section onto a linear curve — the easing has
 * to apply per element to read as a cascade rather than a wipe.
 */
export function useBusinessLiveEntrance(count: number, reducedMotion: boolean): BusinessLiveEntrance {
  const values = useMemo(
    () => Array.from({ length: count }, () => new Animated.Value(0)),
    // `count` is a fixed section count for this screen, so this list is created
    // once; recreating it on every render would restart the cascade.
    [count]
  );

  useEffect(() => {
    if (reducedMotion) {
      // Jump to the settled state. `setValue` rather than a zero-duration
      // timing so nothing is ever queued on the animation loop.
      values.forEach((value) => value.setValue(1));
      return;
    }
    const animation = Animated.stagger(
      logiNexus.motion.stagger,
      values.map((value) =>
        Animated.timing(value, {
          toValue: 1,
          duration: logiNexus.motion.entrance,
          easing: logiNexusMotion.easing.exit,
          useNativeDriver: true
        })
      )
    );
    animation.start();
    return () => animation.stop();
  }, [reducedMotion, values]);

  return {
    styleFor: (index: number) => {
      const value = values[index];
      if (!value) return { opacity: 1, transform: [{ translateY: 0 }] };
      return {
        opacity: value,
        transform: [
          {
            translateY: value.interpolate({
              inputRange: [0, 1],
              outputRange: [ENTRANCE_TRAVEL, 0]
            })
          }
        ]
      };
    }
  };
}

/**
 * Seamless horizontal marquee.
 *
 * The caller renders its content twice, back to back, and passes the width of a
 * single copy. Translating by exactly that width and restarting puts copy two
 * precisely where copy one began, so the loop has no visible seam and no reset
 * flash. Returns `null` before the width has been measured so the caller can
 * render the row statically on first paint rather than at an arbitrary offset.
 */
export function useBusinessLiveMarquee(contentWidth: number, reducedMotion: boolean) {
  const translate = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    translate.setValue(0);
    if (reducedMotion || contentWidth <= 0) return;
    const duration = Math.max(
      logiNexus.motion.tickerCycle / 4,
      (contentWidth / TICKER_SPEED_PX_PER_SEC) * 1000
    );
    const animation = Animated.loop(
      Animated.timing(translate, {
        toValue: -contentWidth,
        duration,
        // Linear only. Any easing here would make the ticker visibly surge and
        // stall at the loop boundary, which is exactly the seam we are hiding.
        easing: Easing.linear,
        useNativeDriver: true
      })
    );
    animation.start();
    return () => animation.stop();
  }, [contentWidth, reducedMotion, translate]);

  return contentWidth > 0 && !reducedMotion ? translate : null;
}

/**
 * Progress ring driver. Animates 0 → `target` once on mount and again whenever
 * the real value arrives from the network, so the ring fills in response to
 * live data rather than replaying a canned intro.
 *
 * `useNativeDriver: false` is required: the consumer maps this onto an SVG
 * `strokeDashoffset`, which the native driver cannot write.
 */
export function useBusinessLiveRing(target: number, reducedMotion: boolean) {
  const progress = useRef(new Animated.Value(0)).current;
  const safeTarget = Number.isFinite(target) ? Math.min(Math.max(target, 0), 100) : 0;

  useEffect(() => {
    if (reducedMotion) {
      progress.setValue(safeTarget);
      return;
    }
    const animation = Animated.timing(progress, {
      toValue: safeTarget,
      duration: logiNexus.motion.ringDraw,
      easing: logiNexusMotion.easing.exit,
      useNativeDriver: false
    });
    animation.start();
    return () => animation.stop();
  }, [progress, reducedMotion, safeTarget]);

  return progress;
}

/**
 * Continuous 0 → 1 loop for the ambient effects: the rotating border sheen, the
 * verification scan stripe, and the live-badge ping. Callers interpolate the
 * single returned value into whatever transform they need, so all three effects
 * cost one animation each and share one shape.
 *
 * `resetTo` controls the settled value under reduce-motion. A shimmer wants to
 * rest invisible (0); a badge dot wants to rest fully lit (1).
 */
export function useBusinessLiveAmbient(
  duration: number,
  reducedMotion: boolean,
  options: { resetTo?: number; pingPong?: boolean } = {}
) {
  const value = useRef(new Animated.Value(0)).current;
  const { resetTo = 0, pingPong = false } = options;

  useEffect(() => {
    if (reducedMotion) {
      value.setValue(resetTo);
      return;
    }
    value.setValue(0);
    const forward = Animated.timing(value, {
      toValue: 1,
      duration,
      easing: pingPong ? logiNexusMotion.easing.standard : Easing.linear,
      useNativeDriver: true
    });
    // A ping-pong loop eases at both ends and is right for anything that
    // breathes. A sweep restarts from zero and must stay linear, because it is
    // travelling in one direction and a reset must be invisible.
    const animation = pingPong
      ? Animated.loop(
          Animated.sequence([
            forward,
            Animated.timing(value, {
              toValue: 0,
              duration,
              easing: logiNexusMotion.easing.standard,
              useNativeDriver: true
            })
          ])
        )
      : Animated.loop(forward);
    animation.start();
    return () => animation.stop();
  }, [duration, pingPong, reducedMotion, resetTo, value]);

  return value;
}
