/**
 * Whether organic Marketplace discovery may place on the social surfaces.
 *
 * ## Why this is a module owner and not a context read
 *
 * The three consuming hooks (`useFeedCommerce`, `useReelsCommerce`,
 * `useMessengerCommerce`) are deliberately plain fetch hooks: options in, state
 * out, no providers. Reaching into `usePreferences()` from inside them would
 * give Home, Reels and Messenger a hard dependency on `<PreferencesProvider>`
 * being mounted — and `usePreferences` *throws* when it is not. That turns a
 * missing provider from "commerce is quiet" into "the feed does not render",
 * which is precisely the failure mode the mission forbids: a recommendation
 * failure must never break the feed.
 *
 * So the preference is pushed *out* to this module instead, the same way
 * `settings/store.tsx` already pushes `accessibility.hapticFeedback` into
 * `native/haptics` via `setHapticsEnabled`. Same effect block, one line apart.
 *
 * ## Which way this fails, and why that is allowed
 *
 * Before the store has hydrated, this reports `true` — discovery is allowed.
 * That is a fail-*open* default, which would be indefensible if this were the
 * enforcement point. It is not. `services/commerce_discovery/preferences.py::
 * viewer_policy` reads the same four keys off the synced document on every
 * request and fails closed; a user who has switched discovery off gets an empty
 * placement list from the server whatever the client believes. What this module
 * buys is the part the server cannot: not making the request at all, and
 * reacting the instant the switch moves rather than a sync round trip later.
 *
 * Getting that backwards — failing closed here — would mean every cold start
 * showed no commerce until hydration finished, for every user, to protect a
 * decision the server already protects.
 */
import { useSyncExternalStore } from "react";
import { CommercePreferences } from "../settings/schema";

/**
 * Length of the pause, shared by the two places that can start one.
 *
 * Deliberately a client-side constant rather than something fetched from the
 * server's `COMMERCE_DISCOVERY_HIDE_DAYS`, and the two cannot drift apart in a
 * way that matters: what gets stored is an **absolute instant**, not a
 * duration. The server honours the timestamp it is given. If the backend's own
 * default is ever retuned, a pause already in flight keeps the end date the
 * user was shown, which is the only behaviour that would not look like a bug
 * from the outside.
 */
export const COMMERCE_PAUSE_DAYS = 30;

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Whether a stored `snoozeUntil` is a pause that is still running.
 *
 * Read against the clock rather than trusted as a flag, because the normalizer
 * deliberately does not clear the value when it lapses — clearing it would make
 * normalization a function of the wall clock and push a patch nobody asked for.
 * Expiry is the reader's question, and this is the reader.
 *
 * Unparseable reads as "no pause". That matches `subject.py::is_expired`, which
 * fails closed on a timestamp it cannot read, so client and server agree about
 * a corrupt value instead of one of them showing a pause the other is ignoring.
 */
export function isPauseActive(snoozeUntil: string, now: number = Date.now()): boolean {
  if (!snoozeUntil) return false;
  const millis = new Date(snoozeUntil).getTime();
  if (!Number.isFinite(millis)) return false;
  return millis > now;
}

/** The instant a pause started now would end. */
export function pauseEndsAt(now: number = Date.now()): string {
  return new Date(now + COMMERCE_PAUSE_DAYS * DAY_MS).toISOString();
}

/** Organic discovery on feed, reels and messenger — not inside Marketplace. */
export function socialDiscoveryAllowed(commerce: CommercePreferences, now: number = Date.now()): boolean {
  if (!commerce.marketplaceRecommendations) return false;
  return !isPauseActive(commerce.snoozeUntil, now);
}

/* -------------------------------------------------------------------------- */
/*                                 The owner                                   */
/* -------------------------------------------------------------------------- */

let allowed = true;
const listeners = new Set<() => void>();

/**
 * Wired from the preferences store; do not call from screens.
 *
 * Mirrors `setHapticsEnabled`. The store calls this whenever the commerce group
 * resolves, so every consuming hook gets the preference for free.
 */
export function setSocialDiscoveryAllowed(next: boolean): void {
  if (next === allowed) return;
  allowed = next;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

// Returns a boolean, so `useSyncExternalStore`'s identity check is a value
// comparison and this cannot loop the way an object snapshot would.
function getSnapshot(): boolean {
  return allowed;
}

/**
 * Subscribe to the master switch.
 *
 * Note what this does *not* do: re-evaluate a running pause against the clock.
 * The value changes when the preference changes, not when time passes, so a
 * thirty-day pause that lapses while the app sits in the foreground stays in
 * effect until the next preference change or the next launch — at which point
 * hydration recomputes it. Polling a timestamp thirty days out on every surface
 * would cost every user a timer to fix a case that requires leaving an app
 * foregrounded for a month, and the server re-reads the same instant on the
 * request that follows anyway.
 */
export function useSocialDiscoveryAllowed(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getSnapshot);
}

/* -------------------------------------------------------------------------- */
/*                          Starting a pause from a card                       */
/* -------------------------------------------------------------------------- */

type PauseWriter = (snoozeUntil: string) => void;

let writePause: PauseWriter | null = null;

/** Wired from the preferences store; do not call from screens. */
export function registerCommercePauseWriter(writer: PauseWriter | null): void {
  writePause = writer;
}

/**
 * Start a pause from the card's ••• menu.
 *
 * Without this the two "hide for 30 days" controls wrote to two different
 * stores: the card's tap recorded a `commerce_discovery_suppressions` row with
 * `scope="all"` server-side, while Settings wrote `commerce.snoozeUntil` on the
 * preference document. Both really did pause discovery, so nothing looked
 * broken — but Settings could only see its own, so a user who paused from a
 * card and then went looking for the off switch found the screen cheerfully
 * reporting that suggestions were on, with no Resume anywhere. Writing the
 * preference here as well makes the screen able to describe, and undo, a pause
 * it did not start.
 *
 * The server-side suppression row is still written by the feedback call; this
 * is additive. Two records of the same pause is the right amount, because they
 * expire on the same instant and either one alone is enough to honour it.
 */
export function startCommercePause(now: number = Date.now()): void {
  writePause?.(pauseEndsAt(now));
}
