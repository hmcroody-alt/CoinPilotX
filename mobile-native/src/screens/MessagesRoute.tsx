/**
 * Route wrapper for the rebuilt commerce inbox. The Business "Messages" card (and
 * message deep links) point here; this simply renders `CommerceInboxScreen`,
 * matching the `OrdersRoute` strangler pattern so the old Messenger tab stays
 * intact while the Business surface uses the new inbox.
 */

import { CommerceInboxScreen } from "./CommerceInboxScreen";
import { RootStackParamList } from "../navigation/types";

type Props = {
  route?: { params?: RootStackParamList["BusinessOsMessages"] };
  navigation?: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function MessagesRoute({ route, navigation }: Props) {
  return <CommerceInboxScreen route={route} navigation={navigation} />;
}
