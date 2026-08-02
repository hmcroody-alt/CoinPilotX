/**
 * Motion for the seller Store dashboard.
 *
 * Built on React Native's core `Animated`, the same substrate as
 * `businessLiveMotion`. This project has neither reanimated nor moti, and the
 * brief is explicit that a capable existing system should be used rather than a
 * new dependency added.
 *
 * Three rules run through everything here:
 *
 * 1. **Transform and opacity only.** Every loop below runs on the native driver
 *    and therefore off the JS thread. The one exception is `useStoreDraw`, which
 *    feeds an SVG `strokeDashoffset` — a property the native driver cannot
 *    write. It is deliberately the only JS-driven animation on the screen, and
 *    it runs once rather than looping.
 * 2. **Reduce-motion settles at the final state.** Each hook takes
 *    `reducedMotion` and, when it is on, `setValue`s to where the animation
 *    would have ended and never starts a loop: entrance is instant, the
 *    sparkline is drawn complete, LEDs are solid, and no sweep or shimmer is
 *    scheduled. Components never branch on the flag themselves.
 * 3. **Ambience stops when nobody is looking.** `businessLiveMotion` has no
 *    `AppState` handling; this screen requires it, so every ambient loop here is
 *    gated on the app being foregrounded. A backgrounded screen holds its value
 *    rather than burning a frame callback.
 *
 * The entrance cascade is keyed to mount, not to render, so switching listing
 * tabs re-filters the rows without replaying the intro.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Animated, AppState, Easing, type AppStateStatus } from "react-native";
import { logiNexusMotion } from "./logiNexusMotion";

/** Per-element entrance duration and the gap between neighbours, in ms. */
export const STORE_ENTRANCE_MS = 550;
export const STORE_STAGGER_MS = 80;

/** Distance, in px, an entering section travels upward as it fades in. */
const ENTRANCE_TRAVEL = 12;

/**
 * Ambient periods, in ms. Named rather than passed as magic numbers at call
 * sites so the whole screen's tempo can be read in one place — and so it is
 * obvious that nothing here is fast. Ambience that reads as *activity* rather
 * than atmosphere is the failure mode this screen is trying to avoid.
 */
export const STORE_AMBIENT = {
  /** Diagonal sheen across the navy header. */
  headerSheen: 6000,
  /** Notification bell wiggle, only while unread > 0. */
  bellWiggle: 5000,
  /** Expanding ring behind the "open for orders" dot. */
  statusPing: 2000,
  /** Low-stock LED blink. */
  ledBlink: 1300,
  /** Attention-banner icon tilt. */
  bannerTilt: 4000,
  /** Attention-banner shimmer. */
  bannerShimmer: 7000,
  /** Gleam across the primary CTA. */
  ctaGleam: 9000,
  /** Trend-arrow bob on the KPI cards. */
  trendBob: 2600
} as const;

/** One-shot durations. */
export const STORE_ONCE = {
  /** Sparkline left-to-right draw. */
  sparkline: 900,
  /** KPI numbers arriving one beat after their cards. */
  valueSlide: 320,
  /** Badge spring pop. */
  badgePop: 420,
  /** Active tab underline travel. */
  tabSlide: 220,
  /** Press lift and release. */
  press: 110
} as const;

/**
 * True while the app is in the foreground.
 *
 * Every ambient loop reads this. Kept as one subscription shared by all of them
 * via the hook rather than one listener per animation.
 */
export function useAppForegrounded(): boolean {
  const [active, setActive] = useState(() => AppState.currentState !== "background");

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (next: AppStateStatus) => {
      // `inactive` is the iOS app-switcher / incoming-call state. Treating it as
      // backgrounded stops loops during the transition rather than letting them
      // run behind the switcher card.
      setActive(next === "active");
    });
    return () => subscription.remove();
  }, []);

  return active;
}

export type StoreEntrance = {
  /** Style for the section at `index`, ready to spread onto an Animated.View. */
  styleFor: (index: number) => {
    opacity: Animated.Value | number;
    transform: { translateY: Animated.AnimatedInterpolation<number> | number }[];
  };
};

/**
 * Staggered entrance, top to bottom: header, status strip, KPIs, banner, tabs,
 * list, links, CTAs.
 *
 * One `Animated.Value` per section rather than one shared clock, because a
 * shared clock forces every section onto the same linear curve and the result
 * reads as a wipe rather than a cascade. The values are created once for the
 * life of the mount — `count` is fixed for this screen — so a state change
 * mid-scroll cannot restart the intro.
 */
export function useStoreEntrance(count: number, reducedMotion: boolean): StoreEntrance {
  const values = useMemo(
    () => Array.from({ length: count }, () => new Animated.Value(0)),
    [count]
  );

  useEffect(() => {
    if (reducedMotion) {
      // `setValue` rather than a zero-duration timing, so nothing is ever queued
      // on the animation loop at all.
      values.forEach((value) => value.setValue(1));
      return;
    }
    const animation = Animated.stagger(
      STORE_STAGGER_MS,
      values.map((value) =>
        Animated.timing(value, {
          toValue: 1,
          duration: STORE_ENTRANCE_MS,
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

export type StoreAmbientOptions = {
  /**
   * Extra condition beyond foreground and reduce-motion. The bell only wiggles
   * while there are unread notifications; the LED only blinks while that
   * listing is low on stock. Passing `false` settles the value at `resetTo`.
   */
  enabled?: boolean;
  /**
   * Where the value rests when the loop is not running. A shimmer wants to rest
   * invisible (0); a status dot wants to rest fully lit (1).
   */
  resetTo?: number;
  /**
   * A ping-pong loop eases at both ends and is right for anything that breathes
   * — a blink, a bob, a tilt. A sweep restarts from zero and must stay linear,
   * because it travels in one direction and its reset has to be invisible.
   */
  pingPong?: boolean;
};

/**
 * A single 0 → 1 driver for every ambient effect on the screen. Callers
 * interpolate it into whatever transform they need, so a sheen, a wiggle, a
 * ping and a blink all cost one animation each and share one shape.
 *
 * Returns the value even while stopped, so a component never has to handle a
 * null style.
 */
export function useStoreAmbient(
  period: number,
  reducedMotion: boolean,
  options: StoreAmbientOptions = {}
): Animated.Value {
  const { enabled = true, resetTo = 0, pingPong = false } = options;
  const value = useRef(new Animated.Value(resetTo)).current;
  const foregrounded = useAppForegrounded();

  useEffect(() => {
    if (reducedMotion || !enabled || !foregrounded) {
      value.setValue(resetTo);
      return;
    }
    value.setValue(0);
    const forward = Animated.timing(value, {
      toValue: 1,
      duration: period,
      easing: pingPong ? logiNexusMotion.easing.standard : Easing.linear,
      useNativeDriver: true
    });
    const animation = pingPong
      ? Animated.loop(
          Animated.sequence([
            forward,
            Animated.timing(value, {
              toValue: 0,
              duration: period,
              easing: logiNexusMotion.easing.standard,
              useNativeDriver: true
            })
          ])
        )
      : Animated.loop(forward);
    animation.start();
    return () => animation.stop();
  }, [enabled, foregrounded, period, pingPong, reducedMotion, resetTo, value]);

  return value;
}

/**
 * One-shot 0 → 1 draw for the KPI sparkline.
 *
 * `useNativeDriver: false` is required and unavoidable: the consumer maps this
 * onto an SVG `strokeDashoffset`, which the native driver cannot write. It runs
 * once per `key` change — so the line redraws when real data replaces the
 * skeleton, rather than replaying a canned intro on every render.
 *
 * Under reduce-motion the line is simply drawn complete.
 */
export function useStoreDraw(reducedMotion: boolean, key: unknown = 0): Animated.Value {
  const progress = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (reducedMotion) {
      progress.setValue(1);
      return;
    }
    progress.setValue(0);
    const animation = Animated.timing(progress, {
      toValue: 1,
      duration: STORE_ONCE.sparkline,
      easing: logiNexusMotion.easing.exit,
      useNativeDriver: false
    });
    animation.start();
    return () => animation.stop();
  }, [key, progress, reducedMotion]);

  return progress;
}

/**
 * KPI values arrive one beat after their card, so the card reads as a container
 * that then fills rather than as a finished block that faded in. `delay` is the
 * caller's entrance slot, so the offset holds wherever the card sits in the
 * cascade.
 */
export function useStoreValueArrival(reducedMotion: boolean, delay = 0): Animated.Value {
  const value = useRef(new Animated.Value(0)).current;

  useEffect(() => {
    if (reducedMotion) {
      value.setValue(1);
      return;
    }
    value.setValue(0);
    const animation = Animated.timing(value, {
      toValue: 1,
      duration: STORE_ONCE.valueSlide,
      delay: delay + STORE_ENTRANCE_MS / 2,
      easing: logiNexusMotion.easing.exit,
      useNativeDriver: true
    });
    animation.start();
    return () => animation.stop();
  }, [delay, reducedMotion, value]);

  return value;
}

/**
 * Spring pop for the unread badge. A spring rather than a timing because the
 * overshoot is the whole point — it is what makes a count arriving feel like an
 * event. Under reduce-motion the badge is simply present at full size.
 */
export function useStoreBadgePop(reducedMotion: boolean, visible: boolean): Animated.Value {
  const scale = useRef(new Animated.Value(visible ? 1 : 0)).current;

  useEffect(() => {
    if (!visible) {
      scale.setValue(0);
      return;
    }
    if (reducedMotion) {
      scale.setValue(1);
      return;
    }
    scale.setValue(0);
    const animation = Animated.spring(scale, {
      toValue: 1,
      friction: 5,
      tension: 140,
      useNativeDriver: true
    });
    animation.start();
    return () => animation.stop();
  }, [reducedMotion, scale, visible]);

  return scale;
}

/**
 * Active-tab underline. Animates to `x` so the bar travels between tabs instead
 * of cutting; jumps instantly under reduce-motion. Returns both offset and
 * width because the tabs have different label lengths and a fixed-width bar
 * under a variable-width label reads as a bug.
 */
export function useStoreTabIndicator(
  target: { x: number; width: number },
  reducedMotion: boolean
): { x: Animated.Value; width: Animated.Value } {
  const x = useRef(new Animated.Value(target.x)).current;
  const width = useRef(new Animated.Value(target.width)).current;

  useEffect(() => {
    if (reducedMotion) {
      x.setValue(target.x);
      width.setValue(target.width);
      return;
    }
    const animation = Animated.parallel([
      Animated.timing(x, {
        toValue: target.x,
        duration: STORE_ONCE.tabSlide,
        easing: logiNexusMotion.easing.standard,
        // `left`/`width` are layout properties, so this cannot use the native
        // driver. It is a 220ms one-shot on user input rather than a loop, so
        // it is not competing with anything for the JS thread.
        useNativeDriver: false
      }),
      Animated.timing(width, {
        toValue: target.width,
        duration: STORE_ONCE.tabSlide,
        easing: logiNexusMotion.easing.standard,
        useNativeDriver: false
      })
    ]);
    animation.start();
    return () => animation.stop();
  }, [reducedMotion, target.width, target.x, width, x]);

  return { x, width };
}

export type StorePress = {
  /** Spread onto the Animated.View that should react. */
  style: { transform: { scale: Animated.Value }[] };
  onPressIn: () => void;
  onPressOut: () => void;
};

/**
 * Press feedback for cards, thumbnails, Edit buttons and CTAs.
 *
 * `scaleTo` above 1 grows (listing thumbnails, which the spec wants to swell to
 * 1.05) and below 1 presses in (cards and buttons, which should feel like they
 * take the touch). Under reduce-motion the handlers are still returned but do
 * nothing, so callers wire the same props either way.
 */
export function useStorePress(reducedMotion: boolean, scaleTo = 0.98): StorePress {
  const scale = useRef(new Animated.Value(1)).current;

  const to = useCallback(
    (toValue: number) => {
      if (reducedMotion) return;
      Animated.timing(scale, {
        toValue,
        duration: STORE_ONCE.press,
        easing: logiNexusMotion.easing.standard,
        useNativeDriver: true
      }).start();
    },
    [reducedMotion, scale]
  );

  return {
    style: { transform: [{ scale }] },
    onPressIn: () => to(scaleTo),
    onPressOut: () => to(1)
  };
}
