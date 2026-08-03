/**
 * Router for the `BusinessOs` route — the seller's front door.
 *
 * This is the same split `SellerStore` and `BusinessOsAdvertising` already use.
 * One registered route name answers to two screens:
 *
 *   • default — the rebuilt Business Hub, which consumes each section's own
 *     source and routes the seller onward.
 *   • `mode: "classic"` — the previous sections screen, untouched, so its
 *     existing test (`screens/__tests__/BusinessOsScreen.test.tsx`, which
 *     imports it directly) keeps passing and there is a one-param escape hatch
 *     if the hub has to be backed out in a hurry.
 *
 * Keeping one route name is the point: every deep link, push notification and
 * tab that says "BusinessOs" today still lands somewhere valid tomorrow.
 */

import { BusinessHubScreen } from "./BusinessHubScreen";
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
  if (route?.params?.mode === "classic") {
    return <BusinessOsScreen navigation={navigation} />;
  }
  return <BusinessHubScreen route={route} navigation={navigation} />;
}

export default BusinessHubRoute;
