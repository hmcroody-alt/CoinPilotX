/**
 * Live state for the Profile OS Premium tile.
 *
 * Split out of `ProfileScreen` because the ordering rule below is the whole
 * feature and deserves to be stated once, in a file small enough to read:
 *
 *   cached label first, live answer second, and *never* the word "Free".
 *
 * `premiumTileState` returns `null` for every non-member state, and `null`
 * renders no micro-status at all. That is what stops the flicker the brief
 * calls out: on a cold start a paying member's tile appears gold with no status
 * word for a few hundred milliseconds and then says ACTIVE. It never says
 * "Free" and then corrects itself, because absence of a badge asserts nothing
 * while the word "Free" would assert something wrong about someone who paid.
 *
 * The cache is a display cache. It decides what the tile *says* and nothing
 * else — the destination screen re-reads live server state before it will sell,
 * restore or manage anything. See `PREMIUM_CACHE_CONTRACT`.
 */

import { useEffect, useRef, useState } from "react";
import {
  getPremiumCenter,
  loadCachedPremiumCenter,
  premiumTileState,
  type PremiumCenter
} from "../api/premiumCenter";
import type { ProfileModuleState } from "../components/ProfileHeader";
import { useTranslation } from "../i18n";
import { trackPremium } from "../payments/premiumAnalytics";
import { premiumTheme } from "../theme/premiumTheme";

type TileState = ReturnType<typeof premiumTileState>;

/**
 * @param enabled Own profile only. A visitor never gets the tile at all
 *   (`profileOsTiles` drops it), so there is nothing to fetch and no reason to
 *   spend a request on someone else's screen.
 */
export function usePremiumTile(enabled: boolean): ProfileModuleState | undefined {
  const { t } = useTranslation();
  const [state, setState] = useState<TileState>(null);
  const impressed = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const apply = (payload: PremiumCenter | null) => {
      if (!cancelled && payload) setState(premiumTileState(payload));
    };

    // Cached first so the tile is not blank on a warm account, then live. The
    // live read overwrites unconditionally: if the server now says the
    // membership lapsed, the stale gold ACTIVE must go.
    loadCachedPremiumCenter().then(apply).catch(() => undefined);
    getPremiumCenter()
      .then((payload) => { if (!cancelled) setState(premiumTileState(payload)); })
      // A failed status read leaves whatever the cache painted. Downgrading to
      // "no status" on a network blip would be the flicker in slow motion.
      .catch(() => undefined);

    return () => { cancelled = true; };
  }, [enabled]);

  useEffect(() => {
    if (!enabled || impressed.current) return;
    impressed.current = true;
    trackPremium("premium_tile_impression");
  }, [enabled]);

  if (!enabled) return undefined;

  const key = state || "none";
  return {
    // `undefined`, not "", when there is no honest status word to show.
    status: state ? t(`premium:tile.status.${state}`) : undefined,
    tint: state === "grace" ? premiumTheme.state.grace : undefined,
    accessibilityLabel: t(`premium:tile.a11y.${key}`),
    accessibilityHint: t("premium:tile.hint")
  };
}
