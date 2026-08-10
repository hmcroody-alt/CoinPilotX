/**
 * Advertising surface components.
 *
 * Two ad products live behind one screen — Marketplace ads (commerce, gold =
 * money) and Post ads (content promotion, violet) — and these are the shared
 * pieces both modes draw from. Like the Store set, every component here is dumb:
 * it takes formatted strings, a `reducedMotion` flag and callbacks, and knows
 * nothing about navigation, locale, or which mode is showing.
 *
 * The money-critical rule lives one layer down in api/adsDashboard: these views
 * never compute a balance or a spend, they only render what they are handed.
 */

export { AdsHeader } from "./AdsHeader";
export type { AdsHeaderProps } from "./AdsHeader";

export { ModeToggle } from "./ModeToggle";
export type { ModeToggleProps } from "./ModeToggle";

export { AdsStatusPill } from "./AdsStatusPill";
export type { AdsStatusPillProps } from "./AdsStatusPill";

export { WalletChip } from "./WalletChip";
export type { WalletChipProps } from "./WalletChip";

export { PauseSwitch } from "./PauseSwitch";
export type { PauseSwitchProps } from "./PauseSwitch";

export { BudgetPacingBar } from "./BudgetPacingBar";
export type { BudgetPacingBarProps } from "./BudgetPacingBar";

export { ACCOUNT_SPEND_TITLE, SEVEN_DAY_SPEND_TITLE, SpendBarChart } from "./SpendBarChart";
export type { SpendBarChartProps } from "./SpendBarChart";

export { CampaignCard } from "./CampaignCard";
export type {
  CampaignCardProps,
  CampaignCardAction,
  CampaignCardBudget,
  CampaignCardMetric
} from "./CampaignCard";

export { PromotedPostCard } from "./PromotedPostCard";
export type { PromotedPostCardProps, PromotedPostMetric } from "./PromotedPostCard";

export { PromoteRail } from "./PromoteRail";
export type { PromoteRailItem, PromoteRailProps } from "./PromoteRail";

export { SuggestionCard } from "./SuggestionCard";
export type { SuggestionCardProps } from "./SuggestionCard";

export { AdsTabBar } from "./AdsTabBar";
export type { AdsTab, AdsTabBarProps } from "./AdsTabBar";

export { AdsScreenShell, adsSubStyles } from "./AdsScreenShell";

export {
  AdsCampaignSkeleton,
  AdsChartSkeleton,
  AdsEmpty,
  AdsKpiSkeleton,
  AdsOfflineNote,
  AdsPreviewNote,
  AdsPromotionSkeleton,
  AdsSectionError,
  AdsSkeletonBlock,
  AdsVerificationBanner,
  AdsWalletUnavailable,
  AdsZeroBalanceBanner
} from "./AdsStates";
