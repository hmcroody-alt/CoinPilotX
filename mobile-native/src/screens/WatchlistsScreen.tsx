/**
 * Watchlists — the crypto market tracking workspace.
 *
 * ## Which tabs exist, and why the obvious ones are missing
 *
 * There is one tab per group this product can actually answer: the user's
 * watchlists, and their favourites. There is no Stocks tab because there is no
 * equities provider behind it — a tab that could only ever render "--" is worse
 * than no tab, and one populated with plausible-looking numbers would be a lie.
 * There is no separate Crypto tab because every asset here is crypto already,
 * so it would be the same list under a second name.
 *
 * ## Cached-first
 *
 * The cache is painted before the request goes out, so opening this screen on a
 * warm start is immediate. Cached prices were true when they were written and
 * are never edited, but they are old — hence the "as of" line, which the fresh
 * response overwrites. The rule that governs this: a cached number may be
 * *displayed*, never *trusted*.
 *
 * ## One request for the whole board
 *
 * `/api/crypto/watchlists/market` returns every list already joined to a single
 * market snapshot. Fetching per row would multiply provider calls by the number
 * of assets and get the API key rate-limited by anyone with a long list.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  Alert,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import {
  AssetQuote,
  UNKNOWN_VALUE,
  Watchlist,
  WatchlistAsset,
  WatchlistMarketView,
  addWatchlistAsset,
  createWatchlist,
  deleteWatchlist,
  formatCompact,
  formatPercent,
  formatPrice,
  getWatchlistMarketView,
  loadCachedWatchlistMarketView,
  removeWatchlistAsset,
  renameWatchlist,
  searchAssets,
  setFavoriteAsset
} from "../api/watchlists";
import { AssetSparkline } from "../components/crypto/AssetSparkline";
import { Panel } from "../components/Panel";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "Watchlists">;

type TabKey = "lists" | "favorites";

const TABS: { key: TabKey; label: string }[] = [
  { key: "lists", label: "My Watchlists" },
  { key: "favorites", label: "Favorites" }
];

/** Green up, red down, neutral when we do not know — never green-by-default. */
function changeColor(change: number | null): string {
  if (change === null) return colors.muted;
  if (change > 0) return colors.accent;
  if (change < 0) return colors.danger;
  return colors.muted;
}

export function WatchlistsScreen({ navigation }: Props) {
  const [view, setView] = useState<WatchlistMarketView | null>(null);
  const [tab, setTab] = useState<TabKey>("lists");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState("");

  const [newListName, setNewListName] = useState("");
  const [renamingId, setRenamingId] = useState<number | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [addingToId, setAddingToId] = useState<number | null>(null);
  const [assetQuery, setAssetQuery] = useState("");
  const [results, setResults] = useState<AssetQuote[]>([]);

  const watchlists = view?.watchlists || [];
  const favorites = view?.favorites || [];
  const market = view?.market;

  const refresh = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    if (mode === "refresh") setRefreshing(true);
    try {
      const next = await getWatchlistMarketView();
      setView(next);
      setOffline(false);
      setError("");
    } catch (loadError) {
      // A market outage must not empty the screen: the lists themselves are our
      // own data, and the last real prices are still worth showing as long as
      // they are labelled old.
      const cached = await loadCachedWatchlistMarketView();
      if (cached) {
        setView(cached);
        setOffline(true);
      }
      setError(loadError instanceof Error ? loadError.message : "Watchlists could not load.");
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    let active = true;
    // Paint the cache first, then revalidate. The cached view is replaced
    // wholesale by the server's answer; nothing is merged, so a removed asset
    // cannot linger.
    loadCachedWatchlistMarketView()
      .then((cached) => {
        if (active && cached) {
          setView(cached);
          setLoading(false);
        }
      })
      .catch(() => undefined)
      .finally(() => {
        if (active) refresh("initial").catch(() => undefined);
      });
    return () => {
      active = false;
    };
  }, [refresh]);

  useEffect(() => {
    const query = assetQuery.trim();
    if (addingToId === null || query.length < 1) {
      setResults([]);
      return;
    }
    let active = true;
    const timer = setTimeout(() => {
      searchAssets(query, 20)
        .then((assets) => {
          if (active) setResults(assets);
        })
        .catch(() => {
          if (active) setResults([]);
        });
    }, 220);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [assetQuery, addingToId]);

  async function run(key: string, action: () => Promise<{ ok?: boolean; message?: string }>, success: string) {
    setBusy(key);
    setError("");
    setNotice("");
    try {
      const result = await action();
      if (!result.ok) {
        setError(result.message || "That did not work.");
        return false;
      }
      setNotice(result.message || success);
      await refresh("refresh");
      return true;
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : "That did not work.");
      return false;
    } finally {
      setBusy("");
    }
  }

  async function onCreate() {
    const name = newListName.trim();
    if (!name) {
      setError("Name your watchlist first.");
      return;
    }
    const ok = await run("create", () => createWatchlist(name), "Watchlist created.");
    if (ok) setNewListName("");
  }

  async function onRename(watchlistId: number) {
    const name = renameValue.trim();
    if (!name) {
      setError("A watchlist needs a name.");
      return;
    }
    const ok = await run(`rename-${watchlistId}`, () => renameWatchlist(watchlistId, name), "Watchlist renamed.");
    if (ok) {
      setRenamingId(null);
      setRenameValue("");
    }
  }

  function confirmDelete(watchlist: Watchlist) {
    Alert.alert(
      "Delete watchlist",
      // Says plainly what survives. Deleting a list the user built is worth one
      // tap of friction, and the alerts question is the one they will ask.
      `Delete "${watchlist.name}"? Its assets are removed from this list. Your alerts and favorites are kept.`,
      [
        { text: "Cancel", style: "cancel" },
        {
          text: "Delete",
          style: "destructive",
          onPress: () => {
            run(`delete-${watchlist.id}`, () => deleteWatchlist(watchlist.id), "Watchlist deleted.").catch(
              () => undefined
            );
          }
        }
      ]
    );
  }

  async function onAddAsset(watchlistId: number, symbol: string) {
    const ok = await run(`add-${watchlistId}`, () => addWatchlistAsset(watchlistId, symbol), `${symbol} added.`);
    if (ok) {
      setAssetQuery("");
      setResults([]);
    }
  }

  async function onToggleFavorite(asset: WatchlistAsset) {
    await run(`fav-${asset.symbol}`, () => setFavoriteAsset(asset.symbol, !asset.favorite), "Favorites updated.");
  }

  const asOf = useMemo(() => {
    if (!market?.updated_at) return "";
    return market.updated_at.replace("T", " ");
  }, [market]);

  function renderAsset(asset: WatchlistAsset, watchlistId: number) {
    const tint = changeColor(asset.change_24h);
    return (
      <View key={`${watchlistId}-${asset.id}`} style={styles.assetRow}>
        <Pressable
          style={styles.assetMain}
          accessibilityRole="button"
          accessibilityLabel={`${asset.name}, ${formatPrice(asset.price)}`}
          onPress={() => navigation.navigate("AssetDetail", { symbol: asset.symbol, name: asset.name })}
        >
          <View style={styles.assetIdentity}>
            <Text style={styles.assetSymbol}>{asset.symbol}</Text>
            <Text style={styles.assetName} numberOfLines={1}>
              {asset.name}
            </Text>
            <Text style={styles.assetMeta}>
              {asset.market_cap === null ? UNKNOWN_VALUE : `Cap ${formatCompact(asset.market_cap)}`}
            </Text>
          </View>
          <AssetSparkline values={asset.sparkline} color={tint} />
          <View style={styles.assetPrices}>
            <Text style={styles.assetPrice}>{formatPrice(asset.price)}</Text>
            <Text style={[styles.assetChange, { color: tint }]}>{formatPercent(asset.change_24h)}</Text>
          </View>
        </Pressable>
        <View style={styles.assetActions}>
          {asset.alert_count > 0 ? (
            <Text style={styles.alertBadge} accessibilityLabel={`${asset.alert_count} alerts`}>
              {asset.alert_count === 1 ? "1 alert" : `${asset.alert_count} alerts`}
            </Text>
          ) : null}
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={asset.favorite ? `Unfavorite ${asset.symbol}` : `Favorite ${asset.symbol}`}
            onPress={() => onToggleFavorite(asset)}
          >
            <Text style={[styles.iconAction, asset.favorite ? styles.iconActionOn : null]}>
              {asset.favorite ? "★" : "☆"}
            </Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={`Remove ${asset.symbol}`}
            onPress={() =>
              run(`remove-${asset.id}`, () => removeWatchlistAsset(watchlistId, asset.id), `${asset.symbol} removed.`)
            }
          >
            <Text style={styles.removeAction}>Remove</Text>
          </Pressable>
        </View>
      </View>
    );
  }

  function renderWatchlist(watchlist: Watchlist) {
    const aggregate = watchlist.average_change_24h;
    const tint = changeColor(aggregate);
    const isRenaming = renamingId === watchlist.id;
    const isAdding = addingToId === watchlist.id;

    return (
      <Panel key={watchlist.id}>
        <View style={styles.cardHead}>
          <View style={styles.cardTitleBlock}>
            {isRenaming ? (
              <TextInput
                style={styles.input}
                value={renameValue}
                onChangeText={setRenameValue}
                autoFocus
                placeholder="Watchlist name"
                placeholderTextColor={colors.muted}
                accessibilityLabel="Watchlist name"
              />
            ) : (
              <Text style={styles.cardTitle}>{watchlist.name}</Text>
            )}
            <Text style={styles.muted}>
              {watchlist.asset_count === 1 ? "1 asset" : `${watchlist.asset_count} assets`}
              {/* Only ever averaged over assets we have a real price for, so an
                  unpriced row cannot drag the summary toward zero. */}
              {aggregate === null ? "" : ` · avg ${formatPercent(aggregate)}`}
            </Text>
          </View>
          <Text style={[styles.cardAggregate, { color: tint }]}>{formatPercent(aggregate)}</Text>
        </View>

        <View style={styles.cardActions}>
          {isRenaming ? (
            <>
              <Pressable accessibilityRole="button" onPress={() => onRename(watchlist.id)}>
                <Text style={styles.action}>Save</Text>
              </Pressable>
              <Pressable accessibilityRole="button" onPress={() => setRenamingId(null)}>
                <Text style={styles.actionMuted}>Cancel</Text>
              </Pressable>
            </>
          ) : (
            <>
              <Pressable
                accessibilityRole="button"
                onPress={() => {
                  setRenamingId(watchlist.id);
                  setRenameValue(watchlist.name);
                }}
              >
                <Text style={styles.action}>Rename</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                onPress={() => {
                  setAddingToId(isAdding ? null : watchlist.id);
                  setAssetQuery("");
                  setResults([]);
                }}
              >
                <Text style={styles.action}>{isAdding ? "Done" : "Add asset"}</Text>
              </Pressable>
              <Pressable accessibilityRole="button" onPress={() => confirmDelete(watchlist)}>
                <Text style={styles.actionDanger}>Delete</Text>
              </Pressable>
            </>
          )}
        </View>

        {isAdding ? (
          <View style={styles.addBlock}>
            <TextInput
              style={styles.input}
              value={assetQuery}
              onChangeText={setAssetQuery}
              autoCapitalize="characters"
              placeholder="Search assets, e.g. BTC"
              placeholderTextColor={colors.muted}
              accessibilityLabel="Search assets"
            />
            {/* Search only offers assets the price engine can actually quote, so
                a user cannot add a row that would permanently read "--". */}
            {results.map((asset) => (
              <Pressable
                key={asset.symbol}
                style={styles.resultRow}
                accessibilityRole="button"
                onPress={() => onAddAsset(watchlist.id, asset.symbol)}
              >
                <Text style={styles.resultSymbol}>{asset.symbol}</Text>
                <Text style={styles.resultName} numberOfLines={1}>
                  {asset.name}
                </Text>
                <Text style={styles.resultPrice}>{formatPrice(asset.price)}</Text>
              </Pressable>
            ))}
            {assetQuery.trim() && !results.length ? (
              <Text style={styles.muted}>No tracked asset matches that.</Text>
            ) : null}
          </View>
        ) : null}

        {watchlist.assets.length ? (
          watchlist.assets.map((asset) => renderAsset(asset, watchlist.id))
        ) : (
          <Text style={styles.muted}>No assets yet. Use “Add asset” to track something.</Text>
        )}
      </Panel>
    );
  }

  if (loading && !view) {
    return (
      <View style={styles.loadingRoot}>
        <ActivityIndicator color={colors.accent} />
      </View>
    );
  }

  return (
    <ScrollView
      style={styles.root}
      contentContainerStyle={styles.content}
      refreshControl={
        <RefreshControl refreshing={refreshing} onRefresh={() => refresh("refresh")} tintColor={colors.accent} />
      }
    >
      <View style={styles.header}>
        <Text style={styles.title}>Watchlists</Text>
        <Text style={styles.subtitle}>Track the assets you care about with live market data.</Text>
      </View>

      {/* Honest provenance line. When the provider is degraded this is the only
          thing distinguishing old real numbers from current ones. */}
      {market && !market.ready ? (
        <Text style={styles.warning}>{market.warning || "Live prices are temporarily unavailable."}</Text>
      ) : null}
      {offline ? <Text style={styles.warning}>Offline — showing the last prices we received.</Text> : null}
      {asOf ? <Text style={styles.muted}>Prices as of {asOf}</Text> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {notice ? <Text style={styles.notice}>{notice}</Text> : null}

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

      {tab === "lists" ? (
        <>
          <Panel>
            <Text style={styles.sectionTitle}>Create watchlist</Text>
            <TextInput
              style={styles.input}
              value={newListName}
              onChangeText={setNewListName}
              placeholder="e.g. My Crypto"
              placeholderTextColor={colors.muted}
              accessibilityLabel="New watchlist name"
            />
            <Pressable
              accessibilityRole="button"
              style={styles.primaryButton}
              disabled={busy === "create"}
              onPress={onCreate}
            >
              <Text style={styles.primaryButtonLabel}>{busy === "create" ? "Creating…" : "Create Watchlist"}</Text>
            </Pressable>
          </Panel>

          {watchlists.length ? (
            watchlists.map(renderWatchlist)
          ) : (
            <Panel>
              <Text style={styles.sectionTitle}>No watchlists yet</Text>
              <Text style={styles.muted}>
                Create one above to start tracking prices, 24h moves and alerts for the assets you follow.
              </Text>
            </Panel>
          )}
        </>
      ) : (
        <Panel>
          <Text style={styles.sectionTitle}>Favorites</Text>
          {favorites.length ? (
            favorites.map((asset) => renderAsset(asset, asset.watchlist_id))
          ) : (
            <Text style={styles.muted}>Star an asset to keep it here. Favorites follow your account.</Text>
          )}
        </Panel>
      )}
    </ScrollView>
  );
}

const styles = createThemedStyles(() => ({
  action: { color: colors.accentStrong, fontSize: 13, fontWeight: "700" },
  actionDanger: { color: colors.danger, fontSize: 13, fontWeight: "700" },
  actionMuted: { color: colors.muted, fontSize: 13, fontWeight: "700" },
  addBlock: { gap: 8 },
  alertBadge: {
    backgroundColor: colors.warningSoft,
    borderRadius: 6,
    color: colors.warning,
    fontSize: 11,
    fontWeight: "800",
    paddingHorizontal: 6,
    paddingVertical: 2
  },
  assetActions: { alignItems: "center", flexDirection: "row", gap: 12, justifyContent: "flex-end" },
  assetChange: { fontSize: 13, fontWeight: "800" },
  assetIdentity: { flex: 1, gap: 2 },
  assetMain: { alignItems: "center", flexDirection: "row", gap: 10 },
  assetMeta: { color: colors.muted, fontSize: 11 },
  assetName: { color: colors.muted, fontSize: 12 },
  assetPrice: { color: colors.text, fontSize: 14, fontWeight: "800" },
  assetPrices: { alignItems: "flex-end", minWidth: 92 },
  assetRow: {
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    gap: 8,
    paddingTop: 10
  },
  assetSymbol: { color: colors.text, fontSize: 15, fontWeight: "900" },
  cardActions: { flexDirection: "row", gap: 16 },
  cardAggregate: { fontSize: 16, fontWeight: "900" },
  cardHead: { alignItems: "flex-start", flexDirection: "row", gap: 10, justifyContent: "space-between" },
  cardTitle: { color: colors.text, fontSize: 17, fontWeight: "900" },
  cardTitleBlock: { flex: 1, gap: 3 },
  content: { gap: 12, padding: 16, paddingBottom: 48 },
  error: { color: colors.danger, fontSize: 13, lineHeight: 19 },
  header: { gap: 4 },
  iconAction: { color: colors.muted, fontSize: 18 },
  iconActionOn: { color: colors.warning },
  input: {
    backgroundColor: colors.background,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    minHeight: 44,
    paddingHorizontal: 12
  },
  loadingRoot: { alignItems: "center", flex: 1, justifyContent: "center" },
  muted: { color: colors.muted, fontSize: 13, lineHeight: 19 },
  notice: { color: colors.accent, fontSize: 13, lineHeight: 19 },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingVertical: 12
  },
  primaryButtonLabel: { color: colors.background, fontSize: 14, fontWeight: "900" },
  removeAction: { color: colors.danger, fontSize: 12, fontWeight: "700" },
  resultName: { color: colors.muted, flex: 1, fontSize: 12 },
  resultPrice: { color: colors.text, fontSize: 13, fontWeight: "700" },
  resultRow: {
    alignItems: "center",
    borderTopColor: colors.border,
    borderTopWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 10,
    paddingVertical: 8
  },
  resultSymbol: { color: colors.text, fontSize: 14, fontWeight: "900", minWidth: 56 },
  root: { backgroundColor: "transparent", flex: 1 },
  sectionTitle: { color: colors.text, fontSize: 15, fontWeight: "900" },
  subtitle: { color: colors.muted, fontSize: 13, lineHeight: 19 },
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
  title: { color: colors.text, fontSize: 24, fontWeight: "900" },
  warning: { color: colors.warning, fontSize: 13, lineHeight: 19 }
}));
