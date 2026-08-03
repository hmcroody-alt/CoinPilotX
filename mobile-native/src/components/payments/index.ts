/**
 * Barrel for the shared Payments components.
 *
 * These are shared in the same sense the Orders and Marketplace barrels are:
 * any surface that renders a balance, a ledger row or a payout destination
 * imports from here, so there is one implementation of "how money looks" and in
 * particular one implementation of the escrow-is-not-a-loss rule. A second
 * hand-rolled ledger row somewhere else in the app is how two screens start
 * disagreeing about whether a hold is negative.
 */

export { BalanceHero } from "./BalanceHero";
export type { BalanceHeroProps } from "./BalanceHero";

export { BalanceCard } from "./BalanceCard";
export type { BalanceCardProps } from "./BalanceCard";

export { LedgerRow } from "./LedgerRow";
export type { LedgerRowProps } from "./LedgerRow";

export { LedgerDayGroup, groupLedgerByDay } from "./LedgerDayGroup";
export type { LedgerDay, LedgerDayGroupProps } from "./LedgerDayGroup";

export { PayoutMethodCard } from "./PayoutMethodCard";
export type { PayoutMethodCardProps, PayoutMethodState } from "./PayoutMethodCard";

export { RefundActionBanner } from "./RefundActionBanner";
export type { RefundActionBannerProps } from "./RefundActionBanner";

export { DocumentTile, DocumentSection } from "./DocumentTile";
export type { PaymentDocument, DocumentTileProps, DocumentSectionProps } from "./DocumentTile";

export {
  PaymentsLoading,
  PaymentsEmpty,
  PaymentsError,
  PaymentsOffline,
  PayoutInFlightNotice,
  PayoutFailedNotice
} from "./PaymentsStates";
