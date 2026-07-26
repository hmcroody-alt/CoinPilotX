import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { useEffect, useMemo, useState } from "react";
import {
  ActivityIndicator,
  FlatList,
  Image,
  Modal,
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
  marketplaceSellerAuthor,
  MarketplaceListing,
  marketplaceWebUrl,
  openMarketplaceCheckout,
  reportMarketplaceListing,
  saveMarketplaceListing,
  searchMarketplace,
  startMarketplaceSellerChat
} from "../api/marketplace";
import { DIGITAL_COMMERCE_ENABLED } from "../api/config";
import { mediaDisplayUrl } from "../api/feed";
import { profileNavigationParams, resolveProfileTarget } from "../api/profileTarget";
import { mediaViewerItemFromPulseMedia, NativeMediaViewer } from "../components/NativeMediaViewer";
import { ContentTranslation } from "../components/ContentTranslation";
import { registerSyncInvalidation } from "../core/eventSync";
import { useTranslation } from "../i18n";
import { useBottomNavSurface } from "../navigation/BottomNavVisibility";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

type Props = Partial<NativeStackScreenProps<RootStackParamList, "MarketplaceDetail">>;

export function MarketplaceScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
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

  async function load(mode: "initial" | "refresh" | "search" = "initial", nextQuery = query) {
    setError("");
    setOffline(false);
    if (mode === "initial") setLoading(true);
    if (mode === "refresh") setRefreshing(true);
    try {
      const result = await searchMarketplace({ query: nextQuery, limit: 32 });
      const nextItems = focusInitialListing(result.items || [], initialListingId);
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
        setError(loadError instanceof Error ? loadError.message : t("commerce:marketplace.loadFailed"));
      }
    } finally {
      setLoading(false);
      setRefreshing(false);
    }
  }

  useEffect(() => {
    load("initial").catch(() => undefined);
  }, [initialListingId]);

  useEffect(() => {
    const unregisterMarketplace = registerSyncInvalidation("marketplace", () => load("refresh"));
    return unregisterMarketplace;
  }, [initialListingId, query]);

  function updateListing(listingId: number, next: Partial<MarketplaceListing>) {
    setItems((current) => current.map((item) => (item.id === listingId ? { ...item, ...next } : item)));
    setDetail((current) => (current?.id === listingId ? { ...current, ...next } : current));
  }

  async function handleSave(listing: MarketplaceListing) {
    setBusyId(listing.id);
    updateListing(listing.id, { saved: true });
    try {
      await saveMarketplaceListing(listing.id);
    } catch (saveError) {
      updateListing(listing.id, { saved: listing.saved });
      setError(saveError instanceof Error ? saveError.message : t("commerce:marketplace.saveFailed"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleReport(listing: MarketplaceListing) {
    setBusyId(listing.id);
    try {
      await reportMarketplaceListing(listing.id, "Needs review");
      setError(t("commerce:marketplace.reportSent"));
    } catch (reportError) {
      setError(reportError instanceof Error ? reportError.message : t("commerce:marketplace.reportFailed"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleContactSeller(listing: MarketplaceListing) {
    if (!listing.seller_user_id) {
      setError(t("commerce:marketplace.sellerNotMessageable"));
      return;
    }
    setBusyId(listing.id);
    try {
      const result = await startMarketplaceSellerChat(listing.seller_user_id);
      if (result.conversation_id && navigation) {
        navigation.navigate("Chat", { conversationId: result.conversation_id, title: listing.seller_name || t("commerce:marketplace.seller") });
      } else {
        setError(t("commerce:marketplace.sellerChatUnavailable"));
      }
    } catch (contactError) {
      setError(contactError instanceof Error ? contactError.message : t("commerce:marketplace.sellerChatFailed"));
    } finally {
      setBusyId(null);
    }
  }

  async function handleCheckout(listing: MarketplaceListing) {
    setBusyId(listing.id);
    try {
      const result = await openMarketplaceCheckout(listing.id);
      if (!result.checkout_url) setError(result.message || t("commerce:marketplace.checkoutUnavailable"));
    } catch (checkoutError) {
      setError(checkoutError instanceof Error ? checkoutError.message : t("commerce:marketplace.checkoutUnavailable"));
    } finally {
      setBusyId(null);
    }
  }

  if (loading && !items.length) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>{t("commerce:marketplace.loading")}</Text>
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
            <Text style={styles.title}>{t("commerce:marketplace.title")}</Text>
            <Text style={styles.subtitle}>{offline ? t("commerce:marketplace.offlineSubtitle") : t("commerce:marketplace.subtitle")}</Text>
            <Pressable accessibilityRole="button" style={styles.sellerGatewayButton} onPress={() => navigation?.navigate("SellerStore", { title: t("common:screens.sellerStore") })}>
              <Text style={styles.sellerGatewayText}>{t("commerce:marketplace.sellerStoreManagement")}</Text>
            </Pressable>
            <Pressable accessibilityRole="button" style={styles.sellerGatewayButton} onPress={() => navigation?.navigate("BuyerOrders", { title: t("common:screens.purchaseHistory") })}>
              <Text style={styles.sellerGatewayText}>{t("common:screens.purchaseHistory")}</Text>
            </Pressable>
            <View style={styles.searchRow}>
              <TextInput
                style={styles.searchInput}
                value={query}
                onChangeText={setQuery}
                placeholder={t("commerce:marketplace.searchPlaceholder")}
                placeholderTextColor={colors.muted}
                returnKeyType="search"
                onSubmitEditing={() => load("search", query).catch(() => undefined)}
              />
              <Pressable accessibilityRole="button" style={styles.searchButton} onPress={() => load("search", query).catch(() => undefined)}>
                <Text style={styles.searchButtonText}>{t("common:actions.search")}</Text>
              </Pressable>
            </View>
            {error ? <Text style={styles.error}>{error}</Text> : null}
          </View>
        }
        ListEmptyComponent={
          <View style={styles.empty}>
            <Text style={styles.emptyTitle}>{error ? t("commerce:marketplace.unavailableTitle") : t("commerce:marketplace.noListingsTitle")}</Text>
            <Text style={styles.emptyText}>{error || t("commerce:marketplace.noListingsBody")}</Text>
          </View>
        }
        renderItem={({ item }) => (
          <MarketplaceCard
            listing={item}
            busy={busyId === item.id}
            onOpen={setDetail}
            onSave={handleSave}
            onReport={handleReport}
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
        onProfile={(listing) => {
          const params = profileNavigationParams(resolveProfileTarget({
            userId: listing.seller_user_id,
            public_player_id: listing.seller_public_player_id,
            username: listing.seller_username,
            display_name: listing.seller_name,
            source: "marketplace"
          }), listing.seller_name || t("commerce:marketplace.seller"));
          if (params) navigation?.navigate("ProfileDetail", params);
        }}
      />
    </View>
  );
}

function MarketplaceCard({ listing, busy, onOpen, onSave, onReport }: {
  listing: MarketplaceListing;
  busy?: boolean;
  onOpen: (listing: MarketplaceListing) => void;
  onSave: (listing: MarketplaceListing) => void;
  onReport: (listing: MarketplaceListing) => void;
}) {
  const { t } = useTranslation();
  const cover = listing.media?.[0] ? mediaDisplayUrl(listing.media[0]) : "";
  return (
    <Pressable accessibilityRole="button" style={styles.card} onPress={() => onOpen(listing)}>
      {cover ? <Image source={{ uri: cover }} style={styles.cover} resizeMode="cover" /> : <View style={styles.coverFallback}><Text style={styles.coverText}>{t("commerce:marketplace.title")}</Text></View>}
      <View style={styles.cardBody}>
        <Text style={styles.cardTitle}>{listing.title}</Text>
        <ContentTranslation
          contentType="marketplace"
          contentRef={listing.id}
          text={listing.short_description || listing.description || t("commerce:marketplace.listingFallbackText")}
          textStyle={styles.cardDescription}
          numberOfLines={2}
        />
        <View style={styles.pillRow}>
          <Text style={styles.pill}>{listing.category || t("commerce:marketplace.categoryFallback")}</Text>
          <Text style={styles.pill}>{listing.price_label || t("commerce:marketplace.priceFallback")}</Text>
          <Text style={styles.pill}>{t("commerce:marketplace.safetyScore", { score: listing.safety_score || 0 })}</Text>
        </View>
        <Text style={styles.sellerText}>{t("commerce:marketplace.sellerLine", { name: listing.seller_name || t("commerce:marketplace.sellerNameFallback") })}</Text>
        <View style={styles.cardActions}>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy || listing.saved }} style={styles.smallButton} disabled={busy || listing.saved} onPress={() => onSave(listing)}>
            <Text style={styles.smallButtonText}>{listing.saved ? t("common:status.saved") : t("common:actions.save")}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy }} style={styles.smallButton} disabled={busy} onPress={() => onReport(listing)}>
            <Text style={styles.smallButtonText}>{t("common:actions.report")}</Text>
          </Pressable>
        </View>
      </View>
    </Pressable>
  );
}

function MarketplaceDetailModal({ listing, busy, onClose, onSave, onReport, onContactSeller, onCheckout, onProfile }: {
  listing: MarketplaceListing | null;
  busy?: boolean;
  onClose: () => void;
  onSave: (listing: MarketplaceListing) => void;
  onReport: (listing: MarketplaceListing) => void;
  onContactSeller: (listing: MarketplaceListing) => void;
  onCheckout: (listing: MarketplaceListing) => void;
  onProfile: (listing: MarketplaceListing) => void;
}) {
  const { t } = useTranslation();
  const [viewerOpen, setViewerOpen] = useState(false);
  const viewerItems = useMemo(() => {
    if (!listing) return [];
    const author = marketplaceSellerAuthor(listing);
    return (listing.media || []).map((media) =>
      mediaViewerItemFromPulseMedia(media, {
        title: listing.title || t("commerce:marketplace.listingTitleFallback"),
        subtitle: listing.price_label || listing.category || t("commerce:marketplace.title"),
        author,
        sourceUrl: marketplaceWebUrl(listing.id)
      })
    );
  }, [listing, t]);
  if (!listing) return null;
  const cover = listing.media?.[0] ? mediaDisplayUrl(listing.media[0]) : "";
  const canNavigateProfile = Boolean(listing.seller_public_player_id || listing.seller_username);
  return (
    <Modal visible={Boolean(listing)} animationType="slide" onRequestClose={onClose}>
      <ScrollView style={styles.detailRoot} contentContainerStyle={styles.detailContent}>
        <View style={styles.detailHeader}>
          <Pressable accessibilityRole="button" style={styles.closeButton} onPress={onClose}>
            <Text style={styles.closeText}>{t("common:actions.close")}</Text>
          </Pressable>
        </View>
        <Pressable accessibilityRole="button" accessibilityState={{ disabled: !viewerItems.length }} disabled={!viewerItems.length} onPress={() => setViewerOpen(true)}>
          {cover ? <Image source={{ uri: cover }} style={styles.detailCover} resizeMode="cover" /> : <View style={styles.detailCoverFallback}><Text style={styles.coverText}>{t("commerce:marketplace.noMediaLoaded")}</Text></View>}
        </Pressable>
        <Text style={styles.detailTitle}>{listing.title}</Text>
        <Text style={styles.detailPrice}>{listing.price_label || t("commerce:marketplace.priceFallback")}</Text>
        <ContentTranslation
          contentType="marketplace"
          contentRef={listing.id}
          text={listing.description || listing.short_description || t("commerce:marketplace.noDescription")}
          textStyle={styles.detailDescription}
        />
        <View style={styles.pillRow}>
          <Text style={styles.pill}>{listing.category || t("commerce:marketplace.categoryFallback")}</Text>
          <Text style={styles.pill}>{t("commerce:marketplace.safetyScore", { score: listing.safety_score || 0 })}</Text>
          <Text style={styles.pill}>{listing.approval_status || listing.status || "approved"}</Text>
        </View>
        <Pressable accessibilityRole="button" accessibilityState={{ disabled: !canNavigateProfile }} style={styles.sellerPanel} disabled={!canNavigateProfile} onPress={() => onProfile(listing)}>
          <Text style={styles.sellerTitle}>{listing.seller_name || t("commerce:marketplace.sellerNameFallback")}</Text>
          <Text style={styles.sellerMeta}>{canNavigateProfile ? t("commerce:marketplace.openProfile") : t("commerce:marketplace.sellerProfileUnavailable")}</Text>
        </Pressable>
        <Text style={styles.safetyNotice}>{t("commerce:marketplace.safetyNotice")}</Text>
        <View style={styles.detailActions}>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy }} style={styles.primaryButton} disabled={busy} onPress={() => onContactSeller(listing)}>
            <Text style={styles.primaryText}>{t("commerce:marketplace.contactSeller")}</Text>
          </Pressable>
          {DIGITAL_COMMERCE_ENABLED ? (
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy }} style={styles.secondaryButton} disabled={busy} onPress={() => onCheckout(listing)}>
              <Text style={styles.secondaryText}>{t("commerce:marketplace.checkout")}</Text>
            </Pressable>
          ) : null}
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy || listing.saved }} style={styles.secondaryButton} disabled={busy || listing.saved} onPress={() => onSave(listing)}>
            <Text style={styles.secondaryText}>{listing.saved ? t("common:status.saved") : t("common:actions.save")}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy }} style={styles.secondaryButton} disabled={busy} onPress={() => onReport(listing)}>
            <Text style={styles.secondaryText}>{t("common:actions.report")}</Text>
          </Pressable>
        </View>
      </ScrollView>
      <NativeMediaViewer visible={viewerOpen} items={viewerItems} title={t("commerce:marketplace.mediaViewerTitle")} onClose={() => setViewerOpen(false)} />
    </Modal>
  );
}

function focusInitialListing(items: MarketplaceListing[], listingId: number) {
  if (!listingId) return items;
  const index = items.findIndex((item) => item.id === listingId);
  if (index <= 0) return items;
  return [items[index], ...items.slice(0, index), ...items.slice(index + 1)];
}

const styles = StyleSheet.create({
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
    backgroundColor: colors.background,
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
    padding: 16,
    paddingBottom: 42
  },
  detailCover: {
    aspectRatio: 16 / 10,
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    width: "100%"
  },
  detailCoverFallback: {
    alignItems: "center",
    aspectRatio: 16 / 10,
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    justifyContent: "center"
  },
  detailDescription: {
    color: colors.text,
    fontSize: 15,
    lineHeight: 23,
    marginTop: 12
  },
  detailHeader: {
    alignItems: "center",
    flexDirection: "row",
    justifyContent: "space-between",
    marginBottom: 12
  },
  detailPrice: {
    color: colors.accent,
    fontSize: 18,
    fontWeight: "900",
    marginTop: 8
  },
  detailRoot: {
    backgroundColor: colors.background,
    flex: 1
  },
  detailTitle: {
    color: colors.text,
    fontSize: 25,
    fontWeight: "900",
    lineHeight: 31,
    marginTop: 16
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
    backgroundColor: colors.background,
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
    backgroundColor: colors.background,
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
    color: colors.muted,
    fontSize: 12,
    marginTop: 3
  },
  sellerPanel: {
    backgroundColor: colors.surface,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    marginTop: 16,
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
    color: colors.text,
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
  }
});
