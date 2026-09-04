/**
 * Cached-screen reconcile for API-level premium denials.
 *
 * A screen can be sitting on data it fetched while entitled when the server
 * starts answering `premium_required` — the trial expired mid-session, or a
 * refund landed. The screen's own catch block decides what to render; this
 * helper makes sure the *shared* canonical answer is re-fetched at the same
 * moment, so the gate wrapping this screen (and every other premium surface)
 * flips to the upsell without waiting for the next foreground.
 *
 * Returns whether the error was a premium denial, so call sites can use it as
 * a drop-in replacement for `isPremiumRequired(error)`.
 */

import { isPremiumRequired } from "../api/cryptoPremium";
import { loadCanonicalTier } from "./useCanonicalTier";

export function reconcilePremiumRequired(error: unknown): boolean {
  if (!isPremiumRequired(error)) return false;
  void loadCanonicalTier();
  return true;
}
