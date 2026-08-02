/**
 * Motion for the two-sided Advertising manager.
 *
 * This adds nothing new to the substrate — it is the same core `Animated` used
 * by `storeMotion`, and it reuses that module's primitives (`useStoreAmbient`,
 * `useAppForegrounded`, entrance cascade, press) directly. The hooks here exist
 * only for the shapes Advertising has that Store does not: a bar chart that
 * grows on data arrival, a budget bar that fills once, a pause switch whose
 * thumb travels, and two quiet content-side ambiences (a violet ring on a
 * promoted post, a spark on the "outperforming" nudge).
 *
 * The three rules from `storeMotion` hold without exception:
 *   1. Transform/opacity on the native driver wherever possible.
 *   2. Reduce-motion settles at the final state and starts no loop.
 *   3. Ambience is gated on the app being foregrounded (via `useStoreAmbient`).
 */

import { useEffect, useMemo, useRef } from "react";
import { Animated } from "react-native";
import { logiNexusMotion } from "./logiNexusMotion";
import { STORE_AMBIENT, useStoreAmbient } from "./storeMotion";

/** One-shot durations specific to Advertising, in ms. */
export const ADS_ONCE = {
  /** A single spend bar growing from the axis. */
  barGrow: 520,
  /** Gap between neighbouring bars in the cascade. */
  barStagger: 55,
  /** Budget pacing bar filling left-to-right. */
  budgetFill: 640,
  /** Pause-switch thumb sliding across its track. */
  switchThrow: 180
} as const;

/** Ambient periods specific to Advertising, in ms. */
export const ADS_AMBIENT = {
  /** Gold shine drifting across the "today" spend bar. */
  todayShine: 5200,
  /** Violet ring breathing behind a promoted post while it delivers. */
  promotedRing: 2800,
  /** Spark bob on the "this post is outperforming" suggestion. */
  suggestionSpark: 3000
} as const;

export type AdsBarCascade = {
  /**
   * Progress 0 → 1 for the bar at `index`. The chart maps it onto a
   * bottom-anchored scaleY so bars grow up from the axis. Redraws when the
   * series changes so real data replaces skeleton bars with a grow, not a cut.
   */
  progressFor: (index: number) => Animated.Value;
};

/**
 * Staggered grow-in for the spend chart's bars, left to right.
 *
 * Keyed to the series (`seriesKey`) rather than to mount: the seven-day chart
 * animates when its data arrives and again if the account switch swaps the
 * series, but not on unrelated re-renders. Values are created for the fixed bar
 * `count`.
 */
export function useAdsBarCascade(
  count: number,
  reducedMotion: boolean,
  seriesKey: unknown = 0
): AdsBarCascade {
  const values = useMemo(
    () => Array.from({ length: count }, () => new Animated.Value(0)),
    [count]
  );

  useEffect(() => {
    if (reducedMotion) {
      values.forEach((value) => value.setValue(1));
      return;
    }
    values.forEach((value) => value.setValue(0));
    const animation = Animated.stagger(
      ADS_ONCE.barStagger,
      values.map((value) =>
        Animated.timing(value, {
          toValue: 1,
          duration: ADS_ONCE.barGrow,
          easing: logiNexusMotion.easing.exit,
          useNativeDriver: true
        })
      )
    );
    animation.start();
    return () => animation.stop();
  }, [reducedMotion, seriesKey, values]);

  return {
    progressFor: (index: number) => values[index] ?? values[0]
  };
}

/**
 * One-shot 0 → 1 fill for the budget pacing bar. The consumer interpolates it
 * into the fill's `width` (`"0%"` → `"<fraction>%"`), so the bar grows from the
 * start of the track to the spent fraction. `width` is a layout property, so
 * this is JS-driven — acceptable because it is a one-shot on data arrival, not
 * a loop. Re-runs when the fraction changes so a mid-session spend tick animates
 * the delta. Drawn complete under reduce-motion.
 */
export function useAdsBudgetFill(reducedMotion: boolean, fraction: number): Animated.Value {
  const value = useRef(new Animated.Value(reducedMotion ? 1 : 0)).current;

  useEffect(() => {
    if (reducedMotion) {
      value.setValue(1);
      return;
    }
    value.setValue(0);
    const animation = Animated.timing(value, {
      toValue: 1,
      duration: ADS_ONCE.budgetFill,
      easing: logiNexusMotion.easing.exit,
      // `width` cannot be driven natively; this is a short one-shot, not a loop.
      useNativeDriver: false
    });
    animation.start();
    return () => animation.stop();
  }, [fraction, reducedMotion, value]);

  return value;
}

/**
 * Pause-switch thumb position, 0 (off/left) → 1 (on/right). A short throw on
 * toggle so the control reads as a physical switch rather than a checkbox that
 * repaints. Jumps instantly under reduce-motion — the switch role and label
 * still announce the state, so no information rides on the travel.
 */
export function useAdsSwitchThumb(reducedMotion: boolean, on: boolean): Animated.Value {
  const value = useRef(new Animated.Value(on ? 1 : 0)).current;

  useEffect(() => {
    if (reducedMotion) {
      value.setValue(on ? 1 : 0);
      return;
    }
    const animation = Animated.timing(value, {
      toValue: on ? 1 : 0,
      duration: ADS_ONCE.switchThrow,
      easing: logiNexusMotion.easing.standard,
      useNativeDriver: true
    });
    animation.start();
    return () => animation.stop();
  }, [on, reducedMotion, value]);

  return value;
}

/**
 * Gold shine drifting across the live "today" spend bar. Atmospheric only — it
 * carries no state, is the first thing reduce-motion removes, and stops when the
 * app is backgrounded. Returns a 0 → 1 driver the bar interpolates into an
 * opacity/translate sweep.
 */
export function useAdsTodayShine(reducedMotion: boolean, enabled = true): Animated.Value {
  return useStoreAmbient(ADS_AMBIENT.todayShine, reducedMotion, {
    enabled,
    resetTo: 0
  });
}

/**
 * Violet ring breathing behind a post while it is actively promoting. Ping-pong
 * so it eases at both ends and reads as a slow pulse, not a strobe. Only runs
 * while `delivering` is true; a completed or paused promotion holds a still
 * ring.
 */
export function useAdsPromotedRing(reducedMotion: boolean, delivering: boolean): Animated.Value {
  return useStoreAmbient(ADS_AMBIENT.promotedRing, reducedMotion, {
    enabled: delivering,
    resetTo: 1,
    pingPong: true
  });
}

/**
 * Spark bob on the "this post is outperforming — promote it?" suggestion. The
 * same breathing shape as the promoted ring, slower, and it never conveys state
 * — the suggestion's text does that. Settles still under reduce-motion.
 */
export function useAdsSuggestionSpark(reducedMotion: boolean, enabled = true): Animated.Value {
  return useStoreAmbient(ADS_AMBIENT.suggestionSpark, reducedMotion, {
    enabled,
    resetTo: 0,
    pingPong: true
  });
}

/** Re-exported so Advertising screens import motion from one module. */
export { STORE_AMBIENT as ADS_STORE_AMBIENT };
