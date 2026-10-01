/**
 * Router for the `SellerStore` route.
 *
 * The Business "Sections" grid opens Store with `mode: "dashboard"`, and that
 * one mode now renders the rebuilt dashboard. Every other mode — `overview`,
 * `apply`, `profile`, `create`, `payouts`, and the `orders` mode the Orders
 * card uses — still renders `SellerStoreScreen` exactly as before.
 *
 * The split lives here rather than in the navigator so that `SellerStore`
 * remains a single registered route: deep links (`pulse/merchant/...`), the
 * `MerchantDashboard` and `MerchantProfile` aliases, and any `navigate("SellerStore", …)`
 * call anywhere in the app all keep working without knowing there are two
 * screens behind them.
 */

import { SellerStoreScreen } from "./SellerStoreScreen";
import { StoreDashboardScreen } from "./StoreDashboardScreen";
import { RootStackParamList } from "../navigation/types";

type Props = {
  route?: { params?: RootStackParamList["SellerStore"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

/** True when this route should render the rebuilt Store dashboard. */
export function isStoreDashboardRoute(params?: RootStackParamList["SellerStore"]) {
  return params?.mode === "dashboard";
}

export function SellerStoreRoute(props: Props) {
  if (isStoreDashboardRoute(props.route?.params)) {
    return <StoreDashboardScreen {...props} />;
  }
  return <SellerStoreScreen {...props} />;
}
