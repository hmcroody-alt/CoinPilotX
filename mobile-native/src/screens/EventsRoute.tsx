/**
 * Router for the `BusinessOsEvents` route.
 *
 * The Business "Events" card (config-driven via EVENTS_CARD_CONFIG) points here.
 * One registered route, one screen — the rebuilt hosted-events manager — so the
 * dashboard tile and any deep link land on a valid target. This mirrors the
 * `OrdersRoute` / `MessagesRoute` split used for the Orders and Messages
 * rebuilds; the legacy `Events` (live discovery) route is untouched.
 */

import { EventsManagerScreen } from "./EventsManagerScreen";
import type { RootStackParamList } from "../navigation/types";

type Props = {
  route?: { params?: RootStackParamList["BusinessOsEvents"] };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function EventsRoute({ route, navigation }: Props) {
  return <EventsManagerScreen route={route} navigation={navigation} />;
}

export default EventsRoute;
