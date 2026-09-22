/**
 * Everything Home needs to show commerce rows, in one hook.
 *
 * Same shape and the same reasoning as `discovery/useHomeDiscovery` — Home is
 * already three thousand lines, and a feature that needs fetching, dismissal
 * state, feedback writes and a settings interaction does not belong inlined
 * into a screen that also owns the composer, the status rail and the drawer.
 * Home's total surface area for this feature is three edits: call this, add one
 * call to the row memo, add one branch to the renderer.
 *
 * ## Why there is no client-side feature flag
 *
 * The obvious next line here is `if (!commerceDiscoveryEnabled()) return;`
 * against an `EXPO_PUBLIC_` flag. `discovery/flags.ts` documents, at length,
 * the build where exactly that read `undefined` in Release and shipped a
 * feature nobody could turn off — Expo's babel plugin only inlines
 * `process.env.EXPO_PUBLIC_X` when the key is a literal static member
 * expression, so the whole class of flag is a trap here.
 *
 * It is also unnecessary. `COMMERCE_DISCOVERY_ENABLED` on the server already
 * turns the engine off without a client release, and when it is off the serve
 * endpoint answers `{ok: true, placements: []}`. An empty placement list is
 * already, structurally, "no commerce unit": `injectCommerceRows` returns its
 * input array unchanged. A client flag would add the trap without adding any
 * capability the server does not already have.
 *
 * ## Why dismissal is held here *and* sent to the server
 *
 * The write is the durable record — "don't recommend this seller" has to
 * outlive the process, and the next fetch is what enforces it. But the next
 * fetch is a network round trip away, and the card has to disappear now. So the
 * set held here covers the window between the tap and the refetch, and it is
 * deliberately *not* cleared when the write fails: a user who hid something and
 * watched it stay hidden, then saw it return after a refresh, has at least been
 * told the truth by the surface they were looking at.
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

export type UseFeedCommerceOptions = {
  /** False while signed out, or while Home is showing a surface that owns the screen. */
  enabled?: boolean;
  /** Bumped by Home on pull-to-refresh. */
  refreshToken?: number;
};

/** Mirrors the server defaults, used until the first response lands. */
const DEFAULT_VISIBLE_PERCENT = 60;
const DEFAULT_VISIBLE_DWELL_MS = 1000;

/**
 * Asked for more than the feed will place (2 per page), on purpose.
 *
 * Every placement the server returns costs it a persisted row and an HMAC, so
 * this is not free — but a page that hides its one card and has nothing behind
 * it shows a gap for the rest of the session, and the client cannot ask for "one
 * more" without a second round trip mid-scroll. Six is two pages of headroom.
 */
const FEED_PLACEMENT_LIMIT = 6;

export type FeedCommerceState = {
  /** Ranked placements for this page. Empty whenever the engine has nothing. */
  placements: CommercePlacement[];
  dismissedPlacementIds: ReadonlySet<string>;
  dismissedSellerIds: ReadonlySet<number>;
  /** Server-owned viewability contract — see `CommerceFeedCard`. */
  visiblePercentThreshold: number;
  visibleDwellMs: number;
  /** One tap on any of the ••• menu's negative actions. */
  onFeedback: (placement: CommercePlacement, action: CommerceFeedbackAction) => void;
  sessionId: string;
};

export function useFeedCommerce({
  enabled: callerEnabled = true,
  refreshToken = 0
}: UseFeedCommerceOptions = {}): FeedCommerceState {
  // Read here rather than taken as an option on purpose. The master switch is
  // not Home's business to remember, and a surface that forgot to pass it would
  // keep placing for a user who had switched discovery off — the one failure
  // this feature cannot be allowed to have. Every social surface gets it by
  // calling the hook at all.
  const consented = useSocialDiscoveryAllowed();
  const enabled = callerEnabled && consented;

  const [placements, setPlacements] = useState<CommercePlacement[]>([]);
  const [dismissedPlacementIds, setDismissedPlacementIds] = useState<ReadonlySet<string>>(new Set());
  const [dismissedSellerIds, setDismissedSellerIds] = useState<ReadonlySet<number>>(new Set());
  const [visiblePercentThreshold, setVisiblePercentThreshold] = useState(DEFAULT_VISIBLE_PERCENT);
  const [visibleDwellMs, setVisibleDwellMs] = useState(DEFAULT_VISIBLE_DWELL_MS);

  const sessionId = commerceSessionId();

  // Held in a ref so that snoozing (which empties `placements`) cannot be undone
  // by a stale in-flight response resolving a moment later.
  const snoozedRef = useRef(false);

  useEffect(() => {
    if (!enabled) {
      setPlacements([]);
      return undefined;
    }
    let cancelled = false;
    fetchCommercePlacements("feed", { sessionId, limit: FEED_PLACEMENT_LIMIT })
      .then((result) => {
        // An obsolete load must not overwrite the current one, and a response
        // that raced a snooze must not resurrect what the snooze just cleared.
        if (cancelled || snoozedRef.current) return;
        setPlacements(result.placements);
        setVisiblePercentThreshold(result.visiblePercentThreshold);
        setVisibleDwellMs(result.visibleDwellMs);
      })
      // `fetchCommercePlacements` already returns empty rather than throwing;
      // this is belt and braces so a recommendation failure can never reach
      // Home as an unhandled rejection.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [enabled, refreshToken, sessionId]);

  // A refresh is a new set of cards, so the session-local dismissals from the
  // previous page are no longer about anything on screen. The *server* still
  // holds them — that is what stops the same product coming back — so dropping
  // them here cannot un-hide anything; it only stops the sets growing for the
  // life of the process.
  useEffect(() => {
    if (refreshToken === 0) return;
    setDismissedPlacementIds(new Set());
    setDismissedSellerIds(new Set());
  }, [refreshToken]);

  const onFeedback = useCallback((placement: CommercePlacement, action: CommerceFeedbackAction) => {
    // Optimistic and unconditional. The card must collapse on the tap, not on
    // the response: a "hide" that waits for the network is a card that sits
    // there for a second looking broken, and an undismissable card is a worse
    // outcome than a preference that failed to save.
    if (action === "snooze") {
      snoozedRef.current = true;
      setPlacements([]);
      // Durable, and visible in Settings. The ref above only covers this
      // process; the server-side suppression row the feedback call writes
      // outlives it but is invisible to the settings screen, which would then
      // report suggestions as on with nothing to resume.
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
      // Belt and braces: a placement whose seller id did not survive the wire
      // still has to disappear, so hide the card itself as well.
      setDismissedPlacementIds((current) => withPlacement(current, placement.placementId));
    } else {
      setDismissedPlacementIds((current) => withPlacement(current, placement.placementId));
    }

    recordCommerceFeedback(placement, action).catch(() => undefined);
  }, []);

  return useMemo(
    () => ({
      placements: enabled ? placements : [],
      dismissedPlacementIds,
      dismissedSellerIds,
      visiblePercentThreshold,
      visibleDwellMs,
      onFeedback,
      sessionId
    }),
    [
      dismissedPlacementIds,
      dismissedSellerIds,
      enabled,
      onFeedback,
      placements,
      sessionId,
      visibleDwellMs,
      visiblePercentThreshold
    ]
  );
}

function withPlacement(current: ReadonlySet<string>, placementId: string): ReadonlySet<string> {
  if (!placementId || current.has(placementId)) return current;
  const next = new Set(current);
  next.add(placementId);
  return next;
}
