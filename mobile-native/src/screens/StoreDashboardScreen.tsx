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
  storeReadiness,
  storeReadinessEnabled,
  type StoreListingRow as StoreListingRowData,
  type StoreLoadResult,
  type StoreSetupActionKey,
  type StoreSetupStep,
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
  StoreQuickLinkGrid,
  StoreRowSkeleton,
  StoreSectionError,
  StoreSetupChecklist,
  StoreSparkline,
  StoreStatusStrip,
  StoreTabBar
} from "../components/store";
import { registerSyncInvalidation } from "../core/eventSync";
import { refreshUnreadCounts, useBellCount } from "../core/unreadCounts";
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
  /**
   * The setup checklist sits directly under the strip, because it is the "why"
   * for the sentence the strip just made. It only renders behind the readiness
   * flag; when the flag is off this slot is simply unused, which costs nothing
   * because `SECTION_COUNT` is derived rather than typed.
   */
  setup: 2,
  kpis: 3,
  banner: 4,
  tabs: 5,
  list: 6,
  links: 7,
  ctas: 8
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

  // The header bell reads the ONE shared unread store — the same number every
  // seller header and the Activity feed show. Pull the authoritative count on
  // mount; the eventSync wiring (initUnreadCountSync) keeps it fresh after that.
  const bellCount = useBellCount();
  useEffect(() => {
    void refreshUnreadCounts();
  }, []);

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
   * Where the store actually stands, read from the listings it already loaded.
   *
   * The flag is read once per render rather than at module load so a test can
   * turn it on without re-importing the screen, matching every other flag in
   * this app. With it off, `status` above still drives the strip and this screen
   * behaves exactly as the shipped build does.
   */
  const readinessOn = storeReadinessEnabled();
  const readiness = useMemo(
    () => storeReadiness({ listings: snapshot.listings, rows: allRows }),
    [snapshot.listings, allRows]
  );

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
   * reimplemented here and is not orphaned by the swap. `listingId` is what
   * makes it land on the row the seller tapped instead of an empty panel.
   */
  const openListing = useCallback(
    (row: StoreListingRowData) => {
      navigation.navigate("SellerStore", { mode: "create", title: row.title, listingId: row.id });
    },
    [navigation]
  );

  /** The buyer-facing marketplace tab — the real "preview as buyer" surface. */
  const openBuyerView = useCallback(() => {
    navigation.navigate("Tabs", { screen: "Marketplace" });
  }, [navigation]);

  /**
   * The four setup actions, each mapped to something that already exists.
   *
   * Every key resolves to a destination this app ships today — the create
   * gateway, a tab of this screen's own list, or the buyer view. The ladder
   * deliberately has no fifth action, because a fifth would have had nowhere to
   * go, and a checklist button that lands nowhere is worse than no button.
   */
  const runSetupAction = useCallback(
    (key: StoreSetupActionKey) => {
      switch (key) {
        case "add_listing":
          navigation.navigate("MarketplaceCreateGateway", { title: "Create Listing" });
          return;
        case "open_drafts":
          setTab("drafts");
          setExpanded(true);
          return;
        case "open_out_of_stock":
          setTab("out");
          setExpanded(true);
          return;
        default:
          openBuyerView();
      }
    },
    [navigation, openBuyerView]
  );

  const onSetupStep = useCallback(
    (step: StoreSetupStep) => {
      if (step.action) runSetupAction(step.action.key);
    },
    [runSetupAction]
  );

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

  /**
   * This screen's hand-composed two-tile rows were the pattern the other
   * surfaces got wrong, so the pattern moved into `StoreQuickLinkGrid` and this
   * screen now consumes it like everyone else. Behaviour is unchanged — three
   * rows of two, in the same order — but the row count is derived rather than
   * typed, which is what stops the next screen from typing four.
   */
  const quickLinks = (
    <View style={styles.linkGrid}>
      <StoreQuickLinkGrid
        reducedMotion={reducedMotion}
        items={[
          {
            icon: "cube-outline",
            label: "Inventory",
            subtitle: loading
              ? "Checking stock…"
              : allRows.length === 0
                ? // "0 items · all stocked" claimed a stocked shelf that does not
                  // exist. An empty catalogue has no stock state at all.
                  "No inventory yet"
                : lowCount > 0
                  ? `${allRows.length} items · ${lowCount} low`
                  : `${allRows.length} items · all stocked`,
            onPress: () => {
              setTab(lowCount > 0 ? "low" : "all");
              setExpanded(true);
            },
            reducedMotion
          },
          {
            icon: "pricetags-outline",
            // The tile counts distinct listing categories, so it is named for
            // what it counts. It becomes "Collections" only when a real
            // collections feature exists to back the word (mission Phase 5) —
            // relabelling the same category count would be the dishonest fix.
            label: "Categories",
            subtitle: (() => {
              const count = new Set(
                snapshot.listings.map((item) => item.category).filter(Boolean)
              ).size;
              return count === 0 ? "No categories yet" : `${count} categories`;
            })(),
            onPress: () => {
              setTab("all");
              setExpanded(true);
            },
            reducedMotion
          },
          {
            icon: "bar-chart-outline",
            label: "Reports",
            subtitle: "Sales, orders and trends",
            onPress: () => navigation.navigate("BusinessOsInsights", { title: "Store reports" }),
            reducedMotion
          },
          {
            icon: "storefront-outline",
            label: "Storefront",
            subtitle: "See your store the way buyers do",
            onPress: openBuyerView,
            reducedMotion
          },
          // Shipping settings and a returns policy have no screen in this app,
          // and neither has a backend to point at. Marked unavailable with an
          // honest subtitle rather than wired to something unrelated — a tile
          // that opens the wrong screen is worse than one that says "not yet".
          {
            icon: "airplane-outline",
            label: "Shipping",
            subtitle: "Not available in the app yet",
            disabled: true,
            reducedMotion
          },
          {
            icon: "return-down-back-outline",
            label: "Returns",
            subtitle: "Not available in the app yet",
            disabled: true,
            reducedMotion
          }
        ]}
      />
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
          onNotifications={() => navigation.navigate("BusinessOsActivity")}
          // The bell number and its destination are now the shared Activity feed
          // and its unread store — not open-orders, which double-counted with the
          // orders card and diverged from every other header.
          unreadCount={bellCount}
          searchPlaceholder="Search your listings and orders"
          reducedMotion={reducedMotion}
        />
      </Animated.View>

      <Animated.View style={entrance.styleFor(SLOT.status)}>
        {/* The strip says where the store stands and nothing more. Under the
            readiness flag the sentence comes from the ladder, which read the
            listings; without it the old two-way open/paused split is kept
            byte-for-byte so an un-flagged build is unchanged. */}
        <StoreStatusStrip
          text={
            readinessOn
              ? `${sellerName} · ${readiness.statusLabel}`
              : status.open
                ? `${sellerName} · Open for orders`
                : `${sellerName} · Paused — buyers can't order`
          }
          open={readinessOn ? readiness.openForOrders : status.open}
          actionLabel={readinessOn ? readiness.action.label : status.open ? "Manage" : "Reopen"}
          onAction={() => {
            if (readinessOn) {
              runSetupAction(readiness.action.key);
              return;
            }
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

            {/* Shown only while something is outstanding. A checklist with every
                row ticked is a screen that has stopped telling the seller
                anything, so at `remaining === 0` it disappears and the strip
                carries the state on its own. The loading guard keeps it from
                claiming "not set up" about a store that simply hasn't answered
                yet. */}
            {readinessOn && !loading && !listingsFailed && readiness.remaining > 0 ? (
              <Animated.View style={[styles.checklistWrap, entrance.styleFor(SLOT.setup)]}>
                <StoreSetupChecklist
                  headline={readiness.headline}
                  steps={readiness.steps}
                  remaining={readiness.remaining}
                  onStepAction={onSetupStep}
                  reducedMotion={reducedMotion}
                />
              </Animated.View>
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
  // The card carries its own horizontal margin, so this only adds the gap above.
  checklistWrap: { paddingTop: storeLight.space.section },
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
  /**
   * Page inset only. The gap between tiles and between rows now belongs to
   * `StoreQuickLinkGrid`, so it is not repeated here — two owners of the same
   * spacing is how the rows drifted out of alignment with each other in the
   * first place. `linkRow` is gone for the same reason: the row is no longer
   * something a screen composes.
   */
  linkGrid: { paddingHorizontal: storeLight.space.card, marginTop: 8 },
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
