/**
 * The seller's Store dashboard — the screen behind card #2 of the Business
 * "Sections" grid.
 *
 * This is a **management** surface, not the buyer storefront. Everything on it
 * answers one of two questions: is my store working right now, and what needs
 * me. That is why the KPI grid is above the listings and why the attention
 * banner appears above both when it appears at all.
 *
 * Structural decisions worth stating:
 *
 * * **`SellerStoreScreen` is untouched.** The `SellerStore` route still points
 *   there for every other mode, including `mode: "orders"` which the Orders
 *   card uses; only `mode: "dashboard"` reaches this screen. Deep links,
 *   navigation params and the listing editor all keep working exactly as they
 *   did.
 * * **The list is virtualized.** The section shows a preview of six, but the
 *   "See all" state renders the seller's full catalogue through a `FlatList`
 *   rather than a mapped array.
 * * **Nothing here formats a number itself.** Currency, counts, percentages and
 *   relative times all go through `useFormatters`, so a euro seller sees euros
 *   and a Spanish locale sees Spanish separators without this file knowing.
 *
 * Figures the reference design shows and this app has no source for — views,
 * seller rating, on-time dispatch, per-listing stars — are absent rather than
 * invented. `STORE_MOCK_DATA_GAPS` in `api/storeDashboard` lists each one and
 * the backend work it needs.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { FlatList, Pressable, RefreshControl, StyleSheet, Text, View, Animated } from "react-native";
import { useSafeAreaInsets } from "react-native-safe-area-context";
import {
  deriveAttention,
  deriveKpis,
  deriveRows,
  deriveStatus,
  deriveTabs,
  filterRows,
  loadStoreDashboard,
  snapshotFrom,
  type StoreListingRow as StoreListingRowData,
  type StoreLoadResult,
  type StoreTabKey
} from "../api/storeDashboard";
import {
  StoreAttentionBanner,
  StoreEmptyListings,
  StoreHeader,
  StoreKpiCard,
  StoreKpiSkeleton,
  StoreListingRow,
  StoreOfflineNote,
  StoreQuickLinkTile,
  StoreRowSkeleton,
  StoreSectionError,
  StoreSparkline,
  StoreStatusStrip,
  StoreTabBar
} from "../components/store";
import { registerSyncInvalidation } from "../core/eventSync";
import { useFormatters } from "../i18n/hooks";
import { BOTTOM_NAV_CONTENT_CLEARANCE } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { storeLight } from "../theme/storeLight";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { useStoreAmbient, useStoreEntrance, STORE_AMBIENT, STORE_STAGGER_MS } from "../theme/storeMotion";

/** How many listings the section previews before "See all". */
const PREVIEW_COUNT = 6;

/**
 * Entrance slots, in the order the spec choreographs them. Named so a section
 * cannot silently animate out of order when one is added.
 */
const SLOT = {
  header: 0,
  status: 1,
  kpis: 2,
  banner: 3,
  tabs: 4,
  list: 5,
  links: 6,
  ctas: 7
} as const;
const SECTION_COUNT = Object.keys(SLOT).length;

type Props = {
  route?: { params?: RootStackParamList["SellerStore"] };
  navigation: { navigate: (...args: any[]) => void; goBack?: () => void };
};

export function StoreDashboardScreen({ route, navigation }: Props) {
  const formatters = useFormatters();
  const reducedMotion = useLogiNexusReducedMotion();
  const insets = useSafeAreaInsets();
  const entrance = useStoreEntrance(SECTION_COUNT, reducedMotion);

  const [result, setResult] = useState<StoreLoadResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [tab, setTab] = useState<StoreTabKey>("all");
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState(false);

  const load = useCallback(async (mode: "initial" | "refresh" = "initial") => {
    if (mode === "refresh") setRefreshing(true);
    else setLoading(true);
    try {
      setResult(await loadStoreDashboard());
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }, []);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  // Same invalidation channels the existing Store screen listens on, so a
  // listing edited elsewhere refreshes here too.
  useEffect(() => {
    const refresh = () => load("refresh");
    const unregister = [
      registerSyncInvalidation("seller_inventory", refresh),
      registerSyncInvalidation("marketplace", refresh),
      registerSyncInvalidation("orders", refresh)
    ];
    return () => unregister.forEach((fn) => fn());
  }, [load]);

  const snapshot = useMemo(() => (result ? snapshotFrom(result) : { listings: [], orders: [] }), [result]);
  const kpis = useMemo(() => deriveKpis(snapshot), [snapshot]);
  const allRows = useMemo(() => deriveRows(snapshot), [snapshot]);
  const tabs = useMemo(() => deriveTabs(allRows), [allRows]);
  const attention = useMemo(() => deriveAttention(allRows), [allRows]);
  const status = useMemo(() => deriveStatus(allRows), [allRows]);

  /**
   * Search filters the seller's own catalogue in place rather than navigating,
   * so the KPIs above stay visible while they look. Title and price label are
   * the two fields a seller actually types — SKU has no field in this API.
   */
  const searched = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const scoped = filterRows(allRows, tab);
    if (!needle) return scoped;
    return scoped.filter(
      (row) =>
        row.title.toLowerCase().includes(needle) ||
        row.priceLabel.toLowerCase().includes(needle) ||
        String(row.id).includes(needle)
    );
  }, [allRows, query, tab]);

  const visible = expanded ? searched : searched.slice(0, PREVIEW_COUNT);

  const sellerName = String(snapshot.listings[0]?.seller_name || "Your store");
  const listingsFailed = result?.listings.status === "error";
  const ordersFailed = result?.orders.status === "error";

  /* -------------------------------------------------------------- *
   * Navigation
   * -------------------------------------------------------------- */

  /**
   * The listing editor is a panel inside `SellerStoreScreen`, and this screen
   * takes over `mode: "dashboard"` — so Edit routes to `mode: "create"`, which
   * renders the same `listings` panel with the same editor. The editor is not
   * reimplemented here and is not orphaned by the swap.
   */
  const openListing = useCallback(
    (row: StoreListingRowData) => {
      navigation.navigate("SellerStore", { mode: "create", title: row.title });
    },
    [navigation]
  );

  /** The buyer-facing marketplace tab — the real "preview as buyer" surface. */
  const openBuyerView = useCallback(() => {
    navigation.navigate("Tabs", { screen: "Marketplace" });
  }, [navigation]);

  /* -------------------------------------------------------------- *
   * Formatted values
   * -------------------------------------------------------------- */

  const salesText = formatters.currency(kpis.salesTodayMinor / 100, { currency: kpis.currency });
  const salesTrend =
    kpis.salesTrend == null
      ? null
      : {
          direction: (kpis.salesTrend >= 0 ? "up" : "down") as "up" | "down",
          label: formatters.percent(Math.abs(kpis.salesTrend))
        };

  const lowCount = tabs.find((entry) => entry.key === "low")?.count ?? 0;
  const outCount = tabs.find((entry) => entry.key === "out")?.count ?? 0;

  /* -------------------------------------------------------------- *
   * Sections
   * -------------------------------------------------------------- */

  const kpiGrid = (
    <View style={styles.kpiGrid}>
      {loading ? (
        <>
          <View style={styles.kpiRow}>
            <StoreKpiSkeleton reducedMotion={reducedMotion} />
            <StoreKpiSkeleton reducedMotion={reducedMotion} />
          </View>
          <View style={styles.kpiRow}>
            <StoreKpiSkeleton reducedMotion={reducedMotion} />
            <StoreKpiSkeleton reducedMotion={reducedMotion} />
          </View>
        </>
      ) : ordersFailed ? (
        <StoreSectionError
          message="Sales and orders didn't load."
          onRetry={() => load("refresh")}
          reducedMotion={reducedMotion}
        />
      ) : (
        <>
          <View style={styles.kpiRow}>
            <StoreKpiCard
              label="Today's sales"
              value={salesText}
              trend={salesTrend}
              visual={
                <StoreSparkline values={kpis.sparkline} reducedMotion={reducedMotion} />
              }
              onPress={() => navigation.navigate("BusinessOsInsights", { title: "Store reports" })}
              destinationHint="reports"
              reducedMotion={reducedMotion}
              delay={SLOT.kpis * STORE_STAGGER_MS}
            />
            <StoreKpiCard
              label="Open orders"
              value={formatters.count(kpis.openOrders)}
              // MOCK-DATA: `shippingToday` needs order.ship_by, so the
              // "N ship today" caption is absent rather than guessed.
              caption={kpis.shippingToday == null ? null : `${kpis.shippingToday} ship today`}
              onPress={() => navigation.navigate("SellerStore", { mode: "orders" })}
              destinationHint="your orders"
              reducedMotion={reducedMotion}
              delay={SLOT.kpis * STORE_STAGGER_MS}
            />
          </View>
          <View style={styles.kpiRow}>
            <StoreKpiCard
              label="Listings live"
              value={formatters.count(
                allRows.filter((row) => row.health === "in_stock" || row.health === "low_stock").length
              )}
              caption={outCount > 0 ? `${outCount} not buyable` : null}
              onPress={() => {
                setTab("all");
                setExpanded(true);
              }}
              destinationHint="all listings"
              reducedMotion={reducedMotion}
              delay={SLOT.kpis * STORE_STAGGER_MS}
            />
            <StoreKpiCard
              label="Sold · 7 days"
              value={formatters.count(allRows.reduce((sum, row) => sum + row.unitsSold7d, 0))}
              onPress={() => navigation.navigate("BusinessOsInsights", { title: "Store reports" })}
              destinationHint="reports"
              reducedMotion={reducedMotion}
              delay={SLOT.kpis * STORE_STAGGER_MS}
            />
          </View>
        </>
      )}
    </View>
  );

  const quickLinks = (
    <View style={styles.linkGrid}>
      <View style={styles.linkRow}>
        <StoreQuickLinkTile
          icon="cube-outline"
          label="Inventory"
          subtitle={
            loading
              ? "Checking stock…"
              : lowCount > 0
                ? `${allRows.length} items · ${lowCount} low`
                : `${allRows.length} items · all stocked`
          }
          onPress={() => {
            setTab(lowCount > 0 ? "low" : "all");
            setExpanded(true);
          }}
          reducedMotion={reducedMotion}
        />
        <StoreQuickLinkTile
          icon="pricetags-outline"
          label="Collections"
          subtitle={`${new Set(snapshot.listings.map((item) => item.category).filter(Boolean)).size} categories`}
          onPress={() => {
            setTab("all");
            setExpanded(true);
          }}
          reducedMotion={reducedMotion}
        />
      </View>
      <View style={styles.linkRow}>
        <StoreQuickLinkTile
          icon="bar-chart-outline"
          label="Reports"
          subtitle="Sales, orders and trends"
          onPress={() => navigation.navigate("BusinessOsInsights", { title: "Store reports" })}
          reducedMotion={reducedMotion}
        />
        <StoreQuickLinkTile
          icon="storefront-outline"
          label="Storefront"
          subtitle="See your store the way buyers do"
          onPress={openBuyerView}
          reducedMotion={reducedMotion}
        />
      </View>
      <View style={styles.linkRow}>
        {/* Shipping settings and a returns policy have no screen in this app,
            and neither has a backend to point at. Greyed with an honest
            subtitle rather than wired to something unrelated — a tile that
            opens the wrong screen is worse than one that says "not yet". */}
        <StoreQuickLinkTile
          icon="airplane-outline"
          label="Shipping"
          subtitle="Not available in the app yet"
          disabled
          reducedMotion={reducedMotion}
        />
        <StoreQuickLinkTile
          icon="return-down-back-outline"
          label="Returns"
          subtitle="Not available in the app yet"
          disabled
          reducedMotion={reducedMotion}
        />
      </View>
    </View>
  );

  const listingsSection = (() => {
    if (loading) {
      return (
        <View>
          {Array.from({ length: 4 }, (_, index) => (
            <StoreRowSkeleton key={index} reducedMotion={reducedMotion} />
          ))}
        </View>
      );
    }
    if (listingsFailed) {
      return (
        <StoreSectionError
          message="Your listings didn't load."
          onRetry={() => load("refresh")}
          reducedMotion={reducedMotion}
        />
      );
    }
    if (allRows.length === 0) {
      return (
        <StoreEmptyListings
          onAddListing={() => navigation.navigate("MarketplaceCreateGateway", { title: "Create Listing" })}
          reducedMotion={reducedMotion}
        />
      );
    }
    if (visible.length === 0) {
      return (
        <View style={styles.noMatches}>
          <Text style={styles.noMatchesText}>
            {query ? `No listings match “${query.trim()}”.` : "Nothing in this tab right now."}
          </Text>
        </View>
      );
    }
    return null;
  })();

  const listData = listingsSection ? [] : visible;

  return (
    <View style={styles.root}>
      <Animated.View style={entrance.styleFor(SLOT.header)}>
        <StoreHeader
          title={route?.params?.title || "Store"}
          query={query}
          onQueryChange={setQuery}
          onSubmitSearch={() => setExpanded(true)}
          onBack={() => navigation.goBack?.()}
          onNotifications={() => navigation.navigate("Notifications")}
          // MOCK-DATA: no seller-notification count endpoint exists, so the
          // badge is driven by open orders — the one thing genuinely waiting on
          // the seller — rather than by a number nobody produces.
          unreadCount={ordersFailed ? 0 : kpis.openOrders}
          searchPlaceholder="Search your listings and orders"
          reducedMotion={reducedMotion}
        />
      </Animated.View>

      <Animated.View style={entrance.styleFor(SLOT.status)}>
        <StoreStatusStrip
          text={
            status.open
              ? `${sellerName} · Open for orders`
              : `${sellerName} · Paused — buyers can't order`
          }
          open={status.open}
          actionLabel={status.open ? "Manage" : "Reopen"}
          onAction={() => {
            setTab(status.open ? "all" : "out");
            setExpanded(true);
          }}
          reducedMotion={reducedMotion}
        />
      </Animated.View>

      <FlatList
        data={listData}
        keyExtractor={(row) => String(row.id)}
        refreshControl={
          <RefreshControl refreshing={refreshing} onRefresh={() => load("refresh")} />
        }
        contentContainerStyle={[
          styles.content,
          { paddingBottom: Math.max(insets.bottom, 16) + BOTTOM_NAV_CONTENT_CLEARANCE }
        ]}
        ListHeaderComponent={
          <View style={styles.headerBlock}>
            {result?.offline && result.cachedAt ? (
              <StoreOfflineNote
                text={`Offline — showing your store as of ${formatters.relative(result.cachedAt)}.`}
              />
            ) : null}

            <Animated.View style={entrance.styleFor(SLOT.kpis)}>{kpiGrid}</Animated.View>

            {attention ? (
              <Animated.View style={[styles.bannerWrap, entrance.styleFor(SLOT.banner)]}>
                <StoreAttentionBanner
                  headline={
                    attention.kind === "out_of_stock"
                      ? `${formatters.count(attention.count)} ${attention.count === 1 ? "listing is" : "listings are"} out of stock`
                      : `${formatters.count(attention.count)} ${attention.count === 1 ? "listing is" : "listings are"} running low`
                  }
                  detail={
                    attention.kind === "out_of_stock"
                      ? "Buyers can't order these until you restock them."
                      : "Restock before they sell out and drop off the storefront."
                  }
                  onPress={() => {
                    setTab(attention.target);
                    setExpanded(true);
                  }}
                  reducedMotion={reducedMotion}
                />
              </Animated.View>
            ) : null}

            <Animated.View style={[styles.sectionHead, entrance.styleFor(SLOT.tabs)]}>
              <Text style={styles.sectionTitle}>Listings</Text>
              <Pressable
                onPress={() => {
                  setTab("all");
                  setExpanded(true);
                }}
                hitSlop={8}
                accessibilityRole="link"
                accessibilityLabel={`Manage all listings, ${allRows.length}`}
              >
                <Text style={styles.sectionLink}>Manage all ({formatters.count(allRows.length)})</Text>
              </Pressable>
            </Animated.View>

            {allRows.length > 0 ? (
              <Animated.View style={entrance.styleFor(SLOT.tabs)}>
                <StoreTabBar tabs={tabs} active={tab} onChange={setTab} reducedMotion={reducedMotion} />
              </Animated.View>
            ) : null}

            {listingsSection ? (
              <Animated.View style={entrance.styleFor(SLOT.list)}>{listingsSection}</Animated.View>
            ) : null}
          </View>
        }
        renderItem={({ item }) => (
          <StoreListingRow
            row={item}
            priceText={item.priceLabel}
            soldText={
              item.unitsSold7d > 0 ? `${formatters.count(item.unitsSold7d)} sold · 7d` : null
            }
            onPress={() => openListing(item)}
            onEdit={() => openListing(item)}
            onAction={() => openListing(item)}
            reducedMotion={reducedMotion}
          />
        )}
        ListFooterComponent={
          <View style={styles.footerBlock}>
            {!expanded && searched.length > PREVIEW_COUNT ? (
              <Pressable
                style={styles.seeAll}
                onPress={() => setExpanded(true)}
                accessibilityRole="link"
                accessibilityLabel={`See all ${searched.length} listings`}
              >
                <Text style={styles.sectionLink}>
                  See all {formatters.count(searched.length)} listings ›
                </Text>
              </Pressable>
            ) : null}

            <Animated.View style={entrance.styleFor(SLOT.links)}>
              <Text style={[styles.sectionTitle, styles.linkHeading]}>Manage your store</Text>
              {quickLinks}
            </Animated.View>

            <Animated.View style={entrance.styleFor(SLOT.ctas)}>
              <StoreFooterCtas
                onAdd={() => navigation.navigate("MarketplaceCreateGateway", { title: "Create Listing" })}
                onPreview={openBuyerView}
                reducedMotion={reducedMotion}
              />
            </Animated.View>
          </View>
        }
      />
    </View>
  );
}

/**
 * The two footer buttons. Primary is filled with the CTA token; secondary is a
 * hairline outline, because two filled pills side by side makes neither read as
 * the main action.
 *
 * The primary's fill comes from `storeLight.cta`, which is a single swappable
 * constant — see the trade-dress note in `theme/storeLight.ts`.
 */
function StoreFooterCtas({
  onAdd,
  onPreview,
  reducedMotion
}: {
  onAdd: () => void;
  onPreview: () => void;
  reducedMotion: boolean;
}) {
  const gleam = useStoreAmbient(STORE_AMBIENT.ctaGleam, reducedMotion, { resetTo: 0 });

  return (
    <View style={styles.ctas}>
      <Pressable
        style={styles.primaryCta}
        onPress={onAdd}
        accessibilityRole="button"
        accessibilityLabel="Add a listing"
      >
        <Animated.View
          pointerEvents="none"
          style={[
            styles.gleam,
            {
              opacity: gleam.interpolate({
                inputRange: [0, 0.45, 0.5, 0.55, 1],
                outputRange: [0, 0, 0.35, 0, 0]
              }),
              transform: [
                { translateX: gleam.interpolate({ inputRange: [0, 1], outputRange: [-200, 260] }) },
                { rotate: "18deg" }
              ]
            }
          ]}
        />
        <Text style={styles.primaryCtaText}>＋ Add a listing</Text>
      </Pressable>

      <Pressable
        style={styles.secondaryCta}
        onPress={onPreview}
        accessibilityRole="button"
        accessibilityLabel="Preview your storefront as a buyer"
      >
        <Text style={styles.secondaryCtaText}>Preview storefront as buyer</Text>
      </Pressable>
    </View>
  );
}

const styles = StyleSheet.create({
  root: { flex: 1, backgroundColor: storeLight.bg.page },
  content: { paddingBottom: 24 },
  headerBlock: { gap: storeLight.space.section },
  kpiGrid: { gap: storeLight.space.gutter, paddingHorizontal: storeLight.space.card, paddingTop: storeLight.space.section },
  kpiRow: { flexDirection: "row", gap: storeLight.space.gutter },
  bannerWrap: { paddingHorizontal: storeLight.space.card },
  sectionHead: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingHorizontal: storeLight.space.card
  },
  sectionTitle: { fontSize: 16, fontWeight: "700", color: storeLight.text.primary },
  sectionLink: { fontSize: 13, fontWeight: "600", color: storeLight.text.link },
  noMatches: { padding: 24, backgroundColor: storeLight.bg.card, alignItems: "center" },
  noMatchesText: { fontSize: 13, color: storeLight.text.muted },
  footerBlock: { gap: storeLight.space.section, paddingTop: storeLight.space.section },
  seeAll: {
    minHeight: storeLight.size.tapTarget,
    justifyContent: "center",
    paddingHorizontal: storeLight.space.card,
    backgroundColor: storeLight.bg.card,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: storeLight.border.hairline
  },
  linkHeading: { paddingHorizontal: storeLight.space.card },
  linkGrid: { gap: storeLight.space.gutter, paddingHorizontal: storeLight.space.card, marginTop: 8 },
  linkRow: { flexDirection: "row", gap: storeLight.space.gutter },
  ctas: { gap: 10, paddingHorizontal: storeLight.space.card },
  primaryCta: {
    minHeight: storeLight.size.tapTarget + 4,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    backgroundColor: storeLight.cta.from,
    overflow: "hidden"
  },
  gleam: { position: "absolute", top: -40, bottom: -40, width: 40, backgroundColor: "#FFFFFF" },
  primaryCtaText: { fontSize: 15, fontWeight: "800", color: storeLight.cta.text },
  secondaryCta: {
    minHeight: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    backgroundColor: storeLight.bg.card
  },
  secondaryCtaText: { fontSize: 14, fontWeight: "600", color: storeLight.text.primary }
});
