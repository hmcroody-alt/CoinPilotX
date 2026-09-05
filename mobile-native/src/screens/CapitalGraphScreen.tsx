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
  CAPITAL_VIEWS,
  CapitalGraph,
  CapitalGraphResult,
  CapitalPortfolio,
  CapitalPortfolioResult,
  CapitalView,
  asCapitalView,
  getCapitalGraph,
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

/** Holdings and coverage read the projected portfolio; the other views don't. */
const wantsPortfolio = (view: CapitalView) => view === "holdings" || view === "coverage";

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
  const [view, setView] = useState<CapitalView>(asCapitalView(route.params?.view) ?? "holdings");
  const [state, setState] = useState<ScreenState>("LOADING");
  const [result, setResult] = useState<CapitalGraphResult | null>(null);
  const [portfolio, setPortfolio] = useState<CapitalPortfolioResult | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  // One request pair at a time: a second Retry tap while the first is still in
  // flight would race two setState pairs and double-hit the server.
  const inFlight = useRef(false);

  const load = useCallback(async (wanted: CapitalView) => {
    if (inFlight.current) return;
    inFlight.current = true;
    try {
      const [next, folio] = await Promise.all([
        getCapitalGraph(wanted),
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
  }, []);

  useEffect(() => {
    let cancelled = false;
    setState("LOADING");
    (async () => {
      const [next, folio] = await Promise.all([
        getCapitalGraph(view),
        wantsPortfolio(view) ? getCapitalPortfolio() : Promise.resolve(null)
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
  }, [view]);

  const onRefresh = useCallback(async () => {
    if (inFlight.current) return;
    setRefreshing(true);
    try {
      await load(view);
    } finally {
      setRefreshing(false);
    }
  }, [load, view]);

  const graph = result && result.state === "READY" ? result.graph : null;
  const minimumTier = result && result.state === "NOT_ENTITLED" ? result.minimumTier : "";
  const deniedReason = result && result.state === "DENIED" ? result.reason : "";

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
              onPress={() => navigation.navigate("CapitalEntity", { id: asset.nodeId, view })}
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
        {CAPITAL_VIEWS.map((candidate) => (
          <Pressable
            key={candidate}
            style={[styles.chip, candidate === view ? styles.chipActive : null]}
            onPress={() => setView(candidate)}
            accessibilityRole="button"
            accessibilityState={{ selected: candidate === view }}
          >
            <Text style={[styles.chipText, candidate === view ? styles.chipTextActive : null]}>
              {t(`premium:privateOffice.capital.views.${candidate}`)}
            </Text>
          </Pressable>
        ))}
      </ScrollView>

      {state === "LOADING" ? (
        <View accessibilityRole="progressbar" style={styles.skeletonStack}>
          <View style={styles.panel}>
            <ActivityIndicator color={colors.accent} />
            <Text style={styles.panelText}>{t("premium:privateOffice.capital.loading")}</Text>
          </View>
          <View style={styles.skeletonBlock} />
          <View style={styles.skeletonBlockShort} />
        </View>
      ) : null}

      {state === "READY" ? holdingsPanels() : null}
      {state === "READY" ? coveragePanels() : null}

      {state === "EMPTY"
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
      {state === "DENIED"
        ? notice(
            "hand-left-outline",
            colors.warning,
            t("premium:privateOffice.capital.denied.title"),
            t("premium:privateOffice.capital.denied.body"),
            false,
            deniedReason || undefined
          )
        : null}

      {state === "NOT_ENTITLED"
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

      {state === "FEATURE_DISABLED"
        ? notice(
            "pause-circle-outline",
            colors.warning,
            t("premium:privateOffice.capital.disabled.title"),
            t("premium:privateOffice.capital.disabled.body"),
            true
          )
        : null}

      {state === "NOT_IMPLEMENTED"
        ? notice(
            "construct-outline",
            colors.muted,
            t("premium:privateOffice.capital.notImplemented.title"),
            t("premium:privateOffice.capital.notImplemented.body"),
            false
          )
        : null}

      {state === "LOCKED"
        ? notice(
            "lock-closed-outline",
            colors.accent,
            t("premium:privateOffice.lock.locked.title"),
            t("premium:privateOffice.lock.locked.body"),
            true
          )
        : null}

      {state === "UNAVAILABLE"
        ? notice(
            "cloud-offline-outline",
            colors.warning,
            t("premium:privateOffice.capital.unavailable.title"),
            t("premium:privateOffice.capital.unavailable.body"),
            true
          )
        : null}

      {state === "ERROR"
        ? notice(
            "alert-circle-outline",
            colors.danger,
            t("premium:privateOffice.capital.error.title"),
            t("premium:privateOffice.capital.error.body"),
            true
          )
        : null}

      {graph && state === "READY" ? (
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
              onPress={() => navigation.navigate("CapitalEntity", { id: node.id, view })}
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
  nodeCaption: { color: colors.muted, fontSize: 11 }
});

export default CapitalGraphScreen;
