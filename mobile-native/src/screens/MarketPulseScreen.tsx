/**
 * Market Pulse — the live crypto command center.
 *
 * ## What this screen is allowed to claim
 *
 * Every number here came from `/api/pulse/market/snapshot`, which reads the
 * shared market foundation the dashboard board and Pulse Briefings already
 * poll. Nothing is computed locally from two numbers we happen to hold, and
 * nothing missing is rendered as zero — `--` is the honest answer, and the
 * global strip shows it rather than inventing a market cap.
 *
 * The LIVE dot is drawn only when the server said `freshness.live`. The client
 * does not decide that: it cannot see the provider's age, only its own request
 * timing, and timing the request would report how long the phone waited rather
 * than how old the price is. When the answer came from the fallback provider or
 * from cache, the header says so in words instead.
 *
 * ## Why pull-to-refresh does not force anything
 *
 * It calls the same endpoint as every other read. The cache, the single-flight
 * and the credit governor it could have bypassed are precisely why one provider
 * call per window serves all users; a bypass flag would make the provider bill
 * scale with how hard people pull.
 *
 * ## Why the chips do not refetch
 *
 * All, Gainers and Losers are sorts of one shared board and Watchlist is that
 * board filtered by the caller's own symbols, so switching chips costs the
 * provider nothing. Trending is the exception and is fetched: it is CoinGecko's
 * own search-trending signal, not the biggest movers, and deriving it from price
 * would put a claim on screen the provider never made.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ActivityIndicator,
  AppState,
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import {
  MARKET_CATEGORIES,
  MarketAsset,
  MarketCategory,
  MarketSnapshot,
  UNKNOWN,
  formatAge,
  formatBigMoney,
  formatDominance,
  getMarketSnapshot,
  loadCachedMarketSnapshot,
  searchMarketAssets
} from "../api/marketPulse";
import { MarketRegime, MarketRotation, formatPlainPct } from "../api/marketIntelligence";
import { formatPercent, formatPrice } from "../api/watchlists";
import { AssetSparkline } from "../components/crypto/AssetSparkline";
import { PremiumFeatureGate } from "../entitlements/PremiumFeatureGate";
import { IntelligenceBadge } from "../components/crypto/IntelligenceBadge";
import { Panel } from "../components/Panel";
import { useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { premiumTheme } from "../theme/premiumTheme";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "MarketPulse">;

/**
 * How often the board is re-read while the screen is in front.
 *
 * Longer than the server's own cache window on purpose: a shorter interval
 * would only ask the same cached answer more often, spending battery and
 * bridge time to receive bytes that have not changed.
 */
const FOREGROUND_REFRESH_MS = 60_000;

/** Green up, red down, muted when unknown — never green by default. */
function changeColor(change: number | null): string {
  if (change === null) return colors.muted;
  if (change > 0) return colors.accent;
  if (change < 0) return colors.danger;
  return colors.muted;
}

export function MarketPulseScreen(props: Props) {
  return (
    <PremiumFeatureGate onUpgrade={() => props.navigation.navigate("Premium")}>
      <MarketPulseScreenBody {...props} />
    </PremiumFeatureGate>
  );
}

function MarketPulseScreenBody({ navigation, route }: Props) {
  const { t } = useTranslation();
  const translate = useRef(t);
  translate.current = t;

  const [category, setCategory] = useState<MarketCategory>(
    (MARKET_CATEGORIES as readonly string[]).includes(String(route.params?.category))
      ? (route.params?.category as MarketCategory)
      : "all"
  );
  const [snapshot, setSnapshot] = useState<MarketSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [fromCache, setFromCache] = useState(false);
  const [error, setError] = useState("");

  const [query, setQuery] = useState("");
  const [results, setResults] = useState<MarketAsset[] | null>(null);
  const [searching, setSearching] = useState(false);

  const load = useCallback(
    async (next: MarketCategory, mode: "initial" | "refresh" = "initial") => {
      if (mode === "refresh") setRefreshing(true);
      try {
        const payload = await getMarketSnapshot(next);
        setSnapshot(payload);
        setFromCache(false);
        setError("");
      } catch (loadError) {
        // A provider outage must not empty the screen. The cached rows were true
        // when written, so they are shown — labelled, and never as "live".
        const cached = await loadCachedMarketSnapshot(next).catch(() => null);
        if (cached) {
          setSnapshot(cached);
          setFromCache(true);
        }
        setError(
          loadError instanceof Error && loadError.message
            ? loadError.message
            : translate.current("discovery:crypto.marketPulse.loadError")
        );
      } finally {
        setLoading(false);
        setRefreshing(false);
      }
    },
    []
  );

  // Cache first, then revalidate: a warm start paints instantly rather than
  // showing a spinner over data we already hold.
  useEffect(() => {
    let active = true;
    setLoading(true);
    loadCachedMarketSnapshot(category)
      .then((cached) => {
        if (active && cached) {
          setSnapshot(cached);
          setFromCache(true);
          setLoading(false);
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (active) load(category, "initial").catch(() => undefined);
      });
    return () => {
      active = false;
    };
  }, [category, load]);

  /**
   * Refresh while in front, stop when backgrounded.
   *
   * A timer that survives backgrounding would keep polling a screen nobody is
   * looking at, and would land the user on numbers timestamped from whenever
   * the last tick happened to fire rather than from when they returned.
   */
  useEffect(() => {
    let timer: ReturnType<typeof setInterval> | null = null;
    const start = () => {
      if (timer) return;
      timer = setInterval(() => load(category, "refresh").catch(() => undefined), FOREGROUND_REFRESH_MS);
    };
    const stop = () => {
      if (timer) clearInterval(timer);
      timer = null;
    };
    start();
    const subscription = AppState.addEventListener("change", (state) => {
      if (state === "active") {
        load(category, "refresh").catch(() => undefined);
        start();
      } else {
        stop();
      }
    });
    return () => {
      stop();
      subscription.remove();
    };
  }, [category, load]);

  // Search resolves server-side against the same board that supplies prices, so
  // every hit is openable. Debounced because a request per keystroke would be a
  // request per keystroke.
  useEffect(() => {
    const needle = query.trim();
    if (needle.length < 2) {
      setResults(null);
      setSearching(false);
      return;
    }
    let active = true;
    setSearching(true);
    const timer = setTimeout(() => {
      searchMarketAssets(needle)
        .then((assets) => {
          if (active) setResults(assets);
        })
        .catch(() => {
          if (active) setResults([]);
        })
        .finally(() => {
          if (active) setSearching(false);
        });
    }, 300);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [query]);

  const openAsset = useCallback(
    (asset: MarketAsset) => {
      navigation.navigate("AssetDetail", { symbol: asset.symbol, name: asset.name });
    },
    [navigation]
  );

  /**
   * `pulsesoc://crypto/bitcoin` — resolve the token, then open the coin.
   *
   * The token in a link may be a ticker, a CoinGecko id or a name, and only the
   * server knows which of those maps to a priceable asset. Resolving it here
   * with a local table would be the second symbol resolver in the product, and
   * the two would eventually disagree. So the link is resolved against the same
   * board that supplies the prices, and an exact match opens the asset while
   * anything else quietly leaves the user on the market — a mistyped or retired
   * link should not open a detail screen that can only read "Unavailable".
   */
  const deepLinkToken = route.params?.openAsset;
  const resolvedLink = useRef("");
  useEffect(() => {
    const token = String(deepLinkToken || "").trim();
    if (!token || resolvedLink.current === token) return;
    resolvedLink.current = token;
    let active = true;
    searchMarketAssets(token)
      .then((matches) => {
        if (!active) return;
        const needle = token.toLowerCase();
        const exact = matches.find(
          (asset) =>
            asset.symbol.toLowerCase() === needle ||
            asset.id.toLowerCase() === needle ||
            asset.name.toLowerCase() === needle
        );
        if (exact) navigation.navigate("AssetDetail", { symbol: exact.symbol, name: exact.name });
      })
      .catch(() => undefined);
    return () => {
      active = false;
    };
  }, [deepLinkToken, navigation]);

  const assets = results ?? snapshot?.assets ?? [];
  const freshness = snapshot?.freshness;
  const metrics = snapshot?.global;

  if (loading && !snapshot) {
    return (
      <View style={styles.loadingRoot}>
        <ActivityIndicator color={premiumTheme.gold} />
      </View>
    );
  }

  return (
    <FlatList
      style={styles.root}
      contentContainerStyle={styles.content}
      data={assets}
      keyExtractor={(asset) => `${asset.id}:${asset.symbol}`}
      refreshControl={
        <RefreshControl
          refreshing={refreshing}
          onRefresh={() => load(category, "refresh")}
          tintColor={premiumTheme.gold}
        />
      }
      ListHeaderComponent={
        <View style={styles.header}>
          <Panel>
            <View style={styles.stripHead}>
              <Text style={styles.stripTitle}>{t("discovery:crypto.marketPulse.globalTitle")}</Text>
              <FreshnessBadge
                live={Boolean(freshness?.live)}
                label={
                  fromCache
                    ? t("discovery:crypto.marketPulse.cached")
                    : freshness?.live
                      ? t("discovery:crypto.marketPulse.live")
                      : t("discovery:crypto.marketPulse.delayed")
                }
              />
            </View>
            <View style={styles.metrics}>
              <Metric label={t("discovery:crypto.marketPulse.totalCap")} value={formatBigMoney(metrics?.totalMarketCap ?? null)} />
              <Metric label={t("discovery:crypto.marketPulse.volume24h")} value={formatBigMoney(metrics?.totalVolume24h ?? null)} />
              <Metric label={t("discovery:crypto.marketPulse.btcDominance")} value={formatDominance(metrics?.btcDominance ?? null)} />
              <Metric label={t("discovery:crypto.marketPulse.ethDominance")} value={formatDominance(metrics?.ethDominance ?? null)} />
              <Metric
                label={t("discovery:crypto.marketPulse.marketChange")}
                value={formatPercent(metrics?.marketCapChange24hPct ?? null)}
                tint={changeColor(metrics?.marketCapChange24hPct ?? null)}
              />
            </View>
            {/* Age, not "just now": the server measures it from when the provider
                answered, so a cache hit cannot reset this label to zero. */}
            {formatAge(freshness?.ageSeconds ?? null) ? (
              <Text style={styles.muted}>{formatAge(freshness?.ageSeconds ?? null)}</Text>
            ) : null}
            {freshness?.degraded ? (
              <Text style={styles.warning}>
                {freshness.warning || t("discovery:crypto.marketPulse.partial")}
              </Text>
            ) : null}
            {/* One line, one tap. The regime is genuinely useful context for
                every row below it, but it is context — it does not get a card
                of its own above the board it describes. */}
            <RegimeStrip regime={snapshot?.regime ?? null} rotation={snapshot?.rotation ?? null} />
            {snapshot && !snapshot.personalized ? (
              <Text style={styles.muted}>{t("discovery:crypto.marketPulse.overlayUnavailable")}</Text>
            ) : null}
            {error ? <Text style={styles.error}>{error}</Text> : null}
          </Panel>

          <TextInput
            style={styles.search}
            value={query}
            onChangeText={setQuery}
            placeholder={t("discovery:crypto.marketPulse.searchPlaceholder")}
            placeholderTextColor={colors.muted}
            autoCapitalize="characters"
            autoCorrect={false}
            accessibilityLabel={t("discovery:crypto.marketPulse.searchPlaceholder")}
            returnKeyType="search"
          />

          {results ? (
            <Text style={styles.muted}>
              {searching
                ? t("discovery:crypto.marketPulse.searching")
                : t("discovery:crypto.marketPulse.searchResults", { count: results.length })}
            </Text>
          ) : (
            <View style={styles.chips}>
              {MARKET_CATEGORIES.map((key) => (
                <Pressable
                  key={key}
                  accessibilityRole="button"
                  accessibilityState={{ selected: category === key }}
                  accessibilityLabel={t(`discovery:crypto.marketPulse.category.${key}`)}
                  style={[styles.chip, category === key ? styles.chipActive : null]}
                  onPress={() => setCategory(key)}
                >
                  <Text style={[styles.chipLabel, category === key ? styles.chipLabelActive : null]}>
                    {t(`discovery:crypto.marketPulse.category.${key}`)}
                  </Text>
                </Pressable>
              ))}
            </View>
          )}
        </View>
      }
      ListEmptyComponent={
        <Panel>
          <Text style={styles.muted}>
            {results
              ? t("discovery:crypto.marketPulse.noMatches")
              : category === "watchlist"
                ? t("discovery:crypto.marketPulse.emptyWatchlist")
                : t("discovery:crypto.marketPulse.empty")}
          </Text>
        </Panel>
      }
      renderItem={({ item }) => <MarketRow asset={item} onPress={openAsset} />}
    />
  );
}

function FreshnessBadge({ live, label }: { live: boolean; label: string }) {
  return (
    <View style={styles.badge}>
      <View style={[styles.dot, live ? styles.dotLive : styles.dotStale]} />
      <Text style={[styles.badgeLabel, live ? styles.badgeLabelLive : null]}>{label}</Text>
    </View>
  );
}

/**
 * The market regime, in one line.
 *
 * Colour follows the state rather than the breadth number, because a
 * risk-off market with 51% advancers is still risk-off and a green chip
 * would say the opposite of the words next to it. The detail sentence is the
 * server's own, so the reason shown here can never drift from the reason the
 * scoring engine actually used.
 *
 * Renders nothing when the board was too small to measure breadth — an
 * unmeasured regime is not a neutral one.
 */
function RegimeStrip({ regime, rotation }: { regime: MarketRegime | null; rotation: MarketRotation | null }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  if (!regime || !regime.state || !regime.label) return null;
  const tint =
    regime.state === "RISK_ON" ? colors.accent : regime.state === "RISK_OFF" ? colors.danger : colors.warning;
  return (
    <View style={styles.regimeWrap}>
      <Pressable
        accessibilityRole="button"
        accessibilityState={{ expanded: open }}
        accessibilityLabel={`${t("discovery:crypto.marketPulse.regime.title")} ${regime.label}`}
        style={({ pressed }) => [styles.regimeHead, pressed && styles.pressed]}
        onPress={() => setOpen((value) => !value)}
      >
        <View style={[styles.regimeDot, { backgroundColor: tint }]} />
        <Text style={[styles.regimeLabel, { color: tint }]}>{regime.label}</Text>
        {regime.breadthPct !== null ? (
          <Text style={styles.muted}>
            {t("discovery:crypto.marketPulse.regime.breadth", { pct: formatPlainPct(regime.breadthPct) })}
          </Text>
        ) : null}
        <Text style={styles.regimeHint}>{t("discovery:crypto.marketPulse.regime.hint")}</Text>
      </Pressable>
      {open ? (
        <View style={styles.regimeBody}>
          <Text style={styles.muted}>{regime.detail}</Text>
          {regime.basis ? <Text style={styles.regimeBasis}>{regime.basis}</Text> : null}
          {/* Rotation is only shown when the board actually grouped into
              something — a "leader" among one group is not a rotation. */}
          {rotation && rotation.leader && rotation.groups.length > 1 ? (
            <Text style={styles.muted}>
              {t("discovery:crypto.marketPulse.regime.leader", { group: rotation.leader })}
            </Text>
          ) : null}
        </View>
      ) : null}
    </View>
  );
}

function Metric({ label, value, tint }: { label: string; value: string; tint?: string }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricLabel}>{label}</Text>
      <Text style={[styles.metricValue, tint ? { color: tint } : null]}>{value}</Text>
    </View>
  );
}

function MarketRow({ asset, onPress }: { asset: MarketAsset; onPress: (asset: MarketAsset) => void }) {
  const tint = changeColor(asset.change24h);
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={`${asset.name} ${asset.symbol}`}
      style={({ pressed }) => [styles.row, pressed && styles.pressed]}
      onPress={() => onPress(asset)}
    >
      <Text style={styles.rank}>{asset.rank === null ? UNKNOWN : `${asset.rank}`}</Text>
      {asset.image ? (
        <Image source={{ uri: asset.image }} style={styles.logo} accessibilityIgnoresInvertColors />
      ) : (
        <View style={styles.logo} />
      )}
      <View style={styles.identity}>
        <View style={styles.identityHead}>
          <Text style={styles.symbol}>{asset.symbol}</Text>
          {asset.watching ? <Text style={styles.watching}>●</Text> : null}
          {asset.favorite ? <Text style={styles.star}>★</Text> : null}
          {asset.alertCount > 0 ? <Text style={styles.alertBadge}>{asset.alertCount}</Text> : null}
        </View>
        <Text style={styles.name} numberOfLines={1}>
          {asset.name}
        </Text>
      </View>
      <AssetSparkline values={asset.sparkline} color={tint} />
      <View style={styles.priceBlock}>
        <Text style={styles.price}>{formatPrice(asset.price)}</Text>
        <Text style={[styles.change, { color: tint }]}>{formatPercent(asset.change24h)}</Text>
        {/* Beneath the price, never instead of it. The price is the fact this
            row exists to show; the verdict is an annotation on it, and a row
            with no analysis simply renders nothing here. */}
        <IntelligenceBadge intelligence={asset.intelligence} />
      </View>
    </Pressable>
  );
}

const styles = createThemedStyles(() => ({
  alertBadge: {
    backgroundColor: colors.warningSoft,
    borderRadius: 8,
    color: colors.warning,
    fontSize: 10,
    fontWeight: "900",
    overflow: "hidden",
    paddingHorizontal: 5,
    paddingVertical: 1
  },
  badge: { alignItems: "center", flexDirection: "row", gap: 5 },
  badgeLabel: { color: colors.muted, fontSize: 11, fontWeight: "800", letterSpacing: 0.4 },
  badgeLabelLive: { color: colors.accent },
  change: { fontSize: 12, fontWeight: "800" },
  chip: {
    borderColor: colors.border,
    borderRadius: premiumTheme.radius.chip,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 6
  },
  chipActive: { backgroundColor: premiumTheme.goldSoft, borderColor: premiumTheme.goldBorder },
  chipLabel: { color: colors.muted, fontSize: 12, fontWeight: "800" },
  chipLabelActive: { color: premiumTheme.gold },
  chips: { flexDirection: "row", flexWrap: "wrap", gap: 6 },
  content: { gap: 8, padding: 16, paddingBottom: 48 },
  dot: { borderRadius: 4, height: 8, width: 8 },
  dotLive: { backgroundColor: colors.accent },
  dotStale: { backgroundColor: colors.muted },
  error: { color: colors.danger, fontSize: 13, lineHeight: 19 },
  header: { gap: 10, paddingBottom: 4 },
  identity: { flex: 1, gap: 2 },
  identityHead: { alignItems: "center", flexDirection: "row", gap: 5 },
  loadingRoot: { alignItems: "center", flex: 1, justifyContent: "center" },
  logo: { backgroundColor: colors.surface, borderRadius: 12, height: 24, width: 24 },
  metric: { gap: 2, minWidth: 92 },
  metricLabel: { color: colors.muted, fontSize: 11, fontWeight: "700" },
  metricValue: { color: colors.text, fontSize: 15, fontWeight: "900" },
  metrics: { flexDirection: "row", flexWrap: "wrap", gap: 12 },
  muted: { color: colors.muted, fontSize: 12, lineHeight: 18 },
  name: { color: colors.muted, fontSize: 12 },
  presetPressed: { opacity: 0.7 },
  pressed: { opacity: 0.7 },
  price: { color: colors.text, fontSize: 14, fontWeight: "900" },
  priceBlock: { alignItems: "flex-end", gap: 2, minWidth: 92 },
  rank: { color: colors.muted, fontSize: 11, fontWeight: "800", minWidth: 22 },
  regimeBasis: { color: colors.muted, fontSize: 11, fontStyle: "italic" },
  regimeBody: { gap: 4, paddingLeft: 14 },
  regimeDot: { borderRadius: 4, height: 8, width: 8 },
  regimeHead: { alignItems: "center", flexDirection: "row", gap: 6 },
  regimeHint: { color: premiumTheme.gold, fontSize: 11, fontWeight: "800" },
  regimeLabel: { fontSize: 12, fontWeight: "900", letterSpacing: 0.3 },
  regimeWrap: { gap: 4 },
  root: { backgroundColor: "transparent", flex: 1 },
  row: {
    alignItems: "center",
    borderBottomColor: colors.border,
    borderBottomWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 10,
    paddingVertical: 10
  },
  search: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 10,
    borderWidth: 1,
    color: colors.text,
    fontSize: 14,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  star: { color: colors.warning, fontSize: 12 },
  stripHead: { alignItems: "center", flexDirection: "row", justifyContent: "space-between" },
  stripTitle: { color: colors.text, fontSize: 15, fontWeight: "900" },
  symbol: { color: colors.text, fontSize: 14, fontWeight: "900" },
  warning: { color: colors.warning, fontSize: 12, lineHeight: 18 },
  watching: { color: premiumTheme.gold, fontSize: 10 }
}));
