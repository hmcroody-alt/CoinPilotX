/**
 * Router for the `BusinessOsAdvertising` route.
 *
 * The Advertising card in the Business dashboard keeps pointing at one route
 * name, so every existing deep link, notification and dashboard tile still
 * lands somewhere valid. What changed is which screen answers:
 *
 *   • default — the rebuilt two-sided ads manager.
 *   • `mode: "classic"` — the previous screen, which owns the ad-account and
 *     campaign creation forms and the objective/budget editor. The manager
 *     routes here for those flows rather than reimplementing a second creation
 *     path that could drift from the first.
 *   • `mode: "create"` — the native campaign-creation wizard (objective →
 *     setup → audience → placements → creative → budget → review → publish),
 *     with a persisted draft that survives app restarts.
 *   • `mode: "audiences" | "creatives" | "account" | "policy"` — the manager's
 *     sub-pages. The first two are the destinations behind what used to be two
 *     locked, unopenable tiles; the third is where the ad account number went
 *     when it was taken out of the dashboard header; the fourth is the Policy
 *     Center, which reads the review board and is the only one of the four that
 *     shows server data about this advertiser's own creatives.
 *
 * Keeping the sub-pages under this route name is deliberate. A tile pointed at
 * a route name the navigator doesn't know does not degrade — it throws. One
 * registered name with a discriminated `mode` makes an unroutable destination
 * a type error at the call site instead.
 *
 * This is the same split `SellerStore` uses for the Store rebuild. The classic
 * screen is untouched, so its existing test keeps passing against it directly.
 */

import { AdsCampaignWizardScreen } from "./AdsCampaignWizardScreen";
import { AdsManagerScreen } from "./AdsManagerScreen";
import { AdsSubPageScreen } from "./AdsSubPageScreen";
import { BusinessOsAdvertisingScreen } from "./BusinessOsAdvertisingScreen";
import type { RootStackParamList } from "../navigation/types";

type Props = {
  route?: { params?: RootStackParamList["BusinessOsAdvertising"] };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function AdvertisingRoute({ route, navigation }: Props) {
  const mode = route?.params?.mode;
  if (mode === "create") {
    return <AdsCampaignWizardScreen route={route} navigation={navigation} />;
  }
  if (mode === "classic") {
    return <BusinessOsAdvertisingScreen navigation={navigation} />;
  }
  if (mode === "audiences" || mode === "creatives" || mode === "account" || mode === "policy") {
    return <AdsSubPageScreen surface={mode} route={route} navigation={navigation} />;
  }
  return <AdsManagerScreen route={route} navigation={navigation} />;
}

export default AdvertisingRoute;
