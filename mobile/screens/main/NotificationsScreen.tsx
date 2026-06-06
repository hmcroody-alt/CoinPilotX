import React from "react";
import { ApiPreview } from "../../components/ApiPreview";
import { ScreenScaffold } from "../../components/ScreenScaffold";

export function NotificationsScreen() {
  return (
    <ScreenScaffold title="Notifications" subtitle="Backed by /api/pulse/notifications.">
      <ApiPreview endpoint="/api/pulse/notifications" listKeys={["notifications", "items"]} emptyLabel="No notifications returned yet." />
    </ScreenScaffold>
  );
}
