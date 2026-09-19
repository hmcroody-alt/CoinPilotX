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

/**
 * Which "era" of entitlement the cache is in. Bumped by every reset.
 *
 * A reset means "everything I knew about this member is now wrong" — they
 * signed out, signed in, or just bought something. But dropping the cache does
 * not stop the requests already on the wire, and those requests were answered
 * for the era that just ended. Without an era stamp a reply has no way to know
 * it has been overtaken, so it publishes anyway and the stale answer wins
 * simply by landing last.
 *
 * That is not a narrow race. The purchase flow guarantees it: buying opens the
 * App Store sheet, and returning from the sheet fires `PremiumFeatureGate`'s
 * AppState listener, which issues a resolve the server answers BEFORE the
 * receipt is verified — i.e. "FREE". `PremiumCenterScreen` then resets and
 * re-reads, and whichever reply is slower is the one the app keeps. When the
 * pre-purchase reply loses the race, a member who has just paid is written
 * back to FREE.
 *
 * Every premium surface reads this one cache (`PremiumFeatureGate` wraps them
 * all), so a single stale publish does not lock one screen — it locks the whole
 * product, and nothing re-asks until the next foreground.
 */
let generation = 0;

function publish(answer: TierAnswer) {
  cached = answer;
  listeners.forEach((listener) => listener(answer));
}

/**
 * Fetch once and share. Concurrent callers join the in-flight request instead
 * of starting their own.
 *
 * A reply is published only if the era it was issued in is still current. A
 * superseded reply resolves to whatever the cache holds now, so callers that
 * await it still get today's truth rather than the answer to a question about
 * a member who is no longer signed in.
 */
export function loadCanonicalTier(): Promise<TierAnswer> {
  if (inFlight) return inFlight;
  const era = generation;
  const request = fetchCanonicalTier()
    .then((answer) => {
      if (era !== generation) return cached;
      publish(answer);
      return answer;
    })
    .finally(() => {
      // Only clear the handle if it is still ours. After a reset, `inFlight`
      // may already hold a NEWER request; nulling it there would strip that
      // request of its de-duplication and let a third one start beside it.
      if (era === generation) inFlight = null;
    });
  inFlight = request;
  return request;
}

/**
 * Drop the cached answer and disown every request already in flight.
 *
 * Call on sign-out and on any event that could change entitlement (a completed
 * purchase, a restore). Keeping a previous account's tier across a sign-in
 * would hand one member another member's access for as long as the cache
 * survived, which is the cross-user leak this whole mission exists to prevent.
 *
 * Bumping the era is what makes that promise true rather than merely intended.
 * Clearing `inFlight` alone stops the next caller from JOINING the old request;
 * it does nothing about the reply, which still lands and still overwrites the
 * cache. The leak this function is named for therefore survived it in both
 * directions — the previous member's PREMIUM handed to whoever signed in next,
 * and the previous member's FREE handed to a subscriber who had just paid.
 *
 * This deliberately does not re-fetch. Sign-out has no session to ask with, and
 * a request fired into a torn-down session is a guaranteed failure that would
 * render as "we can't confirm your membership". Callers that still have a
 * session (purchase, restore, sign-in) follow the reset with an explicit load.
 */
export function resetCanonicalTier(): void {
  generation += 1;
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
