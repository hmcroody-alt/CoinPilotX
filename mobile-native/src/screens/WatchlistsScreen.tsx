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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
import { PremiumFeatureGate } from "../entitlements/PremiumFeatureGate";
import { useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "Watchlists">;

type TabKey = "lists" | "favorites";

const TAB_KEYS: TabKey[] = ["lists", "favorites"];

/** Green up, red down, neutral when we do not know — never green-by-default. */
function changeColor(change: number | null): string {
  if (change === null) return colors.muted;
  if (change > 0) return colors.accent;
  if (change < 0) return colors.danger;
  return colors.muted;
}

export function WatchlistsScreen(props: Props) {
  return (
    <PremiumFeatureGate onUpgrade={() => props.navigation.navigate("Premium")}>
      <WatchlistsScreenBody {...props} />
    </PremiumFeatureGate>
  );
}

function WatchlistsScreenBody({ navigation }: Props) {
  const { t } = useTranslation();
  /**
   * The translator, reachable from `refresh` without being a dependency of it.
   *
   * `refresh` is the mount effect's only dependency, so anything it closes over
   * decides how often the whole board is fetched.
   */
  const translate = useRef(t);
  translate.current = t;
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
      setError(
        loadError instanceof Error ? loadError.message : translate.current("premium:crypto.watchlists.loadError")
      );
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
        setError(result.message || t("premium:crypto.watchlists.actionFailed"));
        return false;
      }
      setNotice(result.message || success);
      await refresh("refresh");
      return true;
    } catch (actionError) {
      setError(actionError instanceof Error ? actionError.message : t("premium:crypto.watchlists.actionFailed"));
      return false;
    } finally {
      setBusy("");
    }
  }

  async function onCreate() {
    const name = newListName.trim();
    if (!name) {
      setError(t("premium:crypto.watchlists.create.nameRequired"));
      return;
    }
    const ok = await run("create", () => createWatchlist(name), t("premium:crypto.watchlists.create.created"));
    if (ok) setNewListName("");
  }

  async function onRename(watchlistId: number) {
    const name = renameValue.trim();
    if (!name) {
      setError(t("premium:crypto.watchlists.rename.nameRequired"));
      return;
    }
    const ok = await run(
      `rename-${watchlistId}`,
      () => renameWatchlist(watchlistId, name),
      t("premium:crypto.watchlists.rename.renamed")
    );
    if (ok) {
      setRenamingId(null);
      setRenameValue("");
    }
  }

  function confirmDelete(watchlist: Watchlist) {
    Alert.alert(
      t("premium:crypto.watchlists.delete.title"),
      // Says plainly what survives. Deleting a list the user built is worth one
      // tap of friction, and the alerts question is the one they will ask.
      t("premium:crypto.watchlists.delete.body", { name: watchlist.name }),
      [
        { text: t("premium:crypto.watchlists.delete.cancel"), style: "cancel" },
        {
          text: t("premium:crypto.watchlists.delete.confirm"),
          style: "destructive",
          onPress: () => {
            run(
              `delete-${watchlist.id}`,
              () => deleteWatchlist(watchlist.id),
              t("premium:crypto.watchlists.delete.deleted")
            ).catch(() => undefined);
          }
        }
      ]
    );
  }

  async function onAddAsset(watchlistId: number, symbol: string) {
    const ok = await run(
      `add-${watchlistId}`,
      () => addWatchlistAsset(watchlistId, symbol),
      t("premium:crypto.watchlists.assets.added", { symbol })
    );
    if (ok) {
      setAssetQuery("");
      setResults([]);
    }
  }

  async function onToggleFavorite(asset: WatchlistAsset) {
    await run(
      `fav-${asset.symbol}`,
      () => setFavoriteAsset(asset.symbol, !asset.favorite),
      t("premium:crypto.watchlists.assets.favoritesUpdated")
    );
  }

  const asOf = useMemo(() => {
    if (!market?.updated_at) return "";
    return market.updated_at.replace("T", " ");
  }, [market]);

  function renderAsset(asset: WatchlistAsset, watchlistId: number) {
    const tint = changeColor(asset.change_24h);
    const alertCountLabel = t("premium:crypto.watchlists.assets.alerts", { count: asset.alert_count });
    return (
      <View key={`${watchlistId}-${asset.id}`} style={styles.assetRow}>
        <Pressable
          style={styles.assetMain}
          accessibilityRole="button"
          accessibilityLabel={t("premium:crypto.watchlists.assets.a11y", { name: asset.name, price: formatPrice(asset.price) })}
          onPress={() => navigation.navigate("AssetDetail", { symbol: asset.symbol, name: asset.name })}
        >
          <View style={styles.assetIdentity}>
            <Text style={styles.assetSymbol}>{asset.symbol}</Text>
            <Text style={styles.assetName} numberOfLines={1}>
              {asset.name}
            </Text>
            <Text style={styles.assetMeta}>
              {asset.market_cap === null
                ? UNKNOWN_VALUE
                : t("premium:crypto.watchlists.assets.cap", { value: formatCompact(asset.market_cap) })}
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
            <Text style={styles.alertBadge} accessibilityLabel={alertCountLabel}>
              {alertCountLabel}
            </Text>
          ) : null}
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={t(
              asset.favorite ? "premium:crypto.watchlists.assets.unfavorite" : "premium:crypto.watchlists.assets.favorite",
              { symbol: asset.symbol }
            )}
            onPress={() => onToggleFavorite(asset)}
          >
            <Text style={[styles.iconAction, asset.favorite ? styles.iconActionOn : null]}>
              {asset.favorite ? "★" : "☆"}
            </Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={t("premium:crypto.watchlists.assets.removeLabel", { symbol: asset.symbol })}
            onPress={() =>
              run(
                `remove-${asset.id}`,
                () => removeWatchlistAsset(watchlistId, asset.id),
                t("premium:crypto.watchlists.assets.removed", { symbol: asset.symbol })
              )
            }
          >
            <Text style={styles.removeAction}>{t("premium:crypto.watchlists.assets.remove")}</Text>
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
                placeholder={t("premium:crypto.watchlists.rename.placeholder")}
                placeholderTextColor={colors.muted}
                accessibilityLabel={t("premium:crypto.watchlists.rename.label")}
              />
            ) : (
              <Text style={styles.cardTitle}>{watchlist.name}</Text>
            )}
            <Text style={styles.muted}>
              {t("premium:crypto.watchlists.assets.count", { count: watchlist.asset_count })}
              {/* Only ever averaged over assets we have a real price for, so an
                  unpriced row cannot drag the summary toward zero. */}
              {aggregate === null
                ? ""
                : ` · ${t("premium:crypto.watchlists.assets.average", { percent: formatPercent(aggregate) })}`}
            </Text>
          </View>
          <Text style={[styles.cardAggregate, { color: tint }]}>{formatPercent(aggregate)}</Text>
        </View>

        <View style={styles.cardActions}>
          {isRenaming ? (
            <>
              <Pressable accessibilityRole="button" onPress={() => onRename(watchlist.id)}>
                <Text style={styles.action}>{t("premium:crypto.watchlists.rename.save")}</Text>
              </Pressable>
              <Pressable accessibilityRole="button" onPress={() => setRenamingId(null)}>
                <Text style={styles.actionMuted}>{t("premium:crypto.watchlists.rename.cancel")}</Text>
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
                <Text style={styles.action}>{t("premium:crypto.watchlists.rename.action")}</Text>
              </Pressable>
              <Pressable
                accessibilityRole="button"
                onPress={() => {
                  setAddingToId(isAdding ? null : watchlist.id);
                  setAssetQuery("");
                  setResults([]);
                }}
              >
                <Text style={styles.action}>
                  {t(isAdding ? "premium:crypto.watchlists.assets.done" : "premium:crypto.watchlists.assets.add")}
                </Text>
              </Pressable>
              <Pressable accessibilityRole="button" onPress={() => confirmDelete(watchlist)}>
                <Text style={styles.actionDanger}>{t("premium:crypto.watchlists.delete.action")}</Text>
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
              placeholder={t("premium:crypto.watchlists.assets.searchPlaceholder")}
              placeholderTextColor={colors.muted}
              accessibilityLabel={t("premium:crypto.watchlists.assets.searchLabel")}
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
              <Text style={styles.muted}>{t("premium:crypto.watchlists.assets.noMatches")}</Text>
            ) : null}
          </View>
        ) : null}

        {watchlist.assets.length ? (
          watchlist.assets.map((asset) => renderAsset(asset, watchlist.id))
        ) : (
          <Text style={styles.muted}>{t("premium:crypto.watchlists.assets.empty")}</Text>
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
        <Text style={styles.title}>{t("premium:crypto.watchlists.title")}</Text>
        <Text style={styles.subtitle}>{t("premium:crypto.watchlists.subtitle")}</Text>
      </View>

      {/* Honest provenance line. When the provider is degraded this is the only
          thing distinguishing old real numbers from current ones. */}
      {market && !market.ready ? (
        <Text style={styles.warning}>{market.warning || t("premium:crypto.watchlists.pricesUnavailable")}</Text>
      ) : null}
      {offline ? <Text style={styles.warning}>{t("premium:crypto.watchlists.offline")}</Text> : null}
      {asOf ? <Text style={styles.muted}>{t("premium:crypto.watchlists.asOf", { timestamp: asOf })}</Text> : null}
      {error ? <Text style={styles.error}>{error}</Text> : null}
      {notice ? <Text style={styles.notice}>{notice}</Text> : null}

      <View style={styles.tabs}>
        {TAB_KEYS.map((key) => (
          <Pressable
            key={key}
            accessibilityRole="tab"
            accessibilityState={{ selected: tab === key }}
            style={[styles.tab, tab === key ? styles.tabActive : null]}
            onPress={() => setTab(key)}
          >
            <Text style={[styles.tabLabel, tab === key ? styles.tabLabelActive : null]}>
              {t(`premium:crypto.watchlists.tabs.${key}`)}
            </Text>
          </Pressable>
        ))}
      </View>

      {tab === "lists" ? (
        <>
          <Panel>
            <Text style={styles.sectionTitle}>{t("premium:crypto.watchlists.create.title")}</Text>
            <TextInput
              style={styles.input}
              value={newListName}
              onChangeText={setNewListName}
              placeholder={t("premium:crypto.watchlists.create.placeholder")}
              placeholderTextColor={colors.muted}
              accessibilityLabel={t("premium:crypto.watchlists.create.label")}
            />
            <Pressable
              accessibilityRole="button"
              style={styles.primaryButton}
              disabled={busy === "create"}
              onPress={onCreate}
            >
              <Text style={styles.primaryButtonLabel}>
                {t(busy === "create" ? "premium:crypto.watchlists.create.creating" : "premium:crypto.watchlists.create.submit")}
              </Text>
            </Pressable>
          </Panel>

          {watchlists.length ? (
            watchlists.map(renderWatchlist)
          ) : (
            <Panel>
              <Text style={styles.sectionTitle}>{t("premium:crypto.watchlists.empty.title")}</Text>
              <Text style={styles.muted}>{t("premium:crypto.watchlists.empty.body")}</Text>
            </Panel>
          )}
        </>
      ) : (
        <Panel>
          <Text style={styles.sectionTitle}>{t("premium:crypto.watchlists.favorites.title")}</Text>
          {favorites.length ? (
            favorites.map((asset) => renderAsset(asset, asset.watchlist_id))
          ) : (
            <Text style={styles.muted}>{t("premium:crypto.watchlists.favorites.empty")}</Text>
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
