/**
 * Router for the `BusinessOsOrders` route.
 *
 * The Orders card in the Business dashboard points here, and the buyer-side
 * "Your orders" entry can point here too with `perspective: "buyer"`. One route
 * name, one screen, two perspectives — so every existing deep link and dashboard
 * tile lands somewhere valid and the perspective toggle owns the seller/buyer
 * split rather than a second navigation target.
 *
 * This mirrors the `AdvertisingRoute` / `SellerStoreRoute` split used for the
 * Advertising and Store rebuilds: the route stays a single registered entry so
 * callers never need to know which screen answers.
 */

import { OrdersManagerScreen } from "./OrdersManagerScreen";
import type { RootStackParamList } from "../navigation/types";

type Props = {
  route?: { params?: RootStackParamList["BusinessOsOrders"] };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function OrdersRoute({ route, navigation }: Props) {
  return <OrdersManagerScreen route={route} navigation={navigation} />;
}

export default OrdersRoute;
