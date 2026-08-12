/**
 * Marketplace discovery — the buyer's browse surface.
 *
 * Two things changed here and both were defects rather than preferences.
 *
 * First, the grid rendered in the dark `colors` tokens while the product detail
 * rendered in `storeLight`, so a buyer crossed a hard theme seam on every tap and
 * the light-mode grid put pale text on a pale card. Marketplace is now one light
 * commerce surface end to end, matching Store and the product page.
 *
 * Second, the product detail lived in a `Modal` mounted by this screen. It is now
 * a real route (`MarketplaceProduct`), so it can be deep-linked, shared, and
 * returned to from checkout. This screen keeps only what discovery needs.
 *
 * The tab strip lists exactly the orderings this backend can actually produce —
 * relevance, recency, and boosted — and nothing else. "Deals", "Best Sellers",
 * "Top Rated" and "Trending" have no backing field on `marketplace_listings`, so
 * they are absent rather than present and empty. Likewise there is no filter chip:
 * the only real filter is the category rail below it, which is built from the
 * categories the returned listings actually carry.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { Ionicons } from "@expo/vector-icons";
import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  Pressable,
  RefreshControl,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import {
  loadCachedMarketplace,
  MarketplaceListing,
  searchMarketplace
} from "../api/marketplace";
import { addToCart, fetchCart } from "../api/marketplaceCommerce";
import {
  canPurchaseMarketplaceListing as canPurchaseListing,
  marketplaceAvailabilityCopy as availabilityCopy
} from "../api/marketplaceBuyerPresentation";
import { mediaDisplayUrl } from "../api/feed";
import { sellerStoreName } from "../api/sellerIdentity";
import { registerSyncInvalidation } from "../core/eventSync";
import { useBottomNavSurface } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { observeSavedStates, peekSaveState, useSavedState } from "../social/savedStore";
import { setSaved } from "../social/useSaveAction";
import { storeLight } from "../theme/marketplaceLight";
import { createThemedStyles } from "../theme/themedStyles";

type Props = Partial<NativeStackScreenProps<RootStackParamList, "MarketplaceDetail">>;

type SortKey = "relevance" | "new" | "boosted";

/**
 * Only orderings the backend can honestly produce. `relevance` is the server's
 * own ordering (`featured DESC, id DESC`), `new` reads `created_at`, `boosted`
 * filters on `featured`. Anything requiring sales, ratings or view counts is
 * omitted because `marketplace_listings` stores none of them.
 */
const SORT_TABS: { key: SortKey; label: string }[] = [
  { key: "relevance", label: "For you" },
  { key: "new", label: "New arrivals" },
  { key: "boosted", label: "Boosted" }
];

export function MarketplaceScreen({ route, navigation }: Props) {
  // Bottom-dock coupling: drives hide-on-scroll-down / reveal-on-scroll-up and
  // reserves the matching clearance so the last row never sits under the dock.
  const dock = useBottomNavSurface();
  const initialListingId = Number(route?.params?.listingId || 0);
  const sellerUserId = Number(route?.params?.sellerUserId || 0);
  const [items, setItems] = useState<MarketplaceListing[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [cartCount, setCartCount] = useState(0);
  const [category, setCategory] = useState("All");
  const [sort, setSort] = useState<SortKey>("relevance");

  const categories = useMemo(
    () => ["All", ...Array.from(new Set(items.map((item) => String(item.category || "").trim()).filter(Boolean))).slice(0, 12)],
    [items]
  );
  const hasBoosted = useMemo(() => items.some(isBoosted), [items]);
  const tabs = useMemo(() => SORT_TABS.filter((tab) => tab.key !== "boosted" || hasBoosted), [hasBoosted]);

  const visibleItems = useMemo(() => {
    const byCategory = category === "All" ? items : items.filter((item) => String(item.category || "") === category);
    if (sort === "boosted") return byCategory.filter(isBoosted);
    if (sort === "new") return [...byCategory].sort((a, b) => listingCreatedAt(b) - listingCreatedAt(a));
    return byCategory;
  }, [category, items, sort]);

  async function load(mode: "initial" | "refresh" | "search" = "initial", nextQuery = query) {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const result = await searchMarketplace({ query: nextQuery, limit: 32, sellerUserId });
      const nextItems = result.items || [];
      // A live search result is newer than anything the store holds, so it
      // corrects it — that is how a listing unsaved from the Saved screen stops
      // showing as saved here without this screen knowing that screen exists.
      // Cached results below deliberately do not: they are, by definition, old.
      observeSavedStates("marketplace", nextItems.map((item) => ({ id: item.id, saved: item.saved })));
      setItems(nextItems);
      openInitialListing(nextItems);
    } catch (loadError) {
      const cached = await loadCachedMarketplace();
      if (cached.length) {
        setItems(cached);
        setOffline(true);
        openInitialListing(cached);
      } else {
        setError(loadError instanceof Error ? loadError.message : "Marketplace could not load.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  /**
   * A deep link or notification that names a listing wants the product page, not
   * the grid with that listing nudged to the front. The grid still renders
   * underneath so Back lands on Marketplace rather than on nothing.
   */
  function openInitialListing(source: MarketplaceListing[]) {
    if (!initialListingId || !navigation) return;
    const target = source.find((item) => item.id === initialListingId);
    if (!target) return;
    navigation.navigate("MarketplaceProduct", { listingId: target.id, listing: target, title: target.title });
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
    refreshCartCount();
  }, [initialListingId, sellerUserId]);

  useEffect(() => {
    const unregisterMarketplace = registerSyncInvalidation("marketplace", () => load("refresh"));
    return unregisterMarketplace;
  }, [initialListingId, query, sellerUserId]);

  function refreshCartCount() {
    fetchCart().then((cart) => setCartCount(cart.badgeCount)).catch(() => undefined);
  }

  function updateListing(listingId: number, next: Partial<MarketplaceListing>) {
    setItems((current) => current.map((item) => (item.id === listingId ? { ...item, ...next } : item)));
  }

  async function handleSave(listing: MarketplaceListing) {
    // Invert the state that is on screen — the store's — not the one baked into
    // the search payload, which may predate a save made on another surface.
    const wasSaved = peekSaveState("marketplace", listing.id)?.saved ?? Boolean(listing.saved);
    setBusyId(listing.id);
    try {
      const outcome = await setSaved({ type: "marketplace", id: listing.id }, !wasSaved);
      updateListing(listing.id, { saved: outcome.saved });
      if (!outcome.ok && outcome.message) setError(outcome.message);
    } finally {
      setBusyId(null);
    }
  }

  async function handleAddToCart(listing: MarketplaceListing) {
    if (!canPurchaseListing(listing)) {
      setError("This item is no longer available.");
      return;
    }
    setBusyId(listing.id);
    try {
      // Add to cart stays on the grid. Navigating to checkout from a grid tile
      // is the behaviour this rebuild exists to remove.
      const cart = await addToCart(listing.id, 1);
      setCartCount(cart.badgeCount);
      setError(`Added to cart · ${listing.title || "Item"}`);
    } catch (cartError) {
      setError(cartError instanceof Error ? cartError.message : "This item could not be added to your cart.");
    } finally {
      setBusyId(null);
    }
  }

  function openProduct(listing: MarketplaceListing) {
    navigation?.navigate("MarketplaceProduct", { listingId: listing.id, listing, title: listing.title });
  }

  if (loading && !items.length) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={storeLight.accent.brandOnLight} />
        <Text style={styles.centerText}>Loading Marketplace</Text>
      </View>
    );
  }

  return (
    <View style={styles.root}>
      <FlatList
        style={styles.list}
        {...dock.handlers}
        contentContainerStyle={[styles.content, dock.contentPadding]}
        data={visibleItems}
        numColumns={2}
        columnWrapperStyle={styles.gridRow}
        keyExtractor={(item) => String(item.id)}
        refreshControl={
          <RefreshControl
            refreshing={refreshing}
            tintColor={storeLight.accent.brandOnLight}
            onRefresh={() => {
              refreshCartCount();
              load("refresh").catch(() => undefined);
            }}
          />
        }
        ListHeaderComponent={
          <View style={styles.header}>
            <View style={styles.headerRow}>
              <View style={styles.headerTitles}>
                <Text style={styles.title}>{sellerUserId ? route?.params?.title || "Seller store" : "Marketplace"}</Text>
                <Text style={styles.subtitle}>
                  {offline ? "Showing saved results" : sellerUserId ? "Products from this seller" : "Buy from PulseSoc sellers"}
                </Text>
              </View>
              <Pressable accessibilityRole="button" accessibilityLabel="Saved items" style={styles.headerIcon} onPress={() => navigation?.navigate("Saved")}>
                <Ionicons name="heart-outline" size={22} color={storeLight.text.primary} />
              </Pressable>
              <Pressable
                accessibilityRole="button"
                accessibilityLabel={`Cart, ${cartCount} item${cartCount === 1 ? "" : "s"}`}
                style={styles.headerIcon}
                onPress={() => navigation?.navigate("MarketplaceCart", { title: "Cart" })}
              >
                <Ionicons name="cart-outline" size={23} color={storeLight.text.primary} />
                {cartCount ? <Text style={styles.cartBadge}>{cartCount > 99 ? "99+" : cartCount}</Text> : null}
              </Pressable>
            </View>

            <View style={styles.searchRow}>
              <Ionicons name="search" size={18} color={storeLight.text.muted} style={styles.searchIcon} />
              <TextInput
                style={styles.searchInput}
                value={query}
                onChangeText={setQuery}
                placeholder="Search products, categories, sellers"
                placeholderTextColor={storeLight.text.muted}
                returnKeyType="search"
                onSubmitEditing={() => load("search", query).catch(() => undefined)}
              />
              <Pressable accessibilityRole="button" accessibilityLabel="Search Marketplace" style={styles.searchButton} onPress={() => load("search", query).catch(() => undefined)}>
                <Text style={styles.searchButtonText}>Search</Text>
              </Pressable>
            </View>

            <View style={styles.tabRow}>
              {tabs.map((tab) => (
                <Pressable
                  key={tab.key}
                  accessibilityRole="tab"
                  accessibilityState={{ selected: sort === tab.key }}
                  style={[styles.tab, sort === tab.key && styles.tabActive]}
                  onPress={() => setSort(tab.key)}
                >
                  <Text style={[styles.tabText, sort === tab.key && styles.tabTextActive]}>{tab.label}</Text>
                </Pressable>
              ))}
            </View>

            {categories.length > 1 ? (
              <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.categoryRail}>
                {categories.map((value) => (
                  <Pressable
                    key={value}
                    accessibilityRole="button"
                    accessibilityState={{ selected: category === value }}
                    style={[styles.categoryChip, category === value && styles.categoryChipActive]}
                    onPress={() => setCategory(value)}
                  >
                    <Text style={[styles.categoryText, category === value && styles.categoryTextActive]}>{value}</Text>
                  </Pressable>
                ))}
              </ScrollView>
            ) : null}

            <View style={styles.utilityRow}>
              <Pressable accessibilityRole="button" onPress={() => navigation?.navigate("BuyerOrders", { title: "Purchase History" })}>
                <Text style={styles.utilityLink}>Your orders</Text>
              </Pressable>
              <Pressable accessibilityRole="button" onPress={() => navigation?.navigate("SellerStore", { title: "Seller / Store" })}>
                <Text style={styles.utilityLink}>Sell on PulseSoc</Text>
              </Pressable>
            </View>

            {error ? <Text style={styles.notice} accessibilityLiveRegion="polite">{error}</Text> : null}
          </View>
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            <Ionicons name="storefront-outline" size={34} color={storeLight.text.muted} />
            <Text style={styles.emptyTitle}>{error ? "Marketplace unavailable" : "Nothing to show here yet"}</Text>
            <Text style={styles.emptyText}>
              {error || (query ? "No products matched that search. Try a different word or clear the search." : "Products from PulseSoc sellers will appear here.")}
            </Text>
          </View>
        }
        renderItem={({ item }) => (
          <ProductCard
            listing={item}
            busy={busyId === item.id}
            onOpen={openProduct}
            onSave={handleSave}
            onAddToCart={handleAddToCart}
          />
        )}
      />
    </View>
  );
}

/**
 * A grid tile. Image first, price second, title third — the buyer scans on price
 * and photo, so leading with the description (as this did) buried both.
 */
function ProductCard({ listing, busy, onOpen, onSave, onAddToCart }: {
  listing: MarketplaceListing;
  busy?: boolean;
  onOpen: (listing: MarketplaceListing) => void;
  onSave: (listing: MarketplaceListing) => void;
  onAddToCart: (listing: MarketplaceListing) => void;
}) {
  const cover = listing.media?.[0] ? mediaDisplayUrl(listing.media[0]) : "";
  // Read from the shared store, not from the payload: a listing saved on the
  // product page or the Saved screen shows as saved here without a refetch.
  const savedState = useSavedState("marketplace", listing.id, listing.saved);
  const purchasable = canPurchaseListing(listing);
  return (
    <Pressable accessibilityRole="button" accessibilityLabel={listing.title || "Marketplace product"} style={styles.card} onPress={() => onOpen(listing)}>
      <View style={styles.cardMedia}>
        {cover ? (
          <Image source={{ uri: cover }} style={styles.cardImage} resizeMode="cover" />
        ) : (
          <View style={[styles.cardImage, styles.cardImageFallback]}>
            <Ionicons name="image-outline" size={26} color={storeLight.text.muted} />
          </View>
        )}
        {isBoosted(listing) ? <Text style={styles.sponsoredBadge}>Sponsored</Text> : null}
        {!purchasable ? (
          <View style={styles.soldScrim}>
            <Text style={styles.soldScrimText}>SOLD OUT</Text>
          </View>
        ) : null}
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={`${savedState.saved ? "Remove" : "Save"} ${listing.title || "product"}`}
          accessibilityState={{ selected: savedState.saved, disabled: busy }}
          disabled={busy}
          hitSlop={8}
          style={styles.heartButton}
          onPress={() => onSave(listing)}
        >
          <Ionicons name={savedState.saved ? "heart" : "heart-outline"} size={18} color={savedState.saved ? storeLight.status.error : storeLight.text.primary} />
        </Pressable>
      </View>
      <View style={styles.cardBody}>
        <Text style={styles.cardPrice} numberOfLines={1}>{listing.price_label || "Price at checkout"}</Text>
        <Text style={styles.cardTitle} numberOfLines={2}>{listing.title || "Marketplace product"}</Text>
        <Text style={[styles.cardAvailability, !purchasable && styles.cardSold]} numberOfLines={1}>{availabilityCopy(listing)}</Text>
        <Text style={styles.cardSeller} numberOfLines={1}>{sellerStoreName(listing)}</Text>
        <Pressable
          accessibilityRole="button"
          accessibilityLabel={purchasable ? `Add ${listing.title || "product"} to cart` : "Sold out"}
          accessibilityState={{ disabled: busy || !purchasable }}
          disabled={busy || !purchasable}
          style={[styles.cardCta, (busy || !purchasable) && styles.disabled]}
          onPress={() => onAddToCart(listing)}
        >
          <Text style={styles.cardCtaText}>{purchasable ? "Add to cart" : "Sold out"}</Text>
        </Pressable>
      </View>
    </Pressable>
  );
}

function isBoosted(listing: MarketplaceListing) {
  return listing.featured === true || Number(listing.featured || 0) > 0;
}

/** Absent `created_at` sorts last rather than as "now" — unknown is not new. */
function listingCreatedAt(listing: MarketplaceListing) {
  const parsed = Date.parse(String(listing.created_at || ""));
  return Number.isNaN(parsed) ? 0 : parsed;
}

const styles = createThemedStyles(() => ({
  card: {
    backgroundColor: storeLight.bg.card,
    borderColor: storeLight.border.hairline,
    borderRadius: 12,
    borderWidth: StyleSheet.hairlineWidth,
    flex: 1,
    marginBottom: 10,
    maxWidth: "49%",
    overflow: "hidden"
  },
  cardAvailability: { color: storeLight.status.success, fontSize: 12, fontWeight: "700" },
  cardBody: { gap: 4, padding: 10 },
  cardCta: {
    alignItems: "center",
    backgroundColor: storeLight.accent.brandOnLight,
    borderRadius: 999,
    justifyContent: "center",
    marginTop: 6,
    minHeight: 38
  },
  cardCtaText: { color: storeLight.cta.text, fontSize: 13, fontWeight: "900" },
  cardImage: { aspectRatio: 1, backgroundColor: storeLight.bg.skeleton, width: "100%" },
  cardImageFallback: { alignItems: "center", justifyContent: "center" },
  cardMedia: { position: "relative" },
  cardPrice: { color: storeLight.status.success, fontSize: 17, fontWeight: "900" },
  cardSeller: { color: storeLight.text.muted, fontSize: 12 },
  cardSold: { color: storeLight.status.error },
  cardTitle: { color: storeLight.text.primary, fontSize: 14, fontWeight: "700", lineHeight: 19 },
  cartBadge: {
    backgroundColor: storeLight.status.error,
    borderRadius: 10,
    color: "#FFFFFF",
    fontSize: 9,
    fontWeight: "900",
    minWidth: 17,
    overflow: "hidden",
    paddingHorizontal: 3,
    paddingVertical: 2,
    position: "absolute",
    right: -3,
    textAlign: "center",
    top: -3
  },
  categoryChip: {
    backgroundColor: storeLight.bg.card,
    borderColor: storeLight.border.hairline,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    justifyContent: "center",
    minHeight: 36,
    paddingHorizontal: 14
  },
  categoryChipActive: { backgroundColor: storeLight.bg.headerFrom, borderColor: storeLight.bg.headerFrom },
  categoryRail: { gap: 8, paddingVertical: 10 },
  categoryText: { color: storeLight.text.primary, fontSize: 12, fontWeight: "700" },
  categoryTextActive: { color: storeLight.text.onDark, fontWeight: "900" },
  center: {
    alignItems: "center",
    backgroundColor: storeLight.bg.page,
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerText: { color: storeLight.text.muted, marginTop: 12 },
  content: { padding: 12, paddingBottom: 32 },
  disabled: { opacity: 0.45 },
  empty: {
    alignItems: "center",
    backgroundColor: storeLight.bg.card,
    borderColor: storeLight.border.hairline,
    borderRadius: 14,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 8,
    padding: 24
  },
  emptyText: { color: storeLight.text.muted, fontSize: 13, lineHeight: 20, textAlign: "center" },
  emptyTitle: { color: storeLight.text.primary, fontSize: 17, fontWeight: "900" },
  gridRow: { gap: 10 },
  header: { marginBottom: 12 },
  headerIcon: {
    alignItems: "center",
    borderColor: storeLight.border.hairline,
    borderRadius: 20,
    borderWidth: StyleSheet.hairlineWidth,
    height: 40,
    justifyContent: "center",
    position: "relative",
    width: 40
  },
  headerRow: { alignItems: "center", flexDirection: "row", gap: 7 },
  headerTitles: { flex: 1 },
  heartButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.92)",
    borderRadius: 16,
    height: 32,
    justifyContent: "center",
    position: "absolute",
    right: 6,
    top: 6,
    width: 32
  },
  list: { backgroundColor: storeLight.bg.page, flex: 1 },
  notice: { color: storeLight.text.primary, fontSize: 13, fontWeight: "700", marginTop: 10 },
  root: { backgroundColor: storeLight.bg.page, flex: 1 },
  searchButton: {
    alignItems: "center",
    backgroundColor: storeLight.accent.brandOnLight,
    borderRadius: 8,
    justifyContent: "center",
    minHeight: 40,
    paddingHorizontal: 14
  },
  searchButtonText: { color: storeLight.cta.text, fontWeight: "900" },
  searchIcon: { left: 11, position: "absolute", zIndex: 1 },
  searchInput: {
    backgroundColor: storeLight.bg.card,
    borderColor: storeLight.border.hairline,
    borderRadius: 8,
    borderWidth: StyleSheet.hairlineWidth,
    color: storeLight.text.primary,
    flex: 1,
    minHeight: 44,
    paddingHorizontal: 12,
    paddingLeft: 34
  },
  searchRow: { alignItems: "center", flexDirection: "row", gap: 8, marginTop: 12 },
  soldScrim: {
    alignItems: "center",
    backgroundColor: "rgba(19, 26, 34, 0.55)",
    bottom: 0,
    justifyContent: "center",
    left: 0,
    position: "absolute",
    right: 0,
    top: 0
  },
  soldScrimText: { color: "#FFFFFF", fontSize: 14, fontWeight: "900", letterSpacing: 1.5 },
  sponsoredBadge: {
    backgroundColor: storeLight.bg.headerFrom,
    borderRadius: 4,
    color: storeLight.accent.brand,
    fontSize: 10,
    fontWeight: "900",
    left: 6,
    overflow: "hidden",
    paddingHorizontal: 6,
    paddingVertical: 3,
    position: "absolute",
    top: 6
  },
  subtitle: { color: storeLight.text.muted, fontSize: 13, marginTop: 3 },
  tab: { borderBottomColor: "transparent", borderBottomWidth: 3, paddingBottom: 7, paddingHorizontal: 4 },
  tabActive: { borderBottomColor: storeLight.accent.brandOnLight },
  tabRow: { borderBottomColor: storeLight.border.hairline, borderBottomWidth: StyleSheet.hairlineWidth, flexDirection: "row", gap: 18, marginTop: 14 },
  tabText: { color: storeLight.text.muted, fontSize: 14, fontWeight: "700" },
  tabTextActive: { color: storeLight.text.primary, fontWeight: "900" },
  title: { color: storeLight.text.primary, fontSize: 24, fontWeight: "900" },
  utilityLink: { color: storeLight.text.link, fontSize: 13, fontWeight: "800" },
  utilityRow: { flexDirection: "row", gap: 18, marginTop: 6 }
}));
