/**
 * Store surface components.
 *
 * These are exported as a set rather than being defined inside the screen
 * because Orders and Insights are next and will reuse the KPI card, the status
 * LED and the section-error treatment. Nothing here imports the Store screen or
 * knows about navigation — every one of them takes formatted strings and
 * callbacks, so they carry no locale or route assumptions.
 */

export { StoreHeader, StoreStatusStrip } from "./StoreHeader";
export type { StoreHeaderProps, StoreStatusStripProps } from "./StoreHeader";

export { StoreStatusLed, StoreLiveDot } from "./StoreStatusLed";
export type { StoreStatusLedProps } from "./StoreStatusLed";

export { StoreSparkline } from "./StoreSparkline";
export type { StoreSparklineProps } from "./StoreSparkline";

export { StoreKpiCard } from "./StoreKpiCard";
export type { StoreKpiCardProps, StoreKpiTrend } from "./StoreKpiCard";

export { StoreListingRow, listingStatusCopy } from "./StoreListingRow";
export type { StoreListingRowProps } from "./StoreListingRow";

export { StoreAttentionBanner } from "./StoreAttentionBanner";
export type { StoreAttentionBannerProps } from "./StoreAttentionBanner";

export {
  StoreQuickLinkTile,
  StoreQuickLinkGrid,
  QUICK_LINK_TILES_PER_ROW,
  QUICK_LINK_LABEL_LINES,
  QUICK_LINK_SUBTITLE_LINES,
  QUICK_LINK_LABEL_MAX_FONT_SCALE,
  QUICK_LINK_SUBTITLE_MAX_FONT_SCALE
} from "./StoreQuickLinkTile";
export type { StoreQuickLinkTileProps, StoreQuickLinkGridProps } from "./StoreQuickLinkTile";

export {
  StoreSetupChecklist,
  CHECKLIST_LABEL_MAX_FONT_SCALE,
  CHECKLIST_DETAIL_MAX_FONT_SCALE
} from "./StoreSetupChecklist";
export type { StoreSetupChecklistProps } from "./StoreSetupChecklist";

export { StoreTabBar } from "./StoreTabBar";
export type { StoreTabBarProps } from "./StoreTabBar";

export {
  StoreSkeletonBlock,
  StoreKpiSkeleton,
  StoreRowSkeleton,
  StoreSectionError,
  StoreEmptyListings,
  StoreOfflineNote
} from "./StoreStates";
