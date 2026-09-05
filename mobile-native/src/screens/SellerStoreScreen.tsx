import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ActivityIndicator, Image, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
import {
  connectMarketplacePayout,
  acceptMarketplaceCommercialTerms,
  getMarketplaceCommercialTerms,
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
import { invalidateNativeSync, registerSyncInvalidation } from "../core/eventSync";
import { SellerStorePanel, sellerStoreHeading, sellerStoreShowsPanel } from "../navigation/sellerStoreMode";
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";
import { createThemedStyles } from "../theme/themedStyles";

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
  const [liability, setLiability] = useState<Record<string, number>>({});
  const [termsAccepted, setTermsAccepted] = useState(false);
  const [loading, setLoading] = useState(true);
  const [offline, setOffline] = useState(false);
  const [busy, setBusy] = useState("");
  const [message, setMessage] = useState("");
  const [viewerOpen, setViewerOpen] = useState(false);
  const [viewerIndex, setViewerIndex] = useState(0);
  const [editingListingId, setEditingListingId] = useState(0);
  const [editTitle, setEditTitle] = useState("");
  const [editShortDescription, setEditShortDescription] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editCategory, setEditCategory] = useState("");
  const [editPriceLabel, setEditPriceLabel] = useState("");
  const [editQuantity, setEditQuantity] = useState("");

  // True once a canonical store response has landed. Guards the cache read
  // below so a slow AsyncStorage hop can never repaint over fresher data.
  const storeIsCanonical = useRef(false);
  const loadInFlight = useRef<Promise<void> | null>(null);

  /**
   * Paint whatever the device already holds. Display only: the listings and
   * orders here are what the seller looks at, never what any write is checked
   * against. Terms acceptance is deliberately absent — it is an entitlement,
   * and an entitlement read from cache is an entitlement granted by a stale
   * file.
   */
  const hydrateFromCache = useCallback(async () => {
    if (storeIsCanonical.current) return false;
    const cached = await loadCachedSellerStore().catch(() => null);
    if (!cached || storeIsCanonical.current) return false;
    setListings(cached.listings || []);
    setOrders(cached.orders || []);
    return true;
  }, []);

  const load = useCallback(() => {
    // One marketplace write invalidates all three channels registered below in
    // the same tick. Ungated, that is three concurrent copies of this load.
    if (loadInFlight.current) return loadInFlight.current;
    setMessage("");
    const run = (async () => {
      // The cache read runs alongside the network, not in front of it: awaiting
      // it first would put a bridge hop between the tap and every request.
      const hydration = hydrateFromCache().catch(() => false);

      // `getMarketplaceCommercialTerms` is independent of the store snapshot.
      // Serialising them cost a full round trip for no reason, and worse: the
      // old code's catch belonged to both, so a terms hiccup discarded listings
      // that had already arrived and replaced them with cache while claiming to
      // be offline. They settle separately now, and neither can void the other.
      const [snapshot, terms] = await Promise.allSettled([
        loadSellerStoreSnapshot(),
        getMarketplaceCommercialTerms()
      ]);

      if (snapshot.status === "fulfilled" && snapshot.value.live) {
        storeIsCanonical.current = true;
        setListings(snapshot.value.listings || []);
        setOrders(snapshot.value.orders || []);
        setLiability(snapshot.value.commercial_summary?.seller_liability_by_state || {});
        setOffline(false);
      } else {
        // Nothing authoritative arrived. Show saved data if there is any, and
        // say so rather than rendering zeros as though the business were empty.
        const painted = (await hydration) || (await hydrateFromCache().catch(() => false));
        setOffline(true);
        if (!painted) {
          const error = snapshot.status === "rejected" ? snapshot.reason : null;
          setMessage(error instanceof Error ? error.message : "Seller tools could not load.");
        }
      }

      // Only a live answer moves an entitlement. A failed terms read leaves the
      // previous value alone; it never grants acceptance the server did not.
      if (terms.status === "fulfilled") setTermsAccepted(Boolean(terms.value?.terms?.acceptance));

      await hydration;
    })().finally(() => {
      setLoading(false);
      loadInFlight.current = null;
    });
    loadInFlight.current = run;
    return run;
  }, [hydrateFromCache]);

  useEffect(() => {
    load().catch(() => undefined);
  }, [load]);

  // Opened from a listing row elsewhere (the Store dashboard): land directly on
  // that listing's editor with its current values, rather than on an empty panel
  // the seller has to hunt through. Runs once per requested id, so a later
  // refresh cannot yank the seller back out of a listing they navigated away to.
  const requestedListingId = route?.params?.listingId || 0;
  const openedListingId = useRef(0);
  useEffect(() => {
    if (!requestedListingId || openedListingId.current === requestedListingId) return;
    const target = listings.find((item) => item.id === requestedListingId);
    if (!target) return;
    openedListingId.current = requestedListingId;
    startListingEdit(target);
  }, [requestedListingId, listings]);

  useEffect(() => {
    const refreshStore = () => {
      load().catch(() => undefined);
    };
    const unregisterSeller = registerSyncInvalidation("seller_inventory", refreshStore);
    const unregisterMarketplace = registerSyncInvalidation("marketplace", refreshStore);
    const unregisterOrders = registerSyncInvalidation("orders", refreshStore);
    return () => {
      unregisterSeller();
      unregisterMarketplace();
      unregisterOrders();
    };
  }, [load]);

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

  async function acceptTerms() {
    setBusy("terms");
    try { await acceptMarketplaceCommercialTerms(); setTermsAccepted(true); setMessage("Marketplace Fees & Terms accepted."); }
    catch (error) { setMessage(error instanceof Error ? error.message : "Terms acceptance could not be saved."); }
    finally { setBusy(""); }
  }

  function startListingEdit(listing: MarketplaceListing) {
    setEditingListingId(listing.id);
    setEditTitle(listing.title || "");
    setEditShortDescription(listing.short_description || "");
    setEditDescription(listing.description || "");
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
      setEditShortDescription("");
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
        short_description: editShortDescription.trim(),
        description: editDescription.trim(),
        category: editCategory.trim() || "Education",
        price_label: editPriceLabel.trim() || "Request access",
        quantity: Number(editQuantity || 0)
      });
      applyListingResponse(result.listing);
      setMessage(result.message || "Listing updated and sent through review.");
      // The edited price/inventory is authoritative for every other surface
      // holding this listing — the Store dashboard and the buyer-facing
      // Marketplace tab both refetch off these channels.
      await invalidateNativeSync(["seller_inventory", "marketplace"], "listing_edit_saved");
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

  const activeListings = listings.filter((listing) => listing.buyer_visible === true);
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

  const heading = sellerStoreHeading(mode);
  const shows = (panel: SellerStorePanel) => sellerStoreShowsPanel(mode, panel);

  return (
    <Screen title={heading.title} subtitle={heading.subtitle}>
      {offline ? <Text style={styles.warning}>Showing saved seller/store metadata.</Text> : null}
      {message ? <Text style={message.toLowerCase().includes("required") || message.toLowerCase().includes("failed") ? styles.error : styles.notice}>{message}</Text> : null}

      {shows("hero") ? (
      <Panel>
        <View style={styles.hero}>
          <Text style={styles.kicker}>Marketplace Command</Text>
          <Text style={styles.heroTitle}>Storefront readiness</Text>
          <Text style={styles.heroCopy}>Seller approval, product review, payments, payouts, and fulfillment are all decided by PulseSoc.</Text>
        </View>
        <View style={styles.metricGrid}>
          <Metric label="Listings loaded" value={String(listings.length)} />
          <Metric label="Published" value={String(activeListings.length)} />
          <Metric label="Pending review" value={String(pendingListings.length)} />
          <Metric label="Orders loaded" value={String(orders.length)} />
        </View>
        <View style={styles.actionRow}>
          <Pressable accessibilityRole="button" style={styles.primaryButton} onPress={() => navigation.navigate("Tabs", { screen: "Marketplace" })}>
            <Text style={styles.primaryText}>{t("commerce:marketplace.title")}</Text>
          </Pressable>
        </View>
      </Panel>
      ) : null}

      {shows("application") ? (
      <Panel>
        <Text style={styles.sectionTitle}>Merchant application</Text>
        {/*
          This panel used to be the application: two free-text boxes posted
          straight at the seller endpoint. It now points at the real one. Two
          places to apply would mean two half-answered applications per person
          and a reviewer with no way to tell which is current, so this is a door
          rather than a second form.
        */}
        <Text style={styles.copy}>Apply to sell on PulseSoc. The application walks you through who you are, what you sell, and the documents we verify. Your answers save as you go, and every decision is made by a person on our review team.</Text>
        <View style={styles.actionRow}>
          <Pressable
            accessibilityRole="button"
            accessibilityHint="Opens the seller application, where you can start or continue your answers"
            style={styles.primaryButton}
            onPress={() => navigation.navigate("MerchantApply")}
          >
            <Text style={styles.primaryText}>Open Seller Application</Text>
          </Pressable>
        </View>
      </Panel>
      ) : null}

      {shows("listings") ? (
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
      ) : null}

      {shows("inventory") ? (
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
              style={styles.input}
              value={editShortDescription}
              onChangeText={setEditShortDescription}
              placeholder="Short description"
              placeholderTextColor={colors.muted}
            />
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
            <Text style={styles.meta}>Your edits are saved to your PulseSoc store, and the listing goes back through marketplace review whenever its content changes. Checkout, payouts, fulfillment, and disputes are handled on pulsesoc.com.</Text>
          </View>
        ) : null}
      </Panel>
      ) : null}

      {shows("media") ? (
      <Panel>
        <Text style={styles.sectionTitle}>Product media gallery</Text>
        <Text style={styles.copy}>Tap any item to open it full screen. Anything the viewer cannot play falls back to a still preview.</Text>
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
      ) : null}

      {shows("orders") ? (
      <Panel>
        <Text style={styles.sectionTitle}>Orders and payouts</Text>
        <Text style={styles.copy}>Your applied fee is shown per order. Current Marketplace terms remain 10%; the proposed 5% policy is not active.</Text>
        <View style={styles.actionRow}>
          {Object.entries(liability).map(([state, amount]) => <View key={state} style={styles.orderRow}><Text style={styles.orderTitle}>{state.replace(/_/g, " ")}</Text><Text style={styles.orderMeta}>{formatMoney(amount, "USD")}</Text></View>)}
        </View>
        {orders.slice(0, 4).map((order) => (
          <View key={`${order.id}-${order.created_at}`} style={styles.orderRow}>
            <Text style={styles.orderTitle}>{order.item_type || "Order"} #{order.item_id || order.id || "pending"}</Text>
            <Text style={styles.orderMeta}>{formatMoney(order.amount_cents || order.gross_amount_cents || 0, order.currency || "USD")} · {order.status || "pending"}</Text>
            {order.commercial_economics ? <>
              <Text style={styles.orderMeta}>Merchandise {formatMoney(order.commercial_economics.merchandise_net_minor || 0, order.currency || "USD")} · Shipping credit {formatMoney(order.commercial_economics.seller_shipping_credit_minor || 0, order.currency || "USD")}</Text>
              <Text style={styles.orderMeta}>PulseSoc fee {(Number(order.commercial_economics.fee_rate_bps || 0) / 100).toFixed(2)}% · Refund adjustments {formatMoney(order.commercial_economics.seller_reversed_minor || 0, order.currency || "USD")}</Text>
              <Text style={styles.orderMeta}>Net earnings {formatMoney(order.commercial_economics.net_seller_earnings_minor || 0, order.currency || "USD")} · {(order.commercial_economics.payout_state || "pending").replace(/_/g, " ")}</Text>
              {order.commercial_economics.blocker_code ? <Text style={styles.meta}>Blocked: {order.commercial_economics.blocker_code.replace(/_/g, " ")}. Resolve this issue before payout.</Text> : null}
            </> : null}
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
        <View style={styles.orderRow}>
          <Text style={styles.orderTitle}>Marketplace Fees &amp; Terms</Text>
          <Text style={styles.orderMeta}>Seller Terms · 10% current platform fee · Returns · Payouts · Prohibited Goods · Appeals</Text>
          <Pressable accessibilityRole="button" accessibilityState={{ disabled: termsAccepted || busy === "terms" }} style={styles.secondaryButton} disabled={termsAccepted || busy === "terms"} onPress={acceptTerms}>
            <Text style={styles.secondaryText}>{termsAccepted ? "Accepted" : busy === "terms" ? "Saving..." : "Review and Accept"}</Text>
          </Pressable>
        </View>
      </Panel>
      ) : null}

      {shows("trust") ? (
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
        <Text style={styles.copy}>Tax forms, bank setup, disputes, refunds, fulfillment, and PulseSoc review are handled on the PulseSoc website for now, not in the app.</Text>
      </Panel>
      ) : null}

      <NativeMediaViewer visible={viewerOpen} items={mediaItems} initialIndex={viewerIndex} title={t("commerce:marketplace.storeMediaViewerTitle")} onClose={() => setViewerOpen(false)} />
    </Screen>
  );
}

function statusKey(listing: MarketplaceListing) {
  const raw = String(listing.publication_state || listing.status || listing.approval_status || "draft").toLowerCase();
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

function statusLabel(listing: MarketplaceListing) {
  if (listing.publication_label) return listing.publication_label;
  const key = statusKey(listing);
  if (key === "live") return "Published";
  if (key === "pending") return "Pending review";
  if (key === "out_of_stock") return "Out of stock";
  if (key === "removed") return "Removed";
  return key.replace(/_/g, " ");
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

const styles = createThemedStyles(() => ({
  actionRow: {
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    marginTop: 8
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
}));
