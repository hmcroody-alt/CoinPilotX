/**
 * Asset detail — price, chart, metrics and the user's alerts for one asset.
 *
 * ## Which chart ranges appear
 *
 * Only the ones the server said this asset can answer (`detail.ranges`). The
 * list is not hardcoded here, because whether a 1Y series exists is a fact
 * about the provider's coverage of that coin, and the client cannot know it. An
 * asset the provider does not carry gets no range buttons at all rather than
 * six buttons that each render "Unavailable".
 *
 * The chart itself is drawn from real `market_chart` points. There is an older
 * backend route that synthesises a sine wave around the current price; nothing
 * here may use it. A chart is a claim about what the price *did*, and a
 * decorative wave that tracks spot is the most convincing kind of wrong.
 *
 * ## Which tabs appear
 *
 * Overview and Alerts. There is no Notes tab and no News tab because neither
 * exists in this product for crypto assets — a tab that opens onto nothing is a
 * promise the app does not keep.
 *
 * ## Alerts
 *
 * This screen reads alerts and links out; it never creates them itself.
 * "Create alert" navigates to the canonical alert flow with this asset
 * prefilled, so there is exactly one place where an alert rule comes into
 * existence and exactly one set of validation rules for it.
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
  AssetDetail,
  AssetHistory,
  HistoryRange,
  UNKNOWN_VALUE,
  formatCompact,
  formatPercent,
  formatPrice,
  formatRank,
  formatSignedPrice,
  formatSupply,
  getAssetDetail,
  getAssetHistory,
  loadCachedAssetDetail,
  loadCachedAssetHistory,
  setFavoriteAsset
} from "../api/watchlists";
import { alertConditionLabel, alertStatusLabel } from "../api/alerts";
import { AssetPriceChart } from "../components/crypto/AssetSparkline";
import { Panel } from "../components/Panel";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "AssetDetail">;

type TabKey = "overview" | "alerts";

const TABS: { key: TabKey; label: string }[] = [
  { key: "overview", label: "Overview" },
  { key: "alerts", label: "Alerts" }
];

function changeColor(change: number | null): string {
  if (change === null) return colors.muted;
  if (change > 0) return colors.accent;
  if (change < 0) return colors.danger;
  return colors.muted;
}

export function AssetDetailScreen({ route, navigation }: Props) {
  const symbol = String(route.params?.symbol || "").toUpperCase();
  const { width } = useWindowDimensions();

  const [detail, setDetail] = useState<AssetDetail | null>(null);
  const [history, setHistory] = useState<AssetHistory | null>(null);
  const [range, setRange] = useState<HistoryRange>("24H");
  const [tab, setTab] = useState<TabKey>("overview");
  const [loading, setLoading] = useState(true);
  const [chartLoading, setChartLoading] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");

  const asset = detail?.asset;
  const ranges = detail?.ranges || [];
  const alerts = detail?.alerts || [];
  const chartWidth = Math.max(180, width - 60);

  const loadDetail = useCallback(
    async (mode: "initial" | "refresh" = "initial") => {
      if (mode === "refresh") setRefreshing(true);
      try {
        const next = await getAssetDetail(symbol);
        setDetail(next);
        setError("");
        // Keep the user on the range they picked when it is still supported;
        // otherwise fall back to one the asset can actually answer.
        setRange((current) => (next.ranges.includes(current) ? current : next.ranges[0] || "24H"));
      } catch (loadError) {
        const cached = await loadCachedAssetDetail(symbol);
        if (cached) setDetail(cached);
        setError(loadError instanceof Error ? loadError.message : "This asset could not load.");
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    [symbol]
  );

  useEffect(() => {
    let active = true;
    loadCachedAssetDetail(symbol)
      .then((cached) => {
        if (active && cached) {
          setDetail(cached);
          setLoading(false);
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (active) loadDetail("initial").catch(() => undefined);
      });
    return () => {
      active = false;
    };
  }, [symbol, loadDetail]);

  useEffect(() => {
    navigation.setOptions({ title: route.params?.name || symbol });
  }, [navigation, route.params?.name, symbol]);

  // History is fetched only once the screen is open, and only for the range in
  // view. Loading six ranges up front would be six provider calls for five
  // charts nobody looked at.
  useEffect(() => {
    if (!ranges.length) {
      setHistory(null);
      return;
    }
    let active = true;
    setChartLoading(true);
    loadCachedAssetHistory(symbol, range)
      .then((cached) => {
        if (active && cached?.points.length) setHistory(cached);
      })
      .catch(() => undefined)
      .finally(() => {
        getAssetHistory(symbol, range)
          .then((next) => {
            if (active) setHistory(next);
          })
          .catch(() => undefined)
          .finally(() => {
            if (active) setChartLoading(false);
          });
      });
    return () => {
      active = false;
    };
  }, [symbol, range, ranges.length]);

  async function onToggleFavorite() {
    if (!asset) return;
    try {
      await setFavoriteAsset(asset.symbol, !asset.favorite);
      await loadDetail("refresh");
    } catch (favoriteError) {
      setError(favoriteError instanceof Error ? favoriteError.message : "Favorites could not be updated.");
    }
  }

  if (loading && !detail) {
    return (
      <View style={styles.loadingRoot}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  const tint = changeColor(asset?.change_24h ?? null);
  const chartPoints = (history?.points || []).map((point) => point.price);

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={() => loadDetail("refresh")} tintColor={colors.accent} />
      }
    >
      <Panel>
        <View style={styles.headerRow}>
          <View style={styles.identity}>
            <Text style={styles.name}>{asset?.name || symbol}</Text>
            <Text style={styles.symbolLine}>
              {symbol} · Rank {formatRank(asset?.market_cap_rank ?? null)}
            </Text>
          </View>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={asset?.favorite ? `Unfavorite ${symbol}` : `Favorite ${symbol}`}
            onPress={onToggleFavorite}
          >
            <Text style={[styles.star, asset?.favorite ? styles.starOn : null]}>{asset?.favorite ? "★" : "☆"}</Text>
          </Pressable>
        </View>

        <Text style={styles.price}>{formatPrice(asset?.price ?? null)}</Text>
        <Text style={[styles.change, { color: tint }]}>
          {formatSignedPrice(asset?.price_change_24h ?? null)} ({formatPercent(asset?.change_24h ?? null)}) 24h
        </Text>
        {/* When the provider has nothing for this asset, say so once, here,
            rather than printing zeros through the whole screen. */}
        {asset && !asset.has_market_data ? (
          <Text style={styles.warning}>Live market data is unavailable for this asset.</Text>
        ) : null}
        {detail?.market && !detail.market.ready ? (
          <Text style={styles.warning}>{detail.market.warning || "Live prices are temporarily unavailable."}</Text>
        ) : null}
        {error ? <Text style={styles.error}>{error}</Text> : null}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Price history</Text>
        {ranges.length ? (
          <>
            <View style={styles.ranges}>
              {ranges.map((item) => (
                <Pressable
                  key={item}
                  accessibilityRole="button"
                  accessibilityState={{ selected: range === item }}
                  style={[styles.rangeChip, range === item ? styles.rangeChipActive : null]}
                  onPress={() => setRange(item)}
                >
                  <Text style={[styles.rangeLabel, range === item ? styles.rangeLabelActive : null]}>{item}</Text>
                </Pressable>
              ))}
            </View>
            {chartPoints.length >= 2 ? (
              <AssetPriceChart values={chartPoints} width={chartWidth} color={tint} />
            ) : (
              <Text style={styles.muted}>
                {chartLoading ? "Loading…" : history?.warning || `No ${range} history is available.`}
              </Text>
            )}
            {history?.stale ? <Text style={styles.warning}>{history.warning}</Text> : null}
          </>
        ) : (
          <Text style={styles.muted}>Price history is not available for this asset.</Text>
        )}
      </Panel>

      <View style={styles.tabs}>
        {TABS.map((item) => (
          <Pressable
            key={item.key}
            accessibilityRole="tab"
            accessibilityState={{ selected: tab === item.key }}
            style={[styles.tab, tab === item.key ? styles.tabActive : null]}
            onPress={() => setTab(item.key)}
          >
            <Text style={[styles.tabLabel, tab === item.key ? styles.tabLabelActive : null]}>{item.label}</Text>
          </Pressable>
        ))}
      </View>

      {tab === "overview" ? (
        <Panel>
          <Text style={styles.sectionTitle}>Market data</Text>
          <MetricRow label="Market cap" value={formatCompact(asset?.market_cap ?? null)} />
          <MetricRow label="24h volume" value={formatCompact(asset?.volume_24h ?? null)} />
          <MetricRow label="Circulating supply" value={formatSupply(asset?.circulating_supply ?? null, symbol)} />
          <MetricRow
            label="Max supply"
            value={
              asset?.max_supply === null || asset?.max_supply === undefined
                ? UNKNOWN_VALUE
                : formatSupply(asset.max_supply, symbol)
            }
          />
          {detail?.watchlists.length ? (
            <Text style={styles.muted}>
              On {detail.watchlists.map((membership) => membership.watchlist_name).join(", ")}
            </Text>
          ) : (
            <Text style={styles.muted}>Not on any of your watchlists yet.</Text>
          )}
        </Panel>
      ) : (
        <Panel>
          <Text style={styles.sectionTitle}>Your alerts</Text>
          {alerts.length ? (
            alerts.map((alert) => (
              <Pressable
                key={alert.id}
                style={styles.alertRow}
                accessibilityRole="button"
                onPress={() => navigation.navigate("AlertManagement", { alertId: alert.id, title: "Alerts" })}
              >
                <Text style={styles.alertCondition}>{alertConditionLabel(alert)}</Text>
                <Text style={styles.muted}>{alertStatusLabel(alert.status)}</Text>
              </Pressable>
            ))
          ) : (
            <Text style={styles.muted}>No alerts for {symbol} yet.</Text>
          )}
          <Pressable
            accessibilityRole="button"
            style={styles.primaryButton}
            // The canonical alert flow, with this asset already selected. No
            // alert is created here.
            onPress={() => navigation.navigate("AlertManagement", { presetSymbol: symbol, title: "Alerts" })}
          >
            <Text style={styles.primaryButtonLabel}>Create alert</Text>
          </Pressable>
        </Panel>
      )}
    </ScrollView>
  );
}

function MetricRow({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metricRow}>
      <Text style={styles.muted}>{label}</Text>
      <Text style={styles.metricValue}>{value}</Text>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  alertCondition: { color: colors.text, fontSize: 14, fontWeight: "700", textTransform: "capitalize" },
  alertRow: {
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    gap: 2,
    paddingTop: 10
  },
  change: { fontSize: 15, fontWeight: "800" },
  content: { gap: 12, padding: 16, paddingBottom: 48 },
  error: { color: colors.danger, fontSize: 13, lineHeight: 19 },
  headerRow: { alignItems: "flex-start", flexDirection: "row", justifyContent: "space-between" },
  identity: { flex: 1, gap: 2 },
  loadingRoot: { alignItems: "center", flex: 1, justifyContent: "center" },
  metricRow: {
    alignItems: "center",
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    justifyContent: "space-between",
    paddingTop: 8
  },
  metricValue: { color: colors.text, fontSize: 14, fontWeight: "700" },
  muted: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  name: { color: colors.text, fontSize: 20, fontWeight: "900" },
  price: { color: colors.text, fontSize: 30, fontWeight: "900" },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingVertical: 12
  },
  primaryButtonLabel: { color: colors.background, fontSize: 14, fontWeight: "900" },
  rangeChip: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 10,
    paddingVertical: 6
  },
  rangeChipActive: { backgroundColor: colors.signalDim, borderColor: colors.accent },
  rangeLabel: { color: colors.muted, fontSize: 12, fontWeight: "800" },
  rangeLabelActive: { color: colors.accent },
  ranges: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  root: { backgroundColor: "transparent", flex: 1 },
  sectionTitle: { color: colors.text, fontSize: 15, fontWeight: "900" },
  star: { color: colors.muted, fontSize: 24 },
  starOn: { color: colors.warning },
  symbolLine: { color: colors.muted, fontSize: 13 },
  tab: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 8
  },
  tabActive: { backgroundColor: colors.signalDim, borderColor: colors.accent },
  tabLabel: { color: colors.muted, fontSize: 13, fontWeight: "700" },
  tabLabelActive: { color: colors.accent },
  tabs: { flexDirection: "row", gap: 8 },
  warning: { color: colors.warning, fontSize: 13, lineHeight: 19 }
}));
