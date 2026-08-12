import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { Ionicons } from "@expo/vector-icons";
import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  Linking,
  Modal,
  Pressable,
  RefreshControl,
  Share,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View
} from "react-native";
import {
  loadCachedMarketplace,
  marketplaceSellerAuthor,
  MarketplaceListing,
  marketplaceWebUrl,
  openMarketplaceCheckout,
  reportMarketplaceListing,
  searchMarketplace,
  startMarketplaceSellerChat
} from "../api/marketplace";
import { addToCart, fetchCart } from "../api/marketplaceCommerce";
import {
  canPurchaseMarketplaceListing as canPurchaseListing,
  isStocklessMarketplaceListing as isStockless,
  marketplaceAvailabilityCopy as availabilityCopy,
  marketplaceFulfillmentCopy as fulfillmentCopy
} from "../api/marketplaceBuyerPresentation";
import { conversationSplitEnabled } from "../api/conversationDomain";
import { mediaDisplayUrl } from "../api/feed";
import { profileNavigationParams, resolveProfileTarget } from "../api/profileTarget";
import { mediaViewerItemFromPulseMedia, NativeMediaViewer } from "../components/NativeMediaViewer";
import { ContentTranslation } from "../components/ContentTranslation";
import { registerSyncInvalidation } from "../core/eventSync";
import { useBottomNavSurface } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { observeSavedStates, peekSaveState, useSavedState } from "../social/savedStore";
import { setSaved } from "../social/useSaveAction";
import { colors } from "../theme/colors";
import { storeLight } from "../theme/marketplaceLight";
import { createThemedStyles } from "../theme/themedStyles";

type Props = Partial<NativeStackScreenProps<RootStackParamList, "MarketplaceDetail">>;

export function MarketplaceScreen({ route, navigation }: Props) {
  // Bottom-dock coupling: drives hide-on-scroll-down / reveal-on-scroll-up and
  // reserves the matching clearance so the last row never sits under the dock.
  const dock = useBottomNavSurface();
  const initialListingId = Number(route?.params?.listingId || 0);
  const [items, setItems] = useState<MarketplaceListing[]>([]);
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [refreshing, setRefreshing] = useState(false);
  const [offline, setOffline] = useState(false);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState<number | null>(null);
  const [detail, setDetail] = useState<MarketplaceListing | null>(null);
  const [cartCount, setCartCount] = useState(0);

  async function load(mode: "initial" | "refresh" | "search" = "initial", nextQuery = query) {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const result = await searchMarketplace({ query: nextQuery, limit: 32 });
      const nextItems = focusInitialListing(result.items || [], initialListingId);
      // A live search result is newer than anything the store holds, so it
      // corrects it — that is how a listing unsaved from the Saved screen stops
      // showing "Saved" here without this screen knowing that screen exists.
      // Cached results below deliberately do not: they are, by definition, old.
      observeSavedStates("marketplace", nextItems.map((item) => ({ id: item.id, saved: item.saved })));
      setItems(nextItems);
      if (initialListingId && nextItems.length) setDetail(nextItems.find((item) => item.id === initialListingId) || nextItems[0]);
    } catch (loadError) {
      const cached = await loadCachedMarketplace();
      if (cached.length) {
        const nextItems = focusInitialListing(cached, initialListingId);
        setItems(nextItems);
        setOffline(true);
        if (initialListingId) setDetail(nextItems[0]);
      } else {
        setError(loadError instanceof Error ? loadError.message : "Marketplace could not load.");
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
    fetchCart().then((cart) => setCartCount(cart.badgeCount)).catch(() => undefined);
  }, [initialListingId]);

  useEffect(() => {
    const unregisterMarketplace = registerSyncInvalidation("marketplace", () => load("refresh"));
    return unregisterMarketplace;
  }, [initialListingId, query]);

  function updateListing(listingId: number, next: Partial<MarketplaceListing>) {
    setItems((current) => current.map((item) => (item.id === listingId ? { ...item, ...next } : item)));
    setDetail((current) => (current?.id === listingId ? { ...current, ...next } : current));
  }

  /**
   * Save *and* unsave. This could only ever add: it forced `saved: true`, and
   * the card's answer to "what if the user taps again" was to disable the
   * button permanently, which left a mis-tap unrecoverable. The route now
   * accepts the state being asked for, so this is the same toggle every other
   * savable surface has.
   */
  async function handleSave(listing: MarketplaceListing) {
    // The state to invert is the one on screen, which is the store's, not the
    // one baked into the search payload. Taking it from `listing.saved` meant a
    // listing saved from the Saved screen was toggled from "not saved" here and
    // the tap re-saved something already saved.
    const wasSaved = peekSaveState("marketplace", listing.id)?.saved ?? Boolean(listing.saved);
    setBusyId(listing.id);
    try {
      const outcome = await setSaved({ type: "marketplace", id: listing.id }, !wasSaved);
      // Keep the payload in step so a later re-seed of an unmounted card agrees
      // with the store. The buttons read the store; this is bookkeeping.
      updateListing(listing.id, { saved: outcome.saved });
      if (!outcome.ok && outcome.message) setError(outcome.message);
    } finally {
      setBusyId(null);
    }
  }

  async function handleReport(listing: MarketplaceListing) {
    setBusyId(listing.id);
    try {
      await reportMarketplaceListing(listing.id, "Needs review");
      setError("Listing report sent.");
    } catch (reportError) {
      setError(reportError instanceof Error ? reportError.message : "Listing report failed.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleContactSeller(listing: MarketplaceListing) {
    if (!listing.seller_user_id) {
      setError("This seller cannot be messaged from the app yet.");
      return;
    }
    setBusyId(listing.id);
    try {
      const result = await startMarketplaceSellerChat(listing.seller_user_id);
      if (result.conversation_id && navigation) {
        // A message about a listing is commerce, so it belongs to the Commerce
        // Inbox. Routing through it (rather than straight to the thread) is what
        // makes Back land on the seller's commerce list instead of their friends.
        if (conversationSplitEnabled()) {
          navigation.navigate("BusinessOsMessages", {
            title: "Messages",
            focusConversationId: result.conversation_id
          });
        } else {
          navigation.navigate("Chat", { conversationId: result.conversation_id, title: listing.seller_name || "Seller" });
        }
      } else {
        setError("Seller chat is not available for this listing yet.");
      }
    } catch (contactError) {
      setError(contactError instanceof Error ? contactError.message : "Seller chat could not be opened.");
    } finally {
      setBusyId(null);
    }
  }

  async function handleCheckout(listing: MarketplaceListing) {
    if (!canPurchaseListing(listing)) {
      setError("This item is no longer available.");
      return;
    }
    setBusyId(listing.id);
    try {
      const result = await openMarketplaceCheckout(listing.id);
      if (result.checkout_url) await Linking.openURL(result.checkout_url);
      else setError(result.message || "Checkout is not available for this listing yet.");
    } catch (checkoutError) {
      setError(checkoutError instanceof Error ? checkoutError.message : "Checkout is not available for this listing yet.");
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
      const cart = await addToCart(listing.id, 1);
      setCartCount(cart.badgeCount);
      setError(`${listing.title || "Item"} added to cart.`);
    } catch (cartError) {
      setError(cartError instanceof Error ? cartError.message : "This item could not be added to your cart.");
    } finally {
      setBusyId(null);
    }
  }

  if (loading && !items.length) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
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
        data={items}
        keyExtractor={(item) => String(item.id)}
        refreshControl={<RefreshControl refreshing={refreshing} tintColor={colors.accent} onRefresh={() => load("refresh").catch(() => undefined)} />}
        ListHeaderComponent={
          <View style={styles.header}>
            <Text style={styles.title}>Marketplace</Text>
            <Text style={styles.subtitle}>{offline ? "Showing saved marketplace results" : "PulseSoc native marketplace"}</Text>
            <Pressable accessibilityRole="button" style={styles.sellerGatewayButton} onPress={() => navigation?.navigate("SellerStore", { title: "Seller / Store" })}>
              <Text style={styles.sellerGatewayText}>Seller / Store Management</Text>
            </Pressable>
            <Pressable accessibilityRole="button" style={styles.sellerGatewayButton} onPress={() => navigation?.navigate("BuyerOrders", { title: "Purchase History" })}>
              <Text style={styles.sellerGatewayText}>Purchase History</Text>
            </Pressable>
            <View style={styles.searchRow}>
              <TextInput
                style={styles.searchInput}
                value={query}
                onChangeText={setQuery}
                placeholder="Search listings, categories, sellers"
                placeholderTextColor={colors.muted}
                returnKeyType="search"
                onSubmitEditing={() => load("search", query).catch(() => undefined)}
              />
              <Pressable accessibilityRole="button" style={styles.searchButton} onPress={() => load("search", query).catch(() => undefined)}>
                <Text style={styles.searchButtonText}>Search</Text>
              </Pressable>
            </View>
            {error ? <Text style={styles.error}>{error}</Text> : null}
          </View>
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>{error ? "Marketplace unavailable" : "No marketplace listings"}</Text>
            <Text style={styles.emptyText}>{error || "Approved PulseSoc marketplace listings will appear here."}</Text>
          </View>
        }
        renderItem={({ item }) => (
          <MarketplaceCard
            listing={item}
            busy={busyId === item.id}
            onOpen={setDetail}
            onSave={handleSave}
            onAddToCart={handleAddToCart}
          />
        )}
      />
      <MarketplaceDetailModal
        listing={detail}
        busy={busyId === detail?.id}
        onClose={() => setDetail(null)}
        onSave={handleSave}
        onReport={handleReport}
        onContactSeller={handleContactSeller}
        onCheckout={handleCheckout}
        onAddToCart={handleAddToCart}
        onOpenCart={() => navigation?.navigate("MarketplaceCart", { title: `Cart${cartCount ? ` (${cartCount})` : ""}` })}
        onProfile={(listing) => {
          const params = profileNavigationParams(resolveProfileTarget({
            userId: listing.seller_user_id,
            public_player_id: listing.seller_public_player_id,
            username: listing.seller_username,
            display_name: listing.seller_name,
            source: "marketplace"
          }), listing.seller_name || "Seller");
          if (params) navigation?.navigate("ProfileDetail", params);
        }}
      />
    </View>
  );
}

function MarketplaceCard({ listing, busy, onOpen, onSave, onAddToCart }: {
  listing: MarketplaceListing;
  busy?: boolean;
  onOpen: (listing: MarketplaceListing) => void;
  onSave: (listing: MarketplaceListing) => void;
  onAddToCart: (listing: MarketplaceListing) => void;
}) {
  const cover = listing.media?.[0] ? mediaDisplayUrl(listing.media[0]) : "";
  // Read from the shared store, not from the listing payload. The payload is a
  // snapshot of whatever the last search returned; the store is what every other
  // surface writes to, so a listing saved from the Saved screen — or from the
  // detail modal rendered on top of this very card — shows as saved here without
  // a refetch. `listing.saved` still seeds it on first sight.
  const savedState = useSavedState("marketplace", listing.id, listing.saved);
  return (
    <Pressable accessibilityRole="button" style={styles.card} onPress={() => onOpen(listing)}>
      {cover ? <Image source={{ uri: cover }} style={styles.cover} resizeMode="cover" /> : <View style={styles.coverFallback}><Text style={styles.coverText}>Marketplace</Text></View>}
      <View style={styles.cardBody}>
        <Text style={styles.cardTitle}>{listing.title}</Text>
        <ContentTranslation
          contentType="marketplace"
          contentRef={listing.id}
          text={listing.short_description || listing.description || "PulseSoc listing"}
          textStyle={styles.cardDescription}
          numberOfLines={2}
        />
        <View style={styles.pillRow}>
          <Text style={styles.pill}>{listing.category || "Education"}</Text>
          <Text style={styles.pill}>{listing.price_label || "Request access"}</Text>
          <Text style={styles.pill}>{availabilityCopy(listing)}</Text>
        </View>
        <Text style={styles.sellerText}>Seller: {listing.seller_name || "PulseSoc Seller"}</Text>
        <View style={styles.cardActions}>
          <Pressable accessibilityRole="button" accessibilityLabel={`${savedState.saved ? "Remove" : "Save"} ${listing.title || "listing"}`} accessibilityState={{ disabled: busy, selected: savedState.saved }} style={styles.smallButton} disabled={busy} onPress={() => onSave(listing)}>
            <Text style={styles.smallButtonText}>{savedState.saved ? "Saved" : "Save"}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy || !canPurchaseListing(listing) }} style={styles.smallButton} disabled={busy || !canPurchaseListing(listing)} onPress={() => onAddToCart(listing)}>
            <Text style={styles.smallButtonText}>{canPurchaseListing(listing) ? "Add to cart" : "Sold out"}</Text>
          </Pressable>
        </View>
      </View>
    </Pressable>
  );
}

function MarketplaceDetailModal({ listing, busy, onClose, onSave, onReport, onContactSeller, onCheckout, onAddToCart, onOpenCart, onProfile }: {
  listing: MarketplaceListing | null;
  busy?: boolean;
  onClose: () => void;
  onSave: (listing: MarketplaceListing) => void;
  onReport: (listing: MarketplaceListing) => void;
  onContactSeller: (listing: MarketplaceListing) => void;
  onCheckout: (listing: MarketplaceListing) => void;
  onAddToCart: (listing: MarketplaceListing) => void;
  onOpenCart: () => void;
  onProfile: (listing: MarketplaceListing) => void;
}) {
  const [viewerOpen, setViewerOpen] = useState(false);
  const viewerItems = useMemo(() => {
    if (!listing) return [];
    const author = marketplaceSellerAuthor(listing);
    return (listing.media || []).map((media) =>
      mediaViewerItemFromPulseMedia(media, {
        title: listing.title || "Marketplace listing",
        subtitle: listing.price_label || listing.category || "Marketplace",
        author,
        sourceUrl: marketplaceWebUrl(listing.id)
      })
    );
  }, [listing]);
  // Same store, same key as the card underneath this modal — which is the point:
  // saving here used to leave that card still offering to save the same listing.
  // Called with id 0 when there is no listing so the hook order stays stable
  // across the early return below; nothing subscribes to `marketplace:0`.
  const savedState = useSavedState("marketplace", listing?.id || 0, listing?.saved);
  if (!listing) return null;
  const cover = listing.media?.[0] ? mediaDisplayUrl(listing.media[0]) : "";
  const canNavigateProfile = Boolean(listing.seller_public_player_id || listing.seller_username);
  const metadata = listing.listing_metadata || {};
  const condition = readMetadata(metadata, "condition");
  const location = readMetadata(metadata, "location");
  const fulfillment = fulfillmentCopy(listing);
  const availability = availabilityCopy(listing);
  const purchasable = canPurchaseListing(listing);
  return (
    <Modal visible={Boolean(listing)} animationType="slide" onRequestClose={onClose}>
      <View style={styles.detailShell}>
      <ScrollView style={styles.detailRoot} contentContainerStyle={styles.detailContent}>
        <View style={styles.detailHeader}>
          <Pressable accessibilityRole="button" accessibilityLabel="Back to Marketplace" style={styles.iconButton} onPress={onClose}>
            <Ionicons name="arrow-back" size={24} color={storeLight.text.primary} />
          </Pressable>
          <View style={styles.detailHeaderActions}>
            <Pressable accessibilityRole="button" accessibilityLabel="Share listing" style={styles.iconButton} onPress={() => Share.share({ title: listing.title || "Marketplace listing", message: `${listing.title || "Marketplace listing"}\n${marketplaceWebUrl(listing.id)}` }).catch(() => undefined)}>
              <Ionicons name="share-outline" size={23} color={storeLight.text.primary} />
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityLabel={savedState.saved ? "Remove from saved" : "Save listing"} accessibilityState={{ selected: savedState.saved }} style={styles.iconButton} onPress={() => onSave(listing)}>
              <Ionicons name={savedState.saved ? "heart" : "heart-outline"} size={24} color={savedState.saved ? storeLight.status.error : storeLight.text.primary} />
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityLabel="Open cart" style={styles.iconButton} onPress={onOpenCart}>
              <Ionicons name="cart-outline" size={24} color={storeLight.text.primary} />
            </Pressable>
          </View>
        </View>
        <Pressable style={styles.detailMedia} accessibilityRole="button" accessibilityLabel={`View ${viewerItems.length || 1} product media item${viewerItems.length === 1 ? "" : "s"}`} accessibilityState={{ disabled: !viewerItems.length }} disabled={!viewerItems.length} onPress={() => setViewerOpen(true)}>
          {cover ? <Image source={{ uri: cover }} style={styles.detailCover} resizeMode="cover" /> : <View style={styles.detailCoverFallback}><Text style={styles.coverText}>No media loaded</Text></View>}
          <View style={styles.mediaCount}><Text style={styles.mediaCountText}>1/{Math.max(1, viewerItems.length)}</Text></View>
        </Pressable>
        <View style={styles.productSection}>
          <Text style={styles.detailTitle}>{listing.title}</Text>
          <Text style={styles.detailPrice}>{listing.price_label || "Request access"}</Text>
          <View style={styles.pillRow}>
            <Text style={styles.consumerPill}>{listing.category || "Marketplace"}</Text>
            {condition ? <Text style={styles.consumerPill}>{humanize(condition)}</Text> : null}
            <Text style={[styles.consumerPill, !purchasable && styles.soldPill]}>{availability}</Text>
          </View>
          <DetailFact label="Category" value={[listing.category, listing.subcategory].filter(Boolean).join(" › ")} />
          {condition ? <DetailFact label="Condition" value={humanize(condition)} /> : null}
          {location ? <DetailFact label="Location" value={location} /> : null}
          <DetailFact label="Delivery" value={fulfillment} />
          {listing.quantity != null && !isStockless(listing) ? <DetailFact label="Quantity" value={availability} /> : null}
        </View>
        <Pressable accessibilityRole="button" accessibilityState={{ disabled: !canNavigateProfile }} style={styles.sellerPanel} disabled={!canNavigateProfile} onPress={() => onProfile(listing)}>
          <View style={styles.sellerAvatar}><Text style={styles.sellerAvatarText}>{(listing.seller_name || "P").trim().slice(0, 1).toUpperCase()}</Text></View>
          <View style={styles.sellerInfo}>
            <Text style={styles.sellerTitle}>{listing.seller_name || "PulseSoc Seller"}</Text>
            <View style={styles.verifiedRow}><Ionicons name="shield-checkmark" size={14} color={storeLight.status.success} /><Text style={styles.sellerMeta}>Verified Marketplace seller</Text></View>
          </View>
          <Text style={styles.viewStoreText}>{canNavigateProfile ? "View store" : "Seller"}</Text>
        </Pressable>
        <View style={styles.infoSection}>
          <Text style={styles.sectionTitle}>Description</Text>
        <ContentTranslation
          contentType="marketplace"
          contentRef={listing.id}
          text={listing.description || listing.short_description || "No description loaded."}
          textStyle={styles.detailDescription}
        />
        </View>
        <View style={styles.infoSection}>
          <Text style={styles.sectionTitle}>Buyer protection</Text>
          <ProtectionRow icon="lock-closed-outline" text="Payment is handled through PulseSoc secure checkout." />
          <ProtectionRow icon="receipt-outline" text="Your order and receipt appear in Purchase History after payment confirmation." />
          <ProtectionRow icon="refresh-outline" text="Eligible returns and disputes are managed from your order." />
        </View>
        <View style={styles.secondaryActions}>
          <Pressable accessibilityRole="button" style={styles.textAction} onPress={() => onContactSeller(listing)}><Ionicons name="chatbubble-outline" size={18} color={storeLight.text.link} /><Text style={styles.textActionLabel}>Message seller</Text></Pressable>
          <Pressable accessibilityRole="button" style={styles.textAction} onPress={() => onReport(listing)}><Ionicons name="flag-outline" size={18} color={storeLight.text.link} /><Text style={styles.textActionLabel}>Report</Text></Pressable>
        </View>
      </ScrollView>
        <View style={styles.purchaseBar}>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy || !purchasable }} style={[styles.cartButton, (!purchasable || busy) && styles.disabledButton]} disabled={busy || !purchasable} onPress={() => onAddToCart(listing)}>
            <Ionicons name="cart-outline" size={19} color={storeLight.text.link} /><Text style={styles.cartButtonText}>{purchasable ? "Add to cart" : "Sold out"}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy || !purchasable }} style={[styles.buyButton, (!purchasable || busy) && styles.disabledButton]} disabled={busy || !purchasable} onPress={() => onCheckout(listing)}>
            <Text style={styles.buyButtonText}>{busy ? "Please wait…" : "Buy now"}</Text>
          </Pressable>
        </View>
      </View>
      <NativeMediaViewer visible={viewerOpen} items={viewerItems} title="Marketplace media" onClose={() => setViewerOpen(false)} />
    </Modal>
  );
}

function DetailFact({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return <View style={styles.factRow}><Text style={styles.factLabel}>{label}</Text><Text style={styles.factValue}>{value}</Text></View>;
}

function ProtectionRow({ icon, text }: { icon: keyof typeof Ionicons.glyphMap; text: string }) {
  return <View style={styles.protectionRow}><Ionicons name={icon} size={19} color={storeLight.status.success} /><Text style={styles.protectionText}>{text}</Text></View>;
}

function readMetadata(metadata: Record<string, unknown>, key: string) {
  const value = metadata[key];
  return typeof value === "string" ? value.trim() : "";
}

function humanize(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

function focusInitialListing(items: MarketplaceListing[], listingId: number) {
  if (!listingId) return items;
  const index = items.findIndex((item) => item.id === listingId);
  if (index <= 0) return items;
  return [items[index], ...items.slice(0, index), ...items.slice(index + 1)];
}

const styles = createThemedStyles(() => ({
  card: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginBottom: 12,
    overflow: "hidden"
  },
  cardActions: {
    flexDirection: "row",
    gap: 12,
    marginTop: 12
  },
  cardBody: {
    gap: 8,
    padding: 14
  },
  cardDescription: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 20
  },
  cardTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  center: {
    alignItems: "center",
    backgroundColor: "transparent",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  centerText: {
    color: colors.muted,
    marginTop: 12
  },
  closeButton: {
    minHeight: 38,
    paddingVertical: 9
  },
  closeText: {
    color: colors.text,
    fontWeight: "900"
  },
  content: {
    padding: 16,
    paddingBottom: 32
  },
  cover: {
    aspectRatio: 16 / 9,
    backgroundColor: colors.surfaceRaised,
    width: "100%"
  },
  coverFallback: {
    alignItems: "center",
    aspectRatio: 16 / 9,
    backgroundColor: colors.surfaceRaised,
    justifyContent: "center"
  },
  coverText: {
    color: colors.muted,
    fontWeight: "900"
  },
  detailActions: {
    gap: 10,
    marginTop: 16
  },
  detailContent: {
    paddingBottom: 28
  },
  detailCover: {
    aspectRatio: 1,
    backgroundColor: storeLight.bg.skeleton,
    width: "100%"
  },
  detailCoverFallback: {
    alignItems: "center",
    aspectRatio: 1,
    backgroundColor: storeLight.bg.skeleton,
    justifyContent: "center"
  },
  detailDescription: {
    color: storeLight.text.primary,
    fontSize: 15,
    lineHeight: 23,
    marginTop: 12
  },
  detailHeader: {
    alignItems: "center",
    backgroundColor: storeLight.bg.card,
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 58,
    paddingHorizontal: 10
  },
  detailPrice: {
    color: storeLight.status.success,
    fontSize: 23,
    fontWeight: "900",
    marginTop: 4
  },
  detailRoot: {
    backgroundColor: storeLight.bg.page,
    flex: 1
  },
  detailTitle: {
    color: storeLight.text.primary,
    fontSize: 27,
    fontWeight: "900",
    lineHeight: 32
  },
  empty: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    padding: 18
  },
  emptyText: {
    color: colors.muted,
    lineHeight: 21,
    marginTop: 6
  },
  emptyTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  error: {
    color: colors.accent,
    fontSize: 13,
    fontWeight: "800",
    marginTop: 10
  },
  header: {
    marginBottom: 14
  },
  list: {
    backgroundColor: "transparent",
    flex: 1
  },
  pill: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.muted,
    fontSize: 12,
    fontWeight: "800",
    overflow: "hidden",
    paddingHorizontal: 9,
    paddingVertical: 6
  },
  pillRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    paddingVertical: 13
  },
  primaryText: {
    color: colors.background,
    fontWeight: "900"
  },
  root: {
    backgroundColor: "transparent",
    flex: 1
  },
  safetyNotice: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18,
    marginTop: 14
  },
  searchButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    justifyContent: "center",
    paddingHorizontal: 14
  },
  searchButtonText: {
    color: colors.background,
    fontWeight: "900"
  },
  searchInput: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    flex: 1,
    padding: 12
  },
  searchRow: {
    flexDirection: "row",
    gap: 8,
    marginTop: 14
  },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingVertical: 13
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "900"
  },
  sellerMeta: {
    color: storeLight.text.muted,
    fontSize: 12,
    fontWeight: "600"
  },
  sellerPanel: {
    alignItems: "center",
    backgroundColor: storeLight.bg.card,
    borderColor: storeLight.border.hairline,
    borderRadius: 14,
    borderWidth: 1,
    flexDirection: "row",
    marginHorizontal: 16,
    marginTop: 12,
    padding: 14
  },
  sellerGatewayButton: {
    alignItems: "center",
    borderColor: "rgba(37, 208, 167, 0.36)",
    borderRadius: 8,
    borderWidth: 1,
    justifyContent: "center",
    marginTop: 12,
    minHeight: 42
  },
  sellerGatewayText: {
    color: colors.accent,
    fontWeight: "900"
  },
  sellerText: {
    color: colors.muted,
    fontSize: 13
  },
  sellerTitle: {
    color: storeLight.text.primary,
    fontWeight: "900"
  },
  smallButton: {
    minHeight: 34,
    paddingVertical: 8
  },
  smallButtonText: {
    color: colors.accentStrong,
    fontSize: 13,
    fontWeight: "900"
  },
  subtitle: {
    color: colors.muted,
    fontSize: 13,
    marginTop: 3
  },
  title: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900"
  },
  webButton: {
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 9
  },
  webButtonText: {
    color: colors.text,
    fontWeight: "900"
  },
  detailShell: {
    backgroundColor: storeLight.bg.page,
    flex: 1
  },
  detailHeaderActions: {
    alignItems: "center",
    flexDirection: "row",
    gap: 4
  },
  iconButton: {
    alignItems: "center",
    height: 46,
    justifyContent: "center",
    width: 46
  },
  detailMedia: {
    backgroundColor: storeLight.bg.skeleton,
    position: "relative"
  },
  mediaCount: {
    backgroundColor: "rgba(15,17,17,.76)",
    borderRadius: 14,
    bottom: 12,
    paddingHorizontal: 9,
    paddingVertical: 5,
    position: "absolute",
    right: 12
  },
  mediaCountText: { color: "#FFFFFF", fontSize: 12, fontWeight: "800" },
  productSection: {
    backgroundColor: storeLight.bg.card,
    borderBottomColor: storeLight.border.hairline,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 8,
    padding: 16
  },
  consumerPill: {
    backgroundColor: storeLight.bg.page,
    borderColor: storeLight.border.hairline,
    borderRadius: 999,
    borderWidth: StyleSheet.hairlineWidth,
    color: storeLight.text.primary,
    fontSize: 12,
    fontWeight: "700",
    overflow: "hidden",
    paddingHorizontal: 10,
    paddingVertical: 6
  },
  soldPill: { color: storeLight.status.error },
  factRow: { flexDirection: "row", gap: 14, paddingTop: 3 },
  factLabel: { color: storeLight.text.muted, fontSize: 13, width: 82 },
  factValue: { color: storeLight.text.primary, flex: 1, fontSize: 13, fontWeight: "600" },
  sellerAvatar: {
    alignItems: "center",
    backgroundColor: "#DDF8EE",
    borderRadius: 24,
    height: 48,
    justifyContent: "center",
    width: 48
  },
  sellerAvatarText: { color: storeLight.text.primary, fontSize: 18, fontWeight: "900" },
  sellerInfo: { flex: 1, gap: 4, marginLeft: 11 },
  verifiedRow: { alignItems: "center", flexDirection: "row", gap: 4 },
  viewStoreText: { color: storeLight.text.link, fontSize: 13, fontWeight: "800" },
  infoSection: {
    backgroundColor: storeLight.bg.card,
    borderColor: storeLight.border.hairline,
    borderRadius: 14,
    borderWidth: StyleSheet.hairlineWidth,
    gap: 10,
    marginHorizontal: 16,
    marginTop: 12,
    padding: 15
  },
  sectionTitle: { color: storeLight.text.primary, fontSize: 17, fontWeight: "900" },
  protectionRow: { alignItems: "flex-start", flexDirection: "row", gap: 10 },
  protectionText: { color: storeLight.text.muted, flex: 1, fontSize: 13, lineHeight: 19 },
  secondaryActions: { flexDirection: "row", gap: 12, marginHorizontal: 16, marginTop: 14 },
  textAction: {
    alignItems: "center",
    borderColor: storeLight.border.secondaryButton,
    borderRadius: 999,
    borderWidth: 1,
    flex: 1,
    flexDirection: "row",
    gap: 7,
    justifyContent: "center",
    minHeight: 46
  },
  textActionLabel: { color: storeLight.text.link, fontSize: 13, fontWeight: "800" },
  purchaseBar: {
    backgroundColor: storeLight.bg.card,
    borderTopColor: storeLight.border.hairline,
    borderTopWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 10,
    paddingBottom: 12,
    paddingHorizontal: 14,
    paddingTop: 10
  },
  cartButton: {
    alignItems: "center",
    borderColor: storeLight.text.link,
    borderRadius: 999,
    borderWidth: 1.5,
    flex: 1,
    flexDirection: "row",
    gap: 7,
    justifyContent: "center",
    minHeight: 52
  },
  cartButtonText: { color: storeLight.text.link, fontSize: 15, fontWeight: "900" },
  buyButton: {
    alignItems: "center",
    backgroundColor: storeLight.accent.brand,
    borderRadius: 999,
    flex: 1,
    justifyContent: "center",
    minHeight: 52
  },
  buyButtonText: { color: storeLight.text.primary, fontSize: 15, fontWeight: "900" },
  disabledButton: { opacity: 0.45 }
}));
