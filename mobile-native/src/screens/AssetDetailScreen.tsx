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
 *
 * ## The quick action bar
 *
 * Watchlist, alert, UNDX — the three things a member wants to do once they have
 * looked at a coin. None of them is implemented here:
 *
 * - **Watchlist** calls `addWatchlistAsset` against the member's existing lists.
 *   There is no "default watchlist" in this product, so the one unambiguous case
 *   — exactly one list — is added to in a single tap, and zero or several lists
 *   both open the Watchlists screen, which is the only place lists are created
 *   and chosen. Silently picking a list for somebody is how an asset ends up on
 *   a list they never open. The button only says "Added" when the server said
 *   `ok`; a request that did not throw is not the same as a write that landed.
 *
 * - **Create alert** hands off to the canonical alert flow (see above).
 *
 * - **Ask UNDX** hands the *typed state of this screen* — asset identity, the
 *   snapshot on display, the selected chart range, watchlist/alert counts — to
 *   the canonical UNDX conversation as a structured market context envelope
 *   (see `src/undx/marketContext.ts`), then navigates there. No second chat
 *   surface, no fabricated user message full of numbers: the member arrives at
 *   an empty composer and UNDX already knows what "it" means. The server
 *   validates the envelope, persists it per conversation, and grounds crypto
 *   answers in the canonical live market layer — so answers come from live
 *   governed reads, not from whatever this screen happened to render.
 *
 * ## What is deliberately absent
 *
 * No portfolio line. The directive permits "IN PORTFOLIO" context only where the
 * existing Portfolio services already supply it, and `AssetDetail` carries no
 * holdings field — asset, memberships, alerts, ranges, market status and nothing
 * else. A holdings badge would have to come from somewhere, and there is nowhere
 * for it to come from that is not a guess.
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
  HistoryPoint,
  HistoryRange,
  UNKNOWN_VALUE,
  addWatchlistAsset,
  formatCompact,
  formatPercent,
  formatPrice,
  formatRank,
  formatSignedPrice,
  formatSupply,
  getAssetDetail,
  getAssetHistory,
  getWatchlistMarketView,
  loadCachedAssetDetail,
  loadCachedAssetHistory,
  setFavoriteAsset
} from "../api/watchlists";
import { alertConditionLabel, alertStatusLabel } from "../api/alerts";
import { buildMarketContextEnvelope, parkMarketContext } from "../undx/marketContext";
import { AssetIntelligencePanel } from "../components/crypto/AssetIntelligencePanel";
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

/**
 * The timestamp under the finger.
 *
 * `t` is the millisecond epoch the provider gave us. Short ranges get a clock,
 * long ones get a date: a 1Y chart labelled "14:35" says nothing, and a 1H chart
 * labelled "30 Aug" repeats itself sixty times.
 */
function scrubTimeLabel(t: number, range: HistoryRange): string {
  const when = new Date(t);
  if (Number.isNaN(when.getTime())) return UNKNOWN_VALUE;
  if (range === "1H" || range === "24H") {
    return when.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  if (range === "1Y" || range === "ALL") {
    return when.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
  }
  return when.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

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
  const [watchlistBusy, setWatchlistBusy] = useState(false);
  const [watchlistNotice, setWatchlistNotice] = useState("");
  // The point under the finger while scrubbing the chart. Purely a read of the
  // series already in memory: dragging never triggers a fetch.
  const [scrubbed, setScrubbed] = useState<HistoryPoint | null>(null);

  const asset = detail?.asset;
  const ranges = detail?.ranges || [];
  const alerts = detail?.alerts || [];
  const memberships = detail?.watchlists || [];
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

  // A new range means a new series, so the old crosshair reading no longer
  // describes anything on screen. Clearing it beats leaving a 24h price hovering
  // over a 1Y line.
  useEffect(() => {
    setScrubbed(null);
  }, [range, symbol]);

  async function onToggleFavorite() {
    if (!asset) return;
    try {
      await setFavoriteAsset(asset.symbol, !asset.favorite);
      await loadDetail("refresh");
    } catch (favoriteError) {
      setError(favoriteError instanceof Error ? favoriteError.message : "Favorites could not be updated.");
    }
  }

  async function onAddToWatchlist() {
    if (!asset || watchlistBusy) return;
    setWatchlistNotice("");
    // Already watched: the button is reporting a state, not offering an action.
    // Tapping it goes to the lists rather than adding a second copy.
    if (memberships.length) {
      navigation.navigate("Watchlists", { title: "Watchlists" });
      return;
    }
    setWatchlistBusy(true);
    try {
      const lists = (await getWatchlistMarketView()).watchlists || [];
      // Zero lists means there is nothing to add to; several means the choice
      // belongs to the member. Both go to the screen that owns creating and
      // choosing lists, which is the honest answer to "which one?".
      if (lists.length !== 1) {
        navigation.navigate("Watchlists", { title: "Watchlists" });
        return;
      }
      const result = await addWatchlistAsset(lists[0].id, asset.symbol);
      // A request that did not throw is not a write that landed. The server's
      // own `ok` is the only thing that turns this button into "Watching".
      if (!result.ok) {
        setWatchlistNotice(result.message || `${symbol} could not be added to your watchlist.`);
        return;
      }
      await loadDetail("refresh");
    } catch (addError) {
      setWatchlistNotice(
        addError instanceof Error ? addError.message : `${symbol} could not be added to your watchlist.`
      );
    } finally {
      setWatchlistBusy(false);
    }
  }

  /**
   * The handoff. Built from the typed state on screen right now — never from
   * rendered text. Values the screen shows as "--" travel as null, so the
   * assistant is told a figure is missing rather than handed a zero it would
   * read as a fact. Parking the envelope and navigating is synchronous and
   * cannot fail on network: the member is never blocked from reaching UNDX
   * because a context payload had a bad day.
   */
  function onAskUndx() {
    parkMarketContext(
      buildMarketContextEnvelope({
        source: "asset_detail",
        symbol,
        name: asset?.name,
        rank: asset?.market_cap_rank ?? null,
        price: asset?.price ?? null,
        change24h: asset?.change_24h ?? null,
        marketCap: asset?.market_cap ?? null,
        volume24h: asset?.volume_24h ?? null,
        snapshotSource: detail?.market?.source || history?.source || null,
        snapshotStale: Boolean(history?.stale),
        selectedRange: range,
        watchlisted: memberships.length > 0,
        alertCount: alerts.length
      })
    );
    navigation.navigate("Tabs", { screen: "PulseAI" });
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

      {/* The three things to do with a coin, in one bar, on every asset. Each
          one hands off to the system that already owns it. */}
      <View style={styles.actionBar}>
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ disabled: watchlistBusy, selected: memberships.length > 0 }}
          accessibilityLabel={
            memberships.length
              ? `${symbol} is on your watchlist. Open watchlists.`
              : `Add ${symbol} to a watchlist`
          }
          disabled={watchlistBusy}
          style={[styles.action, memberships.length ? styles.actionOn : null]}
          onPress={onAddToWatchlist}
        >
          {watchlistBusy ? (
            <ActivityIndicator color={colors.accent} />
          ) : (
            <Text style={[styles.actionLabel, memberships.length ? styles.actionLabelOn : null]}>
              {memberships.length ? "Watching ✓" : "+ Watchlist"}
            </Text>
          )}
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={
            alerts.length ? `Alerts for ${symbol}: ${alerts.length} active` : `Create an alert for ${symbol}`
          }
          style={[styles.action, alerts.length ? styles.actionOn : null]}
          // The canonical alert flow with this asset prefilled. When rules
          // already exist the bar shows how many rather than pretending the
          // member is starting from nothing.
          onPress={() => navigation.navigate("AlertManagement", { presetSymbol: symbol, title: "Alerts" })}
        >
          <Text style={[styles.actionLabel, alerts.length ? styles.actionLabelOn : null]}>
            {alerts.length ? `Alerts · ${alerts.length}` : "Create Alert"}
          </Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`Ask UNDX about ${symbol}`}
          style={styles.action}
          onPress={onAskUndx}
        >
          <Text style={styles.actionLabel}>Ask UNDX</Text>
        </Pressable>
      </View>
      {watchlistNotice ? <Text style={styles.error}>{watchlistNotice}</Text> : null}

      {/* Above the chart, because the chart is what the verdict is about and
          reading the reasoning after scrolling past the evidence inverts the
          order the mission asked for. Collapsed by default and fetched only on
          expand, so opening a coin costs exactly what it cost before. */}
      <AssetIntelligencePanel symbol={symbol} />

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
              <>
                {/* The readout keeps the height of a line whether or not a finger
                    is down, so scrubbing does not shunt the chart up and down. */}
                <View style={styles.scrubReadout}>
                  {scrubbed ? (
                    <>
                      <Text style={styles.scrubPrice}>{formatPrice(scrubbed.price)}</Text>
                      <Text style={styles.scrubTime}>{scrubTimeLabel(scrubbed.t, range)}</Text>
                    </>
                  ) : (
                    <Text style={styles.scrubTime}>Touch and drag the chart to read a point.</Text>
                  )}
                </View>
                <AssetPriceChart
                  values={chartPoints}
                  width={chartWidth}
                  color={tint}
                  // A read of `history.points`, which is already in memory. No
                  // request is made on any part of a drag.
                  onScrub={(index) =>
                    setScrubbed(index === null ? null : history?.points[index] ?? null)
                  }
                />
              </>
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
  action: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    flex: 1,
    justifyContent: "center",
    // Tall enough that swapping the label for a spinner does not resize the bar.
    minHeight: 42,
    paddingHorizontal: 8
  },
  actionBar: { flexDirection: "row", gap: 8 },
  actionLabel: { color: colors.text, fontSize: 13, fontWeight: "800", textAlign: "center" },
  actionLabelOn: { color: colors.accent },
  actionOn: { backgroundColor: colors.signalDim, borderColor: colors.accent },
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
  scrubPrice: { color: colors.text, fontSize: 15, fontWeight: "900" },
  // A fixed height so the chart does not jump when the readout appears.
  scrubReadout: { alignItems: "baseline", flexDirection: "row", gap: 8, height: 20 },
  scrubTime: { color: colors.muted, fontSize: 12 },
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
