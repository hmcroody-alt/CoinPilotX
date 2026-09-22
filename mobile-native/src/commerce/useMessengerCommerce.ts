/**
 * The at-most-one Marketplace suggestion the Messenger *inbox* may show.
 *
 * ## What this hook is not for
 *
 * It is not for conversations. The brief's rule on private messaging is
 * absolute — no recommendations inside a conversation, nothing inserted between
 * messages, the message stream untouched — and the way that rule is kept here is
 * that `ChatScreen` never imports this module or anything else under
 * `src/commerce`. A test asserts exactly that, because "we remembered not to"
 * is not a mechanism.
 *
 * The one permitted placement is a strip above the *list of conversations*,
 * which is an index of threads rather than any thread's contents. Nothing
 * private is on that screen in the first place.
 *
 * ## Why the budget is the smallest in the system
 *
 * `surface_caps()["messenger"]` is `(1, 1)` and `min_score("messenger")` carries
 * a +0.10 lift over the base floor. Messaging is the surface people open with a
 * specific intent — to reach a person — and the cost of interrupting that is
 * higher than anywhere else. One suggestion, above the fold, scrolling away with
 * the header, or nothing at all.
 *
 * The shape deliberately mirrors `useFeedCommerce` and `useReelsCommerce`:
 * fetch, dismissal state, feedback writes. Three surfaces with three private
 * notions of what "hidden" means is how a hide on one surface stops meaning
 * anything on the next.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CommerceFeedbackAction,
  CommercePlacement,
  fetchCommercePlacements,
  recordCommerceFeedback
} from "../api/commerceDiscovery";
import { startCommercePause, useSocialDiscoveryAllowed } from "./consent";
import { commerceSessionId } from "./session";

export type UseMessengerCommerceOptions = {
  /**
   * False whenever the strip must not exist at all.
   *
   * A gate on *existence*: with it false there is no fetch and no placement
   * held. The screen passes false for a signed-out viewer, for an active call,
   * and whenever the user has turned recommendations off.
   */
  enabled?: boolean;
  /** Bumped by the screen on pull-to-refresh. */
  refreshToken?: number;
};

export type MessengerCommerceState = {
  /** The single suggestion, or null — which is the common answer. */
  placement: CommercePlacement | null;
  visibleDwellMs: number;
  onFeedback: (placement: CommercePlacement, action: CommerceFeedbackAction) => void;
  sessionId: string;
};

/**
 * One row, above the fold, and no rhythm to speak of.
 *
 * `leadIn`/`interval` exist for surfaces that interleave units into a stream.
 * The strip is not in the stream — it is a fixed part of the list header — so
 * the only number that means anything here is the cap.
 */
const LOCAL_CADENCE = { leadIn: 0, interval: 1, maxPerPage: 1 };

const DEFAULT_VISIBLE_DWELL_MS = 1000;

export function useMessengerCommerce({
  enabled: callerEnabled = true,
  refreshToken = 0
}: UseMessengerCommerceOptions = {}): MessengerCommerceState {
  // The master switch, read rather than passed — see `useFeedCommerce` for why
  // this is not left to the screen to remember.
  const consented = useSocialDiscoveryAllowed();
  const enabled = callerEnabled && consented;

  const [placements, setPlacements] = useState<CommercePlacement[]>([]);
  const [visibleDwellMs, setVisibleDwellMs] = useState(DEFAULT_VISIBLE_DWELL_MS);
  const [dismissedPlacementIds, setDismissedPlacementIds] = useState<ReadonlySet<string>>(new Set());
  const [dismissedSellerIds, setDismissedSellerIds] = useState<ReadonlySet<number>>(new Set());

  const sessionId = commerceSessionId();

  // Snoozing empties the list; a response already in flight must not undo that.
  const snoozedRef = useRef(false);

  useEffect(() => {
    if (!enabled) {
      setPlacements([]);
      return undefined;
    }
    let cancelled = false;
    fetchCommercePlacements("messenger", {
      sessionId,
      limit: LOCAL_CADENCE.maxPerPage,
      cadence: LOCAL_CADENCE
    })
      .then((result) => {
        if (cancelled || snoozedRef.current) return;
        setPlacements(result.placements);
        setVisibleDwellMs(result.visibleDwellMs);
      })
      // `fetchCommercePlacements` resolves empty rather than throwing; this is
      // belt and braces so a recommendation failure can never reach Messenger as
      // an unhandled rejection. The inbox is not allowed to break for this.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [enabled, refreshToken, sessionId]);

  // A refresh is a new inbox, so session-local dismissals are no longer about
  // anything on screen. The server still holds them, so this cannot un-hide
  // anything — it only stops the sets growing for the life of the process.
  useEffect(() => {
    if (refreshToken === 0) return;
    setDismissedPlacementIds(new Set());
    setDismissedSellerIds(new Set());
  }, [refreshToken]);

  const onFeedback = useCallback((placement: CommercePlacement, action: CommerceFeedbackAction) => {
    // Optimistic and unconditional: an undismissable suggestion is a worse
    // outcome than a preference that failed to save.
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

  /**
   * The first placement that is renderable and not hidden — or null.
   *
   * Note what this does *not* do: it does not promote a runner-up when the
   * first is dismissed. The dismissed one is filtered out and the next in the
   * list takes its place only because the server sent an ordered list and the
   * cap is one, so in practice there is no runner-up to promote. Should an
   * operator raise the cap, "hide this one, get the next one immediately" is
   * the behaviour that makes people stop trusting the hide button — so the
   * whole strip is retired for the session instead, by the strip itself.
   */
  const placement = useMemo(() => {
    for (const candidate of placements) {
      if (!candidate?.placementId) continue;
      if (!candidate.product?.listingId || !candidate.product?.title) continue;
      if (dismissedPlacementIds.has(candidate.placementId)) return null;
      const sellerId = candidate.product.sellerUserId;
      if (sellerId && dismissedSellerIds.has(sellerId)) return null;
      return candidate;
    }
    return null;
  }, [dismissedPlacementIds, dismissedSellerIds, placements]);

  return useMemo(
    () => ({ placement, visibleDwellMs, onFeedback, sessionId }),
    [onFeedback, placement, sessionId, visibleDwellMs]
  );
}

function withPlacement(current: ReadonlySet<string>, placementId: string): ReadonlySet<string> {
  if (!placementId || current.has(placementId)) return current;
  const next = new Set(current);
  next.add(placementId);
  return next;
}
