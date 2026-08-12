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
 *   • `mode: "promote"` (+ `promoteContent`) — the "Promote your content"
 *     wizard: boost an already-published post / reel / live replay (content →
 *     goal → audience → budget → duration → review → submit). The promotion
 *     references the original content object; nothing is duplicated.
 *   • `mode: "detail"` (+ `campaignId`) — the per-campaign management screen:
 *     overview, ad sets, creatives, insights, lifecycle actions.
 *   • `mode: "audiences"` — the audience manager (wave 2): list, create,
 *     lookalike, detail, edit, archive.
 *   • `mode: "creatives"` — the browsable creative library (wave 2): filters,
 *     asset detail, metadata editing, "use in campaign".
 *   • `mode: "policy"` — the Policy Center (wave 2): account status, review
 *     sections, rejection detail, appeals.
 *   • `mode: "reports"` — the reporting table (wave 2): presets, breakdowns,
 *     totals, CSV export.
 *   • `mode: "wallet"` — ads wallet & billing (wave 2): balance, funding,
 *     transactions, invoices, limits, auto top-up.
 *   • `mode: "insights"` — optimization recommendations with Apply (wave 2).
 *   • `mode: "account"` — the read-only account-standing sub-page, still on
 *     `AdsSubPageScreen`.
 *
 * Keeping the sub-pages under this route name is deliberate. A tile pointed at
 * a route name the navigator doesn't know does not degrade — it throws. One
 * registered name with a discriminated `mode` makes an unroutable destination
 * a type error at the call site instead.
 *
 * This is the same split `SellerStore` uses for the Store rebuild. The classic
 * screen is untouched, so its existing test keeps passing against it directly.
 */

import { AdsAudiencesScreen } from "./AdsAudiencesScreen";
import { AdsCampaignDetailScreen } from "./AdsCampaignDetailScreen";
import { AdsCampaignWizardScreen } from "./AdsCampaignWizardScreen";
import { AdsInsightsScreen } from "./AdsInsightsScreen";
import { AdsLibraryScreen } from "./AdsLibraryScreen";
import { AdsManagerScreen } from "./AdsManagerScreen";
import { AdsPolicyCenterScreen } from "./AdsPolicyCenterScreen";
import { AdsReportsScreen } from "./AdsReportsScreen";
import { AdsSubPageScreen } from "./AdsSubPageScreen";
import { AdsWalletScreen } from "./AdsWalletScreen";
import { PromoteContentWizardScreen } from "./PromoteContentWizardScreen";
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
  if (mode === "promote") {
    return <PromoteContentWizardScreen route={route} navigation={navigation} />;
  }
  if (mode === "detail") {
    return <AdsCampaignDetailScreen route={route} navigation={navigation} />;
  }
  if (mode === "classic") {
    return <BusinessOsAdvertisingScreen navigation={navigation} />;
  }
  if (mode === "audiences") {
    return <AdsAudiencesScreen route={route} navigation={navigation} />;
  }
  if (mode === "creatives") {
    return <AdsLibraryScreen route={route} navigation={navigation} />;
  }
  if (mode === "policy") {
    return <AdsPolicyCenterScreen route={route} navigation={navigation} />;
  }
  if (mode === "reports") {
    return <AdsReportsScreen route={route} navigation={navigation} />;
  }
  if (mode === "wallet") {
    return <AdsWalletScreen route={route} navigation={navigation} />;
  }
  if (mode === "insights") {
    return <AdsInsightsScreen route={route} navigation={navigation} />;
  }
  if (mode === "account") {
    return <AdsSubPageScreen surface={mode} route={route} navigation={navigation} />;
  }
  return <AdsManagerScreen route={route} navigation={navigation} />;
}

export default AdvertisingRoute;
