/**
 * Route wrapper for the Activity center — the destination of the bell in every
 * seller header. Mirrors the `EventsRoute` / `OrdersRoute` strangler pattern: one
 * registered route, one screen, so the bell and any deep link land on a valid
 * target. The legacy `ActivityInbox` route (the older category inbox) is
 * untouched; this is the rebuilt unified feed.
 */

import { ActivityScreen } from "./ActivityScreen";
import type { RootStackParamList } from "../navigation/types";

type Props = {
  route?: { params?: RootStackParamList["BusinessOsActivity"] };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function ActivityRoute({ route, navigation }: Props) {
  return <ActivityScreen route={route} navigation={navigation} />;
}

export default ActivityRoute;
