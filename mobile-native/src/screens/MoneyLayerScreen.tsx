/**
 * The money layers — one route, five screens.
 *
 * What these are for
 * ------------------
 * The Payments hub answers "how much". These answer "why", and they exist
 * because the hub had five figures on it that a seller could look at, want an
 * explanation for, and tap — and every one of those taps did nothing. A number
 * you cannot interrogate is a number you either believe or distrust, with no
 * third option. These layers are the third option.
 *
 * One route rather than five
 * --------------------------
 * `MoneyLayer` takes a `layer` param. Five routes would mean five copies of the
 * header, the refresh control, the offline handling, the error card and the
 * support reference — and the way that fails is not with a crash but with four
 * of them drifting while the fifth stays right. The layers differ in *what they
 * read and what they say*, which is the part that is genuinely different, and
 * that part is `moneyLayers.ts` plus one switch here.
 *
 * Where the numbers come from
 * ---------------------------
 * The same canonical sources the hub reads: `fetchMoneyOverview`,
 * `fetchConnectStatus`, `fetchSellerPayouts`, `fetchLedgerPage`. Nothing new was
 * built to feed these screens, and **nothing here adds up money** — no lifetime
 * paid-out total summed from a payout page, no available-minus-processing, no
 * counting. `MONEY_LAYER_GAPS` records what the design asked for that no
 * endpoint reports, and those tiles are absent rather than derived.
 *
 * Reads are scoped per layer on purpose. The Activity layer does not fetch a
 * connect status and the Payout History layer does not fetch a ledger page,
 * because a screen that fetches everything so that any layer might use it is how
 * a pull-to-refresh becomes four requests and a rate limit.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useRef, useState } from "react";
import { AppState, Linking, RefreshControl, ScrollView, StyleSheet, View } from "react-native";
import { connectMarketplacePayout } from "../api/marketplace";
import { PulseApiError } from "../api/pulseApi";
import {
  fetchLedgerPage,
  fetchMoneyOverview,
  formatMoney,
  formatSignedAmount,
  describeEntryForAccessibility,
  supportReferenceFor,
  type LedgerEntry,
  type LedgerKind,
  type SellerMoneyOverview
} from "../api/paymentsHub";
import {
  fetchConnectStatus,
  fetchSellerPayouts,
  maskedConnectRef,
  payoutStatusChip,
  type ConnectStatus,
  type SellerPayout
} from "../api/sellerPayouts";
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
import {
  ACTIVITY_FILTERS,
  activityEmptyKey,
  filterLedgerEntries,
  isMoneyLayerId,
  maskedPayoutReference,
  payoutOnboardingFailure,
  payoutOnboardingOutcome,
  payoutOnboardingPrefersServerMessage,
  payoutReadiness,
  processingExplainer,
  type ActivityFilterId,
  type MoneyLayerId,
  type PayoutOnboardingOutcome
} from "../money/moneyLayers";
import { RootStackParamList } from "../navigation/types";
import { moneyTheme } from "../theme/moneyTheme";

type Props = NativeStackScreenProps<RootStackParamList, "MoneyLayer">;

const NS = "commerce:money";

/** Which layer needs which read. See the file docstring on scoped reads. */
const READS: Record<MoneyLayerId, { overview: boolean; connect: boolean; payouts: boolean; ledger: boolean }> = {
  payout_overview: { overview: true, connect: true, payouts: false, ledger: false },
  processing: { overview: true, connect: false, payouts: false, ledger: true },
  move_money: { overview: true, connect: true, payouts: false, ledger: false },
  payout_history: { overview: false, connect: false, payouts: true, ledger: false },
  // Onboarding reads connect only. It deliberately does not read the balance:
  // a seller who cannot be paid yet is not helped by being shown the figure,
  // and a failed balance read must not be able to hide the setup button.
  payout_onboarding: { overview: false, connect: true, payouts: false, ledger: false },
  activity: { overview: false, connect: false, payouts: false, ledger: true }
};

const LAYER_TITLE_KEY: Record<MoneyLayerId, string> = {
  payout_overview: "layer.payoutOverview",
  processing: "layer.processing",
  move_money: "layer.moveMoney",
  payout_history: "layer.payoutHistory",
  payout_onboarding: "layer.payoutOnboarding",
  activity: "layer.activity"
};

export function MoneyLayerScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const fmt = useFormatters();

  // A bad param is not a crash and not a blank screen. Payout overview is the
  // layer every other one is reachable from, so it is the safe landing.
  const layer: MoneyLayerId = isMoneyLayerId(route?.params?.layer)
    ? (route?.params?.layer as MoneyLayerId)
    : "payout_overview";
  const currency = String(route?.params?.currency || "USD");
  const reads = READS[layer];

  const [overview, setOverview] = useState<SellerMoneyOverview | null>(null);
  const [connect, setConnect] = useState<ConnectStatus | null>(null);
  const [payouts, setPayouts] = useState<SellerPayout[]>([]);
  const [payoutCursor, setPayoutCursor] = useState<number | null>(null);
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [ledgerCursor, setLedgerCursor] = useState<string | null>(null);

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  const [failed, setFailed] = useState(false);
  const [supportReference, setSupportReference] = useState("");
  const [filter, setFilter] = useState<ActivityFilterId>("all");

  /**
   * The duplicate-request guard.
   *
   * A pull-to-refresh on a slow connection is easy to trigger three times, and
   * three overlapping reads of the same money endpoint can resolve out of order
   * and leave the screen showing the oldest answer. A ref rather than state
   * because the guard has to be correct between renders, not after one.
   */
  const inFlight = useRef(false);

  const load = useCallback(
    async (mode: "initial" | "refresh") => {
      if (inFlight.current) return;
      inFlight.current = true;
      if (mode === "initial") setLoading(true);
      if (mode === "refresh") setRefreshing(true);
      try {
        // allSettled, never all: one failed read must not be allowed to erase
        // another that succeeded. A seller who can see their payouts should not
        // lose them because a connect-status check timed out.
        const [overviewResult, connectResult, payoutResult, ledgerResult] = await Promise.allSettled([
          reads.overview ? fetchMoneyOverview(currency) : Promise.resolve(null),
          reads.connect ? fetchConnectStatus() : Promise.resolve(null),
          reads.payouts ? fetchSellerPayouts({ limit: 25 }) : Promise.resolve(null),
          reads.ledger ? fetchLedgerPage({ currency, limit: 25 }) : Promise.resolve(null)
        ]);

        if (overviewResult.status === "fulfilled") setOverview(overviewResult.value);
        if (connectResult.status === "fulfilled") setConnect(connectResult.value);
        if (payoutResult.status === "fulfilled" && payoutResult.value) {
          setPayouts(payoutResult.value.payouts);
          setPayoutCursor(payoutResult.value.has_more ? payoutResult.value.next_before_id : null);
        }
        if (ledgerResult.status === "fulfilled" && ledgerResult.value) {
          setEntries(ledgerResult.value.entries);
          setLedgerCursor(ledgerResult.value.has_more ? ledgerResult.value.next_cursor : null);
        }

        // "Failed" means the layer's own subject could not be read. A connect
        // status that failed on the Payout Overview is a missing explanation,
        // not a missing screen, and `payoutReadiness` renders it as "unknown".
        const primaryFailed =
          (reads.overview && overviewResult.status === "rejected") ||
          (reads.payouts && payoutResult.status === "rejected") ||
          (reads.ledger && ledgerResult.status === "rejected");
        setFailed(primaryFailed);
        setSupportReference(primaryFailed ? supportReferenceFor() : "");
      } finally {
        inFlight.current = false;
        setLoading(false);
        setRefreshing(false);
      }
    },
    [currency, reads.connect, reads.ledger, reads.overview, reads.payouts]
  );

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [load]);

  const loadMore = useCallback(async () => {
    if (inFlight.current || loadingMore) return;
    inFlight.current = true;
    setLoadingMore(true);
    try {
      if (reads.payouts && payoutCursor) {
        const page = await fetchSellerPayouts({ limit: 25, beforeId: payoutCursor });
        setPayouts((current) => [...current, ...page.payouts]);
        setPayoutCursor(page.has_more ? page.next_before_id : null);
      } else if (reads.ledger && ledgerCursor) {
        const page = await fetchLedgerPage({ currency, cursor: ledgerCursor, limit: 25 });
        setEntries((current) => [...current, ...page.entries]);
        setLedgerCursor(page.has_more ? page.next_cursor : null);
      }
    } catch {
      // A failed "load more" leaves what is already on screen alone. Replacing a
      // loaded list with an error card because page three failed would hide
      // pages one and two, which are still true.
    } finally {
      inFlight.current = false;
      setLoadingMore(false);
    }
  }, [currency, ledgerCursor, loadingMore, payoutCursor, reads.ledger, reads.payouts]);

  const goToLayer = useCallback(
    (next: MoneyLayerId) => {
      navigation?.push?.("MoneyLayer", { layer: next, currency });
    },
    [currency, navigation]
  );

  const headerTitle = route?.params?.title || t(`${NS}.${LAYER_TITLE_KEY[layer]}`);

  return (
    <View style={styles.screen}>
      <MoneyHeader
        title={headerTitle}
        onBack={() => navigation?.goBack?.()}
        backLabel={t("common:a11y.back")}
      />

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={styles.body}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => load("refresh").catch(() => undefined)}
            tintColor={moneyTheme.gold}
          />
        }
      >
        {loading ? <MoneyState kind="loading" title={t("common:status.loading")} /> : null}

        {!loading && failed ? (
          <MoneyState
            kind="error"
            title={t(`${NS}.states.errorTitle`)}
            body={t(`${NS}.states.errorBody`)}
            supportReference={supportReference}
            actionLabel={t("common:actions.retry")}
            onAction={() => load("refresh").catch(() => undefined)}
          />
        ) : null}

        {!loading && !failed ? (
          <LayerBody
            layer={layer}
            overview={overview}
            connect={connect}
            payouts={payouts}
            entries={entries}
            filter={filter}
            onFilter={setFilter}
            currency={currency}
            hasMore={Boolean(reads.payouts ? payoutCursor : reads.ledger ? ledgerCursor : null)}
            loadingMore={loadingMore}
            onLoadMore={() => loadMore().catch(() => undefined)}
            onLayer={goToLayer}
            onRetryStatus={() => load("refresh").catch(() => undefined)}
            navigation={navigation}
            t={t}
            fmt={fmt}
          />
        ) : null}
      </ScrollView>
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Bodies
 * ------------------------------------------------------------------ */

type Translate = (key: string, options?: Record<string, unknown>) => string;

type BodyProps = {
  layer: MoneyLayerId;
  overview: SellerMoneyOverview | null;
  connect: ConnectStatus | null;
  payouts: SellerPayout[];
  entries: LedgerEntry[];
  filter: ActivityFilterId;
  onFilter: (filter: ActivityFilterId) => void;
  currency: string;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onLayer: (layer: MoneyLayerId) => void;
  onRetryStatus: () => void;
  navigation: Props["navigation"];
  t: Translate;
  fmt: ReturnType<typeof useFormatters>;
};

function LayerBody(props: BodyProps) {
  switch (props.layer) {
    case "processing":
      return <ProcessingBody {...props} />;
    case "move_money":
      return <MoveMoneyBody {...props} />;
    case "payout_history":
      return <PayoutHistoryBody {...props} />;
    case "payout_onboarding":
      return <PayoutOnboardingBody {...props} />;
    case "activity":
      return <ActivityBody {...props} />;
    case "payout_overview":
    default:
      return <PayoutOverviewBody {...props} />;
  }
}

/**
 * Where the money is, and where it is going.
 *
 * The gold figure is Available, because on this layer that is the number the
 * seller opened the screen to see. Processing and Lifetime earnings render
 * plain — they are context, and three gold figures would mean none of them was
 * the point. There is no "paid out" tile: see `MONEY_LAYER_GAPS`.
 */
function PayoutOverviewBody({ overview, connect, currency, onLayer, onRetryStatus, t }: BodyProps) {
  const readiness = payoutReadiness(connect, overview);
  const destination =
    overview?.payout_method?.destination_masked || maskedConnectRef(connect) || "";

  return (
    <View>
      <MoneyCard accent="gold">
        <MoneyFigure
          label={t(`${NS}.overview.available`)}
          amount={formatMoney(overview?.available_cents, currency)}
          size="hero"
          accent="gold"
          unavailable={!overview}
        />
        <MoneyNote>{t(`${NS}.overview.availableNote`)}</MoneyNote>
      </MoneyCard>

      <View style={styles.pair}>
        <View style={styles.pairItem}>
          <MoneyCard
            onPress={() => onLayer("processing")}
            accessibilityLabel={t(`${NS}.layer.processing`)}
            accessibilityHint={t(`${NS}.a11y.openLayer`, { layer: t(`${NS}.layer.processing`) })}
          >
            <MoneyFigure
              label={t(`${NS}.overview.processing`)}
              amount={formatMoney(overview?.processing_cents, currency)}
              unavailable={!overview}
            />
          </MoneyCard>
        </View>
        <View style={styles.pairItem}>
          <MoneyCard>
            <MoneyFigure
              label={t(`${NS}.overview.lifetime`)}
              amount={formatMoney(overview?.lifetime_earnings_cents, currency)}
              unavailable={!overview}
            />
          </MoneyCard>
        </View>
      </View>

      <MoneySectionTitle>{t(`${NS}.payout.stageLabel`)}</MoneySectionTitle>
      <MoneyCard accent={readiness.payoutsEnabled ? "green" : "plain"}>
        <MoneyChip
          label={t(`${NS}.payout.${readiness.payoutsEnabled ? "enabled" : "disabled"}`)}
          tone={readiness.payoutsEnabled ? "success" : "neutral"}
        />
        <MoneyNote>{t(`${NS}.payout.${readiness.reasonKey}`)}</MoneyNote>
        {destination ? (
          <MoneyNote>{t(`${NS}.overview.destination`, { destination })}</MoneyNote>
        ) : (
          <MoneyNote>{t(`${NS}.overview.noDestination`)}</MoneyNote>
        )}
        <MoneyAction
          label={t(`${NS}.layer.moveMoney`)}
          onPress={() => onLayer("move_money")}
          accent="gold"
        />
      </MoneyCard>

      <MoneySectionTitle>{t(`${NS}.overview.moreTitle`)}</MoneySectionTitle>
      <MoneyCard>
        <MoneyListRow
          title={t(`${NS}.layer.payoutHistory`)}
          onPress={() => onLayer("payout_history")}
          accessibilityLabel={t(`${NS}.layer.payoutHistory`)}
          accessibilityHint={t(`${NS}.a11y.openLayer`, { layer: t(`${NS}.layer.payoutHistory`) })}
        />
        <MoneyListRow
          title={t(`${NS}.layer.activity`)}
          onPress={() => onLayer("activity")}
          accessibilityLabel={t(`${NS}.layer.activity`)}
          accessibilityHint={t(`${NS}.a11y.openLayer`, { layer: t(`${NS}.layer.activity`) })}
        />
      </MoneyCard>
      {/* The paid-out total the design asked for has no server field. Saying so
          is the honest alternative to summing a paginated list. */}
      <MoneyNote>{t(`${NS}.overview.paidOutUnavailable`)}</MoneyNote>

      {!readiness.payoutsEnabled ? (
        <View style={styles.trailingAction}>
          <MoneyAction label={t(`${NS}.payout.action${actionSuffix(readiness.action)}`)} onPress={onRetryStatus} />
        </View>
      ) : null}
    </View>
  );
}

/** Why money is not available yet, in the server's own words. */
function ProcessingBody({ overview, entries, currency, onLayer, t, fmt }: BodyProps) {
  const explainer = processingExplainer(overview);
  const held = filterLedgerEntries(entries, "held");

  return (
    <View>
      <MoneyCard accent="gold">
        <MoneyFigure
          label={t(`${NS}.processing.amountLabel`)}
          amount={formatMoney(overview?.processing_cents, currency)}
          size="hero"
          accent="gold"
          unavailable={!overview}
        />
        <MoneyNote>{t(`${NS}.processing.${explainer.key}`)}</MoneyNote>
      </MoneyCard>

      <MoneySectionTitle>{t(`${NS}.processing.ordersTitle`)}</MoneySectionTitle>
      {held.length === 0 ? (
        <MoneyState kind="empty" title={t(`${NS}.processing.empty`)} body={t(`${NS}.processing.emptyNote`)} />
      ) : (
        <MoneyCard>
          {held.map((entry) => (
            <MoneyListRow
              key={entry.id}
              title={entry.title || t(`${NS}.kind.escrow`)}
              meta={entry.created_at ? fmt.dateTime(entry.created_at) : undefined}
              amount={formatSignedAmount(entry)}
              accessibilityLabel={describeEntryForAccessibility(entry)}
            />
          ))}
        </MoneyCard>
      )}

      <View style={styles.trailingAction}>
        <MoneyAction label={t(`${NS}.layer.moveMoney`)} onPress={() => onLayer("move_money")} />
      </View>
    </View>
  );
}

/**
 * Can this seller be paid, and if not, what is in the way.
 *
 * The single action is `readiness.action` — one button, matched to the stage.
 * A screen offering "Set up payouts" beside "Manage payout account" beside
 * "Check status again" makes the seller guess which of the three applies to
 * them, which is the decision this layer exists to have already made.
 */
function MoveMoneyBody({ overview, connect, currency, onLayer, onRetryStatus, t }: BodyProps) {
  const readiness = payoutReadiness(connect, overview);
  const destination = overview?.payout_method?.destination_masked || maskedConnectRef(connect) || "";

  const onAction = () => {
    if (readiness.action === "retry_status") {
      onRetryStatus();
      return;
    }
    // Setup, resume and manage all open the onboarding layer, which owns the
    // one Connect hand-off. This previously opened the Verification Center —
    // that screen collects identity documents and never touches payouts, so
    // "Set up payouts" led a seller to upload an ID and still have no account.
    onLayer("payout_onboarding");
  };

  return (
    <View>
      <MoneyCard accent={readiness.payoutsEnabled ? "green" : "plain"}>
        <MoneyChip
          label={t(`${NS}.payout.stage.${readiness.stage}`)}
          tone={
            readiness.stage === "ready"
              ? "success"
              : readiness.stage === "blocked"
                ? "error"
                : readiness.stage === "unknown"
                  ? "neutral"
                  : "progress"
          }
        />
        <MoneyNote>{t(`${NS}.payout.${readiness.reasonKey}`)}</MoneyNote>
        <MoneyAction
          label={t(`${NS}.payout.action${actionSuffix(readiness.action)}`)}
          onPress={onAction}
          accent="gold"
        />
      </MoneyCard>

      <MoneySectionTitle>{t(`${NS}.payout.destinationLabel`)}</MoneySectionTitle>
      <MoneyCard>
        <MoneyNote>
          {destination
            ? t(`${NS}.overview.destination`, { destination })
            : t(`${NS}.overview.noDestination`)}
        </MoneyNote>
        {/* This platform stores a payment-provider reference, never an account
            number. Saying so is what stops the masked reference reading as a
            bank account the seller could check. */}
        <MoneyNote>{t(`${NS}.payout.destinationNone`)}</MoneyNote>
      </MoneyCard>

      {readiness.codes.length ? (
        <>
          <MoneySectionTitle>{t(`${NS}.payout.codesLabel`)}</MoneySectionTitle>
          <MoneyCard>
            {readiness.codes.map((code) => (
              <MoneyListRow key={code} title={code} accessibilityLabel={code} />
            ))}
          </MoneyCard>
        </>
      ) : null}

      <MoneySectionTitle>{t(`${NS}.overview.available`)}</MoneySectionTitle>
      <MoneyCard>
        <MoneyFigure
          label={t(`${NS}.overview.available`)}
          amount={formatMoney(overview?.available_cents, currency)}
          accent={readiness.payoutsEnabled ? "green" : "plain"}
          unavailable={!overview}
        />
      </MoneyCard>
    </View>
  );
}

/**
 * Getting a payout account, honestly.
 *
 * The mission design for this layer showed a five-step native flow: an
 * information form, a provider picker offering Bank/PayPal/Wise/Payoneer, and a
 * verification step. None of it was built, because none of it is real:
 *
 *   • This platform is Stripe Connect Express and nothing else. `paypal`,
 *     `wise` and `payoneer` appear nowhere in the backend, so a picker would be
 *     three dead options next to one live one.
 *   • The name, country, currency and bank details are collected by Stripe's
 *     own hosted onboarding. A native form asking for them would be a form
 *     whose values are dropped on submit, which is worse than no form.
 *
 * So this layer does the part that is genuinely ours — say why an account is
 * needed, report where the seller stands, and hand off — and lets Stripe own
 * the steps Stripe owns. `startOnboarding` is the whole flow, and every one of
 * its five outcomes gets a sentence.
 */
function PayoutOnboardingBody({ connect, onRetryStatus, navigation, t }: BodyProps) {
  const readiness = payoutReadiness(connect, null);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<PayoutOnboardingOutcome | null>(null);

  /**
   * Armed only once the seller has actually been sent to Stripe.
   *
   * Stripe onboarding finishes in a browser, outside the app, so the status this
   * screen is showing goes stale the moment the seller leaves — and the return
   * from Stripe is a foreground event, not a navigation this screen can observe.
   * Refreshing on every foreground would re-read the money endpoints each time
   * the seller checks a notification, so the listener stays inert until the
   * hand-off has happened and disarms itself after one refresh.
   */
  const awaitingStripeReturn = useRef(false);

  // Held in a ref because the parent passes a fresh arrow every render; as an
  // effect dependency it would tear down and re-add the listener each time.
  const retryStatusRef = useRef(onRetryStatus);
  retryStatusRef.current = onRetryStatus;

  useEffect(() => {
    const subscription = AppState.addEventListener("change", (state) => {
      if (state !== "active" || !awaitingStripeReturn.current) return;
      awaitingStripeReturn.current = false;
      retryStatusRef.current();
    });
    return () => subscription.remove();
  }, []);

  const startOnboarding = useCallback(async () => {
    if (busy) return;
    setBusy(true);
    setOutcome(null);
    try {
      // `connectMarketplacePayout` is the canonical wrapper for
      // POST /api/pulse/payouts/connect — the one route that mints a Connect
      // account link. It is named for the screen that happened to need it
      // first; a second wrapper for the same route is exactly the duplication
      // this mission forbids, so this layer imports that one rather than
      // adding a payouts-shaped alias beside it.
      const result = await connectMarketplacePayout();
      const next = payoutOnboardingOutcome(result);
      setOutcome(next);
      if (next.kind === "ready") {
        const opened = await Linking.openURL(next.url).then(
          () => true,
          () => false
        );
        awaitingStripeReturn.current = opened;
      }
    } catch (error) {
      const status = error instanceof PulseApiError ? error.status : 0;
      setOutcome(payoutOnboardingFailure(status, error instanceof Error ? error.message : ""));
    } finally {
      setBusy(false);
    }
  }, [busy]);

  return (
    <View>
      <MoneyCard accent={readiness.payoutsEnabled ? "green" : "gold"}>
        <MoneyChip
          label={t(`${NS}.payout.stage.${readiness.stage}`)}
          tone={
            readiness.stage === "ready"
              ? "success"
              : readiness.stage === "blocked"
                ? "error"
                : readiness.stage === "unknown"
                  ? "neutral"
                  : "progress"
          }
        />
        <MoneyNote>{t(`${NS}.payout.${readiness.reasonKey}`)}</MoneyNote>
      </MoneyCard>

      <MoneySectionTitle>{t(`${NS}.onboarding.whyTitle`)}</MoneySectionTitle>
      <MoneyCard>
        <MoneyNote>{t(`${NS}.onboarding.whyBody`)}</MoneyNote>
        {/* Named before the seller taps, not after they land on a Stripe page
            and wonder who is asking for their bank details. */}
        <MoneyNote>{t(`${NS}.onboarding.providerNote`)}</MoneyNote>
      </MoneyCard>

      <MoneySectionTitle>{t(`${NS}.onboarding.stepsTitle`)}</MoneySectionTitle>
      <MoneyCard>
        <MoneyListRow title={t(`${NS}.onboarding.step1`)} accessibilityLabel={t(`${NS}.onboarding.step1`)} />
        <MoneyListRow title={t(`${NS}.onboarding.step2`)} accessibilityLabel={t(`${NS}.onboarding.step2`)} />
        <MoneyListRow title={t(`${NS}.onboarding.step3`)} accessibilityLabel={t(`${NS}.onboarding.step3`)} />
      </MoneyCard>

      {outcome ? (
        <MoneyCard accent={outcome.kind === "ready" ? "green" : "plain"}>
          {/* One sentence, not two — see `payoutOnboardingPrefersServerMessage`. */}
          <MoneyNote>
            {payoutOnboardingPrefersServerMessage(outcome)
              ? outcome.serverMessage
              : t(`${NS}.onboarding.${outcome.messageKey}`)}
          </MoneyNote>
          {outcome.kind === "needs_seller_approval" ? (
            <MoneyAction
              label={t(`${NS}.onboarding.openSeller`)}
              onPress={() => navigation?.navigate?.("SellerStore", { mode: "apply" })}
            />
          ) : null}
        </MoneyCard>
      ) : null}

      <View style={styles.trailingAction}>
        <MoneyAction
          label={t(`${NS}.onboarding.${busy ? "starting" : "start"}`)}
          onPress={startOnboarding}
          accent="gold"
        />
        <MoneyAction label={t(`${NS}.payout.actionRetryStatus`)} onPress={onRetryStatus} />
      </View>
    </View>
  );
}

/** Every payout, newest first, each one openable. */
function PayoutHistoryBody({ payouts, hasMore, loadingMore, onLoadMore, navigation, t, fmt }: BodyProps) {
  if (payouts.length === 0) {
    return (
      <MoneyState
        kind="empty"
        title={t(`${NS}.payouts.empty`)}
        body={t(`${NS}.payouts.emptyNote`)}
      />
    );
  }
  return (
    <View>
      <MoneyCard>
        {payouts.map((payout) => {
          const chip = payoutStatusChip(payout.status);
          const label = chip.key ? t(`commerce:payments.${chip.key}`) : payout.status;
          return (
            <MoneyListRow
              key={payout.id}
              title={formatMoney(payout.amount_cents, payout.currency)}
              meta={payout.created_at ? fmt.dateTime(payout.created_at) : undefined}
              chip={{ label, tone: chip.tone }}
              onPress={() => navigation?.push?.("MoneyDetail", { subject: "payout", payout })}
              accessibilityLabel={`${formatMoney(payout.amount_cents, payout.currency)}, ${label}`}
              accessibilityHint={t(`${NS}.a11y.openPayout`)}
            />
          );
        })}
      </MoneyCard>
      {hasMore ? (
        <View style={styles.trailingAction}>
          <MoneyAction
            label={loadingMore ? t("common:status.loading") : t("common:actions.loadMore")}
            onPress={onLoadMore}
            disabled={loadingMore}
          />
        </View>
      ) : null}
    </View>
  );
}

/** The seller ledger, filtered by the buckets it actually contains. */
function ActivityBody({
  entries,
  filter,
  onFilter,
  hasMore,
  loadingMore,
  onLoadMore,
  navigation,
  t,
  fmt
}: BodyProps) {
  const visible = filterLedgerEntries(entries, filter);
  const emptyKey = activityEmptyKey(entries.length, filter);

  return (
    <View>
      <View style={styles.filters}>
        {ACTIVITY_FILTERS.map((id) => (
          <MoneyAction
            key={id}
            label={t(`${NS}.activity.filter.${id}`)}
            onPress={() => onFilter(id)}
            accent={id === filter ? "gold" : "plain"}
          />
        ))}
      </View>

      {/* The feed's own boundary, stated rather than implied. Ad spend and
          rewards are real money movements that are not on this ledger, and a
          seller who does not know that reads the feed as incomplete. */}
      <MoneyNote>{t(`${NS}.activity.scopeNote`)}</MoneyNote>

      {visible.length === 0 ? (
        <MoneyState
          kind="empty"
          title={t(`${NS}.activity.${emptyKey}`)}
          body={emptyKey === "emptyFeed" ? t(`${NS}.activity.emptyFeedNote`) : undefined}
        />
      ) : (
        <MoneyCard>
          {visible.map((entry) => (
            <MoneyListRow
              key={entry.id}
              title={entry.title || t(`${NS}.kind.${entry.kind}`)}
              meta={[t(`${NS}.kind.${entry.kind}`), entry.created_at ? fmt.dateTime(entry.created_at) : ""]
                .filter(Boolean)
                .join(" · ")}
              amount={formatSignedAmount(entry)}
              // Green for cleared money in. Gold stays reserved for the figure a
              // layer is *about*, and a feed is about no single row.
              amountAccent={entry.sign === "+" ? "green" : "plain"}
              onPress={() => navigation?.push?.("MoneyDetail", { subject: "entry", entry })}
              accessibilityLabel={describeEntryForAccessibility(entry)}
              accessibilityHint={t(`${NS}.a11y.openEntry`)}
            />
          ))}
        </MoneyCard>
      )}

      {hasMore ? (
        <View style={styles.trailingAction}>
          <MoneyAction
            label={loadingMore ? t("common:status.loading") : t("common:actions.loadMore")}
            onPress={onLoadMore}
            disabled={loadingMore}
          />
        </View>
      ) : null}
    </View>
  );
}

/* ------------------------------------------------------------------ *
 * Helpers
 * ------------------------------------------------------------------ */

/** `retry_status` → `RetryStatus`, so one action maps to one catalog key. */
function actionSuffix(action: string): string {
  return action
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join("");
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
  },
  pair: {
    flexDirection: "row",
    gap: 12,
    marginTop: 12
  },
  pairItem: {
    flex: 1
  },
  filters: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  trailingAction: {
    marginTop: 14
  }
});
