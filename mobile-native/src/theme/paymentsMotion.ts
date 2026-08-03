/**
 * Motion for the Payments money hub.
 *
 * Same substrate as `storeMotion` and `adsMotion` — core `Animated`, no new
 * library — and it reuses `useStoreAmbient` / `useStoreEntrance` / `useStorePress`
 * directly. The hooks here exist only for the shapes Payments has that no other
 * screen does: a hero balance that arrives once, a payout dot that pings while a
 * transfer is genuinely in flight, an escrow indicator that breathes, a refund
 * banner that shimmers, a gleam that crosses the pay-out button, and a newly
 * confirmed ledger row that slides in without a reload.
 *
 * The three rules from `storeMotion` hold without exception:
 *   1. Transform/opacity on the native driver wherever possible.
 *   2. Reduce-motion settles at the final state and starts no loop.
 *   3. Ambience is gated on the app being foregrounded (via `useStoreAmbient`).
 *
 * And one rule that belongs to this screen alone:
 *
 *   4. **No ambient animation ever runs on an amount.** Numbers slide in once
 *      on load and then hold perfectly still.
 *
 * Rule 4 is the reason several hooks below animate a *container* or a *sibling*
 * rather than the figure itself. A balance that shimmers, pulses or counts is a
 * number that looks like it is still deciding what it is, and a seller reading
 * their own money needs the opposite impression. It is also a practical
 * accessibility problem: motion on text is what makes a figure hard to read for
 * anyone with a vestibular or attention difficulty, and unlike decoration, the
 * number is the thing they came for and cannot skip.
 */

import { useEffect, useMemo, useRef } from "react";
import { Animated } from "react-native";
import { logiNexusMotion } from "./logiNexusMotion";
import { STORE_AMBIENT, useStoreAmbient } from "./storeMotion";

/** One-shot durations specific to Payments, in ms. */
export const PAYMENTS_ONCE = {
  /** The hero balance sliding up into place on load. Runs exactly once. */
  heroArrive: 450,
  /** A secondary balance card's value following the hero. */
  balanceArrive: 320,
  /** Gap between neighbouring balance cards. */
  balanceStagger: 70,
  /** A newly confirmed payout's row sliding into the ledger. */
  rowInsert: 380,
  /** A day group's rows cascading in on first paint of a page. */
  rowStagger: 40
} as const;

/** Ambient periods specific to Payments, in ms. */
export const PAYMENTS_AMBIENT = {
  /** The dot beside "next payout", pinging while a payout is in flight. */
  payoutDot: 2200,
  /** The escrow card's held-money indicator, breathing. */
  escrowIndicator: 1600,
  /** A slow shimmer crossing the refund action banner. */
  refundShimmer: 7000,
  /** The gleam sweeping across "Pay out now". */
  ctaGleam: 4200
} as const;

/**
 * The hero balance's one-shot arrival: a 0 → 1 driver the hero interpolates
 * into an opacity and a small upward translate.
 *
 * Deliberately keyed to nothing. It has no dependency on the balance value, so
 * a refresh that changes the figure does **not** replay it — the number simply
 * becomes the new number. Re-animating on every value change would turn each
 * background refresh into a little performance around the seller's money, and
 * would violate rule 4 in spirit even though each individual run is a one-shot.
 *
 * `ready` gates the start so the animation belongs to the moment the real
 * figure arrives rather than to the skeleton. Under reduce-motion the hero is
 * simply present.
 */
export function usePaymentsHeroArrival(reducedMotion: boolean, ready: boolean): Animated.Value {
  const value = useRef(new Animated.Value(0)).current;
  const played = useRef(false);

  useEffect(() => {
    if (!ready) return;
    if (played.current) return;
    played.current = true;
    if (reducedMotion) {
      value.setValue(1);
      return;
    }
    const animation = Animated.timing(value, {
      toValue: 1,
      duration: PAYMENTS_ONCE.heroArrive,
      easing: logiNexusMotion.easing.exit,
      useNativeDriver: true
    });
    animation.start();
    return () => animation.stop();
  }, [ready, reducedMotion, value]);

  return value;
}

export type PaymentsBalanceCascade = {
  /** Progress 0 → 1 for the balance card at `index`. */
  progressFor: (index: number) => Animated.Value;
};

/**
 * Staggered arrival for the secondary balance cards (Processing, Escrow, Ad
 * wallet), one beat behind the hero.
 *
 * Like the hero, this plays once per mount and is not keyed to the figures, so
 * a poll that updates Processing does not make the row of cards re-perform.
 */
export function usePaymentsBalanceCascade(
  count: number,
  reducedMotion: boolean,
  ready: boolean
): PaymentsBalanceCascade {
  const values = useMemo(
    () => Array.from({ length: count }, () => new Animated.Value(0)),
    [count]
  );
  const played = useRef(false);

  useEffect(() => {
    if (!ready) return;
    if (played.current) return;
    played.current = true;
    if (reducedMotion) {
      values.forEach((value) => value.setValue(1));
      return;
    }
    const animation = Animated.stagger(
      PAYMENTS_ONCE.balanceStagger,
      values.map((value) =>
        Animated.timing(value, {
          toValue: 1,
          duration: PAYMENTS_ONCE.balanceArrive,
          delay: PAYMENTS_ONCE.heroArrive / 2,
          easing: logiNexusMotion.easing.exit,
          useNativeDriver: true
        })
      )
    );
    animation.start();
    return () => animation.stop();
  }, [reducedMotion, ready, values]);

  return {
    progressFor: (index: number) => values[index] ?? values[0]
  };
}

/**
 * The pinging dot beside the hero's payout sub-line.
 *
 * `inFlight` is not a styling convenience — it is the whole meaning of the
 * element. The dot says "money is moving right now", so it must run only while
 * a real payout row is in flight, and rest invisible otherwise. An always-on
 * dot is decoration that reads as status, and on this screen that is a lie
 * about the seller's money with an animation attached.
 *
 * Ping-pong so it breathes rather than strobes.
 */
export function usePaymentsPayoutDot(reducedMotion: boolean, inFlight: boolean): Animated.Value {
  return useStoreAmbient(PAYMENTS_AMBIENT.payoutDot, reducedMotion, {
    enabled: inFlight,
    resetTo: 0,
    pingPong: true
  });
}

/**
 * The escrow card's held indicator, breathing while money is actually held.
 *
 * Animates the indicator, never the figure beside it (rule 4). Rests fully lit
 * rather than invisible, so under reduce-motion or in the background the card
 * still shows a solid held marker — the state is conveyed by the marker's
 * presence and its label, and the motion adds nothing an AT user would miss.
 */
export function usePaymentsEscrowIndicator(
  reducedMotion: boolean,
  holding: boolean
): Animated.Value {
  return useStoreAmbient(PAYMENTS_AMBIENT.escrowIndicator, reducedMotion, {
    enabled: holding,
    resetTo: 1,
    pingPong: true
  });
}

/**
 * A slow shimmer crossing the refund action banner.
 *
 * Seven seconds is long on purpose: the banner is asking for a response, and a
 * fast pulse on a request that may sit for a day becomes nagging. This is a
 * sweep, so it stays linear and restarts from zero — the reset has to be
 * invisible. Atmospheric only; the deadline and consequence are text.
 */
export function usePaymentsRefundShimmer(reducedMotion: boolean, enabled = true): Animated.Value {
  return useStoreAmbient(PAYMENTS_AMBIENT.refundShimmer, reducedMotion, {
    enabled,
    resetTo: 0
  });
}

/**
 * The gleam sweeping across "Pay out now".
 *
 * Gated on `enabled` so it stops the instant the button is pressed: a button
 * that keeps glinting while its action is in flight reads as still-tappable,
 * which is exactly the wrong signal on the one control that moves money. The
 * caller passes `false` from the same state that disables the button.
 */
export function usePaymentsCtaGleam(reducedMotion: boolean, enabled: boolean): Animated.Value {
  return useStoreAmbient(PAYMENTS_AMBIENT.ctaGleam, reducedMotion, {
    enabled,
    resetTo: 0
  });
}

/**
 * A newly confirmed transaction's row sliding into the ledger.
 *
 * Returns a 0 → 1 driver keyed to the row's own id, so it plays once for a row
 * that genuinely just appeared and never for rows that were already there. This
 * is what lets a confirmed payout update the list without a reload: the balances
 * change once, one row animates in, and nothing else on the screen moves.
 *
 * `isNew` is the caller's judgement — typically "this id was not in the
 * previous page" — because only the caller knows what the list looked like a
 * moment ago. A row that is not new is drawn at rest immediately.
 */
export function usePaymentsRowInsert(
  reducedMotion: boolean,
  isNew: boolean,
  rowKey: unknown
): Animated.Value {
  const value = useRef(new Animated.Value(isNew && !reducedMotion ? 0 : 1)).current;

  useEffect(() => {
    if (!isNew || reducedMotion) {
      value.setValue(1);
      return;
    }
    value.setValue(0);
    const animation = Animated.timing(value, {
      toValue: 1,
      duration: PAYMENTS_ONCE.rowInsert,
      easing: logiNexusMotion.easing.exit,
      useNativeDriver: true
    });
    animation.start();
    return () => animation.stop();
  }, [isNew, reducedMotion, rowKey, value]);

  return value;
}

/** Re-exported so Payments screens import motion from one module. */
export { STORE_AMBIENT as PAYMENTS_STORE_AMBIENT };
