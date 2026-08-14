/**
 * One payout, or one ledger row, in full.
 *
 * Why the subject arrives in the route params
 * -------------------------------------------
 * There is no `GET /payouts/{id}` and no `GET /ledger/{id}` on this platform —
 * both objects are only ever returned inside a page. So this screen is handed
 * the record the list already holds rather than re-fetching one it cannot ask
 * for. The alternative would be a detail screen that refetches the whole page
 * and searches it, which is slower, can fail, and can silently show a different
 * row if the page shifted underneath.
 *
 * The consequence is stated rather than hidden: this screen has no refresh. A
 * record that changed since the list loaded will show its state as of that
 * load, and the honest fix is to go back and pull to refresh the list — which
 * is what the footer note says.
 *
 * What is deliberately not shown
 * ------------------------------
 * The full provider identifier. `maskedPayoutReference` cuts it to four
 * characters for the same reason `maskedConnectRef` does: enough for a seller
 * and support to agree they mean the same payout, and not a whole credential
 * sitting in a screenshot.
 *
 * And no arrival date, no "expected by", no included-earnings list. None of the
 * three exists in the schema — see `MONEY_LAYER_GAPS` — and each is recorded on
 * this screen as a sentence instead of being estimated.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { ScrollView, StyleSheet, View } from "react-native";
import {
  describeEntryForAccessibility,
  formatMoney,
  formatSignedAmount,
  type LedgerEntry
} from "../api/paymentsHub";
import { payoutStatusChip, type SellerPayout } from "../api/sellerPayouts";
import {
  MoneyAction,
  MoneyCard,
  MoneyChip,
  MoneyFigure,
  MoneyHeader,
  MoneyListRow,
  MoneyNote,
  MoneySectionTitle,
  MoneyState
} from "../components/money/MoneyChrome";
import { useFormatters, useTranslation } from "../i18n";
import { maskedPayoutReference, payoutFailure, payoutIsTerminal } from "../money/moneyLayers";
import { RootStackParamList } from "../navigation/types";
import { moneyTheme } from "../theme/moneyTheme";

type Props = NativeStackScreenProps<RootStackParamList, "MoneyDetail">;

const NS = "commerce:money";

export function MoneyDetailScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const fmt = useFormatters();

  const params = route?.params;
  const payout = params?.subject === "payout" ? params.payout : null;
  const entry = params?.subject === "entry" ? params.entry : null;

  const title =
    params?.title || t(`${NS}.${payout ? "payouts.detailTitle" : "activity.detailTitle"}`);

  return (
    <View style={styles.screen}>
      <MoneyHeader title={title} onBack={() => navigation?.goBack?.()} backLabel={t("common:a11y.back")} />
      <ScrollView style={styles.scroll} contentContainerStyle={styles.body}>
        {payout ? (
          <PayoutDetail payout={payout} navigation={navigation} t={t} fmt={fmt} />
        ) : entry ? (
          <EntryDetail entry={entry} t={t} fmt={fmt} />
        ) : (
          // A detail screen with no subject is a navigation bug, not a data
          // outage. It says so and offers the way out rather than an empty page.
          <MoneyState
            kind="error"
            title={t(`${NS}.detail.missingTitle`)}
            body={t(`${NS}.detail.missingBody`)}
            actionLabel={t("common:a11y.back")}
            onAction={() => navigation?.goBack?.()}
          />
        )}
      </ScrollView>
    </View>
  );
}

type Translate = (key: string, options?: Record<string, unknown>) => string;
type Fmt = ReturnType<typeof useFormatters>;

function PayoutDetail({
  payout,
  navigation,
  t,
  fmt
}: {
  payout: SellerPayout;
  navigation: Props["navigation"];
  t: Translate;
  fmt: Fmt;
}) {
  const chip = payoutStatusChip(payout.status);
  const chipLabel = chip.key ? t(`commerce:payments.${chip.key}`) : payout.status;
  const failure = payoutFailure(payout);
  const reference = maskedPayoutReference(payout.stripe_payout_id);

  return (
    <View>
      <MoneyCard accent="gold">
        {/* The amount stays gold even on a failed payout. A payout that failed
            is still a payout; the chip carries the judgement, and recolouring
            the figure would make the amount itself look wrong. */}
        <MoneyFigure
          label={t(`${NS}.detail.amount`)}
          amount={formatMoney(payout.amount_cents, payout.currency)}
          size="hero"
          accent="gold"
        />
        <MoneyChip label={chipLabel} tone={chip.tone} />
      </MoneyCard>

      <MoneySectionTitle>{t(`${NS}.detail.title`)}</MoneySectionTitle>
      <MoneyCard>
        <MoneyListRow
          title={t(`${NS}.detail.status`)}
          meta={chipLabel}
          accessibilityLabel={`${t(`${NS}.detail.status`)}, ${chipLabel}`}
        />
        {payout.created_at ? (
          <MoneyListRow
            title={t(`${NS}.detail.created`)}
            meta={fmt.dateTime(payout.created_at)}
            accessibilityLabel={`${t(`${NS}.detail.created`)}, ${fmt.dateTime(payout.created_at)}`}
          />
        ) : null}
        {payout.updated_at ? (
          <MoneyListRow
            title={t(`${NS}.detail.updated`)}
            meta={fmt.dateTime(payout.updated_at)}
            accessibilityLabel={`${t(`${NS}.detail.updated`)}, ${fmt.dateTime(payout.updated_at)}`}
          />
        ) : null}
        {reference ? (
          <MoneyListRow
            title={t(`${NS}.detail.reference`)}
            meta={reference}
            accessibilityLabel={`${t(`${NS}.detail.reference`)}, ${reference}`}
          />
        ) : null}
        <MoneyListRow
          title={t(`${NS}.detail.currency`)}
          meta={String(payout.currency || "").toUpperCase()}
          accessibilityLabel={`${t(`${NS}.detail.currency`)}, ${payout.currency}`}
        />
      </MoneyCard>

      {failure ? (
        <>
          <MoneySectionTitle>{t(`${NS}.payouts.failureLabel`)}</MoneySectionTitle>
          <MoneyCard>
            {/* The provider's message is written for the account holder and is
                the sentence; the code is the reference. Never the reverse — a
                seller should not be told their payout failed because of
                `account_closed`. */}
            <MoneyNote>{failure.messageKey ? t(`${NS}.payouts.${failure.messageKey}`) : failure.message}</MoneyNote>
            {failure.code ? <MoneyChip label={failure.code} tone="error" /> : null}
          </MoneyCard>
        </>
      ) : null}

      <MoneySectionTitle>{t(`${NS}.payouts.relatedTitle`)}</MoneySectionTitle>
      <MoneyCard>
        <MoneyNote>{t(`${NS}.payouts.relatedNote`)}</MoneyNote>
        <MoneyAction
          label={t(`${NS}.payouts.openActivity`)}
          onPress={() => navigation?.push?.("MoneyLayer", { layer: "activity", currency: payout.currency })}
        />
      </MoneyCard>

      <MoneyNote>{t(`${NS}.payouts.noArrival`)}</MoneyNote>
      {/* Offered only while the payout can still change. A "check again" on a
          payout that settled weeks ago implies its state is still in doubt. */}
      {!payoutIsTerminal(payout.status) ? <MoneyNote>{t(`${NS}.detail.staleNote`)}</MoneyNote> : null}
    </View>
  );
}

function EntryDetail({ entry, t, fmt }: { entry: LedgerEntry; t: Translate; fmt: Fmt }) {
  const kindWord = t(`${NS}.kind.${entry.kind}`);
  return (
    <View>
      <MoneyCard accent="gold">
        <MoneyFigure
          label={kindWord}
          amount={formatSignedAmount(entry)}
          size="hero"
          accent="gold"
          // A held row is announced as held and still theirs. `describeEntry…`
          // is the same sentence the hub's rows use, so the two never diverge.
        />
        <MoneyNote>{describeEntryForAccessibility(entry)}</MoneyNote>
      </MoneyCard>

      <MoneySectionTitle>{t(`${NS}.detail.title`)}</MoneySectionTitle>
      <MoneyCard>
        <MoneyListRow
          title={t(`${NS}.detail.type`)}
          meta={kindWord}
          accessibilityLabel={`${t(`${NS}.detail.type`)}, ${kindWord}`}
        />
        {entry.status ? (
          <MoneyListRow
            title={t(`${NS}.detail.status`)}
            meta={entry.status}
            accessibilityLabel={`${t(`${NS}.detail.status`)}, ${entry.status}`}
          />
        ) : null}
        {entry.created_at ? (
          <MoneyListRow
            title={t(`${NS}.detail.created`)}
            meta={fmt.dateTime(entry.created_at)}
            accessibilityLabel={`${t(`${NS}.detail.created`)}, ${fmt.dateTime(entry.created_at)}`}
          />
        ) : null}
        {entry.reference ? (
          <MoneyListRow
            title={t(`${NS}.detail.reference`)}
            meta={`${entry.reference.type} ${entry.reference.id}`}
            accessibilityLabel={`${t(`${NS}.detail.reference`)}, ${entry.reference.type} ${entry.reference.id}`}
          />
        ) : null}
        {entry.provider ? (
          <MoneyListRow
            title={t(`${NS}.detail.provider`)}
            meta={entry.provider}
            accessibilityLabel={`${t(`${NS}.detail.provider`)}, ${entry.provider}`}
          />
        ) : null}
        <MoneyListRow
          title={t(`${NS}.detail.currency`)}
          meta={String(entry.currency || "").toUpperCase()}
          accessibilityLabel={`${t(`${NS}.detail.currency`)}, ${entry.currency}`}
        />
      </MoneyCard>

      {entry.kind === "escrow" ? <MoneyNote>{t(`${NS}.detail.held`)}</MoneyNote> : null}
      <MoneyNote>{t(`${NS}.detail.staleNote`)}</MoneyNote>
    </View>
  );
}

const styles = StyleSheet.create({
  screen: {
    flex: 1,
    backgroundColor: moneyTheme.bg.page
  },
  scroll: {
    flex: 1
  },
  body: {
    padding: moneyTheme.space.gutter,
    paddingBottom: 48,
    gap: 12
  }
});
