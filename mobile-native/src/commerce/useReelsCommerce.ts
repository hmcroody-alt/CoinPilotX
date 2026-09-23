/**
 * Everything the Reels screen needs to show at most one commerce chip.
 *
 * The sibling of `useFeedCommerce`, and deliberately the same shape — fetch,
 * dismissal state, feedback writes — so that the two surfaces cannot drift into
 * having different ideas about what "hidden" means. The differences are the
 * ones the brief asks for:
 *
 *   * **The budget is one.** `surface_caps()["reels"]` is `(1, 1)`: one per
 *     response, one per session. Reels is the only surface where a placement
 *     shares the frame with something the user is actively watching.
 *   * **The floor is the highest in the system.** `min_score("reels")` is the
 *     base floor plus 0.20, which is the server's way of saying *no placement is
 *     better than a bad placement*. When nothing clears it the response is an
 *     empty list, and an empty list here means the Reels screen renders exactly
 *     what it rendered before this feature existed.
 *
 * ## Why binding is a separate pure module
 *
 * `reelSlots.ts` decides *which reel* carries the chip and can be tested against
 * a list of ids with no React, no network and no clock. This hook owns only the
 * things that genuinely need a component lifecycle.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CommerceCadence,
  CommerceContext,
  CommerceFeedbackAction,
  CommercePlacement,
  fetchCommercePlacements,
  recordCommerceFeedback
} from "../api/commerceDiscovery";
import {
  REELS_INTERVAL,
  REELS_LEAD_IN,
  REELS_MAX_CHIPS,
  bindReelCommerce,
  reelCommerceSlots
} from "./reelSlots";
import { startCommercePause, useSocialDiscoveryAllowed } from "./consent";
import { commerceSessionId } from "./session";

export type UseReelsCommerceOptions = {
  /** Ids of the reels currently in the list, in the order they are shown. */
  reelIds: readonly string[];
  /**
   * False whenever a chip must not exist at all.
   *
   * The Reels screen passes false for the brief's no-interruption zones — an
   * active call, a Live, the camera, the composer — and for a signed-out
   * viewer. It is a gate on *existence*, not on visibility: with it false, no
   * fetch happens and no placement is held.
   */
  enabled?: boolean;
  /** Bumped by the screen on pull-to-refresh. */
  refreshToken?: number;
  /**
   * What the reel that will carry the chip is about (§8).
   *
   * A resolver rather than a prepared context because the hook, not the screen,
   * knows *which* reel that is — `reelCommerceSlots` derives it from the same
   * cadence arithmetic the binder uses, so the reel we describe to the ranker
   * and the reel the chip lands on cannot be two different videos.
   *
   * Optional: with no resolver the request carries no context and ranking falls
   * back to NEUTRAL relevance, which is exactly the pre-§8 behaviour.
   */
  resolveContext?: (reelId: string) => CommerceContext | null;
};

const LOCAL_CADENCE: CommerceCadence = {
  leadIn: REELS_LEAD_IN,
  interval: REELS_INTERVAL,
  maxPerPage: REELS_MAX_CHIPS
};

/** The same rhythm in the binder's vocabulary — `maxChips`, not `maxPerPage`. */
const LOCAL_CADENCE_SLOTS = {
  leadIn: REELS_LEAD_IN,
  interval: REELS_INTERVAL,
  maxChips: REELS_MAX_CHIPS
};

const DEFAULT_VISIBLE_DWELL_MS = 1000;

export type ReelsCommerceState = {
  /** Reel id → the chip that reel carries. Empty whenever there is nothing. */
  chipByReelId: ReadonlyMap<string, CommercePlacement>;
  visibleDwellMs: number;
  onFeedback: (placement: CommercePlacement, action: CommerceFeedbackAction) => void;
  sessionId: string;
};

const NO_CHIPS: ReadonlyMap<string, CommercePlacement> = new Map();

export function useReelsCommerce({
  reelIds,
  enabled: callerEnabled = true,
  refreshToken = 0,
  resolveContext
}: UseReelsCommerceOptions): ReelsCommerceState {
  // The master switch, read rather than passed — see `useFeedCommerce` for why
  // this is not left to the screen to remember.
  const consented = useSocialDiscoveryAllowed();
  const enabled = callerEnabled && consented;

  const [placements, setPlacements] = useState<CommercePlacement[]>([]);
  const [cadence, setCadence] = useState<CommerceCadence>(LOCAL_CADENCE);
  const [visibleDwellMs, setVisibleDwellMs] = useState(DEFAULT_VISIBLE_DWELL_MS);
  const [dismissedPlacementIds, setDismissedPlacementIds] = useState<ReadonlySet<string>>(new Set());
  const [dismissedSellerIds, setDismissedSellerIds] = useState<ReadonlySet<number>>(new Set());

  const sessionId = commerceSessionId();

  // Snoozing empties the list; a response already in flight must not undo that.
  const snoozedRef = useRef(false);

  /**
   * A *content* key for the reel list, with the list itself kept in a ref.
   *
   * `reelIds` is rebuilt by the screen on every render, so depending on its
   * identity would re-bind — and hand the renderer a brand new `Map` — on every
   * frame of a scroll. Depending on a joined string instead re-binds only when
   * the list actually changes.
   *
   * The ref is what keeps this honest. The key is used for *comparison* only and
   * the binder reads the real array, so a separator that happens to occur inside
   * an id can at worst cause one redundant re-bind. Reconstructing the ids by
   * splitting the key back apart would instead corrupt them — that is the
   * version of this trick that looks identical and is wrong.
   */
  const reelIdKey = Array.isArray(reelIds) ? reelIds.join("|") : "";
  const reelIdsRef = useRef<readonly string[]>(reelIds);
  reelIdsRef.current = reelIds;

  /**
   * The reel the chip will sit on, named before the chip is asked for.
   *
   * Computed with the *local* cadence, because the server's cadence arrives in
   * the same response this is used to request — there is no ordering in which
   * we could use it. The local numbers mirror `commerce_discovery/config.py`, so
   * the two agree unless an operator retunes reels, and the worst case of a
   * disagreement is that the context describes a nearby reel rather than the
   * exact one. That degrades the match; it cannot mis-bind anything, because
   * binding below still uses the server's cadence.
   */
  const targetReelId = useMemo(
    () => reelCommerceSlots(reelIdsRef.current, LOCAL_CADENCE_SLOTS)[0] || "",
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [reelIdKey]
  );

  // Read through a ref so a screen that rebuilds the resolver every render (the
  // normal case — it closes over the reel list) does not refire the fetch.
  const resolveContextRef = useRef(resolveContext);
  resolveContextRef.current = resolveContext;

  useEffect(() => {
    // No target reel means the list is shorter than the lead-in, so no slot
    // exists for a chip to occupy. Fetching would spend a request and a
    // server-side placement row on a chip that structurally cannot render —
    // and, worse, would have to be requested with no context at all.
    if (!enabled || !targetReelId) {
      setPlacements([]);
      return undefined;
    }
    let cancelled = false;
    // §8. `null` is passed through as an omitted field rather than as `{}`: a
    // reel with no topic should be ranked at NEUTRAL relevance, not scored
    // against an empty string.
    const context = resolveContextRef.current?.(targetReelId) || undefined;
    fetchCommercePlacements("reels", {
      context,
      sessionId,
      limit: REELS_MAX_CHIPS,
      cadence: LOCAL_CADENCE
    })
      .then((result) => {
        if (cancelled || snoozedRef.current) return;
        setPlacements(result.placements);
        setCadence(result.cadence);
        setVisibleDwellMs(result.visibleDwellMs);
      })
      // `fetchCommercePlacements` returns empty rather than throwing; this is
      // belt and braces so a recommendation failure can never surface on Reels
      // as an unhandled rejection.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [enabled, refreshToken, sessionId, targetReelId]);

  // A refresh is a new list, so session-local dismissals are no longer about
  // anything on screen. The server still holds them, so this cannot un-hide
  // anything — it only stops the sets growing for the life of the process.
  useEffect(() => {
    if (refreshToken === 0) return;
    setDismissedPlacementIds(new Set());
    setDismissedSellerIds(new Set());
  }, [refreshToken]);

  const onFeedback = useCallback((placement: CommercePlacement, action: CommerceFeedbackAction) => {
    // Optimistic and unconditional, for the reason `useFeedCommerce` gives at
    // length: an undismissable chip is a worse outcome than a preference that
    // failed to save.
    if (action === "snooze") {
      snoozedRef.current = true;
      setPlacements([]);
      // Durable, and visible in Settings — the ref above only covers this
      // process, and the server-side suppression row is invisible to the
      // settings screen, which would report suggestions as on with no Resume.
      startCommercePause();
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

  const chipByReelId = useMemo(() => {
    if (!enabled || placements.length === 0) return NO_CHIPS;
    return bindReelCommerce(reelIdsRef.current, placements, {
      leadIn: cadence.leadIn,
      interval: cadence.interval,
      maxChips: cadence.maxPerPage,
      dismissedPlacementIds,
      dismissedSellerIds
    });
  }, [cadence, dismissedPlacementIds, dismissedSellerIds, enabled, placements, reelIdKey]);

  return useMemo(
    () => ({ chipByReelId, visibleDwellMs, onFeedback, sessionId }),
    [chipByReelId, onFeedback, sessionId, visibleDwellMs]
  );
}

function withPlacement(current: ReadonlySet<string>, placementId: string): ReadonlySet<string> {
  if (!placementId || current.has(placementId)) return current;
  const next = new Set(current);
  next.add(placementId);
  return next;
}
