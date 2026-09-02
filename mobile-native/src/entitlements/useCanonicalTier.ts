/**
 * React access to the canonical tier answer, with one shared fetch.
 *
 * Several surfaces need the answer at once — the navigation drawer, the profile
 * header, a feature gate on the screen underneath. Letting each mount its own
 * request would fire three calls for one fact and, worse, would let them land
 * out of order and disagree on screen for a frame. So the answer lives in a
 * module-level cache with a single in-flight promise, and every subscriber gets
 * the same object.
 *
 * The cache holds the answer, never a *derived* decision, so nothing here can
 * drift from `canonicalTier` — subscribers re-run `isEntitled` themselves.
 */

import { useEffect, useState } from "react";

import { fetchCanonicalTier, TierAnswer, UNKNOWN_TIER } from "./canonicalTier";

let cached: TierAnswer = UNKNOWN_TIER;
let inFlight: Promise<TierAnswer> | null = null;
const listeners = new Set<(answer: TierAnswer) => void>();

function publish(answer: TierAnswer) {
  cached = answer;
  listeners.forEach((listener) => listener(answer));
}

/**
 * Fetch once and share. Concurrent callers join the in-flight request instead
 * of starting their own.
 */
export function loadCanonicalTier(): Promise<TierAnswer> {
  if (inFlight) return inFlight;
  inFlight = fetchCanonicalTier()
    .then((answer) => {
      publish(answer);
      return answer;
    })
    .finally(() => {
      inFlight = null;
    });
  return inFlight;
}

/**
 * Drop the cached answer.
 *
 * Call on sign-out and on any event that could change entitlement (a completed
 * purchase, a restore). Keeping a previous account's tier across a sign-in
 * would hand one member another member's access for as long as the cache
 * survived, which is the cross-user leak this whole mission exists to prevent.
 */
export function resetCanonicalTier(): void {
  inFlight = null;
  publish(UNKNOWN_TIER);
}

/**
 * The canonical answer for the signed-in member.
 *
 * Returns `UNKNOWN_TIER` until the first resolve lands, which is honest rather
 * than optimistic: a caller that renders on this value shows "we don't know
 * yet" instead of flashing "Free" at somebody who paid.
 */
export function useCanonicalTier(): TierAnswer {
  const [answer, setAnswer] = useState<TierAnswer>(cached);

  useEffect(() => {
    listeners.add(setAnswer);
    setAnswer(cached);
    void loadCanonicalTier();
    return () => {
      listeners.delete(setAnswer);
    };
  }, []);

  return answer;
}
