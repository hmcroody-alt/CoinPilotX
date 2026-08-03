/**
 * Router for the `BusinessOs` route — the seller's front door.
 *
 * REVERTED. The light Business Hub redesign shipped with visual defects at real
 * device font scales (truncated titles, mid-word wrapping, uneven card heights —
 * see `docs/business_os/BUSINESS_HUB_REVERT.md`) and the product owner called it
 * back. The default render is once again the original dark `BusinessOsScreen`,
 * exactly as it was before the redesign: stack header, "At a glance" panel, and
 * the two-column Sections grid with static two-line subtitles.
 *
 * This is a presentation-layer revert only. Nothing about the wiring was undone:
 * `api/businessHub.ts`, `core/hubBindings.ts` and `components/hub/` are intact
 * and still tested, and the derivations that moved to their owners during the
 * wiring mission stay where they are. `ordersAwaitingSeller` is read by
 * `OrdersManagerScreen`, so removing it would break a live screen;
 * `storeHealthCounts` and `insightsRevenueMajor` currently have no reader
 * outside the dormant hub and their own unit tests, and are kept as tested
 * exports of the module that owns the data rather than deleted, because that
 * ownership is the correction the wiring mission made and undoing it would
 * re-introduce the duplicate derivation.
 *
 * `HUB_LIVE_CARDS` is the single switch. While it is false the light hub is
 * unreachable and — because the import below is deferred rather than
 * module-scope — none of its modules are even evaluated at startup. Turning the
 * flag on and passing `mode: "hub"` is the whole cost of picking the redesign
 * back up, which is why the code was flagged off rather than deleted.
 *
 * Keeping one registered route name remains the point: every deep link, push
 * notification and tab that says "BusinessOs" lands on the dark hub, with no
 * param changes anywhere.
 */

import { HUB_LIVE_CARDS } from "../api/businessOs";
import { BusinessOsScreen } from "./BusinessOsScreen";
import type { RootStackParamList } from "../navigation/types";

type Props = {
  route?: { params?: RootStackParamList["BusinessOs"] };
  navigation: {
    navigate: (...args: any[]) => void;
    goBack?: () => void;
    addListener?: (...args: any[]) => any;
  };
};

export function BusinessHubRoute({ route, navigation }: Props) {
  // Both conditions, deliberately. The flag alone decides whether the redesign
  // exists at all; `mode: "hub"` is what an explicit caller opts in with. A
  // deep link that passes neither — which is all of them — gets the dark screen.
  if (HUB_LIVE_CARDS && route?.params?.mode === "hub") {
    // Deferred on purpose: a module-scope import would pull the light theme,
    // the binding store and every hub component into the startup graph for a
    // screen that no longer renders.
    const { BusinessHubScreen } = require("./BusinessHubScreen") as typeof import("./BusinessHubScreen");
    return <BusinessHubScreen route={route} navigation={navigation} />;
  }
  return <BusinessOsScreen navigation={navigation} />;
}

export default BusinessHubRoute;
