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
 *
 * This is the same split `SellerStore` uses for the Store rebuild. The classic
 * screen is untouched, so its existing test keeps passing against it directly.
 */

import { AdsManagerScreen } from "./AdsManagerScreen";
import { BusinessOsAdvertisingScreen } from "./BusinessOsAdvertisingScreen";
import type { RootStackParamList } from "../navigation/types";

type Props = {
  route?: { params?: RootStackParamList["BusinessOsAdvertising"] };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function AdvertisingRoute({ route, navigation }: Props) {
  if (route?.params?.mode === "classic") {
    return <BusinessOsAdvertisingScreen navigation={navigation} />;
  }
  return <AdsManagerScreen route={route} navigation={navigation} />;
}

export default AdvertisingRoute;
