/**
 * Router for the `BusinessOsEvents` route.
 *
 * The Business "Events" card (config-driven via EVENTS_CARD_CONFIG) points here.
 * One registered route, one screen — the rebuilt hosted-events manager — so the
 * dashboard tile and any deep link land on a valid target. This mirrors the
 * `OrdersRoute` / `MessagesRoute` split used for the Orders and Messages
 * rebuilds; the legacy `Events` (live discovery) route is untouched.
 *
 * It is also where the launch gate is enforced for this route. The Business card
 * already refuses to open it, but a card-level check is a convention, not a
 * gate: a deep link, restored navigation state after a cold start, or a stray
 * `navigate("BusinessOsEvents")` in some other surface all arrive here without
 * passing the card. Putting the check at the router means there is no path in.
 *
 * `EventsManagerScreen` is untouched and still exported. Nothing is deleted —
 * when `/api/pulse/live-now` starts returning scheduled events, the
 * `business:events` row comes out of `readiness.ts` and this file resumes
 * rendering the manager with no edit here.
 */

import { EventsManagerScreen } from "./EventsManagerScreen";
import { EVENTS_CARD_CONFIG } from "../api/businessOs";
import { ComingSoonScreen } from "../launch/ComingSoonScreen";
import { GATED_ROUTES, routeReadiness } from "../launch/readiness";
import type { RootStackParamList } from "../navigation/types";

type Props = {
  route?: { params?: RootStackParamList["BusinessOsEvents"] };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function EventsRoute({ route, navigation }: Props) {
  if (routeReadiness("BusinessOsEvents") !== "READY") {
    return (
      <ComingSoonScreen
        moduleId={GATED_ROUTES.BusinessOsEvents}
        label={EVENTS_CARD_CONFIG.label}
        onBack={navigation?.goBack}
      />
    );
  }
  return <EventsManagerScreen route={route} navigation={navigation} />;
}

export default EventsRoute;
