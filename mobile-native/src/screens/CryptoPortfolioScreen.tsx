/**
 * Portfolio — the premium valuation surface over `/api/mobile/crypto/portfolio`.
 *
 * Numbers are rendered only when the server produced them. `change_24h_pct`
 * and `unrealized_pl` are nullable by contract — a null renders as "--", never
 * as 0, because "we do not know" and "it did not move" are different claims.
 * The history chart reuses the same no-dependency SVG line the asset detail
 * screen draws with (`AssetPriceChart`), and the `coverage` flag is surfaced as
 * a notice rather than silently plotting a partial series as if it were whole.
 *
 * Free accounts get the upsell, driven by the server's `premium_required`
 * denial — the client never decides entitlement on its own.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useState } from "react";
import {
  ActivityIndicator,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  useWindowDimensions,
  View
} from "react-native";
import {
  PORTFOLIO_PERIODS,
  PortfolioHistory,
  PortfolioPeriod,
  PortfolioSnapshot,
  getPremiumPortfolio,
  getPremiumPortfolioHistory,
  isPremiumRequired
} from "../api/cryptoPremium";
import { UNKNOWN_VALUE, formatPercent, formatPrice, formatSignedPrice } from "../api/watchlists";
import { AssetPriceChart } from "../components/crypto/AssetSparkline";
import { Panel } from "../components/Panel";
import { PremiumUpsellPanel } from "../components/crypto/PremiumUpsellPanel";
import { useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "CryptoPortfolio">;

const PERIOD_LABEL_KEYS: Record<PortfolioPeriod, string> = {
  "24h": "discovery:crypto.portfolio.periods.h24",
  "7d": "discovery:crypto.portfolio.periods.d7",
  "30d": "discovery:crypto.portfolio.periods.d30",
  "90d": "discovery:crypto.portfolio.periods.d90",
  "1y": "discovery:crypto.portfolio.periods.y1",
  all: "discovery:crypto.portfolio.periods.all"
};

function changeColor(change: number | null): string {
  if (change === null) return colors.muted;
  if (change > 0) return colors.accent;
  if (change < 0) return colors.danger;
  return colors.muted;
}

export function CryptoPortfolioScreen({ navigation }: Props) {
  const { t } = useTranslation();
  const { width } = useWindowDimensions();
  const chartWidth = Math.max(180, width - 68);

  const [snapshot, setSnapshot] = useState<PortfolioSnapshot | null>(null);
  const [history, setHistory] = useState<PortfolioHistory | null>(null);
  const [period, setPeriod] = useState<PortfolioPeriod>("7d");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [chartLoading, setChartLoading] = useState(false);
  const [gated, setGated] = useState(false);
  const [error, setError] = useState("");

  const load = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (mode === "refresh") setRefreshing(true);
      setError("");
      try {
        const next = await getPremiumPortfolio();
        setSnapshot(next);
        setGated(false);
      } catch (loadError) {
        if (isPremiumRequired(loadError)) {
          setGated(true);
        } else {
          setError(loadError instanceof Error ? loadError.message : t("discovery:crypto.portfolio.loadError"));
        }
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [t]
  );

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [load]);

  useEffect(() => {
    if (gated || loading) return;
    let active = true;
    setChartLoading(true);
    getPremiumPortfolioHistory(period)
      .then((next) => {
        if (active) setHistory(next);
      })
      .catch(() => {
        if (active) setHistory(null);
      })
      .finally(() => {
        if (active) setChartLoading(false);
      });
    return () => {
      active = false;
    };
  }, [period, gated, loading]);

  if (loading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>{t("discovery:crypto.common.loading")}</Text>
      </View>
    );
  }

  if (gated) {
    return (
      <ScrollView style={styles.root} contentContainerStyle={styles.content}>
        <PremiumUpsellPanel
          body={t("discovery:crypto.upsell.portfolioBody")}
          onUpgrade={() => navigation.navigate("Premium")}
        />
      </ScrollView>
    );
  }

  const change = snapshot?.change_24h_pct ?? null;
  const chartValues = (history?.points || []).map((point) => point.value);

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => load("refresh").catch(() => undefined)}
          tintColor={colors.accent}
        />
      }
    >
      <View style={styles.header}>
        <Text style={styles.title}>{t("discovery:crypto.portfolio.title")}</Text>
        <Text style={styles.subtitle}>{t("discovery:crypto.portfolio.subtitle")}</Text>
      </View>

      {error ? <Text style={styles.error}>{error}</Text> : null}

      <Panel>
        <Text style={styles.eyebrow}>{t("discovery:crypto.portfolio.totalValue")}</Text>
        <Text style={styles.totalValue}>{formatPrice(snapshot?.total_value ?? null)}</Text>
        <View style={styles.statRow}>
          <View style={styles.stat}>
            <Text style={styles.rowMeta}>{t("discovery:crypto.portfolio.change24h")}</Text>
            <Text style={[styles.statValue, { color: changeColor(change) }]}>
              {change === null ? UNKNOWN_VALUE : formatPercent(change)}
            </Text>
          </View>
          <View style={styles.stat}>
            <Text style={styles.rowMeta}>{t("discovery:crypto.portfolio.unrealizedPl")}</Text>
            <Text style={[styles.statValue, { color: changeColor(snapshot?.unrealized_pl ?? null) }]}>
              {snapshot?.unrealized_pl === null || snapshot?.unrealized_pl === undefined
                ? UNKNOWN_VALUE
                : formatSignedPrice(snapshot.unrealized_pl)}
            </Text>
          </View>
        </View>
        {snapshot?.calculated_at ? (
          <Text style={styles.rowMeta}>
            {t("discovery:crypto.portfolio.calculatedAt", { time: snapshot.calculated_at })}
          </Text>
        ) : null}
        {snapshot?.market_data_observed_at ? (
          <Text style={styles.rowMeta}>
            {t("discovery:crypto.portfolio.marketDataAt", { time: snapshot.market_data_observed_at })}
          </Text>
        ) : null}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>{t("discovery:crypto.portfolio.historyTitle")}</Text>
        <View style={styles.periodRow}>
          {PORTFOLIO_PERIODS.map((item) => (
            <Pressable
              key={item}
              accessibilityRole="button"
              accessibilityState={{ selected: period === item }}
              style={[styles.periodChip, period === item ? styles.periodChipActive : undefined]}
              onPress={() => setPeriod(item)}
            >
              <Text style={[styles.periodLabel, period === item ? styles.periodLabelActive : undefined]}>
                {t(PERIOD_LABEL_KEYS[item])}
              </Text>
            </Pressable>
          ))}
        </View>
        {history?.coverage === "none" || (history && chartValues.length < 2) ? (
          <Text style={styles.muted}>{t("discovery:crypto.portfolio.noCoverage")}</Text>
        ) : chartValues.length >= 2 ? (
          <AssetPriceChart values={chartValues} width={chartWidth} color={changeColor(change)} />
        ) : (
          <Text style={styles.muted}>{t("discovery:crypto.common.loading")}</Text>
        )}
        {chartLoading ? <Text style={styles.rowMeta}>{t("discovery:crypto.common.loading")}</Text> : null}
        {history?.coverage === "partial" ? (
          <Text style={styles.warning}>{t("discovery:crypto.portfolio.partialCoverage")}</Text>
        ) : null}
      </Panel>

      {snapshot?.concentration.top_symbol ? (
        <Panel>
          <Text style={styles.sectionTitle}>{t("discovery:crypto.portfolio.concentrationTitle")}</Text>
          <Text style={styles.muted}>
            {t("discovery:crypto.portfolio.concentrationBody", {
              symbol: snapshot.concentration.top_symbol,
              pct: snapshot.concentration.top_pct
            })}
          </Text>
        </Panel>
      ) : null}

      <Panel>
        <Text style={styles.sectionTitle}>{t("discovery:crypto.portfolio.holdingsTitle")}</Text>
        {snapshot?.holdings.length ? (
          snapshot.holdings.map((holding) => (
            <View key={`${holding.symbol}-${holding.asset_id ?? ""}`} style={styles.holdingRow}>
              <View style={styles.rowHead}>
                <Text style={styles.rowTitle}>{holding.symbol}</Text>
                <Text style={styles.holdingValue}>{formatPrice(holding.current_value)}</Text>
              </View>
              <Text style={styles.rowMeta}>
                {holding.name} · {holding.amount} × {formatPrice(holding.current_price)}
              </Text>
              <Text style={styles.rowMeta}>
                {t("discovery:crypto.portfolio.allocation", { pct: holding.allocation_pct })}
                {holding.average_buy_price !== null
                  ? ` · ${t("discovery:crypto.portfolio.avgBuy", { price: formatPrice(holding.average_buy_price) })}`
                  : ""}
              </Text>
              {holding.unrealized_pl !== null ? (
                <Text style={[styles.rowMeta, { color: changeColor(holding.unrealized_pl) }]}>
                  {t("discovery:crypto.portfolio.unrealizedPl")}: {formatSignedPrice(holding.unrealized_pl)}
                </Text>
              ) : null}
            </View>
          ))
        ) : (
          <Text style={styles.muted}>{t("discovery:crypto.portfolio.empty")}</Text>
        )}
      </Panel>
    </ScrollView>
  );
}

const styles = createThemedStyles(() => ({
  center: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.muted,
    marginTop: 10
  },
  content: {
    gap: 14,
    padding: 18,
    paddingBottom: 34
  },
  error: {
    color: colors.danger,
    fontSize: 13,
    lineHeight: 19
  },
  eyebrow: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    textTransform: "uppercase"
  },
  header: {
    gap: 5
  },
  holdingRow: {
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 3,
    paddingBottom: 10
  },
  holdingValue: {
    color: colors.text,
    fontSize: 14,
    fontWeight: "800"
  },
  muted: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  periodChip: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 6
  },
  periodChipActive: {
    backgroundColor: "rgba(37, 208, 167, 0.12)",
    borderColor: colors.accent
  },
  periodLabel: {
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800"
  },
  periodLabelActive: {
    color: colors.accent
  },
  periodRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 6
  },
  root: {
    backgroundColor: "transparent",
    flex: 1
  },
  rowHead: {
    alignItems: "center",
    flexDirection: "row",
    gap: 8,
    justifyContent: "space-between"
  },
  rowMeta: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 17
  },
  rowTitle: {
    color: colors.text,
    flex: 1,
    fontSize: 15,
    fontWeight: "900"
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 17,
    fontWeight: "900"
  },
  stat: {
    flex: 1,
    gap: 2
  },
  statRow: {
    flexDirection: "row",
    gap: 14
  },
  statValue: {
    fontSize: 16,
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 15,
    lineHeight: 21
  },
  title: {
    color: colors.text,
    fontSize: 28,
    fontWeight: "900"
  },
  totalValue: {
    color: colors.text,
    fontSize: 34,
    fontWeight: "900"
  },
  warning: {
    color: colors.warning,
    fontSize: 13,
    lineHeight: 19
  }
}));
