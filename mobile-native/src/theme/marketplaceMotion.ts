/**
 * Motion additions for the two-sided Marketplace screen.
 *
 * `storeMotion` is the substrate and already provides entrance stagger, a
 * foreground-gated ambient driver, press feedback, badge pop and the tab
 * indicator. Everything here is a Marketplace-specific addition; nothing is
 * reimplemented.
 *
 * The three rules from `storeMotion` still hold — transform/opacity only,
 * reduce-motion settles at the final state, ambience stops when backgrounded —
 * plus one this screen adds:
 *
 * 4. **Ambience stops when off-screen.** The buying grid is virtualized and can
 *    hold dozens of cards, each with a breathing glow. A glow on a card that is
 *    scrolled out of view is pure cost, so every glow takes a `visible` flag fed
 *    from the list's viewability callback and parks itself when false. This is
 *    the difference between two running loops and forty.
 */

import { useEffect, useMemo, useRef } from "react";
import { Animated, Easing } from "react-native";
import { logiNexusMotion } from "./logiNexusMotion";
import { useAppForegrounded } from "./storeMotion";

/** Ambient periods specific to this screen, in ms. */
export const MARKETPLACE_AMBIENT = {
  /** Fresh-offer dot ping. Matches the Store status ping so the two agree. */
  offerPing: 2000,
  /** Shimmer across a fresh offer card. Long — it is a nudge, not a strobe. */
  offerShimmer: 7000,
  /** Cart and offer button glow breathing. */
  buttonGlow: 2600,
  /** Occasional gleam sweep across a glowing button. */
  buttonGleam: 9000,
  /** Boost card rocket bob. */
  rocketBob: 2600,
  /** Saved-search dot ping. */
  savedSearchPing: 2000
} as const;

/** One-shot durations specific to this screen. */
export const MARKETPLACE_ONCE = {
  /** Heart fill pop on save toggle. */
  heartPop: 380,
  /** "Added ✓" confirmation dwell on the cart button before it reverts. */
  addedConfirm: 1400,
  /** Crossfade when switching between Selling and Buying. */
  modeSwap: 180,
  /** SOLD overlay wiping across a row after a sale. */
  soldWipe: 420
} as const;

/**
 * How many distinct phase slots the grid's glows are spread across.
 *
 * With every card starting its cycle at the same instant the grid pulses in
 * unison, which reads as a system-wide alert rather than as ambience. Four slots
 * is enough to break the pattern without any two adjacent cards — in a 2-column
 * grid, indices n and n+1 — ever sharing a phase.
 */
const GLOW_PHASE_SLOTS = 4;

export type MarketplaceGlowOptions = {
  /**
   * False while the card is scrolled out of the viewport. The loop parks rather
   * than running for a card nobody can see.
   */
  visible?: boolean;
  /** Position in the grid. Drives the phase offset. */
  index?: number;
};

/**
 * The breathing glow behind a "Add to cart" or "Make offer" button.
 *
 * Returns a 0 → 1 → 0 value the caller interpolates into opacity or scale.
 * Colour is the caller's business: this hook only supplies the rhythm, which is
 * why one hook serves both the amber cart variant and the green offer variant.
 *
 * The stagger is implemented as a one-time delay before the loop starts, not as
 * a per-iteration offset, so the phase relationship established at mount holds
 * for as long as the card lives.
 */
export function useMarketplaceGlow(
  reducedMotion: boolean,
  options: MarketplaceGlowOptions = {}
): Animated.Value {
  const { visible = true, index = 0 } = options;
  const value = useRef(new Animated.Value(0)).current;
  const foregrounded = useAppForegrounded();

  // Stable per-card offset. `index % slots` rather than a random phase so the
  // pattern is deterministic and a snapshot test sees the same thing twice.
  const phaseDelay = useMemo(
    () => (index % GLOW_PHASE_SLOTS) * (MARKETPLACE_AMBIENT.buttonGlow / GLOW_PHASE_SLOTS),
    [index]
  );

  useEffect(() => {
    if (reducedMotion || !visible || !foregrounded) {
      // Rest dark rather than mid-breath, so a card scrolling back into view
      // starts its cycle cleanly instead of snapping to a half-lit state.
      value.setValue(0);
      return;
    }
    value.setValue(0);
    const animation = Animated.loop(
      Animated.sequence([
        Animated.timing(value, {
          toValue: 1,
          duration: MARKETPLACE_AMBIENT.buttonGlow,
          easing: logiNexusMotion.easing.standard,
          useNativeDriver: true
        }),
        Animated.timing(value, {
          toValue: 0,
          duration: MARKETPLACE_AMBIENT.buttonGlow,
          easing: logiNexusMotion.easing.standard,
          useNativeDriver: true
        })
      ])
    );
    const timer = setTimeout(() => animation.start(), phaseDelay);
    return () => {
      clearTimeout(timer);
      animation.stop();
    };
  }, [foregrounded, phaseDelay, reducedMotion, value, visible]);

  return value;
}

/**
 * The gleam that sweeps across a glowing button every so often.
 *
 * Separate from the glow because it is a sweep, not a breath: it must stay
 * linear and restart from zero, and its period is long enough that pairing it
 * with the glow's easing would look like a stutter.
 */
export function useMarketplaceGleam(
  reducedMotion: boolean,
  options: MarketplaceGlowOptions = {}
): Animated.Value {
  const { visible = true, index = 0 } = options;
  const value = useRef(new Animated.Value(0)).current;
  const foregrounded = useAppForegrounded();

  const phaseDelay = useMemo(
    () => (index % GLOW_PHASE_SLOTS) * (MARKETPLACE_AMBIENT.buttonGleam / GLOW_PHASE_SLOTS),
    [index]
  );

  useEffect(() => {
    if (reducedMotion || !visible || !foregrounded) {
      value.setValue(0);
      return;
    }
    value.setValue(0);
    const animation = Animated.loop(
      Animated.timing(value, {
        toValue: 1,
        duration: MARKETPLACE_AMBIENT.buttonGleam,
        easing: Easing.linear,
        useNativeDriver: true
      })
    );
    const timer = setTimeout(() => animation.start(), phaseDelay);
    return () => {
      clearTimeout(timer);
      animation.stop();
    };
  }, [foregrounded, phaseDelay, reducedMotion, value, visible]);

  return value;
}

/**
 * Heart fill pop on save toggle.
 *
 * Springs past 1 and settles back, so the tap registers as a small celebration.
 * Only fires on the transition into saved — un-saving snaps back without
 * ceremony, because there is nothing to celebrate about removing something.
 */
export function useMarketplaceHeartPop(reducedMotion: boolean, saved: boolean): Animated.Value {
  const scale = useRef(new Animated.Value(1)).current;
  const previous = useRef(saved);

  useEffect(() => {
    const justSaved = saved && !previous.current;
    previous.current = saved;
    if (!justSaved || reducedMotion) {
      scale.setValue(1);
      return;
    }
    scale.setValue(0.7);
    const animation = Animated.spring(scale, {
      toValue: 1,
      friction: 4,
      tension: 160,
      useNativeDriver: true
    });
    animation.start();
    return () => animation.stop();
  }, [reducedMotion, saved, scale]);

  return scale;
}

/**
 * Crossfade between Selling and Buying.
 *
 * The brief is specific that a mode switch must not replay the full entrance
 * cascade — the cascade is an arrival, and switching modes is not an arrival.
 * This is a short dip to partial opacity and back, fast enough to read as a
 * swap rather than a transition.
 *
 * Returns a value that sits at 1 when settled, so a caller can bind it to
 * opacity unconditionally.
 */
export function useMarketplaceModeSwap(mode: string, reducedMotion: boolean): Animated.Value {
  const value = useRef(new Animated.Value(1)).current;

  useEffect(() => {
    if (reducedMotion) {
      value.setValue(1);
      return;
    }
    value.setValue(0.35);
    const animation = Animated.timing(value, {
      toValue: 1,
      duration: MARKETPLACE_ONCE.modeSwap,
      easing: logiNexusMotion.easing.exit,
      useNativeDriver: true
    });
    animation.start();
    return () => animation.stop();
  }, [mode, reducedMotion, value]);

  return value;
}

/**
 * The SOLD overlay wiping across a row when an item sells.
 *
 * Runs once, on the transition into sold, and is keyed on that transition rather
 * than on the sold flag itself — otherwise every row that is already sold would
 * replay the wipe on each render. Under reduce-motion the overlay is simply
 * present.
 */
export function useMarketplaceSoldWipe(reducedMotion: boolean, sold: boolean): Animated.Value {
  const value = useRef(new Animated.Value(sold ? 1 : 0)).current;
  const previous = useRef(sold);

  useEffect(() => {
    const justSold = sold && !previous.current;
    previous.current = sold;
    if (!sold) {
      value.setValue(0);
      return;
    }
    if (!justSold || reducedMotion) {
      value.setValue(1);
      return;
    }
    value.setValue(0);
    const animation = Animated.timing(value, {
      toValue: 1,
      duration: MARKETPLACE_ONCE.soldWipe,
      easing: logiNexusMotion.easing.exit,
      useNativeDriver: true
    });
    animation.start();
    return () => animation.stop();
  }, [reducedMotion, sold, value]);

  return value;
}
