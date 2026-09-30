/**
 * The one delivery line every buyer surface renders.
 *
 * ## Why a component and not a string on each screen
 *
 * There are eight surfaces that will eventually show a delivery promise — product
 * card, product page, variant change, cart line, checkout summary, order
 * confirmation, purchase history, order detail. Each one writing its own sentence
 * is eight sentences for one parcel, and they diverge the first time anybody
 * changes one. So the sentence is built once in `deliveryCopy`, drawn once here,
 * and every surface mounts this.
 *
 * ## The four states, and why exactly one renders
 *
 * `loading`, `estimate`, `unavailable`, `blocked`. They are mutually exclusive by
 * construction rather than by a chain of `&&`s: the hook returns either an answer
 * or `null` while in flight, and an answer always carries a tone. There is no
 * input for which this component renders nothing — an absent delivery line is the
 * gap the old "arranged with the seller after your order is confirmed" copy grew
 * into, and the way to not grow it back is to make silence unrepresentable.
 *
 * The failure state deliberately does not look like an error. A missing estimate
 * is not a broken product page: the item is real, the price is real, and the
 * buyer can still add it to a cart. A red banner here would cost sales to defend
 * a line that is a nicety.
 *
 * ## Why the estimate is not fetched by this component's parent
 *
 * `useDeliveryEstimate` lives here alongside it so that a screen mounts one thing
 * and gets both the request and the rendering. A parent that owned the fetch
 * would own the abort, the country change and the retry too, and those are the
 * three places a delivery line goes wrong: a stale answer from the previous
 * variant, a request that resolves after the screen is gone, a retry that fires
 * twice.
 */

import { Ionicons } from "@expo/vector-icons";
import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator, Pressable, StyleSheet, Text, View } from "react-native";

import type { DeliveryAnswer } from "../../api/delivery";
import { fetchDeliveryEstimate } from "../../api/delivery";
import { storeLight } from "../../theme/marketplaceLight";
import { DELIVERY_LOADING_TEXT, deliveryCopy } from "./deliveryCopy";

export type UseDeliveryEstimate = {
  /** `null` until the first answer arrives. Not an empty estimate: a pending
   * request and a refused one are different things and the caller must be able
   * to tell them apart without inspecting a reason. */
  answer: DeliveryAnswer | null;
  loading: boolean;
  /** Ask again. Safe to call while a request is in flight — the older answer is
   * discarded by the sequence check below rather than racing the newer one. */
  retry: () => void;
};

/**
 * Fetch one estimate and keep it in step with its inputs.
 *
 * Three hazards, each handled explicitly:
 *
 * 1. **An answer from the previous inputs.** Changing the country or the quantity
 *    starts a second request, and the first can land after it — which would show
 *    a date for the wrong corridor. Guarded by a monotonic sequence number, not
 *    by comparing the answers: two corridors can legitimately produce the same
 *    window, so the only reliable discriminator is which request it was.
 * 2. **A request that has not been sent yet for a screen that is gone.** The
 *    debounce window is where a supplier call leaks: the product page is popped
 *    before the timer fires and the request is pure waste. Guarded by
 *    `clearTimeout` in the cleanup, which is observable and tested.
 * 3. **A request for a listing that has no id yet.** The product screen can
 *    render before its listing loads. No ref, no request: there is nothing to
 *    quote and a placeholder request would spend a supplier call on nothing.
 *
 * There is deliberately *no* fourth guard for "the request resolved after
 * unmount". Under React 19 a state update on an unmounted tree is a silent no-op
 * — the old warning is gone — so an `alive` boolean beside the sequence check has
 * no behavioural signature at all: a test written for it passes with the flag
 * removed, which is worse than not having the test. The sequence number already
 * covers every case where a *live* screen could be shown the wrong answer, and
 * that is the case that harms a buyer.
 *
 * `debounceMs` exists for the quantity stepper. Freight depends on quantity, so
 * the estimate has to follow it — but a buyer tapping `+` five times would
 * otherwise fire five requests, and `pulseApi` only coalesces GETs, so all five
 * reach the server and four of them spend the supplier's rate limit on an answer
 * nobody will see. The delay applies to the first request too rather than only to
 * changes: a branch that fires immediately on mount and lazily thereafter is a
 * branch that has to know which one it is, and a few hundred milliseconds before
 * a delivery line resolves is invisible next to the network round trip it is
 * waiting for anyway.
 */
export function useDeliveryEstimate(
  variantRef: string | null | undefined,
  options?: { quantity?: number; country?: string; debounceMs?: number }
): UseDeliveryEstimate {
  const quantity = options?.quantity;
  const country = options?.country;
  const debounceMs = options?.debounceMs ?? 0;
  const [answer, setAnswer] = useState<DeliveryAnswer | null>(null);
  const [loading, setLoading] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const sequence = useRef(0);

  useEffect(() => {
    const ref = String(variantRef || "").trim();
    if (!ref) {
      setAnswer(null);
      setLoading(false);
      return;
    }
    const mine = ++sequence.current;
    setLoading(true);
    // Clearing the previous answer is the point: an estimate for the old country
    // sitting under a spinner reads as the answer to the new question.
    setAnswer(null);
    const timer = setTimeout(() => {
      fetchDeliveryEstimate({ variantRef: ref, quantity, country }).then((next) => {
        // The one guard that matters: a superseded request landing on a screen
        // that is still mounted is the only way a buyer sees a window for a
        // parcel they are not looking at.
        if (sequence.current !== mine) return;
        setAnswer(next);
        setLoading(false);
      });
    }, debounceMs);
    // Bumping the sequence here, not just clearing the timer: a request already in
    // flight when the inputs change is superseded by that change itself, even
    // though the effect that replaces it will bump the sequence again. Without
    // this, unmounting mid-flight leaves the last sequence number matching and
    // the resolved request would still call `setAnswer`.
    return () => {
      sequence.current += 1;
      clearTimeout(timer);
    };
  }, [variantRef, quantity, country, attempt, debounceMs]);

  const retry = useCallback(() => setAttempt((n) => n + 1), []);
  return { answer, loading, retry };
}

export type DeliveryEstimateLineProps = {
  answer: DeliveryAnswer | null;
  loading: boolean;
  onRetry?: () => void;
  /** Hide the `FREE Shipping` line. For surfaces that state the shipping price
   * themselves — a cart total already has a shipping row, and two of them is a
   * buyer counting the same zero twice. */
  hideShipping?: boolean;
};

/**
 * Draw one delivery line.
 *
 * Presentational and total. Every branch returns a row with an icon and at least
 * one line of text; the only conditional content is the retry affordance, which
 * appears exactly when the copy layer says the underlying reason is retryable
 * *and* the caller gave it somewhere to go.
 */
export function DeliveryEstimateLine({
  answer,
  loading,
  onRetry,
  hideShipping
}: DeliveryEstimateLineProps) {
  if (loading || !answer) {
    return (
      <View style={styles.row}>
        <ActivityIndicator
          size="small"
          color={storeLight.text.muted}
          // The spinner is the state, so it is what gets announced. Without this
          // a screen reader hears the text and not the fact that it is pending.
          accessibilityLabel={DELIVERY_LOADING_TEXT}
        />
        <Text style={[styles.text, styles.muted]}>{DELIVERY_LOADING_TEXT}</Text>
      </View>
    );
  }

  const copy = deliveryCopy(answer);
  const problem = copy.tone !== "ESTIMATE";
  return (
    <View style={styles.block}>
      <View style={styles.row}>
        <Ionicons
          name={copy.icon}
          size={18}
          color={problem ? storeLight.text.muted : storeLight.status.success}
        />
        {/* One Text for the whole sentence, so a screen reader reads a delivery
            promise as one phrase rather than as a date and then a country. */}
        <Text style={[styles.text, problem && styles.muted]}>{copy.text}</Text>
      </View>
      {copy.stale ? (
        <Text style={styles.footnote}>Last checked with the supplier a short while ago.</Text>
      ) : null}
      {copy.shipping && !hideShipping ? (
        <View style={styles.row}>
          <Ionicons name="pricetag-outline" size={18} color={storeLight.status.success} />
          <Text style={[styles.text, styles.free]}>{copy.shipping}</Text>
        </View>
      ) : null}
      {copy.retryable && onRetry ? (
        <Pressable
          accessibilityRole="button"
          accessibilityLabel="Check delivery again"
          onPress={onRetry}
          style={styles.retry}
        >
          <Text style={styles.retryText}>Check again</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  block: { gap: 6 },
  row: { flexDirection: "row", alignItems: "center", gap: 10 },
  text: { flex: 1, fontSize: 14, color: storeLight.text.primary, lineHeight: 20 },
  muted: { color: storeLight.text.muted },
  free: { fontWeight: "700", color: storeLight.status.success },
  footnote: { fontSize: 12, color: storeLight.text.muted, marginLeft: 28 },
  retry: { marginLeft: 28, alignSelf: "flex-start", paddingVertical: 4 },
  retryText: { fontSize: 13, fontWeight: "600", color: storeLight.text.link }
});
