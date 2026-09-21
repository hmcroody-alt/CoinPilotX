/**
 * The Marketplace shelves — the densest placement in the system, and the only
 * one that has to know when to get out of the way.
 *
 * ## Why this surface gets the largest budget
 *
 * `surface_caps()["marketplace"]` is `(8, 1000)` and `min_score("marketplace")`
 * carries a **−0.15** lift — the only negative one. Everywhere else a
 * recommendation interrupts something; here the user opened a shop. Suggesting
 * products to someone who came to browse products is the surface working, not
 * the surface intruding, so the floor drops and the cap rises.
 *
 * ## Why most of this file is about *not* showing them
 *
 * The density is exactly why the suppression rules matter. A shelf is a browse
 * affordance: it answers "show me something" and it is ranked against the whole
 * catalogue. The moment the user narrows — types a search, picks a category,
 * opens one seller's store — they have asked a *specific* question, and a rail
 * of whole-catalogue recommendations sitting above their answer is not dense,
 * it is obstructive. Four states stand the shelves down:
 *
 *   - **A search query.** The grid below is the answer to what they typed.
 *     Shelves ranked on something else occupy the top of it.
 *   - **A category filter.** They narrowed to "shoes"; shelves that ignore the
 *     narrowing contradict the control the user just used.
 *   - **A seller's store.** Taking the top of a seller's own storefront to show
 *     other sellers' products is a thing a marketplace can do and should not.
 *     This one is not a UX preference — it is whose surface it is.
 *   - **Offline.** The grid is honestly labelled as cached. Freshly-ranked
 *     shelves beside it would not be, and "Trending" from an unknown time ago
 *     is a claim rather than a fact.
 *
 * The hook takes these as one `suppressed` boolean rather than four fields
 * because the screen owns the definition of "narrowed" and this module should
 * not grow a second opinion about it.
 *
 * ## Shape
 *
 * Mirrors `useFeedCommerce` / `useReelsCommerce` / `useMessengerCommerce`:
 * fetch, dismissal state, feedback writes. The difference is that dismissals
 * here prune *within* a shelf and drop the shelf when it empties, because a
 * heading over an empty rail is the blank hole the brief calls out.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CommerceFeedbackAction,
  CommerceModule,
  CommercePlacement,
  fetchCommerceModules,
  recordCommerceFeedback
} from "../api/commerceDiscovery";
import { commerceSessionId } from "./session";

export type UseMarketplaceCommerceOptions = {
  /**
   * False whenever the shelves must not exist at all — signed out, or the user
   * has turned recommendations off. A gate on *existence*: no fetch, nothing
   * held.
   */
  enabled?: boolean;
  /**
   * True while the user has narrowed the grid. See the header: a search, a
   * category filter, a single-seller store, or an offline grid. The screen
   * decides; this hook only obeys.
   */
  suppressed?: boolean;
  /** Bumped by the screen on pull-to-refresh. */
  refreshToken?: number;
};

export type MarketplaceCommerceState = {
  /** Shelves with at least one surviving card. Empty is the normal answer. */
  modules: CommerceModule[];
  onFeedback: (placement: CommercePlacement, action: CommerceFeedbackAction) => void;
  sessionId: string;
};

export function useMarketplaceCommerce({
  enabled = true,
  suppressed = false,
  refreshToken = 0
}: UseMarketplaceCommerceOptions = {}): MarketplaceCommerceState {
  const [served, setServed] = useState<CommerceModule[]>([]);
  const [dismissedPlacementIds, setDismissedPlacementIds] = useState<ReadonlySet<string>>(new Set());
  const [dismissedSellerIds, setDismissedSellerIds] = useState<ReadonlySet<number>>(new Set());

  const sessionId = commerceSessionId();

  // Snoozing empties the shelves; a response already in flight must not undo it.
  const snoozedRef = useRef(false);

  const active = enabled && !suppressed;

  useEffect(() => {
    if (!active) {
      // Dropped, not merely hidden. Holding a ranked pool across a search means
      // the shelves that reappear when the search is cleared were ranked for a
      // user who has since told us something about what they wanted.
      setServed([]);
      return undefined;
    }
    let cancelled = false;
    fetchCommerceModules(sessionId)
      .then((modules) => {
        if (cancelled || snoozedRef.current) return;
        setServed(modules);
      })
      // `fetchCommerceModules` resolves `[]` rather than throwing; this is belt
      // and braces so a recommendation failure can never reach Marketplace as an
      // unhandled rejection. Browsing is the feature; this is not.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [active, refreshToken, sessionId]);

  // A refresh is a new browse, so session-local dismissals are no longer about
  // anything on screen. The server still holds them, so this cannot un-hide
  // anything — it only stops the sets growing for the life of the process.
  useEffect(() => {
    if (refreshToken === 0) return;
    setDismissedPlacementIds(new Set());
    setDismissedSellerIds(new Set());
  }, [refreshToken]);

  const onFeedback = useCallback((placement: CommercePlacement, action: CommerceFeedbackAction) => {
    // Optimistic and unconditional: an undismissable card is a worse outcome
    // than a preference that failed to save.
    if (action === "snooze") {
      snoozedRef.current = true;
      setServed([]);
    } else if (action === "hide_seller") {
      const sellerId = placement.product.sellerUserId;
      if (sellerId) {
        setDismissedSellerIds((current) => {
          if (current.has(sellerId)) return current;
          const next = new Set(current);
          next.add(sellerId);
          return next;
        });
      }
      setDismissedPlacementIds((current) => withPlacement(current, placement.placementId));
    } else {
      setDismissedPlacementIds((current) => withPlacement(current, placement.placementId));
    }

    recordCommerceFeedback(placement, action).catch(() => undefined);
  }, []);

  /**
   * Shelves with their hidden cards removed, and shelves that lost everything
   * removed entirely.
   *
   * Dropping the empty shelf is the whole point: "Because you viewed" above a
   * blank rail tells the user the hide worked and then leaves the evidence of
   * what they hid on screen anyway.
   *
   * Note what is *not* here — no backfill. Hiding a card does not pull a
   * replacement in behind it. A shelf that refills on hide teaches people the
   * button does nothing.
   */
  const modules = useMemo(() => {
    const out: CommerceModule[] = [];
    for (const module of served) {
      if (!module?.key) continue;
      const placements = (module.placements || []).filter((candidate) => {
        if (!candidate?.placementId) return false;
        if (!candidate.product?.listingId || !candidate.product?.title) return false;
        if (dismissedPlacementIds.has(candidate.placementId)) return false;
        const sellerId = candidate.product.sellerUserId;
        if (sellerId && dismissedSellerIds.has(sellerId)) return false;
        return true;
      });
      if (!placements.length) continue;
      out.push({ ...module, placements });
    }
    return out;
  }, [dismissedPlacementIds, dismissedSellerIds, served]);

  return useMemo(() => ({ modules, onFeedback, sessionId }), [modules, onFeedback, sessionId]);
}

function withPlacement(current: ReadonlySet<string>, placementId: string): ReadonlySet<string> {
  if (!placementId || current.has(placementId)) return current;
  const next = new Set(current);
  next.add(placementId);
  return next;
}
