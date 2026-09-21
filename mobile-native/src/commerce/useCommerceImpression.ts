/**
 * "Served" and "seen", counted the same way on every surface.
 *
 * Both the feed card and the reels chip owe the server two different beacons,
 * and the rule separating them is easy to state and easy to get subtly wrong:
 *
 *   * `{visible: false}` on mount — *this was served and drawn*.
 *   * `{visible: true}` once the unit has held the viewability threshold for the
 *     server's dwell — *this was actually seen*.
 *
 * Folding them into one call makes every unit that blurred past during a fast
 * scroll count as seen. That is the number every recommender is tempted to
 * report and the one nobody should, so the split is kept here rather than
 * re-derived per surface — two copies of a timing rule is two copies that drift,
 * and the direction they drift is always toward over-counting.
 *
 * ## What "visible" means, and why it is not the same number everywhere
 *
 * The serve response carries `visible_percent_threshold` so the rule has one
 * owner. But the *percentage* is evaluated by whichever list owns the unit, and
 * a list cannot hold two percentage thresholds without swapping
 * `viewabilityConfigCallbackPairs`, which RN throws on if they change after
 * mount. So callers pass their list's own viewability as `isViewable` and this
 * hook honours the server's **dwell** exactly. Home's list reports at 72% and
 * Reels' at 72%, both stricter than the server's 60 — which means we under-count
 * rather than over-count, and an analytics gap is a smaller lie than a claim
 * that someone saw a product they did not.
 */
import { useCallback, useEffect, useRef } from "react";
import { CommercePlacement, recordCommerceImpression } from "../api/commerceDiscovery";

export type CommerceImpressionOptions = {
  placement: CommercePlacement;
  /** This unit's viewability, as reported by the list that owns it. */
  isViewable: boolean;
  /** Server-owned dwell in ms. The percentage is the list's — see the header. */
  visibleDwellMs: number;
  /**
   * False suspends accumulation without discarding it.
   *
   * Reels needs this: a chip on the active reel is on screen, but the moment a
   * call comes in or the app backgrounds, the video is not being watched and the
   * seconds that follow are not seconds anyone saw the product for.
   */
  active?: boolean;
};

export function useCommerceImpression({
  placement,
  isViewable,
  visibleDwellMs,
  active = true
}: CommerceImpressionOptions): void {
  const visibleMsRef = useRef(0);
  const viewableSinceRef = useRef<number | null>(null);
  const visibleReportedRef = useRef(false);

  const placementId = placement?.placementId || "";

  // Keyed on the placement id so a recycled row reports the placement it now
  // holds. The server dedupes on the impression token, so a re-render of the
  // *same* placement cannot double count even if this were to run twice.
  useEffect(() => {
    if (!placementId) return;
    visibleMsRef.current = 0;
    viewableSinceRef.current = null;
    visibleReportedRef.current = false;
    recordCommerceImpression(placement, { visible: false }).catch(() => undefined);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [placementId]);

  const flush = useCallback(() => {
    if (viewableSinceRef.current != null) {
      visibleMsRef.current += Date.now() - viewableSinceRef.current;
      viewableSinceRef.current = null;
    }
    if (visibleReportedRef.current) return;
    if (!placementId) return;
    if (visibleMsRef.current < Math.max(0, visibleDwellMs)) return;
    visibleReportedRef.current = true;
    recordCommerceImpression(placement, {
      visible: true,
      viewDurationMs: visibleMsRef.current
    }).catch(() => undefined);
  }, [placement, placementId, visibleDwellMs]);

  useEffect(() => {
    if (isViewable && active) {
      if (viewableSinceRef.current == null) viewableSinceRef.current = Date.now();
      // Fires the moment the dwell is satisfied rather than waiting for the unit
      // to scroll away, so something the user is still looking at is already
      // counted.
      const timer = setTimeout(flush, Math.max(0, visibleDwellMs));
      return () => clearTimeout(timer);
    }
    flush();
    return undefined;
  }, [isViewable, active, flush, visibleDwellMs]);

  // Unmount closes the window — otherwise a unit dropped from the recycling
  // window loses whatever time it had accumulated.
  useEffect(() => () => flush(), [flush]);
}
