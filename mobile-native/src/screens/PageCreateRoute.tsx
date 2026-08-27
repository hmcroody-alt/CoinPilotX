/**
 * Router for the `PageCreate` route.
 *
 * Presence Home's three creation buttons already refuse to open this screen —
 * they route through `useLaunchGate` and get the Coming Soon sheet. But a
 * button-level check is a convention, not a gate. This route is also reached by
 * `navigate("PageCreate")` in `PagesHubScreen`, by the `pulse/pages/create`
 * deep link in `linking.ts`, and by navigation state restored after a cold
 * start. All three arrive without passing any of the gated buttons.
 *
 * Putting the check here means there is no path in, which is the same shape as
 * `EventsRoute` and for the same reason.
 *
 * `PageCreateScreen` is untouched and still exported; nothing is deleted. When
 * the creation workflow is finished, the `presence:create*` rows come out of
 * `readiness.ts` and this file resumes rendering the real form with no edit
 * here.
 */

import { PageCreateScreen } from "./PageCreateScreen";
import { ComingSoonScreen } from "../launch/ComingSoonScreen";
import { GATED_ROUTES, routeReadiness } from "../launch/readiness";
import type { RootStackParamList } from "../navigation/types";

type Props = {
  route: { params?: RootStackParamList["PageCreate"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function PageCreateRoute({ route, navigation }: Props) {
  if (routeReadiness("PageCreate") !== "READY") {
    return (
      <ComingSoonScreen
        moduleId={GATED_ROUTES.PageCreate}
        label="New Presence"
        onBack={navigation?.goBack}
      />
    );
  }
  return <PageCreateScreen route={route as never} navigation={navigation as never} />;
}

export default PageCreateRoute;
