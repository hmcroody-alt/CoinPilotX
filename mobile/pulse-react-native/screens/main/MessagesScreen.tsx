import React from "react";
import { ApiPreview } from "../../components/ApiPreview";
import { ScreenScaffold } from "../../components/ScreenScaffold";

export function MessagesScreen() {
  return (
    <ScreenScaffold title="Messages" subtitle="Backed by /api/pulse/messages/conversations and Communications v2.">
      <ApiPreview endpoint="/api/pulse/messages/conversations" listKeys={["conversations", "threads"]} emptyLabel="No conversations returned yet." />
    </ScreenScaffold>
  );
}
