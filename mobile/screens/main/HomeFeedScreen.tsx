import React from "react";
import { ApiPreview } from "../../components/ApiPreview";
import { ScreenScaffold } from "../../components/ScreenScaffold";

export function HomeFeedScreen() {
  return (
    <ScreenScaffold title="Home Feed" subtitle="Backed by /api/pulse/feed.">
      <ApiPreview endpoint="/api/pulse/feed" listKeys={["posts", "feed"]} emptyLabel="No feed items returned yet." />
    </ScreenScaffold>
  );
}
