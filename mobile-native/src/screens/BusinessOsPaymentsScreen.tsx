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
 * 1. **"Pay out now"** (`payoutInitiationIsLive`). No endpoint in this codebase
 *    initiates a payout. `seller_payouts` rows are inserted only by the Stripe
 *    Connect webhook. A disabled button would still tell the seller a payout is
 *    something they can nearly do.
 * 2. **Instant payout** (`instantPayoutIsLive`). Same absence, plus there is no
 *    fee schedule to quote, and this screen computes no fees.
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
import { Animated, Pressable, RefreshControl, ScrollView, StyleSheet, Text, View } from "react-native";
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
import { paymentsLight } from "../theme/paymentsLight";
import {
  usePaymentsBalanceCascade,
  usePaymentsEscrowIndicator,
  usePaymentsRowInsert
} from "../theme/paymentsMotion";

const PAGE_SIZE = 25;

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

      const [moneyResult, activityResult, walletResult] = await Promise.allSettled([
        fetchMoneyOverview(),
        fetchLedgerPage({ limit: PAGE_SIZE }),
        fetchAdWallet()
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

  const currency = overview?.currency || "USD";
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

        {/* "Pay out now" and instant payout live behind these flags and render
            nothing while they are off. See the module docstring. */}
        {payoutInitiationIsLive() || instantPayoutIsLive() ? (
          <View style={styles.payoutActions} accessible>
            <Text style={styles.sectionHeading} accessibilityRole="header" allowFontScaling>
              Move your money
            </Text>
          </View>
        ) : null}

        {!loading && !balanceError ? (
          <View style={styles.ledger}>
            <Text style={styles.sectionHeading} accessibilityRole="header" allowFontScaling>
              Activity
            </Text>
            {entries.length ? (
              <>
                {days.map((day) => (
                  <LedgerDayGroup key={day.key} day={day} entranceFor={entranceFor} />
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
  }
});
