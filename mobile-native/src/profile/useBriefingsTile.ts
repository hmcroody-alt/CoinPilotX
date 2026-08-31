/**
 * Live state for the Profile OS Briefings tile.
 *
 * Same contract as `usePremiumTile`: cached answer first so a warm account is
 * never blank, live answer second and unconditionally, and `undefined` — not
 * an empty or guessed label — whenever there is no honest status to show.
 *
 * The only status this tile ever shows is NEW, and only when the *server's*
 * unseen counter says so. The unread signal is the briefing-specific seen
 * cursor (`/api/pulse/briefings/status` → `unseen_count`), never device-local
 * state and never the general notification badge. Opening the hub clears the
 * cursor server-side, and the next status read makes the label go away.
 */

import { useEffect, useRef, useState } from "react";
import {
  getBriefingStatus,
  loadCachedBriefingStatus,
  type BriefingDeliveryStatus
} from "../api/briefings";
import { trackBriefings } from "../briefings/briefingsAnalytics";
import type { ProfileModuleState } from "../components/ProfileHeader";
import { useTranslation } from "../i18n";

/**
 * @param enabled Own profile only. A visitor never gets the tile
 *   (`profileOsTiles` drops it), so there is nothing to fetch.
 */
export function useBriefingsTile(enabled: boolean): ProfileModuleState | undefined {
  const { t } = useTranslation();
  const [unseen, setUnseen] = useState<number>(0);
  const impressed = useRef(false);

  useEffect(() => {
    if (!enabled) return;
    let cancelled = false;
    const apply = (status: BriefingDeliveryStatus | null) => {
      if (!cancelled && status && typeof status.unseen_count === "number") {
        setUnseen(status.unseen_count);
      }
    };

    loadCachedBriefingStatus().then(apply).catch(() => undefined);
    getBriefingStatus()
      .then((status) => { if (!cancelled) setUnseen(status.unseen_count || 0); })
      // A failed read leaves whatever the cache painted; asserting "nothing
      // new" on a network blip would be a false negative.
      .catch(() => undefined);

    return () => { cancelled = true; };
  }, [enabled]);

  useEffect(() => {
    if (!enabled || impressed.current) return;
    impressed.current = true;
    trackBriefings("briefings_tile_impression");
  }, [enabled]);

  if (!enabled) return undefined;

  const hasNew = unseen > 0;
  return {
    // `undefined`, not "", when everything has been seen.
    status: hasNew ? t("briefings:tile.status.new") : undefined,
    accessibilityLabel: hasNew ? t("briefings:tile.a11y.new") : t("briefings:tile.a11y.none"),
    accessibilityHint: t("briefings:tile.hint")
  };
}
