/**
 * Everything a feed needs to show product Signals, in one hook.
 *
 * ## Why a hook rather than more of `HomeScreen`
 *
 * The same reasoning `discovery/useHomeDiscovery.ts` gives. `HomeScreen.tsx` is
 * already three thousand lines and owns the feed, the status rail, the composer,
 * ads, the drawer, live polling, the spatial pager and discovery. Home ends up
 * with three edits for this feature: call this, compose the rows, render the
 * branch. Any other surface that wants shoppable Signals makes the same three,
 * which is the whole point of the row/card/hook split.
 *
 * ## What "off" means here
 *
 * With `feedProductSignalsEnabled()` false this hook requests nothing and
 * returns an empty list, so `injectProductSignalRows` returns its input array
 * and the feed renders precisely the rows it did before. The flag check lives
 * here rather than at the call site so a disabled feature cannot cost a request.
 *
 * ## Why a failed load is silent
 *
 * Products are an *addition* to a social feed, not its content. A marketplace
 * outage must cost the feed two cards and nothing else — no error banner, no
 * retry spinner, no empty state. This is the one place where swallowing the
 * failure is the correct behaviour rather than a shortcut, because there is no
 * user action that would fix it and no claim being made that could be wrong.
 *
 * That is narrower than it sounds: it is licence to show *fewer* rows, never
 * licence to show an empty state that claims the marketplace has nothing. The
 * hook returns `[]` for "we do not have products to show", the feed draws no
 * product rows, and no copy anywhere asserts why.
 */
import { useEffect, useState } from "react";
import { searchMarketplace } from "../api/marketplace";
import { feedProductSignalsEnabled } from "./flags";
import { productSignalsFromListings, type ProductSignal } from "./productSignal";

/**
 * Listings to ask for.
 *
 * Deliberately more than {@link PRODUCT_SIGNAL_MAX_ROWS} places, because
 * `productSignalsFromListings` drops sold-out and id-less rows and a page that
 * asked for exactly two would show one whenever a seller sold something. Small
 * enough to stay cheap on a feed that is already fetching posts, statuses, ads
 * and suggestions.
 */
const FEED_PRODUCT_LIMIT = 12;

export type UseFeedProductSignalsOptions = {
  /**
   * Bumped by the surface on pull-to-refresh. Not a timestamp: the effect keys
   * off identity, and a clock would re-run it on every render.
   */
  refreshNonce?: string | number;
};

export type FeedProductSignalsState = {
  signals: ProductSignal[];
  loading: boolean;
};

export function useFeedProductSignals(
  options: UseFeedProductSignalsOptions = {}
): FeedProductSignalsState {
  const { refreshNonce } = options;
  const [signals, setSignals] = useState<ProductSignal[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!feedProductSignalsEnabled()) {
      setSignals([]);
      return;
    }
    let active = true;
    setLoading(true);
    searchMarketplace({ limit: FEED_PRODUCT_LIMIT })
      .then((result) => {
        if (!active) return;
        setSignals(productSignalsFromListings(result.items || [], Date.now()));
      })
      .catch(() => {
        if (active) setSignals([]);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [refreshNonce]);

  return { signals, loading };
}
