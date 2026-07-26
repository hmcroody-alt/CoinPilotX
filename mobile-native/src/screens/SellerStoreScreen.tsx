import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Image, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import {
  applyMarketplaceSeller,
  connectMarketplacePayout,
  deleteMarketplaceSellerListing,
  loadCachedSellerStore,
  loadSellerStoreSnapshot,
  MarketplaceListing,
  MarketplaceSellerOrder,
  marketplaceSellerAuthor,
  pauseMarketplaceSellerListing,
  resumeMarketplaceSellerListing,
  sellerStoreWebUrl,
  updateMarketplaceSellerListing
} from "../api/marketplace";
import { DIGITAL_COMMERCE_ENABLED } from "../api/config";
import { mediaDisplayUrl } from "../api/feed";
import { mediaViewerItemFromPulseMedia, NativeMediaViewer } from "../components/NativeMediaViewer";
import { Panel } from "../components/Panel";
import { Screen } from "../components/Screen";
import { registerSyncInvalidation } from "../core/eventSync";
import { Formatters, useFormatters, useTranslation } from "../i18n";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

type Props = {
  route?: { params?: RootStackParamList["SellerStore"] };
  navigation: {
    navigate: (...args: any[]) => void;
  };
};

export function SellerStoreScreen({ route, navigation }: Props) {
  const { t } = useTranslation();
  const fmt = useFormatters();
  const mode = route?.params?.mode || "overview";
  const [listings, setListings] = useState<MarketplaceListing[]>([]);
  const [orders, setOrders] = useState<MarketplaceSellerOrder[]>([]);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [bio, setBio] = useState("");
  const [viewerOpen, setViewerOpen] = useState(false);
  const [viewerIndex, setViewerIndex] = useState(0);
  const [editingListingId, setEditingListingId] = useState(0);
  const [editTitle, setEditTitle] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editCategory, setEditCategory] = useState("");
  const [editPriceLabel, setEditPriceLabel] = useState("");
  const [editQuantity, setEditQuantity] = useState("");

  async function load() {
    setMessage("");
    setOffline(false);
    setLoading(true);
    try {
      const snapshot = await loadSellerStoreSnapshot();
      setListings(snapshot.listings || []);
      setOrders(snapshot.orders || []);
    } catch (error) {
      const cached = await loadCachedSellerStore();
      if (cached) {
        setListings(cached.listings || []);
        setOrders(cached.orders || []);
        setOffline(true);
      } else {
        setMessage(error instanceof Error ? error.message : t("commerce:marketplace.sellerToolsLoadFailed"));
      }
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load().catch(() => undefined);
  }, []);

  useEffect(() => {
    const refreshStore = () => load();
    const unregisterSeller = registerSyncInvalidation("seller_inventory", refreshStore);
    const unregisterMarketplace = registerSyncInvalidation("marketplace", refreshStore);
    const unregisterOrders = registerSyncInvalidation("orders", refreshStore);
    return () => {
      unregisterSeller();
      unregisterMarketplace();
      unregisterOrders();
    };
  }, []);

  async function submitApplication() {
    setBusy("apply");
    setMessage("");
    try {
      const result = await applyMarketplaceSeller({ display_name: displayName, bio });
      setMessage(result.message || t("commerce:marketplace.sellerApplicationSaved"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("commerce:marketplace.sellerApplicationFailed"));
    } finally {
      setBusy("");
    }
  }

  async function startPayoutConnect() {
    setBusy("payout");
    setMessage("");
    try {
      const result = await connectMarketplacePayout();
      setMessage(result.message || t("commerce:marketplace.payoutChecked"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("commerce:marketplace.payoutUnavailable"));
    } finally {
      setBusy("");
    }
  }

  function startListingEdit(listing: MarketplaceListing) {
    setEditingListingId(listing.id);
    setEditTitle(listing.title || "");
    setEditDescription(listing.description || listing.short_description || "");
    setEditCategory(listing.category || "Education");
    setEditPriceLabel(listing.price_label || "Request access");
    setEditQuantity(String(listing.quantity || 0));
    setMessage("");
  }

  function applyListingResponse(listing?: MarketplaceListing) {
    if (!listing?.id) return;
    if (statusKey(listing) === "removed") {
      setListings((current) => current.filter((item) => item.id !== listing.id));
      setEditingListingId(0);
      setEditTitle("");
      setEditDescription("");
      setEditCategory("");
      setEditPriceLabel("");
      setEditQuantity("");
      return;
    }
    setListings((current) => current.map((item) => (item.id === listing.id ? { ...item, ...listing } : item)));
    startListingEdit(listing);
  }

  async function saveListingEdit() {
    if (!editingListingId) return;
    setBusy(`edit:${editingListingId}`);
    setMessage("");
    try {
      const result = await updateMarketplaceSellerListing(editingListingId, {
        title: editTitle.trim(),
        description: editDescription.trim(),
        category: editCategory.trim() || "Education",
        price_label: editPriceLabel.trim() || "Request access",
        quantity: Number(editQuantity || 0)
      });
      applyListingResponse(result.listing);
      setMessage(result.message || t("commerce:marketplace.listingUpdatedReview"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("commerce:marketplace.listingUpdateFailed"));
    } finally {
      setBusy("");
    }
  }

  async function mutateListingStatus(listing: MarketplaceListing, action: "pause" | "resume" | "delete") {
    setBusy(`${action}:${listing.id}`);
    setMessage("");
    try {
      const result =
        action === "pause"
          ? await pauseMarketplaceSellerListing(listing.id)
          : action === "resume"
            ? await resumeMarketplaceSellerListing(listing.id)
            : await deleteMarketplaceSellerListing(listing.id);
      applyListingResponse(result.listing);
      setMessage(result.message || t("commerce:marketplace.listingUpdated"));
    } catch (error) {
      setMessage(error instanceof Error ? error.message : t("commerce:marketplace.listingStatusUpdateFailed"));
    } finally {
      setBusy("");
    }
  }

  const mediaItems = useMemo(
    () =>
      listings
        .flatMap((listing) => {
          const author = marketplaceSellerAuthor(listing);
          return (listing.media || []).map((media) =>
            mediaViewerItemFromPulseMedia(media, {
              title: listing.title || t("commerce:marketplace.listingTitleFallback"),
              subtitle: listing.price_label || listing.category || t("commerce:marketplace.title"),
              author,
              sourceUrl: sellerStoreWebUrl("profile", listing.seller_username || "")
            })
          );
        })
        .slice(0, 32),
    [listings, t]
  );

  const activeListings = listings.filter((listing) => ["active", "approved", "review_ready"].includes(String(listing.status || listing.approval_status || "").toLowerCase()));
  const pendingListings = listings.filter((listing) => statusKey(listing) === "pending");
  const editingListing = listings.find((listing) => listing.id === editingListingId) || listings[0] || null;

  if (loading && !listings.length && !orders.length) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>{t("commerce:marketplace.loadingSellerControls")}</Text>
      </View>
    );
  }

  return (
    <Screen title={t("common:screens.sellerStore")} subtitle={t("commerce:marketplace.storeSubtitle")}>
      {offline ? <Text style={styles.warning}>{t("commerce:marketplace.storeOfflineNotice")}</Text> : null}
      {message ? <Text style={message.toLowerCase().includes("required") || message.toLowerCase().includes("failed") ? styles.error : styles.notice}>{message}</Text> : null}

      <Panel>
        <View style={styles.hero}>
          <Text style={styles.kicker}>{t("commerce:marketplace.storeKicker")}</Text>
          <Text style={styles.heroTitle}>{t("commerce:marketplace.storefrontReadiness")}</Text>
          <Text style={styles.heroCopy}>{t("commerce:marketplace.storeHeroCopy")}</Text>
        </View>
        <View style={styles.metricGrid}>
          <Metric label={t("commerce:marketplace.metricListingsLoaded")} value={fmt.number(listings.length)} />
          <Metric label={t("commerce:marketplace.metricActiveReviewReady")} value={fmt.number(activeListings.length)} />
          <Metric label={t("commerce:marketplace.metricPendingReview")} value={fmt.number(pendingListings.length)} />
          <Metric label={t("commerce:marketplace.metricOrdersLoaded")} value={fmt.number(orders.length)} />
        </View>
        <View style={styles.actionRow}>
          <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={() => navigation.navigate("Tabs", { screen: "Marketplace" })}>
            <Text style={styles.primaryText}>{t("commerce:marketplace.title")}</Text>
          </Pressable>
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>{t("commerce:marketplace.merchantApplication")}</Text>
        <Text style={styles.copy}>{t("commerce:marketplace.merchantApplicationCopy")}</Text>
        <TextInput
          style={styles.input}
          value={displayName}
          onChangeText={setDisplayName}
          placeholder={t("commerce:marketplace.sellerDisplayNamePlaceholder")}
          placeholderTextColor={colors.muted}
          autoCapitalize="words"
        />
        <TextInput
          style={[styles.input, styles.textArea]}
          value={bio}
          onChangeText={setBio}
          placeholder={t("commerce:marketplace.sellerBioPlaceholder")}
          placeholderTextColor={colors.muted}
          multiline
        />
        <View style={styles.actionRow}>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy === "apply" }} style={styles.primaryButton} disabled={busy === "apply"} onPress={submitApplication}>
            <Text style={styles.primaryText}>{busy === "apply" ? t("commerce:marketplace.saving") : t("commerce:marketplace.saveSellerApplication")}</Text>
          </Pressable>
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>{t("commerce:marketplace.listingManagement")}</Text>
        <Text style={styles.copy}>{t("commerce:marketplace.listingManagementCopy")}</Text>
        <View style={styles.actionRow}>
          <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={() => navigation.navigate("CameraStudio", { target: "marketplace", title: t("commerce:marketplace.mediaScreenTitle") })}>
            <Text style={styles.primaryText}>{t("commerce:marketplace.captureProductMedia")}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("MarketplaceCreateGateway", { title: t("common:screens.createListing") })}>
            <Text style={styles.secondaryText}>{t("common:screens.createListing")}</Text>
          </Pressable>
        </View>
        {listings.slice(0, 5).map((listing) => (
          <ListingRow key={listing.id} listing={listing} onOpen={() => navigation.navigate("MarketplaceDetail", { listingId: listing.id, title: listing.title || t("commerce:marketplace.title") })} />
        ))}
        {!listings.length ? <Text style={styles.emptyText}>{t("commerce:marketplace.noListingsLoaded")}</Text> : null}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>{t("commerce:marketplace.sellerInventory")}</Text>
        <Text style={styles.copy}>{t("commerce:marketplace.sellerInventoryCopy")}</Text>
        <View style={styles.inventoryList}>
          {listings.slice(0, 8).map((listing) => (
            <Pressable accessibilityRole="button"
              key={`inventory-${listing.id}`}
              style={[styles.inventoryRow, editingListing?.id === listing.id && styles.inventoryRowActive]}
              onPress={() => startListingEdit(listing)}
            >
              <View style={styles.inventoryCopy}>
                <Text style={styles.listingTitle} numberOfLines={1}>{listing.title || t("commerce:marketplace.listingTitleFallback")}</Text>
                <Text style={styles.listingMeta} numberOfLines={1}>{listing.price_label || t("commerce:marketplace.priceFallback")} · {listing.category || t("commerce:marketplace.title")}</Text>
              </View>
              <StatusPill listing={listing} />
            </Pressable>
          ))}
        </View>
        {!listings.length ? <Text style={styles.emptyText}>{t("commerce:marketplace.emptyInventory")}</Text> : null}

        {editingListing ? (
          <View style={styles.editorBox}>
            <View style={styles.editorHeader}>
              <Text style={styles.editorTitle}>{t("commerce:marketplace.editListingTitle", { id: String(editingListing.id) })}</Text>
              <StatusPill listing={editingListing} />
            </View>
            <TextInput style={styles.input} value={editTitle} onChangeText={setEditTitle} placeholder={t("commerce:marketplace.titlePlaceholder")} placeholderTextColor={colors.muted} />
            <TextInput
              style={[styles.input, styles.textArea]}
              value={editDescription}
              onChangeText={setEditDescription}
              placeholder={t("commerce:marketplace.descriptionPlaceholder")}
              placeholderTextColor={colors.muted}
              multiline
            />
            <View style={styles.twoCol}>
              <TextInput style={[styles.input, styles.flex]} value={editCategory} onChangeText={setEditCategory} placeholder={t("commerce:marketplace.categoryPlaceholder")} placeholderTextColor={colors.muted} />
              <TextInput style={[styles.input, styles.flex]} value={editPriceLabel} onChangeText={setEditPriceLabel} placeholder={t("commerce:marketplace.priceLabelPlaceholder")} placeholderTextColor={colors.muted} />
            </View>
            <TextInput
              style={styles.input}
              value={editQuantity}
              onChangeText={setEditQuantity}
              placeholder={t("commerce:marketplace.quantityPlaceholder")}
              placeholderTextColor={colors.muted}
              keyboardType="numeric"
            />
            <View style={styles.actionRow}>
              <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy === `edit:${editingListing.id}` }} style={styles.primaryButton} disabled={busy === `edit:${editingListing.id}`} onPress={saveListingEdit}>
                <Text style={styles.primaryText}>{busy === `edit:${editingListing.id}` ? t("commerce:marketplace.saving") : t("commerce:marketplace.saveAndReview")}</Text>
              </Pressable>
              <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("CameraStudio", { target: "marketplace", title: t("commerce:marketplace.mediaScreenTitle") })}>
                <Text style={styles.secondaryText}>{t("commerce:marketplace.addMedia")}</Text>
              </Pressable>
            </View>
            <View style={styles.actionRow}>
              <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy === `pause:${editingListing.id}` || statusKey(editingListing) === "paused" }}
                style={styles.secondaryButton}
                disabled={busy === `pause:${editingListing.id}` || statusKey(editingListing) === "paused"}
                onPress={() => mutateListingStatus(editingListing, "pause")}
              >
                <Text style={styles.secondaryText}>{busy === `pause:${editingListing.id}` ? t("commerce:marketplace.pausing") : t("common:actions.pause")}</Text>
              </Pressable>
              <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy === `resume:${editingListing.id}` || statusKey(editingListing) === "live" }}
                style={styles.secondaryButton}
                disabled={busy === `resume:${editingListing.id}` || statusKey(editingListing) === "live"}
                onPress={() => mutateListingStatus(editingListing, "resume")}
              >
                <Text style={styles.secondaryText}>{busy === `resume:${editingListing.id}` ? t("commerce:marketplace.resuming") : t("commerce:marketplace.resumeReview")}</Text>
              </Pressable>
              <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy === `delete:${editingListing.id}` || statusKey(editingListing) === "removed" }}
                style={styles.dangerButton}
                disabled={busy === `delete:${editingListing.id}` || statusKey(editingListing) === "removed"}
                onPress={() => mutateListingStatus(editingListing, "delete")}
              >
                <Text style={styles.dangerText}>{busy === `delete:${editingListing.id}` ? t("commerce:marketplace.removing") : t("common:actions.remove")}</Text>
              </Pressable>
            </View>
            <Text style={styles.meta}>{t("commerce:marketplace.editorFooterNote")}</Text>
          </View>
        ) : null}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>{t("commerce:marketplace.productMediaGallery")}</Text>
        <Text style={styles.copy}>{t("commerce:marketplace.productMediaGalleryCopy")}</Text>
        <View style={styles.mediaGrid}>
          {mediaItems.slice(0, 8).map((item, index) => (
            <Pressable
              key={`${item.url}-${index}`}
              accessibilityLabel={t("commerce:marketplace.openStoreMediaA11y", { index: index + 1 })}
              accessibilityRole="button"
              style={styles.mediaTile}
              onPress={() => {
                setViewerIndex(index);
                setViewerOpen(true);
              }}
            >
              {item.thumbnailUrl || item.url ? <Image source={{ uri: item.thumbnailUrl || item.url }} style={styles.mediaImage} resizeMode="cover" /> : <Text style={styles.mediaFallback}>{t("commerce:marketplace.mediaFallback")}</Text>}
              <Text style={styles.mediaOverlay}>{t("commerce:marketplace.openMedia")}</Text>
            </Pressable>
          ))}
        </View>
        {!mediaItems.length ? <Text style={styles.emptyText}>{t("commerce:marketplace.noMediaYet")}</Text> : null}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>{t("commerce:marketplace.ordersAndPayouts")}</Text>
        <Text style={styles.copy}>{t("commerce:marketplace.ordersAndPayoutsCopy")}</Text>
        {orders.slice(0, 4).map((order) => (
          <View key={`${order.id}-${order.created_at}`} style={styles.orderRow}>
            <Text style={styles.orderTitle}>{order.item_type || t("commerce:orders.orderFallbackTitle")} #{order.item_id || order.id || "pending"}</Text>
            <Text style={styles.orderMeta}>{formatMoney(order.amount_cents || order.gross_amount_cents || 0, order.currency || "USD", fmt)} · {order.status || "pending"}</Text>
          </View>
        ))}
        {!orders.length ? <Text style={styles.emptyText}>{t("commerce:marketplace.noSellerOrders")}</Text> : null}
        {DIGITAL_COMMERCE_ENABLED ? (
          <View style={styles.actionRow}>
            <Pressable accessibilityRole="button" accessibilityState={{ disabled: busy === "payout" }} style={styles.primaryButton} disabled={busy === "payout"} onPress={startPayoutConnect}>
              <Text style={styles.primaryText}>{busy === "payout" ? t("commerce:marketplace.checking") : t("commerce:marketplace.connectPayouts")}</Text>
            </Pressable>
          </View>
        ) : (
          <Text style={styles.meta}>{t("commerce:marketplace.payoutManagedNote")}</Text>
        )}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>{t("commerce:marketplace.trustAndEligibility")}</Text>
        <View style={styles.actionRow}>
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("VerificationCenter", { title: t("common:screens.verificationCenter"), track: "business" })}>
            <Text style={styles.secondaryText}>{t("commerce:marketplace.verification")}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("SafetyHub", { title: t("common:screens.safetyHub"), section: "reports" })}>
            <Text style={styles.secondaryText}>{t("common:screens.safetyHub")}</Text>
          </Pressable>
          <Pressable accessibilityRole="button" style={styles.secondaryButton} onPress={() => navigation.navigate("Premium")}>
            <Text style={styles.secondaryText}>{t("common:screens.premium")}</Text>
          </Pressable>
        </View>
        <Text style={styles.copy}>{t("commerce:marketplace.trustCopy")}</Text>
      </Panel>

      <NativeMediaViewer visible={viewerOpen} items={mediaItems} initialIndex={viewerIndex} title={t("commerce:marketplace.storeMediaViewerTitle")} onClose={() => setViewerOpen(false)} />
    </Screen>
  );
}

function statusKey(listing: MarketplaceListing) {
  const raw = String(listing.status || listing.approval_status || "draft").toLowerCase();
  if (raw.includes("delete") || raw.includes("removed")) return "removed";
  if (raw.includes("reject") || raw.includes("blocked")) return "rejected";
  if (raw.includes("pause")) return "paused";
  if (raw.includes("sold")) return "sold";
  if (raw.includes("stock")) return "out_of_stock";
  if (raw.includes("pending") || raw.includes("review")) return "pending";
  if (["active", "approved", "live"].includes(raw)) return "live";
  if (raw.includes("draft")) return "draft";
  return raw || "draft";
}

/** Catalog key for a status, or `null` when the raw slug is shown verbatim. */
function statusLabelKey(key: string) {
  if (key === "live") return "commerce:marketplace.statusApprovedLive";
  if (key === "pending") return "commerce:marketplace.statusPendingReview";
  if (key === "out_of_stock") return "commerce:marketplace.outOfStock";
  if (key === "removed") return "commerce:marketplace.statusRemoved";
  return null;
}

function StatusPill({ listing }: { listing: MarketplaceListing }) {
  const { t } = useTranslation();
  const key = statusKey(listing);
  const labelKey = statusLabelKey(key);
  const style =
    key === "live"
      ? styles.statusLive
      : key === "pending"
        ? styles.statusPending
        : key === "rejected" || key === "removed"
          ? styles.statusDanger
          : styles.statusNeutral;
  return <Text style={[styles.statusPill, style]}>{labelKey ? t(labelKey) : key.replace(/_/g, " ")}</Text>;
}

function Metric({ label, value }: { label: string; value: string }) {
  return (
    <View style={styles.metric}>
      <Text style={styles.metricValue}>{value}</Text>
      <Text style={styles.metricLabel}>{label}</Text>
    </View>
  );
}

function ListingRow({ listing, onOpen }: { listing: MarketplaceListing; onOpen: () => void }) {
  const { t } = useTranslation();
  const cover = listing.media?.[0] ? mediaDisplayUrl(listing.media[0]) : "";
  return (
    <Pressable accessibilityRole="button" style={styles.listingRow} onPress={onOpen}>
      {cover ? <Image source={{ uri: cover }} style={styles.listingImage} /> : <View style={styles.listingImageFallback} />}
      <View style={styles.listingBody}>
        <Text style={styles.listingTitle} numberOfLines={1}>{listing.title || t("commerce:marketplace.listingTitleFallback")}</Text>
        <Text style={styles.listingMeta} numberOfLines={1}>{listing.price_label || t("commerce:marketplace.priceFallback")} · {listing.status || listing.approval_status || "review"}</Text>
      </View>
      <Text style={styles.chevron}>{t("common:actions.open")}</Text>
    </Pressable>
  );
}

/**
 * `fmt.currency` rather than `toFixed(2)` plus a currency code: the symbol, its
 * position and the digit grouping all belong to the active locale.
 */
function formatMoney(cents: number, currency: string, fmt: Formatters) {
  const amount = Number(cents || 0) / 100;
  return fmt.currency(amount, { currency: String(currency || "USD").toUpperCase() });
}

const styles = StyleSheet.create({
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    marginTop: 8
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
    marginTop: 10
  },
  chevron: {
    color: colors.accentStrong,
    fontSize: 12,
    fontWeight: "900"
  },
  copy: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 21
  },
  dangerButton: {
    alignItems: "center",
    borderColor: "rgba(255, 107, 107, 0.45)",
    borderRadius: 8,
    borderWidth: 1,
    flexGrow: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  dangerText: {
    color: colors.danger,
    fontWeight: "900",
    textAlign: "center"
  },
  editorBox: {
    backgroundColor: "rgba(37, 208, 167, 0.06)",
    borderColor: "rgba(37, 208, 167, 0.2)",
    borderRadius: 8,
    borderWidth: 1,
    gap: 10,
    padding: 12
  },
  editorHeader: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    justifyContent: "space-between"
  },
  editorTitle: {
    color: colors.text,
    fontSize: 16,
    fontWeight: "900"
  },
  emptyText: {
    color: colors.muted,
    fontSize: 13,
    lineHeight: 19
  },
  error: {
    backgroundColor: "rgba(255, 107, 107, 0.12)",
    borderColor: "rgba(255, 107, 107, 0.28)",
    borderRadius: 8,
    borderWidth: 1,
    color: colors.danger,
    fontWeight: "800",
    padding: 12
  },
  hero: {
    backgroundColor: "rgba(79, 140, 255, 0.08)",
    borderColor: "rgba(79, 140, 255, 0.28)",
    borderRadius: 8,
    borderWidth: 1,
    gap: 8,
    padding: 14
  },
  heroCopy: {
    color: colors.muted,
    fontSize: 14,
    lineHeight: 21
  },
  heroTitle: {
    color: colors.text,
    fontSize: 24,
    fontWeight: "900",
    lineHeight: 29
  },
  flex: {
    flex: 1
  },
  input: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    color: colors.text,
    minHeight: 46,
    paddingHorizontal: 12,
    paddingVertical: 10
  },
  kicker: {
    color: colors.accent,
    fontSize: 12,
    fontWeight: "900",
    letterSpacing: 0,
    textTransform: "uppercase"
  },
  listingBody: {
    flex: 1,
    gap: 3
  },
  listingImage: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: 8,
    height: 48,
    width: 48
  },
  listingImageFallback: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    height: 48,
    width: 48
  },
  listingMeta: {
    color: colors.muted,
    fontSize: 12
  },
  listingRow: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    minHeight: 66,
    padding: 10
  },
  listingTitle: {
    color: colors.text,
    fontSize: 15,
    fontWeight: "900"
  },
  inventoryCopy: {
    flex: 1,
    gap: 3
  },
  inventoryList: {
    gap: 8
  },
  inventoryRow: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    minHeight: 58,
    padding: 10
  },
  inventoryRowActive: {
    borderColor: "rgba(37, 208, 167, 0.5)",
    shadowColor: colors.accent,
    shadowOpacity: 0.18,
    shadowRadius: 12
  },
  mediaFallback: {
    color: colors.muted,
    fontWeight: "900"
  },
  mediaGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 8
  },
  mediaImage: {
    height: "100%",
    width: "100%"
  },
  mediaOverlay: {
    backgroundColor: "rgba(16, 18, 20, 0.72)",
    borderRadius: 8,
    bottom: 6,
    color: colors.text,
    fontSize: 10,
    fontWeight: "900",
    left: 6,
    overflow: "hidden",
    paddingHorizontal: 6,
    paddingVertical: 3,
    position: "absolute"
  },
  mediaTile: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    height: 78,
    justifyContent: "center",
    overflow: "hidden",
    width: 78
  },
  metric: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexBasis: "47%",
    flexGrow: 1,
    padding: 12
  },
  metricGrid: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  metricLabel: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 4
  },
  metricValue: {
    color: colors.text,
    fontSize: 22,
    fontWeight: "900"
  },
  meta: {
    color: colors.muted,
    fontSize: 12,
    lineHeight: 18
  },
  notice: {
    backgroundColor: "rgba(37, 208, 167, 0.12)",
    borderColor: "rgba(37, 208, 167, 0.28)",
    borderRadius: 8,
    borderWidth: 1,
    color: colors.accent,
    fontWeight: "800",
    padding: 12
  },
  orderMeta: {
    color: colors.muted,
    fontSize: 12,
    marginTop: 3
  },
  orderRow: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    padding: 12
  },
  orderTitle: {
    color: colors.text,
    fontWeight: "900"
  },
  primaryButton: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: 8,
    flexGrow: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  primaryText: {
    color: colors.background,
    fontWeight: "900",
    textAlign: "center"
  },
  secondaryButton: {
    alignItems: "center",
    borderColor: colors.border,
    borderRadius: 8,
    borderWidth: 1,
    flexGrow: 1,
    minHeight: 44,
    justifyContent: "center",
    paddingHorizontal: 12
  },
  secondaryText: {
    color: colors.text,
    fontWeight: "900",
    textAlign: "center"
  },
  sectionTitle: {
    color: colors.text,
    fontSize: 18,
    fontWeight: "900"
  },
  statusDanger: {
    backgroundColor: "rgba(255, 107, 107, 0.12)",
    borderColor: "rgba(255, 107, 107, 0.35)",
    color: colors.danger
  },
  statusLive: {
    backgroundColor: "rgba(37, 208, 167, 0.12)",
    borderColor: "rgba(37, 208, 167, 0.35)",
    color: colors.accent
  },
  statusNeutral: {
    backgroundColor: "rgba(255, 255, 255, 0.06)",
    borderColor: colors.border,
    color: colors.muted
  },
  statusPending: {
    backgroundColor: "rgba(243, 185, 78, 0.12)",
    borderColor: "rgba(243, 185, 78, 0.35)",
    color: colors.warning
  },
  statusPill: {
    borderRadius: 999,
    borderWidth: 1,
    fontSize: 11,
    fontWeight: "900",
    overflow: "hidden",
    paddingHorizontal: 8,
    paddingVertical: 5,
    textTransform: "capitalize"
  },
  textArea: {
    minHeight: 104,
    textAlignVertical: "top"
  },
  twoCol: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10
  },
  warning: {
    backgroundColor: "rgba(243, 185, 78, 0.12)",
    borderColor: "rgba(243, 185, 78, 0.28)",
    borderRadius: 8,
    borderWidth: 1,
    color: colors.warning,
    fontWeight: "800",
    padding: 12
  }
});
