import React from "react";
import { ApiPreview } from "../../components/ApiPreview";
import { ScreenScaffold } from "../../components/ScreenScaffold";

export function ReelsScreen() {
  return (
    <ScreenScaffold title="Reels" subtitle="Backed by /api/pulse/reels/feed.">
      <ApiPreview endpoint="/api/pulse/reels/feed" listKeys={["reels", "items"]} emptyLabel="No reels returned yet." />
    </ScreenScaffold>
  );
}
