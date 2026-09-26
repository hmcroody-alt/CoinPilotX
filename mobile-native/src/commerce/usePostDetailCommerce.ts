/**
 * The at-most-one product card a post's own screen may show.
 *
 * ## Why this surface exists
 *
 * `ranking.REASON_CONTEXT` is spelled `related_to_this_post`, and until this hook
 * no client on a *post* ever sent a ranking context — only `useReelsCommerce`
 * did. The most specific reason the engine can give was reachable from exactly
 * one surface, and it was named after a different one.
 *
 * ## Why the card is under the post and above the comments
 *
 * `PostDetailScreen` renders the post as its list header and the comment thread
 * as the list data. The card is the last thing in the header, which makes three
 * properties structural rather than defended in code:
 *
 *   * **It does not displace a comment.** It is not in `data` at all, so the
 *     thread, its ordering and its pagination are untouched.
 *   * **It is adjacent to the thing it claims to relate to.** "Related to this
 *     post" placed *after* fifty comments is a claim about something scrolled off
 *     the screen.
 *   * **It is above the composer, never between the composer and the thread.**
 *     A reply box separated from what it replies to is worse than no card.
 *
 * ## Why the context is derived here and not by the screen
 *
 * The hook takes the post and calls `postCommerceContext` itself, where
 * `useReelsCommerce` takes a *resolver* from its screen. The difference is that a
 * reels screen holds many reels and only it knows which one is on frame, whereas
 * this screen holds exactly one post. A resolver here would be a function whose
 * only possible implementation is `() => post`, and an indirection with one
 * implementation is a place for the two to disagree.
 *
 * ## Fetching waits for the post
 *
 * There is no request until the post has loaded. Asking earlier would send a
 * context-free request for the one surface whose relevance floor assumes a
 * context is present — `min_score("post_detail")` sits a tenth above the feed's
 * precisely because this surface can measure relevance rather than defaulting it
 * to NEUTRAL. A request fired before the post arrived would be held to that
 * floor while carrying none of the signal it was raised for, so it would mostly
 * answer empty, and the card would be missing on exactly the slow connections
 * where the screen sits visible longest.
 *
 * The shape otherwise mirrors `useMessengerCommerce` deliberately: fetch,
 * dismissal state, feedback writes. Surfaces with private notions of what
 * "hidden" means are how a hide on one surface stops meaning anything on the next.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  CommerceFeedbackAction,
  CommercePlacement,
  fetchCommercePlacements,
  recordCommerceFeedback
} from "../api/commerceDiscovery";
import type { PulsePost } from "../api/feed";
import { startCommercePause, useSocialDiscoveryAllowed } from "./consent";
import { postCommerceContext } from "./postContext";
import { commerceSessionId } from "./session";

export type UsePostDetailCommerceOptions = {
  /**
   * The post being read, or null while it loads.
   *
   * Null is not merely "not yet" — it is also the answer for a post that failed
   * to load or was deleted, and in both cases there must be no card. A product
   * suggestion under an error state is the worst version of this surface.
   */
  post: PulsePost | null;
  /**
   * False whenever the card must not exist at all.
   *
   * A gate on *existence*: with it false there is no fetch and no placement
   * held. The screen passes false for a signed-out viewer and while the post is
   * still loading.
   */
  enabled?: boolean;
  /** Bumped by the screen on pull-to-refresh. */
  refreshToken?: number;
};

export type PostDetailCommerceState = {
  /** The single card, or null — which is the common answer. */
  placement: CommercePlacement | null;
  visibleDwellMs: number;
  onFeedback: (placement: CommercePlacement, action: CommerceFeedbackAction) => void;
  sessionId: string;
};

/**
 * One card, and no rhythm to speak of.
 *
 * `leadIn`/`interval` exist for surfaces that interleave units into a stream.
 * This screen holds a single post, so there is nothing for a card to be spaced
 * against; the only numbers that pace this surface are the per-session cap and
 * the seller cooldown, both enforced server-side.
 */
const LOCAL_CADENCE = { leadIn: 0, interval: 1, maxPerPage: 1 };

const DEFAULT_VISIBLE_DWELL_MS = 1000;

export function usePostDetailCommerce({
  post,
  enabled: callerEnabled = true,
  refreshToken = 0
}: UsePostDetailCommerceOptions): PostDetailCommerceState {
  // The master switch, read rather than passed — see `useFeedCommerce` for why
  // this is not left to the screen to remember.
  const consented = useSocialDiscoveryAllowed();
  const postId = Number(post?.post_id || post?.id || 0);
  const enabled = callerEnabled && consented && postId > 0;

  const [placements, setPlacements] = useState<CommercePlacement[]>([]);
  const [visibleDwellMs, setVisibleDwellMs] = useState(DEFAULT_VISIBLE_DWELL_MS);
  const [dismissedPlacementIds, setDismissedPlacementIds] = useState<ReadonlySet<string>>(new Set());
  const [dismissedSellerIds, setDismissedSellerIds] = useState<ReadonlySet<number>>(new Set());

  const sessionId = commerceSessionId();

  // Snoozing empties the list; a response already in flight must not undo that.
  const snoozedRef = useRef(false);

  /**
   * Derived from the post's text, so it changes only when the post does.
   *
   * Keyed into the fetch effect by `postId` rather than by this object: a
   * refetch triggered by a new context identity would fire on every edit to a
   * caption, and the dependency would be an object literal that is a new
   * reference on each render regardless.
   */
  const context = useMemo(() => postCommerceContext(post), [post]);

  useEffect(() => {
    if (!enabled) {
      setPlacements([]);
      return undefined;
    }
    let cancelled = false;
    fetchCommercePlacements("post_detail", {
      sessionId,
      limit: LOCAL_CADENCE.maxPerPage,
      cadence: LOCAL_CADENCE,
      // Omitted rather than sent empty when the post says nothing: an absent
      // context and a context that matches nothing score differently, and only
      // one of them is an honest description of a post with no readable subject.
      ...(context ? { context } : {})
    })
      .then((result) => {
        if (cancelled || snoozedRef.current) return;
        setPlacements(result.placements);
        setVisibleDwellMs(result.visibleDwellMs);
      })
      // `fetchCommercePlacements` resolves empty rather than throwing; this is
      // belt and braces so a recommendation failure can never reach the post
      // screen as an unhandled rejection. Reading a post is not allowed to break
      // for this.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
    // `context` is intentionally not a dependency — see its docstring above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, postId, refreshToken, sessionId]);

  // A refresh is a re-read of the post, so session-local dismissals are no
  // longer about anything on screen. The server still holds them, so this cannot
  // un-hide anything — it only stops the sets growing for the life of the process.
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
   * Dismissing does not promote a runner-up. The cap is one so in practice there
   * is none, but should an operator raise it, "hide this one, get the next one
   * immediately" is the behaviour that makes people stop trusting the hide
   * button — so the card is retired for the post instead.
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
