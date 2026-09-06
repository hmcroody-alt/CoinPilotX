/**
 * Capital Graph — the member's holdings, coverage, structure and documents,
 * as the server delivered them.
 *
 * ## Money is the server's, arithmetic is presentation-only
 *
 * The graph refuses to total an estate whose parts have different truth
 * states, and this client does not invent one. The portfolio block is a
 * separate server-computed Decimal contract: the full total only exists when
 * every holding is priced (`totals.complete`). When it is partial, the client
 * may *re-arrange* the server's own per-asset numbers — a priced subtotal and
 * allocation shares are sums and ratios of values the server sent — but it
 * must label them as covering priced holdings only, and it never fabricates a
 * price, a basis, or a P&L the server withheld.
 *
 * ## `complete` is read, never derived
 *
 * "3 properties" is only an honest sentence while the server says the view is
 * complete. Otherwise the copy switches to "3 so far". The flag comes down the
 * wire; nothing here infers it from list lengths.
 *
 * ## The states are not interchangeable
 *
 * Same discipline as Private Facts: READY/EMPTY, DENIED, NOT_ENTITLED,
 * FEATURE_DISABLED, NOT_IMPLEMENTED, UNAVAILABLE, LOCKED and ERROR are
 * different sentences. UNAVAILABLE, ERROR and DENIED must never be drawn as
 * EMPTY — "we could not look" or "we refused to answer" dressed as "you own
 * nothing" is exactly the confusion this surface exists to prevent. EMPTY is
 * READY with zero nodes, and only that.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  View
} from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  CAPITAL_CURRENCY_UNSPECIFIED,
  CAPITAL_VIEWS,
  CapitalCashFlow,
  CapitalCashFlowResult,
  CapitalGraph,
  CapitalGraphResult,
  CapitalObligations,
  CapitalObligationsResult,
  CapitalOverview,
  CapitalOverviewResult,
  CapitalPortfolio,
  CapitalPortfolioResult,
  CapitalView,
  asCapitalView,
  getCapitalCashFlow,
  getCapitalGraph,
  getCapitalObligations,
  getCapitalOverview,
  getCapitalPortfolio
} from "../api/capitalGraph";
import { useTranslation } from "../i18n";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { PrivateOfficeLockGate } from "../privateOffice/PrivateOfficeLockGate";
import { lockOfficeLocally } from "../privateOffice/officeLock";
import { colors } from "../theme/colors";

type Props = NativeStackScreenProps<RootStackParamList, "CapitalGraph">;

/**
 * The server's states plus the two the screen adds. LOADING is the window
 * before an answer exists; EMPTY is READY-with-nothing (see `settle`),
 * promoted to its own word so it can never be written by the same branch that
 * writes UNAVAILABLE.
 */
type ScreenState = "LOADING" | "EMPTY" | CapitalGraphResult["state"];

/**
 * The screen's tabs are wider than the graph's views.
 *
 * `CAPITAL_VIEWS` are arguments the graph route accepts. Overview, Cash Flow
 * and Obligations are not: each is a different endpoint with a different
 * payload and no `view` parameter at all. Folding them into one union and
 * filtering at the call site would leave a bogus `view=overview` request one
 * careless refactor away, so the tab type is widened here and narrowed back to
 * a real view before anything is fetched.
 */
const CAPITAL_PANELS = ["overview", "cash_flow", "obligations"] as const;

/** A tab served by its own endpoint rather than by `view=`. */
type CapitalPanel = (typeof CAPITAL_PANELS)[number];

/**
 * Cash Flow sits beside Obligations rather than next to Overview: it is the
 * same recorded debts on a timeline, and a member comparing "what is owed" with
 * "when it falls due" should not have to cross the graph views to do it.
 */
const CAPITAL_TABS = ["overview", ...CAPITAL_VIEWS, "cash_flow", "obligations"] as const;

type CapitalTab = (typeof CAPITAL_TABS)[number];

const asPanel = (tab: CapitalTab): CapitalPanel | null =>
  (CAPITAL_PANELS as readonly string[]).includes(tab) ? (tab as CapitalPanel) : null;

/** null means "this tab is not a graph read" — never a default view. */
const asGraphView = (tab: CapitalTab): CapitalView | null =>
  asPanel(tab) === null ? (tab as CapitalView) : null;

/**
 * The panels are the screen's own tabs; every other name is delegated to the
 * API module's parser rather than re-checked against a copy of the list here,
 * so the graph's accepted vocabulary keeps exactly one definition.
 */
const asCapitalTab = (value: unknown): CapitalTab | null => {
  const word = String(value ?? "").trim().toLowerCase();
  const panel = CAPITAL_PANELS.find((name) => name === word);
  return panel ?? asCapitalView(value);
};

/** Holdings and coverage read the projected portfolio; the other tabs don't. */
const wantsPortfolio = (tab: CapitalTab) => tab === "holdings" || tab === "coverage";

/**
 * Fetch a panel, tagged with which one it was.
 *
 * The tag travels with the answer instead of being re-derived where the answer
 * lands, so an awaited read can never be filed under the tab that happens to
 * be in front when it returns.
 */
const readPanel = async (
  which: CapitalPanel
): Promise<
  | { panel: "overview"; result: CapitalOverviewResult }
  | { panel: "cash_flow"; result: CapitalCashFlowResult }
  | { panel: "obligations"; result: CapitalObligationsResult }
> => {
  if (which === "overview") return { panel: "overview", result: await getCapitalOverview() };
  if (which === "cash_flow") return { panel: "cash_flow", result: await getCapitalCashFlow() };
  return { panel: "obligations", result: await getCapitalObligations() };
};

/**
 * EMPTY is a claim — "nothing recorded" — and on the portfolio-backed views
 * two endpoints must both back it: the graph (READY, zero nodes) and the
 * portfolio (READY, zero assets). A failed portfolio fetch can never be
 * dressed as an empty one; if the portfolio call refused or errored, the
 * screen stays READY and the portfolio panel says exactly what went wrong.
 */
function settle(
  next: CapitalGraphResult,
  folio: CapitalPortfolioResult | null
): ScreenState {
  if (next.state !== "READY") return next.state;
  if (next.graph.nodes.length > 0) return "READY";
  if (folio === null) return "EMPTY";
  return folio.state === "READY" && folio.portfolio.assets.length === 0 ? "EMPTY" : "READY";
}

/** Distinct swatches for the allocation bar; cycles past six holdings. */
const ALLOCATION_PALETTE = [
  colors.accent,
  "#7c6cf6",
  "#4fb6e0",
  "#e0a84f",
  "#d96fa8",
  "#8a9bb2"
];

export function CapitalGraphScreen(props: Props) {
  return (
    <PrivateOfficeLockGate
      onDismiss={() => props.navigation.goBack()}
      onRenew={() => props.navigation.navigate("Premium")}
    >
      <CapitalGraphBody {...props} />
    </PrivateOfficeLockGate>
  );
}

function CapitalGraphBody({ navigation, route }: Props) {
  const { t } = useTranslation();
  const insets = useSafeAreaInsets();
  const [tab, setTab] = useState<CapitalTab>(asCapitalTab(route.params?.view) ?? "overview");
  const [state, setState] = useState<ScreenState>("LOADING");
  const [result, setResult] = useState<CapitalGraphResult | null>(null);
  const [portfolio, setPortfolio] = useState<CapitalPortfolioResult | null>(null);
  const [overviewState, setOverviewState] = useState<ScreenState>("LOADING");
  const [overviewResult, setOverviewResult] = useState<CapitalOverviewResult | null>(null);
  const [obligationsState, setObligationsState] = useState<ScreenState>("LOADING");
  const [obligationsResult, setObligationsResult] =
    useState<CapitalObligationsResult | null>(null);
  const [cashFlowState, setCashFlowState] = useState<ScreenState>("LOADING");
  const [cashFlowResult, setCashFlowResult] = useState<CapitalCashFlowResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  // One request pair at a time: a second Retry tap while the first is still in
  // flight would race two setState pairs and double-hit the server.
  const inFlight = useRef(false);

  /** The graph view this tab reads, or null when the tab is not a graph read. */
  const view = asGraphView(tab);
  /** The panel this tab reads, or null when the tab *is* a graph read. */
  const panel = asPanel(tab);

  /**
   * Each panel's answer lands in its own pair of slots.
   *
   * They are deliberately not merged into one "last panel read": the reads
   * fail separately, and a shared slot would let the newest failure erase an
   * older tab's good answer — or, worse, let one tab's refusal be drawn under
   * another tab's heading.
   */
  const applyPanel = useCallback(
    (read:
      | { panel: "overview"; result: CapitalOverviewResult }
      | { panel: "cash_flow"; result: CapitalCashFlowResult }
      | { panel: "obligations"; result: CapitalObligationsResult }) => {
      // The server said the grant is dead (revoked elsewhere, expired). Drop
      // the local token so the enclosing gate flips back to the unlock door.
      if (read.result.state === "LOCKED") lockOfficeLocally();
      if (read.panel === "overview") {
        setOverviewResult(read.result);
        // Read straight through. The panels have no EMPTY: a member with
        // nothing on file still gets a real answer — zero priced assets, or
        // zero obligations recorded and the warning that this is not the same
        // as zero owed — which is a different sentence from "we found nothing
        // to show you".
        setOverviewState(read.result.state);
        return;
      }
      if (read.panel === "cash_flow") {
        // Same reasoning as the other two, and it bites hardest here: an empty
        // schedule is "nothing recorded falls due in any window we can date",
        // which a generic EMPTY would render as "you owe nothing".
        setCashFlowResult(read.result);
        setCashFlowState(read.result.state);
        return;
      }
      setObligationsResult(read.result);
      setObligationsState(read.result.state);
    },
    []
  );

  const load = useCallback(async (wanted: CapitalTab) => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const wantedPanel = asPanel(wanted);
      if (wantedPanel !== null) {
        applyPanel(await readPanel(wantedPanel));
        return;
      }
      const wantedView = asGraphView(wanted);
      if (wantedView === null) return;
      const [next, folio] = await Promise.all([
        getCapitalGraph(wantedView),
        wantsPortfolio(wanted) ? getCapitalPortfolio() : Promise.resolve(null)
      ]);
      // The server said the grant is dead (revoked elsewhere, expired). Drop
      // the local token so the enclosing gate flips back to the unlock door.
      if (next.state === "LOCKED" || folio?.state === "LOCKED") lockOfficeLocally();
      setResult(next);
      setPortfolio(folio);
      setState(settle(next, folio));
    } finally {
      inFlight.current = false;
    }
  }, [applyPanel]);

  useEffect(() => {
    let cancelled = false;
    // Only the active tab's state is reset. Blanking the others would make a
    // return to an already-loaded tab flash LOADING over an answer we hold.
    if (panel === "overview") setOverviewState("LOADING");
    else if (panel === "cash_flow") setCashFlowState("LOADING");
    else if (panel === "obligations") setObligationsState("LOADING");
    else setState("LOADING");
    (async () => {
      if (panel !== null) {
        const read = await readPanel(panel);
        if (cancelled) return;
        applyPanel(read);
        return;
      }
      if (view === null) return;
      const [next, folio] = await Promise.all([
        getCapitalGraph(view),
        wantsPortfolio(tab) ? getCapitalPortfolio() : Promise.resolve(null)
      ]);
      if (cancelled) return;
      if (next.state === "LOCKED" || folio?.state === "LOCKED") lockOfficeLocally();
      setResult(next);
      setPortfolio(folio);
      setState(settle(next, folio));
    })();
    return () => {
      cancelled = true;
    };
  }, [applyPanel, panel, tab, view]);

  const onRefresh = useCallback(async () => {
    if (inFlight.current) return;
    setRefreshing(true);
    try {
      await load(tab);
    } finally {
      setRefreshing(false);
    }
  }, [load, tab]);

  const graph = result && result.state === "READY" ? result.graph : null;

  /**
   * The tab in front owns the screen's verdict.
   *
   * The panels and the graph views are separate reads that fail separately, so
   * the banner must name the state of the request that actually backs what is
   * on screen. Reading the graph's `state` while Overview is showing would let
   * a healthy graph vouch for an overview that never answered — and reading
   * Overview's while Obligations is showing would do it in the other
   * direction.
   */
  const panelRead: {
    result: CapitalOverviewResult | CapitalCashFlowResult | CapitalObligationsResult | null;
    state: ScreenState;
  } | null =
    panel === "overview"
      ? { result: overviewResult, state: overviewState }
      : panel === "cash_flow"
        ? { result: cashFlowResult, state: cashFlowState }
        : panel === "obligations"
          ? { result: obligationsResult, state: obligationsState }
          : null;

  const active:
    | CapitalOverviewResult
    | CapitalCashFlowResult
    | CapitalObligationsResult
    | CapitalGraphResult
    | null =
    panelRead !== null ? panelRead.result : result;
  const shown: ScreenState = panelRead !== null ? panelRead.state : state;
  const minimumTier = active && active.state === "NOT_ENTITLED" ? active.minimumTier : "";
  const deniedReason = active && active.state === "DENIED" ? active.reason : "";
  const overview =
    overviewResult && overviewResult.state === "READY" ? overviewResult.overview : null;
  const obligations =
    obligationsResult && obligationsResult.state === "READY"
      ? obligationsResult.obligations
      : null;
  const cashFlow =
    cashFlowResult && cashFlowResult.state === "READY" ? cashFlowResult.cashFlow : null;

  const ot = (key: string, options?: Record<string, unknown>) =>
    t(`premium:privateOffice.capital.overview.${key}`, options);

  const bt = (key: string, options?: Record<string, unknown>) =>
    t(`premium:privateOffice.capital.obligations.${key}`, options);

  const ft = (key: string, options?: Record<string, unknown>) =>
    t(`premium:privateOffice.capital.cashFlow.${key}`, options);

  const nodeTypeLabel = (token: string) =>
    t(`premium:privateOffice.capital.nodeType.${token}`, { defaultValue: token });

  const truthLabel = (token: string) =>
    t(`premium:privateOffice.capital.truth.${token}`, { defaultValue: token });

  const truthStyle = (truth: string) =>
    truth === "CONFLICTING" || truth === "MISSING"
      ? styles.truthDanger
      : truth === "STALE" || truth === "ESTIMATED"
        ? styles.truthWarning
        : null;

  const pt = (key: string, options?: Record<string, unknown>) =>
    t(`premium:privateOffice.capital.portfolio.${key}`, options);

  const money = (value: number) =>
    new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(value);

  const signedMoney = (value: number) => `${value >= 0 ? "+" : ""}${money(value)}`;

  /**
   * Money in the currency the server named, or null.
   *
   * The overview reads carry their own currency code, and it is blank exactly
   * when the server refused to reduce a mixed-currency set to one figure.
   * Falling back to the screen's default currency there would relabel a
   * withheld total as dollars; printing the bare number would invite the
   * reader to assume the same thing. Both are lies about provenance, so an
   * unusable code yields null and the caller says "not stated" instead.
   */
  const moneyIn = (value: number | null, currency: string): string | null => {
    if (value === null) return null;
    const code = currency.trim().toUpperCase();
    if (!/^[A-Z]{3}$/.test(code)) return null;
    try {
      return new Intl.NumberFormat(undefined, { style: "currency", currency: code }).format(value);
    } catch {
      return null;
    }
  };

  /** A bare count, routed through i18n so digit shaping follows the locale. */
  const countText = (count: number) =>
    t("premium:privateOffice.capital.countExact", { count });

  const percent = (ratio: number) =>
    new Intl.NumberFormat(undefined, { style: "percent", maximumFractionDigits: 1 }).format(ratio);

  const signedPercent = (ratio: number) => `${ratio >= 0 ? "+" : ""}${percent(ratio)}`;

  const amount = (quantity: number) =>
    quantity.toLocaleString(undefined, { maximumFractionDigits: 8 });

  /**
   * The price feed's confession as a tier word. Thresholds are verbatim and
   * conservative — "Live" is never said about numbers older than 90 seconds,
   * and an absent age can only ever be "Stale".
   */
  const freshnessTier = (folio: CapitalPortfolio) => {
    if (folio.prices.source === "unavailable") return "unavailable";
    const age = folio.prices.ageSeconds;
    if (age === null) return "stale";
    if (age < 90) return "live";
    if (age < 600) return "fresh";
    if (age < 3600) return "delayed";
    return "stale";
  };

  const freshnessTint = (tier: string) =>
    tier === "live" || tier === "fresh"
      ? colors.accent
      : tier === "unavailable"
        ? colors.danger
        : colors.warning;

  const freshnessAge = (folio: CapitalPortfolio) => {
    if (folio.prices.source === "unavailable") return pt("sourceUnavailable");
    const age = folio.prices.ageSeconds;
    if (age === null) return pt("updatedStale");
    if (age < 90) return pt("updatedSeconds", { count: Math.max(0, Math.round(age)) });
    if (age < 3600) return pt("updatedMinutes", { count: Math.round(age / 60) });
    return pt("updatedStale");
  };

  /**
   * A refusal or failure of the portfolio read, drawn as its own compact card.
   * Each state keeps its own sentence — a tier wall, a denial and an outage
   * are different facts — and none of them is ever drawn as an empty
   * portfolio. Retryable failures get a Retry that reruns both requests.
   */
  const portfolioFailure = (failed: Exclude<CapitalPortfolioResult, { state: "READY" }>) => {
    const card = (body: string, retry: boolean, caption?: string) => (
      <View style={styles.folioPanel}>
        <View style={styles.failureHead}>
          <Ionicons name="alert-circle-outline" size={18} color={colors.warning} />
          <Text style={styles.folioTitle}>{pt("unavailableTitle")}</Text>
        </View>
        <Text style={styles.panelText}>{body}</Text>
        {caption ? <Text style={styles.panelCaption}>{caption}</Text> : null}
        {retry ? (
          refreshing ? (
            <ActivityIndicator color={colors.accent} style={styles.retrySpinner} />
          ) : (
            <Pressable style={styles.retry} onPress={onRefresh} accessibilityRole="button">
              <Text style={styles.retryText}>{t("premium:privateOffice.retry")}</Text>
            </Pressable>
          )
        ) : null}
      </View>
    );
    switch (failed.state) {
      case "DENIED":
        return card(
          t("premium:privateOffice.capital.denied.body"),
          false,
          failed.reason || undefined
        );
      case "NOT_ENTITLED":
        return card(
          failed.minimumTier
            ? t("premium:privateOffice.capital.notEntitled.body", { tier: failed.minimumTier })
            : t("premium:privateOffice.capital.notEntitled.bodyGeneric"),
          false
        );
      case "FEATURE_DISABLED":
        return card(t("premium:privateOffice.capital.disabled.body"), true);
      case "NOT_IMPLEMENTED":
        return card(t("premium:privateOffice.capital.notImplemented.body"), false);
      case "LOCKED":
        return card(t("premium:privateOffice.lock.locked.body"), true);
      default:
        // UNAVAILABLE and ERROR: we could not look — say so and offer Retry.
        // The raw transport message stays out of the UI on purpose.
        return card(pt("unavailable"), true);
    }
  };

  /** Priced holdings and the sum of their server-sent values. */
  const pricedSlice = (folio: CapitalPortfolio) => {
    const priced = folio.assets.filter(
      (asset): asset is (typeof folio.assets)[number] & { value: number } => asset.value !== null
    );
    const sum = priced.reduce((total, asset) => total + asset.value, 0);
    return { priced, sum };
  };

  const summaryCard = (folio: CapitalPortfolio) => {
    const { sum } = pricedSlice(folio);
    const unpricedCount = folio.totals.assets - folio.totals.priced;
    const tier = freshnessTier(folio);
    const totalValue = folio.totals.complete && folio.totals.value !== null
      ? folio.totals.value
      : folio.totals.priced > 0
        ? sum
        : null;
    const pnlRatio =
      folio.totals.pnlValue !== null && folio.totals.cost !== null && folio.totals.cost > 0
        ? folio.totals.pnlValue / folio.totals.cost
        : null;
    return (
      <View style={styles.folioPanel}>
        <View style={styles.folioHead}>
          <Text style={styles.folioTitle}>{pt("title")}</Text>
          <Text style={[styles.freshTier, { color: freshnessTint(tier) }]}>
            {pt(`fresh.${tier}`)}
          </Text>
        </View>
        <View style={styles.folioTotals}>
          <Text style={styles.folioTotalLabel}>
            {folio.totals.complete ? pt("totalValue") : pt("pricedValue")}
          </Text>
          {totalValue !== null ? (
            <Text style={styles.folioTotalValue}>{money(totalValue)}</Text>
          ) : (
            <Text style={styles.folioPartial}>{pt("valueUnavailable")}</Text>
          )}
          {!folio.totals.complete && unpricedCount > 0 ? (
            <Text style={styles.folioWarn}>
              {pt("excludesUnpriced", { count: unpricedCount })}
            </Text>
          ) : null}
        </View>
        <View style={styles.statRow}>
          <View style={styles.statCell}>
            <Text style={styles.statLabel}>{pt("pnlLabel")}</Text>
            {folio.totals.pnlValue !== null ? (
              <>
                <Text
                  style={[
                    styles.statValue,
                    folio.totals.pnlValue >= 0 ? styles.folioPnlUp : styles.folioPnlDown
                  ]}
                >
                  {signedMoney(folio.totals.pnlValue)}
                </Text>
                {pnlRatio !== null ? (
                  <Text
                    style={[
                      styles.statCaption,
                      pnlRatio >= 0 ? styles.folioPnlUp : styles.folioPnlDown
                    ]}
                  >
                    {signedPercent(pnlRatio)}
                  </Text>
                ) : null}
              </>
            ) : (
              <Text style={styles.statMuted}>{pt("pnlUnavailable")}</Text>
            )}
          </View>
          <View style={styles.statCell}>
            <Text style={styles.statLabel}>{pt("basisLabel")}</Text>
            {folio.totals.cost !== null ? (
              <>
                <Text style={styles.statValue}>{money(folio.totals.cost)}</Text>
                {folio.totals.basisKnown < folio.totals.assets ? (
                  <Text style={styles.statCaption}>
                    {pt("basisPartial", {
                      known: folio.totals.basisKnown,
                      total: folio.totals.assets
                    })}
                  </Text>
                ) : null}
              </>
            ) : (
              <Text style={styles.statMuted}>{pt("basisUnknown")}</Text>
            )}
          </View>
          <View style={styles.statCell}>
            <Text style={styles.statLabel}>{pt("holdingsLabel")}</Text>
            <Text style={styles.statValue}>{String(folio.totals.assets)}</Text>
          </View>
        </View>
        <View style={styles.qualityStrip}>
          <Text style={styles.qualityChip}>
            {pt("qualityPriced", { priced: folio.totals.priced, total: folio.totals.assets })}
          </Text>
          <Text style={styles.qualityChip}>
            {pt("qualityBasis", { known: folio.totals.basisKnown, total: folio.totals.assets })}
          </Text>
          <Text style={styles.qualityChip}>{freshnessAge(folio)}</Text>
        </View>
        {folio.totals.unpricedSymbols.length ? (
          <Text style={styles.folioWarn}>
            {pt("unpriced", { symbols: folio.totals.unpricedSymbols.join(", ") })}
          </Text>
        ) : null}
        {folio.sync.pending > 0 ? (
          <Text style={styles.folioSync}>{pt("syncPending", { count: folio.sync.pending })}</Text>
        ) : null}
        {folio.sync.failed > 0 ? <Text style={styles.folioWarn}>{pt("syncFailed")}</Text> : null}
      </View>
    );
  };

  const allocationCard = (folio: CapitalPortfolio) => {
    const { priced, sum } = pricedSlice(folio);
    if (priced.length === 0 || sum <= 0) return null;
    const hasUnpriced = folio.totals.priced < folio.totals.assets;
    return (
      <View style={styles.folioPanel}>
        <View style={styles.folioHead}>
          <Text style={styles.folioTitle}>{pt("allocationTitle")}</Text>
          {hasUnpriced ? (
            <Text style={styles.panelCaption}>{pt("allocationPricedOnly")}</Text>
          ) : null}
        </View>
        <View style={styles.allocationBar}>
          {priced.map((asset, index) => (
            <View
              key={asset.nodeId}
              style={[
                styles.allocationSegment,
                {
                  flex: asset.value / sum,
                  backgroundColor: ALLOCATION_PALETTE[index % ALLOCATION_PALETTE.length]
                }
              ]}
            />
          ))}
        </View>
        {priced.map((asset, index) => (
          <View key={asset.nodeId} style={styles.allocationRow}>
            <View
              style={[
                styles.allocationSwatch,
                { backgroundColor: ALLOCATION_PALETTE[index % ALLOCATION_PALETTE.length] }
              ]}
            />
            <Text style={styles.allocationSymbol}>{asset.symbol}</Text>
            <Text style={styles.allocationShare}>{percent(asset.value / sum)}</Text>
          </View>
        ))}
      </View>
    );
  };

  const holdingsCard = (folio: CapitalPortfolio) => {
    if (folio.assets.length === 0) return null;
    const { sum } = pricedSlice(folio);
    return (
      <View style={styles.folioPanel}>
        <Text style={styles.panelCaption}>{pt("subtitle")}</Text>
        {folio.assets.map((asset) => {
          const pnlRatio =
            asset.pnlValue !== null && asset.costBasis !== null && asset.costBasis > 0
              ? asset.pnlValue / asset.costBasis
              : null;
          return (
            <Pressable
              key={asset.nodeId}
              style={styles.folioRow}
              onPress={() => navigation.navigate("CapitalEntity", { id: asset.nodeId, view: view ?? undefined })}
              accessibilityRole="button"
              accessibilityLabel={asset.symbol}
            >
              <View style={styles.folioRowLeft}>
                <Text style={styles.folioSymbol}>{asset.symbol}</Text>
                {asset.name && asset.name !== asset.symbol ? (
                  <Text style={styles.folioName}>{asset.name}</Text>
                ) : null}
                <Text style={styles.folioMeta}>
                  {asset.quantity !== null ? `${amount(asset.quantity)} · ` : ""}
                  {pt("lots", { count: asset.lotCount })}
                </Text>
                <Text style={styles.folioSource}>{pt("manualSource")}</Text>
              </View>
              <View style={styles.folioRowRight}>
                {asset.value !== null ? (
                  <>
                    <Text style={styles.folioValue}>{money(asset.value)}</Text>
                    {sum > 0 ? (
                      <Text style={styles.folioMeta}>{percent(asset.value / sum)}</Text>
                    ) : null}
                  </>
                ) : (
                  <>
                    <Text style={styles.folioWarn}>{pt("priceUnavailable")}</Text>
                    <Text style={styles.folioMeta}>{pt("excludedFromTotal")}</Text>
                  </>
                )}
                {asset.price !== null ? (
                  <Text style={styles.folioMeta}>{money(asset.price)}</Text>
                ) : null}
                {asset.pnlValue !== null ? (
                  <Text
                    style={[
                      styles.folioPnlSmall,
                      asset.pnlValue >= 0 ? styles.folioPnlUp : styles.folioPnlDown
                    ]}
                  >
                    {signedMoney(asset.pnlValue)}
                    {pnlRatio !== null ? ` · ${signedPercent(pnlRatio)}` : ""}
                  </Text>
                ) : asset.costBasis === null ? (
                  <>
                    <Text style={styles.folioMeta}>{pt("basisUnknown")}</Text>
                    <Text style={styles.folioMeta}>{pt("pnlUnavailable")}</Text>
                  </>
                ) : null}
              </View>
            </Pressable>
          );
        })}
      </View>
    );
  };

  /** The whole holdings dashboard, or the failure card when the read failed. */
  /**
   * The Overview tab: net position, what it excludes, coverage, concentration
   * and the named gaps.
   *
   * ## `estimated` never travels alone
   *
   * It is priced assets minus quantified liabilities — not net worth — and the
   * server ships `complete` and `incomplete_reasons` precisely so the figure
   * cannot be quoted without them. They render in the same card, above the
   * fold, not as a footnote. When the server withheld the figure the card
   * shows no number at all rather than a zero.
   *
   * ## Counts are shown next to the money they qualify
   *
   * "Priced assets" beside "3/7 priced" is a different claim from "assets".
   * Every money cell here carries the count of records that actually fed it.
   */
  const overviewPanels = (data: CapitalOverview) => {
    const net = data.netPosition;
    const headline = moneyIn(net.estimated, net.currency);
    const assetsValue = moneyIn(net.knownAssets, net.currency);
    const liabilitiesValue = moneyIn(net.knownLiabilities, net.currency);

    // Only non-zero exclusions are listed. A row reading "no price: none" is
    // noise; the absence of the row is the same fact, said quieter.
    const excluded = (
      [
        ["excludedUnpricedAssets", net.excluded.unpricedAssets],
        ["excludedUnquantifiedLiabilities", net.excluded.unquantifiedLiabilities],
        ["excludedForeignCurrency", net.excluded.foreignCurrencyLiabilities],
        ["excludedUnspecifiedCurrency", net.excluded.unspecifiedCurrencyLiabilities]
      ] as const
    ).filter(([, count]) => count > 0);

    // Currencies the obligations are denominated in, minus the one already
    // reported as the known amount. What is left is what was set aside.
    const otherCurrencies = Object.entries(data.liabilities.byCurrency).filter(
      ([code]) => code !== data.liabilities.currency
    );

    const scored = data.coverage.scoredDimensions;
    const assetTotal = data.concentrations.assetTotal;
    const liabilityTotal = data.concentrations.liabilityTotal;

    const concentrationRows = (
      slices: typeof data.concentrations.assets,
      total: number | null,
      currency: string
    ) =>
      slices.map((slice, index) => {
        const value = moneyIn(slice.value, currency);
        return (
          <View key={slice.key} style={styles.allocationRow}>
            <View
              style={[
                styles.allocationSwatch,
                { backgroundColor: ALLOCATION_PALETTE[index % ALLOCATION_PALETTE.length] }
              ]}
            />
            <Text style={styles.allocationSymbol} numberOfLines={1}>
              {slice.label || slice.key}
            </Text>
            {value !== null ? <Text style={styles.folioMeta}>{value}</Text> : null}
            {/* `share` is the server's ratio. It is null when there was no
                total to divide by, and a computed stand-in would be a number
                the server declined to publish. */}
            {slice.share !== null && total !== null ? (
              <Text style={styles.allocationShare}>{percent(slice.share)}</Text>
            ) : (
              <Text style={styles.statMuted}>{ot("shareUnknown")}</Text>
            )}
          </View>
        );
      });

    return (
      <>
        <View style={styles.folioPanel}>
          <View style={styles.folioHead}>
            <Text style={styles.folioTitle}>{ot("title")}</Text>
            <Text
              style={[
                styles.freshTier,
                { color: net.complete ? colors.accent : colors.warning }
              ]}
            >
              {net.complete ? ot("complete") : ot("partial")}
            </Text>
          </View>

          <View style={styles.folioTotals}>
            {headline !== null ? (
              <Text style={styles.folioTotalValue}>{headline}</Text>
            ) : (
              <>
                <Text style={styles.folioPartial}>{ot("withheld")}</Text>
                <Text style={styles.folioWarn}>{ot("notSummable")}</Text>
              </>
            )}
          </View>

          {/* The qualifier rides with the number, always drawn, never
              collapsed when `complete` is true — "this is not net worth" is
              true of a complete figure too. */}
          <View style={styles.warnPanel}>
            <View style={styles.warnHead}>
              <Ionicons name="information-circle-outline" size={16} color={colors.warning} />
              <Text style={styles.warnTitle}>{ot("disclaimerTitle")}</Text>
            </View>
            <Text style={styles.conflictReason}>{ot("disclaimerBody")}</Text>
            {/* The server's own wording, verbatim: it is policy text, and a
                paraphrase here would drift from it silently. */}
            {net.disclaimer ? <Text style={styles.panelCaption}>{net.disclaimer}</Text> : null}
          </View>

          {net.incompleteReasons.length ? (
            <View style={styles.reasonList}>
              {net.incompleteReasons.map((token) => (
                <Text key={token} style={styles.reasonRow}>
                  {ot(`reason.${token}`, { defaultValue: token })}
                </Text>
              ))}
            </View>
          ) : null}

          <View style={styles.statRow}>
            <View style={styles.statCell}>
              <Text style={styles.statLabel}>{ot("assets")}</Text>
              {assetsValue !== null ? (
                <Text style={styles.statValue}>{assetsValue}</Text>
              ) : (
                <Text style={styles.statMuted}>{ot("withheld")}</Text>
              )}
              <Text style={styles.statCaption}>
                {pt("qualityPriced", { priced: data.assets.priced, total: data.assets.count })}
              </Text>
            </View>
            <View style={styles.statCell}>
              <Text style={styles.statLabel}>{ot("liabilities")}</Text>
              {liabilitiesValue !== null ? (
                <Text style={styles.statValue}>{liabilitiesValue}</Text>
              ) : (
                <Text style={styles.statMuted}>{ot("withheld")}</Text>
              )}
              <Text style={styles.statCaption}>
                {ot("quantifiedOf", {
                  done: data.liabilities.quantified,
                  total: data.liabilities.count
                })}
              </Text>
            </View>
          </View>

          {/* Zero recorded liabilities is not zero owed, and the server puts
              `no_liabilities_recorded` in the reasons for exactly that. */}
          {data.liabilities.count === 0 ? (
            <Text style={styles.folioWarn}>{ot("noLiabilities")}</Text>
          ) : null}

          {excluded.length ? (
            <View style={styles.reasonList}>
              <Text style={styles.statLabel}>{ot("excludedTitle")}</Text>
              {excluded.map(([token, count]) => (
                <View key={token} style={styles.excludedRow}>
                  <Text style={styles.coverageLabel}>{ot(token)}</Text>
                  <Text style={styles.coverageCount}>{countText(count)}</Text>
                </View>
              ))}
            </View>
          ) : null}

          {/* The magnitude behind "excluded, other currency". Naming the count
              without it would tell the member something was left out while
              withholding how much. */}
          {otherCurrencies.length ? (
            <View style={styles.reasonList}>
              <Text style={styles.statLabel}>{ot("otherCurrencies")}</Text>
              {otherCurrencies.map(([code, bucket]) => {
                const value = moneyIn(bucket.amount, code);
                return (
                  <View key={code} style={styles.excludedRow}>
                    <Text style={styles.coverageLabel}>{code}</Text>
                    <Text style={styles.coverageCount}>
                      {value !== null ? value : countText(bucket.count)}
                    </Text>
                  </View>
                );
              })}
            </View>
          ) : null}
        </View>

        <View style={styles.folioPanel}>
          <View style={styles.folioHead}>
            <Text style={styles.folioTitle}>{ot("coverageTitle")}</Text>
            {/* A null score means nothing was scoreable. Rendering it as zero
                would accuse the member of having recorded nothing. */}
            {data.coverage.score !== null ? (
              <Text style={styles.folioTitle}>{percent(data.coverage.score)}</Text>
            ) : (
              <Text style={styles.statMuted}>{ot("unscoreable")}</Text>
            )}
          </View>
          {Object.entries(data.coverage.dimensions).map(([name, dimension]) => (
            <View key={name} style={styles.coverageRow}>
              <Text style={styles.coverageLabel}>
                {ot(`dimension.${name}`, { defaultValue: name })}
              </Text>
              <Text style={styles.coverageCount}>
                {ot("dimensionCount", {
                  known: dimension.known,
                  total: dimension.countable
                })}
              </Text>
              {dimension.ratio !== null ? (
                <Text style={styles.allocationShare}>{percent(dimension.ratio)}</Text>
              ) : (
                <Text style={styles.statMuted}>{ot("withheld")}</Text>
              )}
            </View>
          ))}
          {/* Which dimensions the headline score is an average of — without it
              the percentage looks like it covers all four. */}
          {scored.length ? (
            <Text style={styles.panelCaption}>
              {scored.map((name) => ot(`dimension.${name}`, { defaultValue: name })).join(", ")}
            </Text>
          ) : null}
        </View>

        {data.concentrations.assets.length ? (
          <View style={styles.folioPanel}>
            <Text style={styles.folioTitle}>{ot("concentrationTitle")}</Text>
            {concentrationRows(
              data.concentrations.assets,
              assetTotal,
              data.concentrations.currency
            )}
            {data.concentrations.assetsUnrankedTail > 0 ? (
              <Text style={styles.panelCaption}>{ot("concentrationUnranked")}</Text>
            ) : null}
          </View>
        ) : null}

        {data.concentrations.liabilities.length ? (
          <View style={styles.folioPanel}>
            <Text style={styles.folioTitle}>{ot("liabilityConcentrationTitle")}</Text>
            {concentrationRows(
              data.concentrations.liabilities,
              liabilityTotal,
              data.concentrations.currency
            )}
          </View>
        ) : null}

        {data.needsReview.length ? (
          <View style={styles.folioPanel}>
            <Text style={styles.folioTitle}>{ot("reviewTitle")}</Text>
            {data.needsReview.map((item, index) => (
              <View key={`${item.kind}:${item.subject}:${index}`} style={styles.reviewRow}>
                <Text style={styles.reviewSubject}>{item.subject}</Text>
                {/* The server explains each gap in prose written for a person
                    and names the system that owns the fix. Both verbatim. */}
                <Text style={styles.reviewDetail}>{item.detail}</Text>
                <Text style={styles.reviewSource}>{item.source}</Text>
              </View>
            ))}
            {/* The server caps what it sends, so the list can be shorter than
                the count. Saying so beats implying these are all of them. */}
            {data.needsReviewTotal > data.needsReview.length ? (
              <Text style={styles.panelCaption}>{ot("reviewMore")}</Text>
            ) : null}
          </View>
        ) : null}
      </>
    );
  };

  /**
   * The Obligations tab: what is recorded as owed, and everything that keeps
   * that from being the whole of it.
   *
   * ## The total is a subset, and says so in the same card
   *
   * `known_amount` is the sum of obligations that have an amount *and* share a
   * single currency. The server sets it to null the moment either condition
   * fails, and null is not zero: a member with three unquantified debts is not
   * debt-free. So the headline is either the server's figure or the words for
   * "not stated" — never a fallback, never a bare number without its currency —
   * and the quantified/unquantified split sits directly beneath it rather than
   * in a footnote.
   *
   * ## An empty list is a claim about our records, not about the member
   *
   * Zero rows renders as "nothing is recorded, which is not the same as
   * nothing being owed". That is why this tab has no EMPTY state: the sentence
   * a member needs here is longer than "nothing to show".
   *
   * ## Row-level absences stay distinguishable
   *
   * "No amount recorded", "an amount with no currency", and a real figure are
   * three different rows. Collapsing the middle one into either neighbour
   * either invents a currency or hides a number the member entered.
   */
  const obligationsPanels = (data: CapitalObligations) => {
    const totals = data.totals;
    const headline = moneyIn(totals.knownAmount, totals.currency);

    // Everything the headline does not speak for. When the headline exists,
    // its own currency is dropped from this list — what is left is exactly
    // what was set aside. When it does not, `currency` is "" and nothing is
    // dropped, which is the correct answer: none of it was summed.
    const otherCurrencies = Object.entries(totals.byCurrency).filter(
      ([code]) => code !== totals.currency
    );

    return (
      <>
        <View style={styles.folioPanel}>
          <View style={styles.folioHead}>
            <Text style={styles.folioTitle}>{bt("title")}</Text>
            {/* `complete` is the server's, and it means "no row was left out
                of this sum for any reason" — not "the list is non-empty". */}
            <Text
              style={[
                styles.freshTier,
                { color: totals.complete ? colors.accent : colors.warning }
              ]}
            >
              {totals.complete ? bt("complete") : bt("partial")}
            </Text>
          </View>

          <View style={styles.folioTotals}>
            {headline !== null ? (
              <Text style={styles.folioTotalValue}>{headline}</Text>
            ) : (
              <>
                <Text style={styles.folioPartial}>{bt("withheld")}</Text>
                <Text style={styles.folioWarn}>{bt("notSummable")}</Text>
              </>
            )}
          </View>

          {/* Drawn on every render, including the complete case. "What you
              recorded" and "what you owe" are different quantities even when
              every record is perfect, so this is not a defect notice that
              disappears once the data is clean. */}
          <View style={styles.warnPanel}>
            <View style={styles.warnHead}>
              <Ionicons name="information-circle-outline" size={16} color={colors.warning} />
              <Text style={styles.warnTitle}>{bt("disclaimerTitle")}</Text>
            </View>
            <Text style={styles.conflictReason}>{bt("disclaimerBody")}</Text>
          </View>

          {/* The projection is what this whole tab reads. If the sweep did not
              run, the caveat belongs beside the figure, not at the bottom of
              the screen next to its counters. */}
          {!data.sync.projected ? (
            <Text style={styles.folioWarn}>{bt("notProjected")}</Text>
          ) : null}

          <View style={styles.statRow}>
            <View style={styles.statCell}>
              <Text style={styles.statLabel}>{bt("recordedLabel")}</Text>
              <Text style={styles.statValue}>{countText(totals.count)}</Text>
            </View>
            <View style={styles.statCell}>
              <Text style={styles.statLabel}>{bt("quantifiedLabel")}</Text>
              <Text style={styles.statValue}>{countText(totals.quantified)}</Text>
            </View>
            <View style={styles.statCell}>
              <Text style={styles.statLabel}>{bt("unquantifiedLabel")}</Text>
              {/* Unquantified rows are the reason the headline understates.
                  They are counted in the warning colour so the number reads as
                  a gap rather than as a tally. */}
              <Text style={totals.unquantified > 0 ? styles.folioWarn : styles.statValue}>
                {countText(totals.unquantified)}
              </Text>
            </View>
          </View>

          {totals.unspecifiedCurrency > 0 ? (
            <View style={styles.excludedRow}>
              <Text style={styles.coverageLabel}>{bt("unspecifiedCurrency")}</Text>
              <Text style={styles.coverageCount}>
                {countText(totals.unspecifiedCurrency)}
              </Text>
            </View>
          ) : null}

          {/* Nothing recorded is a statement about this app's records. The
              member may still owe money it has never been told about. */}
          {totals.count === 0 ? <Text style={styles.folioWarn}>{bt("none")}</Text> : null}

          {/* The server caps the rows it sends. Without this the list reads as
              the complete set of obligations. */}
          {totals.truncated ? <Text style={styles.folioWarn}>{bt("truncated")}</Text> : null}

          {otherCurrencies.length ? (
            <View style={styles.reasonList}>
              <Text style={styles.statLabel}>{bt("currenciesTitle")}</Text>
              {otherCurrencies.map(([code, bucket]) => {
                // The server files amounts with no stated currency under a
                // sentinel key. Printing it verbatim beside EUR and JPY would
                // dress a missing field as a currency of its own.
                const named = code !== CAPITAL_CURRENCY_UNSPECIFIED;
                const value = named ? moneyIn(bucket.amount, code) : null;
                return (
                  <View key={code} style={styles.excludedRow}>
                    <Text style={styles.coverageLabel}>
                      {named ? code : bt("unspecifiedCurrency")}
                    </Text>
                    <Text style={styles.coverageCount}>
                      {value !== null ? value : countText(bucket.count)}
                    </Text>
                  </View>
                );
              })}
            </View>
          ) : null}
        </View>

        {data.liabilities.length ? (
          <View style={styles.folioPanel}>
            {data.liabilities.map((row) => {
              const amount = moneyIn(row.amount, row.currency);
              return (
                <View key={row.nodeId} style={styles.obligationRow}>
                  <View style={styles.obligationHead}>
                    <Text style={styles.folioSymbol} numberOfLines={1}>
                      {row.title || nodeTypeLabel(row.kind)}
                    </Text>
                    {row.amount === null ? (
                      // Nothing was ever entered. A zero here would be this
                      // screen inventing a debt-free line item.
                      <Text style={styles.statMuted}>{bt("amountMissing")}</Text>
                    ) : amount !== null ? (
                      <Text style={styles.statValue}>{amount}</Text>
                    ) : (
                      // A figure exists but no currency does. Rendering it in
                      // the screen's default would name a currency the member
                      // never gave; hiding it would lose a number they did.
                      <Text style={styles.folioWarn}>{bt("unspecifiedCurrency")}</Text>
                    )}
                  </View>
                  <Text style={styles.folioMeta}>{nodeTypeLabel(row.kind)}</Text>
                  <View style={styles.coverageRow}>
                    <Text style={styles.coverageLabel}>{bt("dueLabel")}</Text>
                    {row.dueAt ? (
                      // The record store's own text, verbatim. Reformatting a
                      // string whose shape is not guaranteed risks printing a
                      // date the member never wrote.
                      <Text style={styles.coverageCount}>{row.dueAt}</Text>
                    ) : (
                      <Text style={styles.statMuted}>{bt("dueMissing")}</Text>
                    )}
                  </View>
                  {row.freshness?.stale ? (
                    <Text style={styles.folioWarn}>{bt("staleTitle")}</Text>
                  ) : null}
                  <View style={styles.coverageRow}>
                    {row.evidence.factIds.length ? (
                      <>
                        <Text style={styles.coverageLabel}>{bt("evidenceLabel")}</Text>
                        <Text style={styles.coverageCount}>
                          {countText(row.evidence.factIds.length)}
                        </Text>
                      </>
                    ) : (
                      <Text style={styles.statMuted}>{bt("evidenceMissing")}</Text>
                    )}
                  </View>
                </View>
              );
            })}
          </View>
        ) : null}

        <View style={styles.folioPanel}>
          <Text style={styles.folioTitle}>{bt("syncTitle")}</Text>
          <View style={styles.coverageRow}>
            <Text style={styles.coverageLabel}>{bt("projectedLabel")}</Text>
            <Text style={styles.coverageCount}>{countText(data.sync.obligations)}</Text>
          </View>
          <View style={styles.coverageRow}>
            <Text style={styles.coverageLabel}>{bt("retiredLabel")}</Text>
            <Text style={styles.coverageCount}>{countText(data.sync.retired)}</Text>
          </View>
          <View style={styles.coverageRow}>
            <Text style={styles.coverageLabel}>{bt("skippedLabel")}</Text>
            {/* Skipped rows are records the sweep could not project. They are
                absent from everything above, so the count is the only place
                they are visible at all. */}
            <Text style={data.sync.skipped > 0 ? styles.folioWarn : styles.coverageCount}>
              {countText(data.sync.skipped)}
            </Text>
          </View>
        </View>
      </>
    );
  };

  /**
   * The Cash Flow tab: when the recorded obligations fall due.
   *
   * ## It is outflows, and the screen says so before it says anything else
   *
   * PulseSoc has no income ledger — no salary record, no dividend record, no
   * rent received. A screen headed "Cash Flow" showing only money leaving is
   * describing half a balance while implying the other half was checked and
   * found to be zero. The server states this in `basis.inflows`; it is rendered
   * verbatim, in the same card as the figure, on every render. It is not a
   * defect notice and it never disappears.
   *
   * ## Nothing recurs unless the member recorded it recurring
   *
   * One dated mortgage payment is one outflow here, not twelve. `basis.recurrence`
   * carries that sentence and is shown for the same reason: a member who sees a
   * single payment in a yearly window needs to know the app did not silently
   * decide their mortgage was a one-off.
   *
   * ## Undated is not never, and unquantified is not zero
   *
   * An obligation with an amount but no due date is real money owed at an
   * unknown time, and it appears in no bucket because it belongs to none. The
   * server counts those in `excluded`; drawing the buckets without them would
   * present a schedule that quietly omits money. So the excluded counters are
   * rendered whenever they are non-zero, and `complete` is read from the server
   * rather than inferred from whether the list looks full.
   *
   * ## Buckets are the server's, including ones this build has no name for
   *
   * The rows are ordered and labelled from `basis.buckets`. Any bucket present
   * in `buckets` but absent from that list is still drawn, under its raw key —
   * an unrecognised bucket is the one case where silently dropping it would
   * hide money from a member while looking perfectly healthy.
   */
  const cashFlowPanels = (data: CapitalCashFlow) => {
    const totals = data.totals;
    const headline = moneyIn(totals.scheduledAmount, totals.currency);

    // Declared buckets first, in the server's order, then any the payload
    // carries without declaring. The second list is normally empty; when it is
    // not, the alternative to drawing it is losing a bucket that has money in
    // it because this build shipped before the name did.
    const declared = data.basis.buckets.map((definition) => ({
      name: definition.name,
      fromDays: definition.fromDays,
      toDays: definition.toDays,
      declared: true
    }));
    const undeclared = Object.keys(data.buckets)
      .filter((name) => !declared.some((entry) => entry.name === name))
      .map((name) => ({ name, fromDays: null, toDays: null, declared: false }));
    const buckets = [...declared, ...undeclared];

    const bucketLabel = (name: string) =>
      t(`premium:privateOffice.capital.cashFlow.bucket.${name}`, { defaultValue: name });

    /** The window in the server's own numbers, so an unfamiliar name is still readable. */
    const bucketRange = (entry: (typeof buckets)[number]) => {
      if (!entry.declared) return ft("bucketUndeclared");
      if (entry.fromDays === null && entry.toDays === null) return null;
      if (entry.fromDays === null) return ft("bucketBefore", { to: entry.toDays });
      if (entry.toDays === null) return ft("bucketAfter", { from: entry.fromDays });
      return ft("bucketBetween", { from: entry.fromDays, to: entry.toDays });
    };

    const excludedRows: [string, number][] = [
      ["undated", data.excluded.undated],
      ["unquantified", data.excluded.unquantified],
      ["undatedAndUnquantified", data.excluded.undatedAndUnquantified]
    ];
    const anyExcluded = excludedRows.some(([, count]) => count > 0);

    return (
      <>
        <View style={styles.folioPanel}>
          <View style={styles.folioHead}>
            <Text style={styles.folioTitle}>{ft("title")}</Text>
            {/* The server's flag. It is false whenever *anything* was left out
                — an undated debt, a truncated list, a currency that could not
                be summed — not merely when the list is short. */}
            <Text
              style={[
                styles.freshTier,
                { color: totals.complete ? colors.accent : colors.warning }
              ]}
            >
              {totals.complete ? ft("complete") : ft("partial")}
            </Text>
          </View>

          <View style={styles.folioTotals}>
            {headline !== null ? (
              <Text style={styles.folioTotalValue}>{headline}</Text>
            ) : (
              <>
                {/* Not a zero and not a bare number. The server withheld the
                    sum because the obligations span currencies it has no
                    approved rate for. */}
                <Text style={styles.folioPartial}>{ft("withheld")}</Text>
                <Text style={styles.folioWarn}>{ft("notSummable")}</Text>
              </>
            )}
          </View>

          {/* The load-bearing sentence of this entire tab, drawn on every
              render including the complete case. `basis.inflows` is the
              server's own wording and is printed verbatim rather than
              paraphrased: it is the difference between "what leaves" and "what
              is left". */}
          <View style={styles.warnPanel}>
            <View style={styles.warnHead}>
              <Ionicons name="information-circle-outline" size={16} color={colors.warning} />
              <Text style={styles.warnTitle}>{ft("outflowsTitle")}</Text>
            </View>
            <Text style={styles.conflictReason}>{data.basis.inflows}</Text>
            <Text style={styles.conflictReason}>{data.basis.recurrence}</Text>
          </View>

          {!data.sync.projected ? (
            <Text style={styles.folioWarn}>{ft("notProjected")}</Text>
          ) : null}

          <View style={styles.statRow}>
            <View style={styles.statCell}>
              <Text style={styles.statLabel}>{ft("scheduledLabel")}</Text>
              <Text style={styles.statValue}>{countText(totals.scheduledCount)}</Text>
            </View>
            <View style={styles.statCell}>
              <Text style={styles.statLabel}>{ft("seenLabel")}</Text>
              <Text style={styles.statValue}>{countText(totals.obligationsSeen)}</Text>
            </View>
            <View style={styles.statCell}>
              <Text style={styles.statLabel}>{ft("excludedLabel")}</Text>
              {/* Excluded rows are the reason the schedule understates. Shown
                  in the warning colour so the number reads as a gap, not a
                  tally. */}
              <Text style={totals.excludedCount > 0 ? styles.folioWarn : styles.statValue}>
                {countText(totals.excludedCount)}
              </Text>
            </View>
          </View>

          {/* Nothing scheduled is a statement about dated records, not about
              the member's debts. Both halves of that sentence are needed:
              undated obligations are excluded from every bucket by design. */}
          {totals.scheduledCount === 0 ? (
            <Text style={styles.folioWarn}>{ft("noneScheduled")}</Text>
          ) : null}

          {totals.truncated ? <Text style={styles.folioWarn}>{ft("truncated")}</Text> : null}

          {/* Published by the server as an invariant that must always be 0. If
              it is ever non-zero, a row in a currency the total does not name
              reached a bucket, and the figure above is FX by accident. Loud,
              because the alternative is a wrong total that looks right. */}
          {totals.mixedCurrencyRows > 0 ? (
            <Text style={styles.folioWarn}>
              {ft("mixedCurrency", { count: totals.mixedCurrencyRows })}
            </Text>
          ) : null}
        </View>

        <View style={styles.folioPanel}>
          <Text style={styles.folioTitle}>{ft("bucketsTitle")}</Text>
          {buckets.map((entry) => {
            const bucket = data.buckets[entry.name];
            const range = bucketRange(entry);
            return (
              <View key={entry.name} style={styles.obligationRow}>
                <View style={styles.obligationHead}>
                  <Text style={styles.folioSymbol} numberOfLines={1}>
                    {bucketLabel(entry.name)}
                  </Text>
                  {bucket === undefined ? (
                    // Declared but not reported. Not zero: the server named
                    // this window and then sent no figure for it.
                    <Text style={styles.statMuted}>{ft("bucketMissing")}</Text>
                  ) : bucket.amount !== null ? (
                    <Text style={styles.statValue}>
                      {moneyIn(bucket.amount, totals.currency) ?? ft("withheld")}
                    </Text>
                  ) : (
                    // A count with no sum. The rows exist; the currency does
                    // not allow adding them. A 0 here would read as "nothing
                    // falls due in this window".
                    <Text style={styles.folioWarn}>{ft("notSummable")}</Text>
                  )}
                </View>
                {range !== null ? <Text style={styles.folioMeta}>{range}</Text> : null}
                <View style={styles.coverageRow}>
                  <Text style={styles.coverageLabel}>{ft("bucketCountLabel")}</Text>
                  <Text style={styles.coverageCount}>
                    {bucket === undefined ? ft("bucketMissing") : countText(bucket.count)}
                  </Text>
                </View>
              </View>
            );
          })}
        </View>

        {anyExcluded ? (
          <View style={styles.folioPanel}>
            <Text style={styles.folioTitle}>{ft("excludedTitle")}</Text>
            {/* Why these are not in any bucket, in the member's terms. Without
                it, the excluded count above is a number with no meaning. */}
            <Text style={styles.panelCaption}>{ft("excludedBody")}</Text>
            {excludedRows.map(([key, count]) =>
              count > 0 ? (
                <View key={key} style={styles.excludedRow}>
                  <Text style={styles.coverageLabel}>{ft(key)}</Text>
                  <Text style={styles.coverageCount}>{countText(count)}</Text>
                </View>
              ) : null
            )}
          </View>
        ) : null}

        {data.schedule.length ? (
          <View style={styles.folioPanel}>
            {data.schedule.map((row) => {
              const amount = moneyIn(row.amount, row.currency);
              return (
                <View key={row.nodeId} style={styles.obligationRow}>
                  <View style={styles.obligationHead}>
                    <Text style={styles.folioSymbol} numberOfLines={1}>
                      {row.title || nodeTypeLabel(row.kind)}
                    </Text>
                    {row.amount === null ? (
                      <Text style={styles.statMuted}>{ft("amountMissing")}</Text>
                    ) : amount !== null ? (
                      <Text style={styles.statValue}>{amount}</Text>
                    ) : (
                      // A figure with no currency. Printing it in the screen's
                      // default would name a currency the member never gave.
                      <Text style={styles.folioWarn}>{ft("unspecifiedCurrency")}</Text>
                    )}
                  </View>
                  <Text style={styles.folioMeta}>{bucketLabel(row.bucket)}</Text>
                  <View style={styles.coverageRow}>
                    <Text style={styles.coverageLabel}>{ft("dueLabel")}</Text>
                    {row.dueAt ? (
                      // The record store's own text, verbatim — reformatting a
                      // string whose shape is not guaranteed risks printing a
                      // date the member never wrote.
                      <Text style={styles.coverageCount}>{row.dueAt}</Text>
                    ) : (
                      <Text style={styles.statMuted}>{ft("dueMissing")}</Text>
                    )}
                  </View>
                  {/* Overdue is the server's, computed from its own read
                      instant. A client clock could disagree with the bucket the
                      same payload already assigned. */}
                  {row.overdue ? <Text style={styles.folioWarn}>{ft("overdue")}</Text> : null}
                  <View style={styles.coverageRow}>
                    {row.evidence.factIds.length ? (
                      <>
                        <Text style={styles.coverageLabel}>{ft("evidenceLabel")}</Text>
                        <Text style={styles.coverageCount}>
                          {countText(row.evidence.factIds.length)}
                        </Text>
                      </>
                    ) : (
                      <Text style={styles.statMuted}>{ft("evidenceMissing")}</Text>
                    )}
                  </View>
                </View>
              );
            })}
          </View>
        ) : null}

        <View style={styles.folioPanel}>
          <Text style={styles.folioTitle}>{ft("syncTitle")}</Text>
          <View style={styles.coverageRow}>
            <Text style={styles.coverageLabel}>{ft("projectedLabel")}</Text>
            <Text style={styles.coverageCount}>{countText(data.sync.obligations)}</Text>
          </View>
          <View style={styles.coverageRow}>
            <Text style={styles.coverageLabel}>{ft("skippedLabel")}</Text>
            <Text style={data.sync.skipped > 0 ? styles.folioWarn : styles.coverageCount}>
              {countText(data.sync.skipped)}
            </Text>
          </View>
        </View>
      </>
    );
  };

  const holdingsPanels = () => {
    if (view !== "holdings" || !portfolio) return null;
    if (portfolio.state !== "READY") return portfolioFailure(portfolio);
    const folio = portfolio.portfolio;
    if (folio.assets.length === 0) return null;
    return (
      <>
        {summaryCard(folio)}
        {allocationCard(folio)}
        {holdingsCard(folio)}
      </>
    );
  };

  /**
   * Coverage is a report on how much of the portfolio the graph can vouch
   * for: pricing coverage and basis coverage come from the projected
   * portfolio, verification and evidence counts from the coverage view's own
   * facts. Nothing here is asserted beyond what either endpoint sent.
   */
  const coveragePanels = () => {
    if (view !== "coverage" || !portfolio) return null;
    if (portfolio.state !== "READY") return portfolioFailure(portfolio);
    const folio = portfolio.portfolio;
    const verificationCounts = new Map<string, number>();
    let documentBacked = 0;
    if (graph) {
      for (const fact of graph.facts) {
        const token = fact.provenance.verification || "SELF_REPORTED";
        verificationCounts.set(token, (verificationCounts.get(token) ?? 0) + 1);
        if (fact.provenance.hasSourceDocument) documentBacked += 1;
      }
    }
    const knownFacts = graph?.truthCounts.KNOWN ?? 0;
    const totalFacts = graph
      ? Object.values(graph.truthCounts).reduce((total, count) => total + count, 0)
      : 0;
    const ct = (key: string, options?: Record<string, unknown>) =>
      t(`premium:privateOffice.capital.coverage.${key}`, options);
    return (
      <>
        {folio.assets.length > 0 ? (
          <View style={styles.folioPanel}>
            <Text style={styles.folioTitle}>{ct("portfolioTitle")}</Text>
            <Text style={styles.panelText}>
              {ct("pricing", { priced: folio.totals.priced, total: folio.totals.assets })}
            </Text>
            <Text style={styles.panelText}>
              {ct("basis", { known: folio.totals.basisKnown, total: folio.totals.assets })}
            </Text>
            {folio.totals.unpricedSymbols.length ? (
              <Text style={styles.folioWarn}>
                {pt("unpriced", { symbols: folio.totals.unpricedSymbols.join(", ") })}
              </Text>
            ) : null}
          </View>
        ) : null}
        {verificationCounts.size > 0 || totalFacts > 0 ? (
          <View style={styles.folioPanel}>
            <Text style={styles.folioTitle}>{ct("factsTitle")}</Text>
            {totalFacts > 0 ? (
              <Text style={styles.panelText}>
                {ct("knownShare", { pct: percent(knownFacts / totalFacts) })}
              </Text>
            ) : null}
            {[...verificationCounts.entries()].map(([token, count]) => (
              <View key={token} style={styles.coverageRow}>
                <Text style={styles.coverageLabel}>
                  {t(`premium:privateOffice.verification.${token}`, { defaultValue: token })}
                </Text>
                <Text style={styles.coverageCount}>{String(count)}</Text>
              </View>
            ))}
            <Text style={styles.panelCaption}>
              {ct("documentBacked", { count: documentBacked })}
            </Text>
          </View>
        ) : null}
      </>
    );
  };

  /**
   * Relationships as the projection recorded them: every edge resolved to
   * names through the node map, grouped by relation type with counts. An
   * absent group is absent — no relationship is ever invented.
   */
  const structurePanels = (current: CapitalGraph) => {
    if (view !== "structure") return null;
    const names = new Map(
      current.nodes.map((node) => [node.id, node.externalRef || nodeTypeLabel(node.nodeType)])
    );
    if (current.edges.length === 0) {
      return (
        <View style={styles.folioPanel}>
          <Text style={styles.folioTitle}>
            {t("premium:privateOffice.capital.structure.relationshipsTitle")}
          </Text>
          <Text style={styles.panelText}>
            {t("premium:privateOffice.capital.structure.noRelationships")}
          </Text>
        </View>
      );
    }
    const groups = new Map<string, typeof current.edges>();
    for (const edge of current.edges) {
      const bucket = groups.get(edge.relationType) ?? [];
      bucket.push(edge);
      groups.set(edge.relationType, bucket);
    }
    return (
      <View style={styles.folioPanel}>
        <Text style={styles.folioTitle}>
          {t("premium:privateOffice.capital.structure.relationshipsTitle")}
        </Text>
        {[...groups.entries()].map(([relation, edges]) => (
          <View key={relation} style={styles.relationGroup}>
            <View style={styles.relationHead}>
              <Text style={styles.relationType}>
                {t(`premium:privateOffice.capital.relation.${relation}`, {
                  defaultValue: relation
                })}
              </Text>
              <Text style={styles.relationCount}>{String(edges.length)}</Text>
            </View>
            {edges.map((edge) => (
              <Text key={edge.id} style={styles.relationRow}>
                {`${names.get(edge.sourceNodeId) ?? String(edge.sourceNodeId)} → ${
                  names.get(edge.targetNodeId) ?? String(edge.targetNodeId)
                }`}
              </Text>
            ))}
          </View>
        ))}
      </View>
    );
  };

  /**
   * Evidence actually connected to capital facts — never an invented
   * document. Rows are facts whose provenance carries a source document.
   */
  const documentsPanels = (current: CapitalGraph) => {
    if (view !== "documents") return null;
    const evidence = current.facts.filter((fact) => fact.provenance.hasSourceDocument);
    if (evidence.length === 0) return null;
    return (
      <View style={styles.folioPanel}>
        <Text style={styles.folioTitle}>
          {t("premium:privateOffice.capital.documents.evidenceTitle")}
        </Text>
        {evidence.map((fact) => (
          <View key={fact.id} style={styles.coverageRow}>
            <Text style={styles.coverageLabel}>{fact.factType}</Text>
            <Text style={styles.coverageCount}>
              {t(`premium:privateOffice.verification.${fact.provenance.verification}`, {
                defaultValue: fact.provenance.verification
              })}
            </Text>
          </View>
        ))}
      </View>
    );
  };

  const notice = (
    icon: keyof typeof Ionicons.glyphMap,
    tint: string,
    title: string,
    body: string,
    retry: boolean,
    caption?: string,
    action?: { label: string; onPress: () => void }
  ) => (
    <View style={styles.panel}>
      <Ionicons name={icon} size={22} color={tint} />
      <Text style={styles.panelTitle}>{title}</Text>
      <Text style={styles.panelText}>{body}</Text>
      {caption ? <Text style={styles.panelCaption}>{caption}</Text> : null}
      {retry ? (
        refreshing ? (
          <ActivityIndicator color={colors.accent} style={styles.retrySpinner} />
        ) : (
          <Pressable style={styles.retry} onPress={onRefresh} accessibilityRole="button">
            <Text style={styles.retryText}>{t("premium:privateOffice.retry")}</Text>
          </Pressable>
        )
      ) : null}
      {action ? (
        <Pressable style={styles.retry} onPress={action.onPress} accessibilityRole="button">
          <Text style={styles.retryText}>{action.label}</Text>
        </Pressable>
      ) : null}
    </View>
  );

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={[
        styles.content,
        { paddingBottom: Math.max(insets.bottom, 18) + BOTTOM_NAV_CONTENT_CLEARANCE }
      ]}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.accent} />
      }
    >
      <View style={styles.header}>
        <Text style={styles.title}>{t("premium:privateOffice.capital.title")}</Text>
        <Text style={styles.subtitle}>{t("premium:privateOffice.capital.subtitle")}</Text>
      </View>

      <ScrollView
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.chips}
      >
        {CAPITAL_TABS.map((candidate) => (
          <Pressable
            key={candidate}
            style={[styles.chip, candidate === tab ? styles.chipActive : null]}
            onPress={() => setTab(candidate)}
            accessibilityRole="button"
            accessibilityState={{ selected: candidate === tab }}
          >
            <Text style={[styles.chipText, candidate === tab ? styles.chipTextActive : null]}>
              {t(`premium:privateOffice.capital.views.${candidate}`)}
            </Text>
          </Pressable>
        ))}
      </ScrollView>

      {shown === "LOADING" ? (
        <View accessibilityRole="progressbar" style={styles.skeletonStack}>
          <View style={styles.panel}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.panelText}>{t("premium:privateOffice.capital.loading")}</Text>
          </View>
          <View style={styles.skeletonBlock} />
          <View style={styles.skeletonBlockShort} />
        </View>
      ) : null}

      {/* Each panel is named explicitly. The extra guard is not redundant: a
          panel's answer survives a tab switch, so testing the payload alone
          would paint the net position over Obligations — or the obligation
          totals over the net position. */}
      {panel === "overview" && shown === "READY" && overview ? overviewPanels(overview) : null}
      {panel === "cash_flow" && shown === "READY" && cashFlow ? cashFlowPanels(cashFlow) : null}
      {panel === "obligations" && shown === "READY" && obligations
        ? obligationsPanels(obligations)
        : null}
      {shown === "READY" ? holdingsPanels() : null}
      {shown === "READY" ? coveragePanels() : null}

      {shown === "EMPTY" && view
        ? notice(
            "file-tray-outline",
            colors.muted,
            t("premium:privateOffice.capital.empty.title"),
            t(`premium:privateOffice.capital.emptyBody.${view}`),
            false,
            undefined,
            view === "holdings"
              ? {
                  label: t("premium:privateOffice.capital.empty.openPortfolio"),
                  onPress: () => navigation.navigate("Portfolio")
                }
              : undefined
          )
        : null}

      {/* The headline is ours; the reason is the server's, shown verbatim in
          the caption because it was written for a person. */}
      {shown === "DENIED"
        ? notice(
            "hand-left-outline",
            colors.warning,
            t("premium:privateOffice.capital.denied.title"),
            t("premium:privateOffice.capital.denied.body"),
            false,
            deniedReason || undefined
          )
        : null}

      {shown === "NOT_ENTITLED"
        ? notice(
            "lock-closed-outline",
            colors.warning,
            t("premium:privateOffice.capital.notEntitled.title"),
            minimumTier
              ? t("premium:privateOffice.capital.notEntitled.body", { tier: minimumTier })
              : t("premium:privateOffice.capital.notEntitled.bodyGeneric"),
            false
          )
        : null}

      {shown === "FEATURE_DISABLED"
        ? notice(
            "pause-circle-outline",
            colors.warning,
            t("premium:privateOffice.capital.disabled.title"),
            t("premium:privateOffice.capital.disabled.body"),
            true
          )
        : null}

      {shown === "NOT_IMPLEMENTED"
        ? notice(
            "construct-outline",
            colors.muted,
            t("premium:privateOffice.capital.notImplemented.title"),
            t("premium:privateOffice.capital.notImplemented.body"),
            false
          )
        : null}

      {shown === "LOCKED"
        ? notice(
            "lock-closed-outline",
            colors.accent,
            t("premium:privateOffice.lock.locked.title"),
            t("premium:privateOffice.lock.locked.body"),
            true
          )
        : null}

      {shown === "UNAVAILABLE"
        ? notice(
            "cloud-offline-outline",
            colors.warning,
            t("premium:privateOffice.capital.unavailable.title"),
            t("premium:privateOffice.capital.unavailable.body"),
            true
          )
        : null}

      {shown === "ERROR"
        ? notice(
            "alert-circle-outline",
            colors.danger,
            t("premium:privateOffice.capital.error.title"),
            t("premium:privateOffice.capital.error.body"),
            true
          )
        : null}

      {graph && shown === "READY" ? (
        <>
          {/* Counts of things, never of money. `complete` gates the phrasing:
              exact counts only while the server says nothing was truncated. */}
          {Object.keys(graph.counted).length ? (
            <View style={styles.countStrip}>
              {Object.entries(graph.counted).map(([token, count]) => (
                <View key={token} style={styles.countCard}>
                  <Text style={styles.countValue}>
                    {graph.complete
                      ? t("premium:privateOffice.capital.countExact", { count })
                      : t("premium:privateOffice.capital.countSoFar", { count })}
                  </Text>
                  <Text style={styles.countLabel}>{nodeTypeLabel(token)}</Text>
                </View>
              ))}
            </View>
          ) : null}

          {structurePanels(graph)}
          {documentsPanels(graph)}

          {graph.conflicts.length ? (
            <View style={styles.warnPanel}>
              <View style={styles.warnHead}>
                <Ionicons name="warning-outline" size={18} color={colors.warning} />
                <Text style={styles.warnTitle}>
                  {t("premium:privateOffice.capital.conflicts.title")}
                </Text>
              </View>
              {graph.conflicts.map((conflict) => (
                <View key={conflict.conflictId} style={styles.conflictRow}>
                  <Text style={styles.conflictType}>{conflict.factType}</Text>
                  {conflict.reason ? (
                    <Text style={styles.conflictReason}>{conflict.reason}</Text>
                  ) : null}
                  <Text style={styles.conflictDisagree}>
                    {t("premium:privateOffice.capital.conflicts.disagree")}
                  </Text>
                  {conflict.competing.map((side) => (
                    <View key={side.factId} style={styles.conflictSide}>
                      <Text style={styles.conflictValue}>{side.value}</Text>
                      <Text style={styles.conflictMeta}>
                        {t(`premium:privateOffice.verification.${side.verification}`, {
                          defaultValue: side.verification
                        })}
                      </Text>
                    </View>
                  ))}
                </View>
              ))}
            </View>
          ) : null}

          {graph.stale.length ? (
            <View style={styles.warnPanel}>
              <View style={styles.warnHead}>
                <Ionicons name="time-outline" size={18} color={colors.warning} />
                <Text style={styles.warnTitle}>
                  {t("premium:privateOffice.capital.stale.title")}
                </Text>
              </View>
              {graph.stale.map((flag) => (
                <View key={flag.factId} style={styles.staleRow}>
                  <Text style={styles.staleType}>{flag.factType}</Text>
                  {flag.ageDays !== null ? (
                    <Text style={styles.staleAge}>
                      {t("premium:privateOffice.capital.stale.age", { days: flag.ageDays })}
                    </Text>
                  ) : null}
                </View>
              ))}
            </View>
          ) : null}

          {/* Nodes render in the order the server delivered them. */}
          {graph.nodes.map((node) => (
            <Pressable
              key={node.id}
              style={styles.nodeRow}
              onPress={() => navigation.navigate("CapitalEntity", { id: node.id, view: view ?? undefined })}
              accessibilityRole="button"
              accessibilityLabel={node.externalRef || nodeTypeLabel(node.nodeType)}
            >
              <View style={styles.nodeHead}>
                <Text style={styles.nodeName}>
                  {node.externalRef || nodeTypeLabel(node.nodeType)}
                </Text>
                <Text style={[styles.truthMark, truthStyle(node.truth)]}>
                  {truthLabel(node.truth)}
                </Text>
              </View>
              <Text style={styles.nodeCaption}>
                {t("premium:privateOffice.capital.factCount", { count: node.factCount })}
              </Text>
            </Pressable>
          ))}
        </>
      ) : null}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: colors.background },
  content: { padding: 18, gap: 16 },
  header: { gap: 6 },
  title: { color: colors.text, fontSize: 24, fontWeight: "800" },
  subtitle: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  chips: { gap: 8, paddingVertical: 2 },
  chip: {
    paddingHorizontal: 13,
    paddingVertical: 7,
    borderRadius: 999,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1
  },
  chipActive: { backgroundColor: colors.surfaceRaised, borderColor: colors.accentStrong },
  chipText: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  chipTextActive: { color: colors.accentStrong },
  panel: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 16,
    padding: 18,
    gap: 8,
    alignItems: "flex-start"
  },
  panelTitle: { color: colors.text, fontSize: 15, fontWeight: "700" },
  panelText: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  panelCaption: { color: colors.muted, fontSize: 11, lineHeight: 16, fontStyle: "italic" },
  retry: {
    marginTop: 6,
    paddingHorizontal: 14,
    paddingVertical: 8,
    borderRadius: 999,
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1
  },
  retryText: { color: colors.accentStrong, fontSize: 13, fontWeight: "700" },
  retrySpinner: { marginTop: 6 },
  skeletonStack: { gap: 16 },
  skeletonBlock: {
    height: 120,
    borderRadius: 16,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1
  },
  skeletonBlockShort: {
    height: 64,
    borderRadius: 16,
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1
  },
  countStrip: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  countCard: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 12,
    paddingHorizontal: 13,
    paddingVertical: 9,
    gap: 2,
    alignItems: "flex-start"
  },
  countValue: { color: colors.text, fontSize: 15, fontWeight: "800" },
  countLabel: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 0.8 },
  warnPanel: {
    backgroundColor: colors.surface,
    borderColor: colors.warning,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 10
  },
  warnHead: { flexDirection: "row", alignItems: "center", gap: 6 },
  warnTitle: { color: colors.warning, fontSize: 13, fontWeight: "800" },
  conflictRow: { gap: 4 },
  conflictType: { color: colors.text, fontSize: 12, fontWeight: "800", letterSpacing: 0.8 },
  conflictReason: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  conflictDisagree: { color: colors.warning, fontSize: 11, fontWeight: "700" },
  conflictSide: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 10,
    paddingLeft: 10,
    borderLeftWidth: 2,
    borderLeftColor: colors.border
  },
  conflictValue: { color: colors.text, fontSize: 13, fontWeight: "600", flexShrink: 1 },
  conflictMeta: { color: colors.muted, fontSize: 11 },
  staleRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 10
  },
  staleType: { color: colors.text, fontSize: 12, fontWeight: "700", flexShrink: 1 },
  staleAge: { color: colors.warning, fontSize: 11, fontWeight: "700" },
  nodeRow: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 14,
    padding: 14,
    gap: 6
  },
  nodeHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10
  },
  folioPanel: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 16,
    padding: 16,
    gap: 10
  },
  folioHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 10
  },
  failureHead: { flexDirection: "row", alignItems: "center", gap: 8 },
  folioTitle: { color: colors.text, fontSize: 16, fontWeight: "800" },
  freshTier: { fontSize: 10, fontWeight: "800", letterSpacing: 0.8 },
  folioTotals: { gap: 2 },
  folioTotalLabel: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 0.8 },
  folioTotalValue: { color: colors.text, fontSize: 28, fontWeight: "800" },
  folioPartial: { color: colors.text, fontSize: 14, fontWeight: "700" },
  statRow: {
    flexDirection: "row",
    gap: 8,
    borderTopWidth: 1,
    borderTopColor: colors.border,
    paddingTop: 10
  },
  statCell: { flex: 1, gap: 2 },
  statLabel: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 0.8 },
  statValue: { color: colors.text, fontSize: 14, fontWeight: "800" },
  statCaption: { color: colors.muted, fontSize: 11, fontWeight: "700" },
  statMuted: { color: colors.muted, fontSize: 12, fontWeight: "600" },
  qualityStrip: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  qualityChip: {
    color: colors.muted,
    fontSize: 10,
    fontWeight: "700",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderWidth: 1,
    borderRadius: 999,
    paddingHorizontal: 9,
    paddingVertical: 4,
    overflow: "hidden"
  },
  allocationBar: {
    flexDirection: "row",
    height: 10,
    borderRadius: 5,
    overflow: "hidden",
    backgroundColor: colors.surfaceRaised
  },
  allocationSegment: { height: 10 },
  allocationRow: { flexDirection: "row", alignItems: "center", gap: 8 },
  allocationSwatch: { width: 10, height: 10, borderRadius: 3 },
  allocationSymbol: { color: colors.text, fontSize: 13, fontWeight: "700", flex: 1 },
  allocationShare: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  coverageRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 10
  },
  coverageLabel: { color: colors.text, fontSize: 13, fontWeight: "600", flexShrink: 1 },
  coverageCount: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  relationGroup: { gap: 4 },
  relationHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "center",
    gap: 10
  },
  relationType: { color: colors.text, fontSize: 12, fontWeight: "800", letterSpacing: 0.8 },
  relationCount: { color: colors.muted, fontSize: 12, fontWeight: "700" },
  relationRow: { color: colors.muted, fontSize: 13, lineHeight: 19, paddingLeft: 10 },
  folioPnlSmall: { fontSize: 11, fontWeight: "700" },
  folioPnlUp: { color: colors.accent },
  folioPnlDown: { color: colors.danger },
  folioWarn: { color: colors.warning, fontSize: 11, lineHeight: 16 },
  folioRow: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 10,
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: colors.border
  },
  folioRowLeft: { gap: 2, flexShrink: 1 },
  folioRowRight: { gap: 2, alignItems: "flex-end" },
  folioSymbol: { color: colors.text, fontSize: 15, fontWeight: "800" },
  folioName: { color: colors.muted, fontSize: 12 },
  folioMeta: { color: colors.muted, fontSize: 11 },
  folioSource: { color: colors.muted, fontSize: 10, fontStyle: "italic" },
  folioValue: { color: colors.text, fontSize: 15, fontWeight: "700" },
  folioSync: { color: colors.accentStrong, fontSize: 11, fontWeight: "700" },
  nodeName: { color: colors.text, fontSize: 15, fontWeight: "700", flexShrink: 1 },
  truthMark: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 0.8 },
  truthDanger: { color: colors.danger },
  truthWarning: { color: colors.warning },
  nodeCaption: { color: colors.muted, fontSize: 11 },
  reasonList: { gap: 4 },
  reasonRow: { color: colors.warning, fontSize: 12, lineHeight: 17 },
  excludedRow: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    gap: 8
  },
  obligationRow: {
    gap: 3,
    paddingVertical: 10,
    borderTopWidth: 1,
    borderTopColor: colors.border
  },
  obligationHead: {
    flexDirection: "row",
    justifyContent: "space-between",
    alignItems: "flex-start",
    gap: 10
  },
  reviewRow: { gap: 2 },
  reviewSubject: { color: colors.text, fontSize: 13, fontWeight: "800" },
  reviewDetail: { color: colors.muted, fontSize: 12, lineHeight: 17 },
  reviewSource: { color: colors.muted, fontSize: 10, fontWeight: "800", letterSpacing: 0.8 }
});

export default CapitalGraphScreen;
