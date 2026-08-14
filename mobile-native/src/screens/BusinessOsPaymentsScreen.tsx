/**
 * Payments — the seller's money hub.
 *
 * The rule that governs every line below
 * --------------------------------------
 * Every number on this screen is a direct render of a backend financial record.
 * There is no placeholder for money. Where a module has no real source, the
 * module is **absent** — not empty, not disabled, not showing a zero — behind a
 * feature flag that defaults off.
 *
 * What is absent today, and why
 * -----------------------------
 * 1. **Withdraw / request payout** is LIVE (`payoutInitiationIsLive`), bound to
 *    `POST /api/pulse/payments/seller/payouts` via `api/sellerPayouts`. The
 *    button renders only when the connect status says `payouts_enabled` and the
 *    available balance is positive; otherwise the section states the honest
 *    reason and routes to Verification Center. The idempotency key is minted
 *    when the confirm step opens, cart-checkout style.
 * 2. **Instant payout** (`instantPayoutIsLive`) stays absent — there is no fee
 *    quote endpoint, and this screen computes no fees.
 * 3. **The escrow card** (`escrowCardIsLive`). Per-order escrow lives only in
 *    the Business OS ledger, whose routes are inert in production. The live
 *    wallet has a `hold` entry type but nothing writes per-order holds.
 * 4. **Statements** (`statementsAreLive`) and **tax documents**
 *    (`taxDocumentsAreLive`). Nothing generates either. An empty tax section is
 *    itself a claim — "no form for this year" asserts a threshold determination
 *    that this system never performs — so the section does not render at all.
 *
 * Balances, ledger and ad wallet settle independently
 * ---------------------------------------------------
 * `Promise.allSettled` rather than `Promise.all`, so a ledger that loads cannot
 * paper over balances that did not. A failed balance read sets `balanceError`,
 * which renders "—" and a retry; it never falls back to a cached figure.
 * Cached money appears on exactly one path — `offlineAsOf` — and always carries
 * the time it was true.
 *
 * Money math
 * ----------
 * None here. Every figure is a finished server total; the only arithmetic in
 * this file is `Math.max` on a list length. Fee and net computation stay
 * server-side by design.
 *
 * One source of truth with the other screens
 * ------------------------------------------
 * The ad wallet comes through `fetchAdWallet`, which is Advertising's own call.
 * The refund count comes from the same query behind Orders' "Returns & issues"
 * tile. Divergence between screens is a bug, so neither figure is recomputed.
 *
 * One shell, one header, one error
 * --------------------------------
 * This screen shipped with two of each. It was the only Business OS screen
 * registered *without* `headerShown: false` while still drawing its own
 * gradient header, so the stack header and the screen header both rendered:
 * two titles, two back chevrons, one of them stacked on the other. The route is
 * now registered like every other business screen and this header is the only
 * one. It renders `route.params.title` rather than a hard-coded "Payments",
 * because Advertising arrives here saying "Ad wallet" and Orders saying
 * "Payouts" — that context used to be carried by the stack header, and dropping
 * it along with the duplicate would have been a quieter regression than the one
 * being fixed.
 *
 * The failure story was told three times over: an em dash in the hero with a
 * Retry hung off it, a hero sub-line saying the balance could not be read, and
 * a separate error card below with a second Retry. Three statements of one fact
 * read as three faults. The hero now shows the dash alone — a statement about
 * the number — and `PaymentsError` is the single place that explains the
 * failure, offers the one retry, and carries a support reference the seller can
 * quote. The "display problem, not a change to your money" sentence is kept
 * verbatim, because it is the most important sentence on the screen; it is now
 * said once.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Animated,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import { AccessibilityInfo } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";

import type { AdBilling, AdWallet } from "../api/businessOs";
import {
  LedgerEntry,
  LedgerPage,
  SellerMoneyOverview,
  adWalletSpendableCents,
  describeAvailability,
  escrowCardIsLive,
  fetchAdWallet,
  fetchLedgerPage,
  fetchMoneyOverview,
  formatMoney,
  instantPayoutIsLive,
  loadCachedActivity,
  loadCachedOverview,
  payoutInitiationIsLive,
  payoutIsScheduled,
  payoutMethodState,
  statementsAreLive,
  supportReferenceFor,
  taxDocumentsAreLive
} from "../api/paymentsHub";
import {
  ConnectStatus,
  SellerPayout,
  fetchConnectStatus,
  fetchSellerPayouts,
  maskedConnectRef,
  mintPayoutKey,
  payoutErrorKey,
  payoutStatusChip,
  requestSellerPayout,
  type PayoutStatusTone
} from "../api/sellerPayouts";
import { PulseApiError } from "../api/pulseApi";
import { PaymentController } from "../payments/PaymentController";
import {
  BalanceCard,
  BalanceHero,
  DocumentSection,
  LedgerDayGroup,
  PayoutFailedNotice,
  PayoutInFlightNotice,
  PayoutMethodCard,
  PaymentsEmpty,
  PaymentsError,
  PaymentsLoading,
  PaymentsOffline,
  groupLedgerByDay
} from "../components/payments";
import { registerSyncInvalidation } from "../core/eventSync";
import { useTranslation } from "../i18n";
import type { MoneyLayerId } from "../money/moneyLayers";
import { paymentsLight } from "../theme/paymentsLight";
import {
  usePaymentsBalanceCascade,
  usePaymentsEscrowIndicator,
  usePaymentsRowInsert
} from "../theme/paymentsMotion";

const PAGE_SIZE = 25;
const PAYOUTS_PAGE_SIZE = 10;

/** New money-flow copy lives here; see AdsWalletScreen for the pattern. */
const NS = "commerce:payments";

/**
 * Copy for the layers underneath this screen. It lives in its own namespace
 * rather than being folded into `commerce:payments` because the hub and the
 * layers are read in different places: this screen answers "how much", the
 * layers answer "why, and what do I do about it". Keeping the two apart means
 * a change to a layer's explanation can't silently reword a figure's label.
 */
const MONEY_NS = "commerce:money";

/**
 * Colour for a payout status chip's tone. The tone is decided in
 * `payoutStatusChip`; this maps it onto the payments palette — progress takes
 * the processing blue rather than a hue of its own, because "on its way to the
 * bank" is the same claim the Processing card already makes in that colour.
 */
const TONE_COLOR: Record<PayoutStatusTone, string> = {
  progress: paymentsLight.balance.processingAccent,
  success: paymentsLight.status.success,
  error: paymentsLight.status.error,
  neutral: paymentsLight.status.neutral
};

/** "12.50" → 1250; empty → null; junk → NaN so the caller can complain. */
function parseDollars(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const value = Number(trimmed.replace(/,/g, "."));
  if (!Number.isFinite(value) || value < 0) return Number.NaN;
  return Math.round(value * 100);
}

type Navigation = {
  navigate: (...args: any[]) => void;
  goBack?: () => void;
};

/**
 * Callers arrive here with a context word — Advertising sends "Ad wallet",
 * Orders sends "Payouts". It used to land in the stack header; now that this
 * screen owns the only header, it lands here.
 */
type PaymentsRoute = { params?: { title?: string; accountId?: number } };

export function BusinessOsPaymentsScreen({
  navigation,
  route
}: { navigation?: Navigation; route?: PaymentsRoute } = {}) {
  const insets = useSafeAreaInsets();
  const { t } = useTranslation();
  const headerTitle = route?.params?.title || "Payments";

  const [overview, setOverview] = useState<SellerMoneyOverview | null>(null);
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [wallet, setWallet] = useState<AdWallet | null>(null);
  const [, setBilling] = useState<AdBilling | null>(null);

  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [loadingMore, setLoadingMore] = useState(false);
  /** A *fresh* balance read that failed. Drives "—" plus one error card, never a cache. */
  const [balanceError, setBalanceError] = useState(false);
  /**
   * Minted at the moment of failure and held until the next successful read, so
   * the code the seller reads to support is the time it broke rather than the
   * time they got through. Cleared on success — a reference with no live failure
   * behind it would be a code for nothing.
   */
  const [supportReference, setSupportReference] = useState<string | null>(null);
  /** Set only when figures on screen came from cache. Always paired with a time. */
  const [offlineAsOf, setOfflineAsOf] = useState<string | null>(null);
  const [reducedMotion, setReducedMotion] = useState(false);

  /**
   * The Stripe Connect status the withdraw affordance gates on. `null` with
   * `connectFailed` false means "not loaded yet"; with `connectFailed` true it
   * means the read failed — and the section says so rather than guessing a
   * connection state that would either hide a live control or show a dead one.
   */
  const [connect, setConnect] = useState<ConnectStatus | null>(null);
  const [connectFailed, setConnectFailed] = useState(false);

  const [payouts, setPayouts] = useState<SellerPayout[]>([]);
  const [payoutsNext, setPayoutsNext] = useState<number | null>(null);
  const [payoutsHasMore, setPayoutsHasMore] = useState(false);
  const [payoutsFailed, setPayoutsFailed] = useState(false);
  const [payoutsLoadingMore, setPayoutsLoadingMore] = useState(false);

  /** The withdraw sheet. The intent key lives in a ref, cart-checkout style. */
  const [withdrawOpen, setWithdrawOpen] = useState(false);
  const [withdrawStep, setWithdrawStep] = useState<"amount" | "confirm">("amount");
  const [withdrawAmount, setWithdrawAmount] = useState("");
  const [withdrawBusy, setWithdrawBusy] = useState(false);
  const [withdrawNote, setWithdrawNote] = useState("");
  const payoutKey = useRef<string>("");

  useEffect(() => {
    AccessibilityInfo.isReduceMotionEnabled()
      .then(setReducedMotion)
      .catch(() => undefined);
  }, []);

  /**
   * Row-entrance bookkeeping.
   *
   * `seenIds` is every id this session has rendered. An id absent from it is
   * genuinely new — which is how a confirmed payout animates one row in without
   * a reload — and an id present in it never animates again, so pagination and
   * refresh do not re-play entrances on rows the seller has already read.
   */
  const seenIds = useRef<Set<number>>(new Set());
  const newIds = useRef<Set<number>>(new Set());

  const applyPage = useCallback((page: LedgerPage, mode: "replace" | "append") => {
    const arriving = new Set<number>();
    for (const entry of page.entries) {
      if (!seenIds.current.has(entry.id)) {
        arriving.add(entry.id);
        seenIds.current.add(entry.id);
      }
    }
    // On the very first load nothing is "new" — the whole list arriving at once
    // is the list, not an insertion, and animating all of it would be the
    // ambient movement on amounts the brief rules out.
    newIds.current = seenIds.current.size === arriving.size ? new Set() : arriving;

    setEntries((previous) => {
      if (mode === "replace") return page.entries;
      const known = new Set(previous.map((entry) => entry.id));
      return previous.concat(page.entries.filter((entry) => !known.has(entry.id)));
    });
    setCursor(page.next_cursor);
    setHasMore(Boolean(page.has_more));
  }, []);

  const load = useCallback(
    async (mode: "initial" | "refresh") => {
      if (mode === "initial") setLoading(true);
      else setRefreshing(true);

      const [moneyResult, activityResult, walletResult, connectResult, payoutsResult] =
        await Promise.allSettled([
          fetchMoneyOverview(),
          fetchLedgerPage({ limit: PAGE_SIZE }),
          fetchAdWallet(),
          fetchConnectStatus(),
          fetchSellerPayouts({ limit: PAYOUTS_PAGE_SIZE })
        ]);

      let stale: string | null = null;

      if (moneyResult.status === "fulfilled") {
        setOverview(moneyResult.value);
        setBalanceError(false);
        setSupportReference(null);
      } else {
        // A cached balance may be shown only under an "as of" label, so the
        // label is the precondition — not a decoration added afterwards. If the
        // cache carries no usable timestamp there is no honest way to present
        // its figures, and the hero falls back to "—" with a retry. Reading the
        // clock *before* deciding is what makes that rule hold rather than
        // depend on a later branch remembering it.
        const cached = await loadCachedOverview().catch(() => null);
        const clock = cached ? formatClock(cached.cachedAt) : "";
        if (cached && clock) {
          setOverview(cached.overview);
          setBalanceError(false);
          setSupportReference(null);
          stale = clock;
        } else {
          setOverview(null);
          setBalanceError(true);
          setSupportReference(supportReferenceFor());
        }
      }

      if (activityResult.status === "fulfilled") {
        applyPage(activityResult.value, "replace");
      } else {
        // The ledger is held to the same rule, but it is a weaker claim than a
        // balance, so an unusable timestamp costs the seller the offline banner
        // rather than the rows: cached activity without a clock still renders,
        // and `stale` simply stays whatever the balance branch decided.
        const cached = await loadCachedActivity().catch(() => null);
        if (cached) {
          applyPage(cached.page, "replace");
          stale = stale || formatClock(cached.cachedAt);
        }
      }

      if (walletResult.status === "fulfilled" && walletResult.value) {
        setWallet(walletResult.value.wallet);
        setBilling(walletResult.value.billing);
      } else {
        // Null, not zero. A zero ad balance would look like a measurement.
        setWallet(null);
        setBilling(null);
      }

      if (connectResult.status === "fulfilled") {
        setConnect(connectResult.value);
        setConnectFailed(false);
      } else {
        // A failed status read is not "not connected" — the section states the
        // read failed rather than telling a connected seller to onboard again.
        setConnect(null);
        setConnectFailed(true);
      }

      if (payoutsResult.status === "fulfilled") {
        setPayouts(payoutsResult.value.payouts);
        setPayoutsNext(payoutsResult.value.next_before_id);
        setPayoutsHasMore(payoutsResult.value.has_more);
        setPayoutsFailed(false);
      } else {
        setPayouts([]);
        setPayoutsNext(null);
        setPayoutsHasMore(false);
        setPayoutsFailed(true);
      }

      setOfflineAsOf(stale);
      setLoading(false);
      setRefreshing(false);
    },
    [applyPage]
  );

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [load]);

  useEffect(() => {
    const refresh = () => {
      load("refresh").catch(() => undefined);
    };
    const unregisterOrders = registerSyncInvalidation("orders", refresh);
    const unregisterMarketplace = registerSyncInvalidation("marketplace", refresh);
    return () => {
      unregisterOrders();
      unregisterMarketplace();
    };
  }, [load]);

  const loadMore = useCallback(async () => {
    if (!cursor || loadingMore || !hasMore) return;
    setLoadingMore(true);
    try {
      const page = await fetchLedgerPage({ cursor, limit: PAGE_SIZE });
      applyPage(page, "append");
    } catch {
      // A failed page is not a shorter ledger. Leave the list and the cursor
      // exactly as they were so "Load more" can be tapped again.
    } finally {
      setLoadingMore(false);
    }
  }, [applyPage, cursor, hasMore, loadingMore]);

  const loadMorePayouts = useCallback(async () => {
    if (!payoutsNext || !payoutsHasMore || payoutsLoadingMore) return;
    setPayoutsLoadingMore(true);
    try {
      const page = await fetchSellerPayouts({ limit: PAYOUTS_PAGE_SIZE, beforeId: payoutsNext });
      setPayouts((previous) => {
        const known = new Set(previous.map((payout) => payout.id));
        return previous.concat(page.payouts.filter((payout) => !known.has(payout.id)));
      });
      setPayoutsNext(page.next_before_id);
      setPayoutsHasMore(page.has_more);
    } catch {
      // A failed page is not a shorter list — leave everything tappable again.
    } finally {
      setPayoutsLoadingMore(false);
    }
  }, [payoutsHasMore, payoutsLoadingMore, payoutsNext]);

  /** Opening the sheet mints the intent key; closing it discards it. */
  const openWithdraw = useCallback((availableCents: number, activeCurrency: string) => {
    payoutKey.current = mintPayoutKey();
    setWithdrawAmount(availableCents > 0 ? (availableCents / 100).toFixed(2) : "");
    setWithdrawStep("amount");
    setWithdrawNote("");
    setWithdrawOpen(true);
    void activeCurrency;
  }, []);

  const closeWithdraw = useCallback(() => {
    payoutKey.current = "";
    setWithdrawOpen(false);
    setWithdrawStep("amount");
    setWithdrawNote("");
  }, []);

  const continueWithdraw = useCallback(
    (availableCents: number) => {
      const cents = parseDollars(withdrawAmount);
      if (cents === null || Number.isNaN(cents) || cents <= 0) {
        setWithdrawNote(t(`${NS}.invalidAmount`));
        return;
      }
      if (cents > availableCents) {
        setWithdrawNote(t(`${NS}.amountTooHigh`));
        return;
      }
      setWithdrawNote("");
      setWithdrawStep("confirm");
    },
    [t, withdrawAmount]
  );

  const submitWithdraw = useCallback(async () => {
    const cents = parseDollars(withdrawAmount);
    if (cents === null || Number.isNaN(cents) || cents <= 0 || !payoutKey.current) return;
    setWithdrawBusy(true);
    setWithdrawNote("");
    try {
      const policy = await PaymentController.instruction("seller_payout");
      if (!policy.ok || policy.provider !== "stripe_connect" || policy.flow !== "connect_payout") {
        throw new Error("PulseSoc could not authorize the payout provider.");
      }
      const result = await requestSellerPayout(cents, payoutKey.current);
      closeWithdraw();
      setWithdrawNote(t(`${NS}.${result.duplicate ? "requestedDuplicate" : "requested"}`));
      await load("refresh").catch(() => undefined);
    } catch (error) {
      // The endpoint's declared codes get their own sentences; anything else
      // shows the server's message verbatim when there is one. The intent key
      // survives, so retrying replays the same payout rather than minting two.
      const key = payoutErrorKey(error);
      if (key === "errGeneric" && error instanceof PulseApiError && error.message) {
        setWithdrawNote(error.message);
      } else {
        setWithdrawNote(t(`${NS}.${key}`));
      }
    } finally {
      setWithdrawBusy(false);
    }
  }, [closeWithdraw, load, t, withdrawAmount]);

  const currency = overview?.currency || "USD";

  /**
   * Every figure on this screen now opens the layer that explains it.
   *
   * The hub used to carry five things a seller could look at, want an
   * explanation for, and tap — the hero, Processing, a payout row, a ledger
   * row, the section headings — and every one of those taps did nothing. A dead
   * tap on a money figure is worse than a plainly inert one: the seller
   * concludes the screen is broken and stops trusting the number above it.
   *
   * These are the only new destinations on this screen. Nothing that already
   * went somewhere was re-pointed — the ad wallet card still opens Advertising,
   * the payout card still opens Verification Center, Rewards still opens
   * Rewards.
   */
  const openMoneyLayer = useCallback(
    (layer: MoneyLayerId) => {
      navigation?.navigate("MoneyLayer", { layer, currency });
    },
    [currency, navigation]
  );
  const openPayoutDetail = useCallback(
    (payout: SellerPayout) => {
      navigation?.navigate("MoneyDetail", { subject: "payout", payout });
    },
    [navigation]
  );
  const openEntryDetail = useCallback(
    (entry: LedgerEntry) => {
      navigation?.navigate("MoneyDetail", { subject: "entry", entry });
    },
    [navigation]
  );

  const availableCents = overview?.available_cents ?? 0;
  /** Masked Stripe reference for the confirm step — the overview's own word
   *  when it carries one, else the connect status account id's last four. */
  const withdrawDestination =
    overview?.payout_method?.destination_masked || maskedConnectRef(connect);
  const scheduled = payoutIsScheduled(overview);
  const methodState = payoutMethodState(overview);
  const adSpendable = adWalletSpendableCents(wallet);

  const cards = useMemo(() => {
    const list: Array<"processing" | "escrow" | "adwallet"> = ["processing"];
    if (escrowCardIsLive() && overview?.escrow.supported) list.push("escrow");
    if (wallet) list.push("adwallet");
    return list;
  }, [overview?.escrow.supported, wallet]);

  const cascade = usePaymentsBalanceCascade(cards.length, reducedMotion, !loading);
  const escrowIndicator = usePaymentsEscrowIndicator(
    reducedMotion,
    Boolean(overview && overview.processing_cents > 0)
  );

  const days = useMemo(() => groupLedgerByDay(entries), [entries]);

  // Drivers exist only for the ids that just arrived. A row that is not new
  // gets null and simply renders in place.
  const rowDrivers = useRowDrivers(entries, newIds.current, reducedMotion);
  const entranceFor = useCallback(
    (entry: LedgerEntry) => rowDrivers.get(entry.id) || null,
    [rowDrivers]
  );

  const heroAmount = balanceError ? "—" : formatMoney(overview?.available_cents ?? null, currency);
  /**
   * Empty on failure, deliberately. The hero's job during an outage is to stop
   * asserting a number; explaining the outage is `PaymentsError`'s job, and it
   * used to be done in both places at once. The hero still announces
   * "Unavailable" to assistive technology, so nothing is lost for a seller who
   * cannot see the dash.
   */
  const heroSubline = overview ? describeAvailability(overview) : "";

  return (
    <View style={styles.screen}>
      <LinearGradient
        colors={[paymentsLight.bg.headerFrom, paymentsLight.bg.headerTo]}
        start={{ x: 0, y: 0 }}
        end={{ x: 1, y: 1 }}
        style={[styles.header, { paddingTop: insets.top + 10 }]}
      >
        <View style={styles.headerRow}>
          <Pressable
            onPress={() => navigation?.goBack?.()}
            style={styles.back}
            accessibilityRole="button"
            accessibilityLabel="Go back"
            hitSlop={10}
          >
            <Ionicons name="chevron-back" size={22} color={paymentsLight.text.onDark} />
          </Pressable>
          {/* The only title on this screen. Two lines rather than one, and a
              ceiling on growth, because "Ad wallet" at a large text size on a
              narrow device is the same clipping failure Tier 0.1 closed on the
              quick-link tiles — a header is not exempt from it. */}
          <Text
            style={styles.headerTitle}
            allowFontScaling
            numberOfLines={2}
            ellipsizeMode="tail"
            maxFontSizeMultiplier={1.5}
            accessibilityRole="header"
          >
            {headerTitle}
          </Text>
          <View style={styles.back} />
        </View>

        {/* No `onRetry`. The single retry lives on the error card below, where
            the sentence explaining what to retry is. Two retries for one action
            invited the seller to wonder whether they did different things. */}
        <BalanceHero
          availableCents={balanceError ? null : overview?.available_cents ?? null}
          formattedAmount={heroAmount}
          subline={heroSubline}
          payoutScheduled={scheduled}
          asOfLabel={offlineAsOf}
          reducedMotion={reducedMotion}
          ready={!loading}
          // The hero is the figure most likely to be questioned, so it is the
          // one that had to stop being inert first. It opens the layer that
          // says where the money is and whether it can leave.
          onPress={() => openMoneyLayer("payout_overview")}
        />
      </LinearGradient>

      <ScrollView
        style={styles.scroll}
        contentContainerStyle={[styles.scrollBody, { paddingBottom: insets.bottom + 32 }]}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            onRefresh={() => load("refresh").catch(() => undefined)}
            tintColor={paymentsLight.text.muted}
          />
        }
      >
        {loading ? <PaymentsLoading /> : null}

        {!loading && offlineAsOf ? <PaymentsOffline asOf={offlineAsOf} /> : null}

        {!loading && balanceError ? (
          <PaymentsError
            onRetry={() => load("refresh").catch(() => undefined)}
            supportReference={supportReference}
          />
        ) : null}

        {!loading && !balanceError ? (
          <View style={styles.cardRow}>
            {cards.map((card, index) => {
              const entrance = cascade.progressFor(index);
              if (card === "processing") {
                return (
                  <BalanceCard
                    key="processing"
                    label="Processing"
                    formattedAmount={formatMoney(overview?.processing_cents ?? null, currency)}
                    caption="Not yet released for payout"
                    accent={paymentsLight.balance.processingAccent}
                    entrance={entrance}
                    accessibilityLabel={`Processing, ${formatMoney(
                      overview?.processing_cents ?? null,
                      currency
                    )}, still yours, not yet released for payout`}
                    // "Not yet released" is the caption a seller most wants a
                    // reason for. The Processing layer gives the server's own
                    // reason, derived from `release_path`.
                    onPress={() => openMoneyLayer("processing")}
                    hint={t(`${MONEY_NS}.a11y.openLayer`, { layer: t(`${MONEY_NS}.layer.processing`) })}
                  />
                );
              }
              if (card === "escrow") {
                // Deliberately `escrowCentsOf`, not `processing_cents`. Those
                // are different quantities and reusing one for the other is
                // exactly the fabrication this screen exists to avoid — it
                // would render a plausible figure under the wrong name.
                const held = escrowCentsOf(overview);
                return (
                  <BalanceCard
                    key="escrow"
                    label="Held in escrow"
                    formattedAmount={formatMoney(held, currency)}
                    unavailable={held === null}
                    caption="Still yours — released when the order completes"
                    accent={paymentsLight.balance.escrowAccent}
                    tinted
                    indicator={escrowIndicator}
                    entrance={entrance}
                    accessibilityLabel={`Held in escrow, ${formatMoney(
                      held,
                      currency
                    )}, still yours, released when the order completes`}
                  />
                );
              }
              return (
                <BalanceCard
                  key="adwallet"
                  label="Ad wallet"
                  formattedAmount={formatMoney(adSpendable, wallet?.currency || currency)}
                  unavailable={adSpendable === null}
                  caption="Funds your campaigns"
                  accent={paymentsLight.balance.adWalletAccent}
                  entrance={entrance}
                  onPress={() => navigation?.navigate("BusinessOsAdvertising")}
                  hint="Opens Advertising"
                />
              );
            })}
          </View>
        ) : null}

        {overview?.last_failed_payout ? (
          <PayoutFailedNotice reason={String(overview.last_failed_payout.failure_reason || "")} />
        ) : null}

        {overview?.payout_in_flight ? (
          <PayoutInFlightNotice
            detail={`A payout of ${formatMoney(
              overview.payout_in_flight.amount_cents,
              overview.payout_in_flight.currency
            )} is on its way to your ${overview.payout_in_flight.provider} account.`}
          />
        ) : null}

        {!loading ? (
          <View style={styles.methodWrap}>
            <PayoutMethodCard
              state={methodState}
              method={overview?.payout_method || null}
              onManage={() => navigation?.navigate("VerificationCenter")}
            />
          </View>
        ) : null}

        {/* Withdraw is live; instant payout stays behind its off flag and
            contributes nothing here while it is off. See the module docstring. */}
        {!loading && (payoutInitiationIsLive() || instantPayoutIsLive()) ? (
          <View style={styles.payoutActions}>
            <Text style={styles.sectionHeading} accessibilityRole="header" allowFontScaling>
              {t(`${NS}.withdrawTitle`)}
            </Text>
            {!payoutInitiationIsLive() ? null : connectFailed ? (
              // The status read failed. Saying "not connected" here would tell
              // a connected seller to onboard again; saying nothing would hide
              // a live control. So the section states exactly what it knows.
              <Text style={styles.withdrawBody} allowFontScaling>
                {t(`${NS}.withdrawStatusUnknown`)}
              </Text>
            ) : !connect ? null : !connect.connected || !connect.payouts_enabled ? (
              <>
                <Text style={styles.withdrawBody} allowFontScaling>
                  {t(`${NS}.${connect.connected ? "withdrawDisabledBody" : "withdrawNotConnectedBody"}`)}
                </Text>
                <Pressable
                  onPress={() => navigation?.navigate("VerificationCenter")}
                  style={styles.withdrawSecondary}
                  accessibilityRole="button"
                  accessibilityLabel={t(`${NS}.withdrawSetupCta`)}
                >
                  <Text style={styles.withdrawSecondaryText} allowFontScaling>
                    {t(`${NS}.withdrawSetupCta`)}
                  </Text>
                </Pressable>
              </>
            ) : availableCents <= 0 ? (
              <Text style={styles.withdrawBody} allowFontScaling>
                {t(`${NS}.withdrawNothing`)}
              </Text>
            ) : withdrawOpen ? (
              <View style={styles.withdrawSheet}>
                {withdrawStep === "amount" ? (
                  <>
                    <Text style={styles.withdrawLabel} allowFontScaling>
                      {t(`${NS}.withdrawAmountLabel`)}
                    </Text>
                    <TextInput
                      value={withdrawAmount}
                      onChangeText={setWithdrawAmount}
                      keyboardType="decimal-pad"
                      style={styles.withdrawInput}
                      accessibilityLabel={t(`${NS}.withdrawAmountLabel`)}
                      testID="withdraw-amount-input"
                    />
                    <Text style={styles.withdrawHint} allowFontScaling>
                      {t(`${NS}.withdrawAvailable`, {
                        amount: formatMoney(availableCents, currency)
                      })}
                    </Text>
                    {withdrawNote ? (
                      <Text style={styles.withdrawError} accessibilityLiveRegion="polite">
                        {withdrawNote}
                      </Text>
                    ) : null}
                    <View style={styles.withdrawButtonRow}>
                      <Pressable
                        onPress={closeWithdraw}
                        style={styles.withdrawSecondary}
                        accessibilityRole="button"
                        accessibilityLabel={t(`${NS}.cancel`)}
                      >
                        <Text style={styles.withdrawSecondaryText} allowFontScaling>
                          {t(`${NS}.cancel`)}
                        </Text>
                      </Pressable>
                      <Pressable
                        onPress={() => continueWithdraw(availableCents)}
                        style={styles.withdrawPrimary}
                        accessibilityRole="button"
                        accessibilityLabel={t(`${NS}.withdrawContinue`)}
                      >
                        <Text style={styles.withdrawPrimaryText} allowFontScaling>
                          {t(`${NS}.withdrawContinue`)}
                        </Text>
                      </Pressable>
                    </View>
                  </>
                ) : (
                  <>
                    <Text style={styles.withdrawLabel} allowFontScaling>
                      {t(`${NS}.confirmTitle`)}
                    </Text>
                    <Text style={styles.withdrawBody} allowFontScaling>
                      {t(`${NS}.confirmAmount`, {
                        amount: formatMoney(parseDollars(withdrawAmount) || 0, currency)
                      })}
                    </Text>
                    <Text style={styles.withdrawBody} allowFontScaling>
                      {withdrawDestination
                        ? t(`${NS}.destinationStripe`, { ref: withdrawDestination })
                        : t(`${NS}.destinationStripeNoRef`)}
                    </Text>
                    {withdrawNote ? (
                      <Text style={styles.withdrawError} accessibilityLiveRegion="polite">
                        {withdrawNote}
                      </Text>
                    ) : null}
                    <View style={styles.withdrawButtonRow}>
                      <Pressable
                        onPress={closeWithdraw}
                        disabled={withdrawBusy}
                        style={[styles.withdrawSecondary, withdrawBusy && styles.withdrawBusy]}
                        accessibilityRole="button"
                        accessibilityLabel={t(`${NS}.cancel`)}
                        accessibilityState={{ disabled: withdrawBusy }}
                      >
                        <Text style={styles.withdrawSecondaryText} allowFontScaling>
                          {t(`${NS}.cancel`)}
                        </Text>
                      </Pressable>
                      <Pressable
                        onPress={() => submitWithdraw().catch(() => undefined)}
                        disabled={withdrawBusy}
                        style={[styles.withdrawPrimary, withdrawBusy && styles.withdrawBusy]}
                        accessibilityRole="button"
                        accessibilityLabel={t(`${NS}.confirmCta`)}
                        accessibilityState={{ disabled: withdrawBusy, busy: withdrawBusy }}
                      >
                        <Text style={styles.withdrawPrimaryText} allowFontScaling>
                          {withdrawBusy ? t(`${NS}.working`) : t(`${NS}.confirmCta`)}
                        </Text>
                      </Pressable>
                    </View>
                  </>
                )}
              </View>
            ) : (
              <>
                <Pressable
                  onPress={() => openWithdraw(availableCents, currency)}
                  style={styles.withdrawPrimary}
                  accessibilityRole="button"
                  accessibilityLabel={t(`${NS}.withdrawCta`)}
                >
                  <Text style={styles.withdrawPrimaryText} allowFontScaling>
                    {t(`${NS}.withdrawCta`)}
                  </Text>
                </Pressable>
                {withdrawNote ? (
                  <Text style={styles.withdrawSuccess} accessibilityLiveRegion="polite">
                    {withdrawNote}
                  </Text>
                ) : null}
              </>
            )}
          </View>
        ) : null}

        {/* Payout history — every row is a server payout record, chips decided
            by `payoutStatusChip`. An unknown status renders its raw word. */}
        {!loading && payoutInitiationIsLive() ? (
          <View style={styles.payoutsSection}>
            <View style={styles.sectionHeadingRow}>
              <Text style={styles.sectionHeading} accessibilityRole="header" allowFontScaling>
                {t(`${NS}.payoutsTitle`)}
              </Text>
              <Pressable
                onPress={() => openMoneyLayer("payout_history")}
                accessibilityRole="button"
                accessibilityLabel={t("common:actions.viewAll")}
                accessibilityHint={t(`${MONEY_NS}.a11y.openLayer`, {
                  layer: t(`${MONEY_NS}.layer.payoutHistory`)
                })}
                hitSlop={10}
              >
                <Text style={styles.sectionLink} allowFontScaling>
                  {t("common:actions.viewAll")}
                </Text>
              </Pressable>
            </View>
            {payoutsFailed ? (
              <Text style={styles.withdrawBody} allowFontScaling>
                {t(`${NS}.payoutsUnavailable`)}
              </Text>
            ) : payouts.length ? (
              <>
                {payouts.map((payout) => {
                  const chip = payoutStatusChip(payout.status);
                  const chipColor = TONE_COLOR[chip.tone];
                  const showFailure =
                    (payout.status === "failed" || payout.status === "returned") &&
                    Boolean(payout.failure_message);
                  return (
                    // Was an inert `View`. A payout row carries a failure
                    // message it can only truncate and a provider reference it
                    // cannot show at all, so it was the row most in need of
                    // somewhere to go.
                    <Pressable
                      key={payout.id}
                      style={styles.payoutRow}
                      accessible
                      accessibilityRole="button"
                      accessibilityLabel={`${formatMoney(payout.amount_cents, payout.currency)}, ${
                        chip.key ? t(`${NS}.${chip.key}`) : payout.status
                      }`}
                      accessibilityHint={t(`${MONEY_NS}.a11y.openPayout`)}
                      onPress={() => openPayoutDetail(payout)}
                    >
                      <View style={styles.payoutRowTop}>
                        <Text style={styles.payoutAmount} allowFontScaling>
                          {formatMoney(payout.amount_cents, payout.currency)}
                        </Text>
                        <View style={[styles.payoutChip, { borderColor: chipColor }]}>
                          <Text
                            style={[styles.payoutChipText, { color: chipColor }]}
                            allowFontScaling
                          >
                            {chip.key ? t(`${NS}.${chip.key}`) : payout.status}
                          </Text>
                        </View>
                      </View>
                      <Text style={styles.payoutMeta} allowFontScaling>
                        {formatDay(payout.created_at)}
                      </Text>
                      {showFailure ? (
                        <Text style={styles.payoutFailure} allowFontScaling>
                          {payout.failure_message}
                        </Text>
                      ) : null}
                    </Pressable>
                  );
                })}
                {payoutsHasMore ? (
                  <Pressable
                    onPress={() => loadMorePayouts().catch(() => undefined)}
                    disabled={payoutsLoadingMore}
                    style={[styles.loadMore, payoutsLoadingMore && styles.loadMoreBusy]}
                    accessibilityRole="button"
                    accessibilityLabel={t(`${NS}.loadMore`)}
                    accessibilityState={{ disabled: payoutsLoadingMore, busy: payoutsLoadingMore }}
                  >
                    <Text style={styles.loadMoreText}>{t(`${NS}.loadMore`)}</Text>
                  </Pressable>
                ) : null}
              </>
            ) : (
              <Text style={styles.withdrawBody} allowFontScaling>
                {t(`${NS}.payoutsEmpty`)}
              </Text>
            )}
          </View>
        ) : null}

        {/* Rewards entry — a link, not a figure; the balance lives on the
            Rewards screen where the server's total renders. */}
        {!loading ? (
          <View style={styles.rewardsWrap}>
            <Pressable
              onPress={() => navigation?.navigate("Rewards")}
              style={styles.rewardsCard}
              accessibilityRole="button"
              accessibilityLabel={t(`${NS}.rewardsEntryTitle`)}
              accessibilityHint={t(`${NS}.rewardsEntryBody`)}
            >
              <View style={styles.rewardsText}>
                <Text style={styles.rewardsTitle} allowFontScaling>
                  {t(`${NS}.rewardsEntryTitle`)}
                </Text>
                <Text style={styles.rewardsBody} allowFontScaling>
                  {t(`${NS}.rewardsEntryBody`)}
                </Text>
              </View>
              <Ionicons name="chevron-forward" size={18} color={paymentsLight.text.muted} />
            </Pressable>
          </View>
        ) : null}

        {!loading && !balanceError ? (
          <View style={styles.ledger}>
            <View style={styles.sectionHeadingRow}>
              <Text style={styles.sectionHeading} accessibilityRole="header" allowFontScaling>
                {t(`${MONEY_NS}.layer.activity`)}
              </Text>
              <Pressable
                onPress={() => openMoneyLayer("activity")}
                accessibilityRole="button"
                accessibilityLabel={t("common:actions.viewAll")}
                accessibilityHint={t(`${MONEY_NS}.a11y.openLayer`, {
                  layer: t(`${MONEY_NS}.layer.activity`)
                })}
                hitSlop={10}
              >
                <Text style={styles.sectionLink} allowFontScaling>
                  {t("common:actions.viewAll")}
                </Text>
              </Pressable>
            </View>
            {entries.length ? (
              <>
                {days.map((day) => (
                  // `onPressEntry` was already a supported prop and was never
                  // passed, so every ledger row on this screen was inert. It
                  // now opens the row's own detail.
                  <LedgerDayGroup
                    key={day.key}
                    day={day}
                    entranceFor={entranceFor}
                    onPressEntry={openEntryDetail}
                  />
                ))}
                {hasMore ? (
                  <Pressable
                    onPress={() => loadMore().catch(() => undefined)}
                    disabled={loadingMore}
                    style={[styles.loadMore, loadingMore && styles.loadMoreBusy]}
                    accessibilityRole="button"
                    accessibilityLabel="Load older activity"
                    accessibilityState={{ disabled: loadingMore, busy: loadingMore }}
                  >
                    <Text style={styles.loadMoreText}>{loadingMore ? "Loading…" : "Load more"}</Text>
                  </Pressable>
                ) : null}
              </>
            ) : (
              <PaymentsEmpty hasPayoutMethod={methodState === "ready"} />
            )}
          </View>
        ) : null}

        {/* Both sections return null on an empty list, so an off flag renders
            them absent rather than as a heading over nothing. */}
        {statementsAreLive() ? (
          <DocumentSection heading="Statements" documents={[]} onOpen={() => undefined} />
        ) : null}
        {taxDocumentsAreLive() ? (
          <DocumentSection heading="Tax documents" documents={[]} onOpen={() => undefined} />
        ) : null}
      </ScrollView>
    </View>
  );
}

/**
 * One entrance driver per genuinely-new row.
 *
 * Kept in a hook so the `usePaymentsRowInsert` calls are stable in count: the
 * map is rebuilt from the current entry list each render and holds a driver
 * only for ids in `arriving`. Rows already on screen get nothing, which is what
 * keeps a refresh from re-animating a ledger the seller is mid-read of.
 */
function useRowDrivers(
  entries: readonly LedgerEntry[],
  arriving: Set<number>,
  reducedMotion: boolean
): Map<number, Animated.Value> {
  const drivers = useRef<Map<number, Animated.Value>>(new Map()).current;
  const settled = useRef<Set<number>>(new Set()).current;

  for (const entry of entries) {
    if (settled.has(entry.id)) continue;
    if (!arriving.has(entry.id)) {
      settled.add(entry.id);
      continue;
    }
    if (!drivers.has(entry.id)) {
      drivers.set(entry.id, new Animated.Value(reducedMotion ? 1 : 0));
    }
  }

  useEffect(() => {
    if (reducedMotion) {
      for (const value of drivers.values()) value.setValue(1);
      return;
    }
    for (const id of arriving) {
      const value = drivers.get(id);
      if (!value) continue;
      Animated.timing(value, { toValue: 1, duration: 380, useNativeDriver: true }).start();
    }
    // `arriving` is a ref-held set replaced wholesale on each page apply, so it
    // is a sound dependency: a new set means new rows.
  }, [arriving, drivers, reducedMotion]);

  return drivers;
}

/**
 * The escrow figure, which the server does not publish.
 *
 * `SellerMoneyOverview` has no held-in-escrow total, because live per-order
 * escrow does not exist — holds live only in the Business OS ledger, whose
 * routes are inert in production. So this returns null, `formatMoney` renders
 * "—", and the card is in any case gated absent by `escrowCardIsLive()`.
 *
 * It is a function rather than an inline `null` so that the day the server adds
 * the field, there is exactly one place to change, and so that nobody reaches
 * for `processing_cents` because it happens to be nearby and non-null.
 */
function escrowCentsOf(_overview: SellerMoneyOverview | null): number | null {
  return null;
}

/** A calendar-day label for a payout row. Local, like every date on screen. */
function formatDay(iso: string): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso.slice(0, 10);
  try {
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric"
    }).format(date);
  } catch {
    return iso.slice(0, 10);
  }
}

/** A wall-clock label for a cached figure. Local time, because the seller is. */
function formatClock(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  try {
    return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(date);
  } catch {
    return date.toTimeString().slice(0, 5);
  }
}

const styles = StyleSheet.create({
  back: {
    alignItems: "center",
    height: 34,
    justifyContent: "center",
    width: 34
  },
  cardRow: {
    flexDirection: "row",
    gap: 10,
    marginTop: 16,
    paddingHorizontal: paymentsLight.space.gutter
  },
  header: {
    paddingBottom: 22
  },
  headerRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    paddingHorizontal: paymentsLight.space.gutter
  },
  headerTitle: {
    color: paymentsLight.text.onDark,
    flex: 1,
    fontSize: 16,
    fontWeight: "700",
    textAlign: "center"
  },
  ledger: {
    marginTop: paymentsLight.space.section
  },
  loadMore: {
    alignSelf: "center",
    backgroundColor: paymentsLight.bg.card,
    borderColor: paymentsLight.border.secondaryButton,
    borderRadius: paymentsLight.radius.control,
    borderWidth: 1,
    justifyContent: "center",
    marginTop: 16,
    minHeight: paymentsLight.size.tapTarget,
    paddingHorizontal: 22
  },
  loadMoreBusy: {
    opacity: 0.6
  },
  loadMoreText: {
    color: paymentsLight.text.primary,
    fontSize: 14,
    fontWeight: "700"
  },
  methodWrap: {
    marginTop: paymentsLight.space.section
  },
  payoutActions: {
    marginTop: paymentsLight.space.section,
    paddingHorizontal: paymentsLight.space.gutter
  },
  payoutAmount: {
    color: paymentsLight.text.primary,
    fontSize: paymentsLight.money.row.fontSize,
    fontWeight: paymentsLight.money.row.fontWeight,
    fontVariant: ["tabular-nums"]
  },
  payoutChip: {
    borderRadius: paymentsLight.radius.pill,
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 2
  },
  payoutChipText: {
    fontSize: 12,
    fontWeight: "700"
  },
  payoutFailure: {
    color: paymentsLight.status.error,
    fontSize: 13,
    marginTop: 4
  },
  payoutMeta: {
    color: paymentsLight.text.muted,
    fontSize: 12,
    marginTop: 2
  },
  payoutRow: {
    backgroundColor: paymentsLight.bg.card,
    borderColor: paymentsLight.border.hairline,
    borderRadius: paymentsLight.radius.card,
    borderWidth: 1,
    marginBottom: 8,
    marginHorizontal: paymentsLight.space.gutter,
    padding: paymentsLight.space.card
  },
  payoutRowTop: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between"
  },
  payoutsSection: {
    marginTop: paymentsLight.space.section
  },
  rewardsBody: {
    color: paymentsLight.text.muted,
    fontSize: 13,
    marginTop: 2
  },
  rewardsCard: {
    alignItems: "center",
    backgroundColor: paymentsLight.bg.card,
    borderColor: paymentsLight.border.hairline,
    borderRadius: paymentsLight.radius.card,
    borderWidth: 1,
    flexDirection: "row",
    minHeight: paymentsLight.size.tapTarget,
    padding: paymentsLight.space.card
  },
  rewardsText: {
    flex: 1,
    paddingRight: 8
  },
  rewardsTitle: {
    color: paymentsLight.text.primary,
    fontSize: 15,
    fontWeight: "700"
  },
  rewardsWrap: {
    marginTop: paymentsLight.space.section,
    paddingHorizontal: paymentsLight.space.gutter
  },
  screen: {
    backgroundColor: paymentsLight.bg.page,
    flex: 1
  },
  scroll: {
    flex: 1
  },
  scrollBody: {
    paddingTop: 4
  },
  sectionHeading: {
    color: paymentsLight.text.primary,
    fontSize: 16,
    fontWeight: "700",
    marginBottom: 6,
    paddingHorizontal: paymentsLight.space.gutter
  },
  /* The heading keeps its own gutter padding, so the row adds none of its own
     and the link supplies the matching padding on the right. `flexShrink` is on
     the heading and not the link: at large text sizes the title is the part
     that can wrap, and "View all" losing a letter would make it unreadable. */
  sectionHeadingRow: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between"
  },
  sectionLink: {
    color: paymentsLight.text.link,
    fontSize: 14,
    fontWeight: "600",
    marginBottom: 6,
    paddingHorizontal: paymentsLight.space.gutter
  },
  withdrawBody: {
    color: paymentsLight.text.muted,
    fontSize: 14,
    lineHeight: 20,
    marginTop: 2
  },
  withdrawBusy: {
    opacity: 0.6
  },
  withdrawButtonRow: {
    flexDirection: "row",
    gap: 10,
    marginTop: 12
  },
  withdrawError: {
    color: paymentsLight.status.error,
    fontSize: 13,
    marginTop: 8
  },
  withdrawHint: {
    color: paymentsLight.text.muted,
    fontSize: 12,
    marginTop: 6
  },
  withdrawInput: {
    borderColor: paymentsLight.border.secondaryButton,
    borderRadius: paymentsLight.radius.control,
    borderWidth: 1,
    color: paymentsLight.text.primary,
    fontSize: 16,
    marginTop: 6,
    minHeight: paymentsLight.size.tapTarget,
    paddingHorizontal: 12
  },
  withdrawLabel: {
    color: paymentsLight.text.primary,
    fontSize: 14,
    fontWeight: "700"
  },
  withdrawPrimary: {
    alignItems: "center",
    backgroundColor: paymentsLight.cta.from,
    borderRadius: paymentsLight.radius.control,
    flexGrow: 1,
    justifyContent: "center",
    marginTop: 8,
    minHeight: paymentsLight.size.tapTarget,
    paddingHorizontal: 18
  },
  withdrawPrimaryText: {
    color: paymentsLight.cta.text,
    fontSize: 14,
    fontWeight: "700"
  },
  withdrawSecondary: {
    alignItems: "center",
    backgroundColor: paymentsLight.bg.card,
    borderColor: paymentsLight.border.secondaryButton,
    borderRadius: paymentsLight.radius.control,
    borderWidth: 1,
    flexGrow: 1,
    justifyContent: "center",
    marginTop: 8,
    minHeight: paymentsLight.size.tapTarget,
    paddingHorizontal: 18
  },
  withdrawSecondaryText: {
    color: paymentsLight.text.primary,
    fontSize: 14,
    fontWeight: "700"
  },
  withdrawSheet: {
    backgroundColor: paymentsLight.bg.card,
    borderColor: paymentsLight.border.hairline,
    borderRadius: paymentsLight.radius.card,
    borderWidth: 1,
    marginTop: 8,
    padding: paymentsLight.space.card
  },
  withdrawSuccess: {
    color: paymentsLight.status.success,
    fontSize: 13,
    fontWeight: "600",
    marginTop: 8
  }
});
