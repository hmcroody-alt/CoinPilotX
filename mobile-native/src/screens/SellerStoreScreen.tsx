import { useEffect, useMemo, useState } from "react";
import { ActivityIndicator, Image, Linking, Pressable, StyleSheet, Text, TextInput, View } from "react-native";
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
import { RootStackParamList } from "../navigation/types";
import { colors } from "../theme/colors";

type Props = {
  route?: { params?: RootStackParamList["SellerStore"] };
  navigation: {
    navigate: (...args: any[]) => void;
  };
};

export function SellerStoreScreen({ route, navigation }: Props) {
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
        setMessage(error instanceof Error ? error.message : "Seller tools could not load.");
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
      setMessage(result.message || "Seller application saved.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Seller application could not be saved.");
    } finally {
      setBusy("");
    }
  }

  async function startPayoutConnect() {
    setBusy("payout");
    setMessage("");
    try {
      const result = await connectMarketplacePayout();
      setMessage(result.message || "Payout onboarding checked.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Payout onboarding is not available yet.");
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
      setMessage(result.message || "Listing updated and sent through review.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Listing could not be updated.");
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
      setMessage(result.message || "Listing updated.");
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Listing status could not be updated.");
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
              title: listing.title || "Marketplace listing",
              subtitle: listing.price_label || listing.category || "Marketplace",
              author,
              sourceUrl: sellerStoreWebUrl("profile", listing.seller_username || "")
            })
          );
        })
        .slice(0, 32),
    [listings]
  );

  const activeListings = listings.filter((listing) => ["active", "approved", "review_ready"].includes(String(listing.status || listing.approval_status || "").toLowerCase()));
  const pendingListings = listings.filter((listing) => statusKey(listing) === "pending");
  const editingListing = listings.find((listing) => listing.id === editingListingId) || listings[0] || null;

  if (loading && !listings.length && !orders.length) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.accent} />
        <Text style={styles.centerText}>Loading seller controls</Text>
      </View>
    );
  }

  return (
    <Screen title="Seller / Store" subtitle="Native marketplace control layer using PulseSoc approval, media, payout, and checkout systems.">
      {offline ? <Text style={styles.warning}>Showing saved seller/store metadata.</Text> : null}
      {message ? <Text style={message.toLowerCase().includes("required") || message.toLowerCase().includes("failed") ? styles.error : styles.notice}>{message}</Text> : null}

      <Panel>
        <View style={styles.hero}>
          <Text style={styles.kicker}>Marketplace Command</Text>
          <Text style={styles.heroTitle}>Storefront readiness</Text>
          <Text style={styles.heroCopy}>Seller approval, product review, payment, payout, trust, and fulfillment decisions remain server-authoritative.</Text>
        </View>
        <View style={styles.metricGrid}>
          <Metric label="Listings loaded" value={String(listings.length)} />
          <Metric label="Active/review ready" value={String(activeListings.length)} />
          <Metric label="Pending review" value={String(pendingListings.length)} />
          <Metric label="Orders loaded" value={String(orders.length)} />
        </View>
        <View style={styles.actionRow}>
          <Pressable style={styles.primaryButton} onPress={() => navigation.navigate("Tabs", { screen: "Marketplace" })}>
            <Text style={styles.primaryText}>Marketplace</Text>
          </Pressable>
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Merchant application</Text>
        <Text style={styles.copy}>Submit the native seller application to the existing seller endpoint. Document verification and admin review are completed by PulseSoc after your application is received.</Text>
        <TextInput
          style={styles.input}
          value={displayName}
          onChangeText={setDisplayName}
          placeholder="Seller display name"
          placeholderTextColor={colors.muted}
          autoCapitalize="words"
        />
        <TextInput
          style={[styles.input, styles.textArea]}
          value={bio}
          onChangeText={setBio}
          placeholder="Describe what you sell and how it helps buyers"
          placeholderTextColor={colors.muted}
          multiline
        />
        <View style={styles.actionRow}>
          <Pressable style={styles.primaryButton} disabled={busy === "apply"} onPress={submitApplication}>
            <Text style={styles.primaryText}>{busy === "apply" ? "Saving..." : "Save Seller Application"}</Text>
          </Pressable>
        </View>
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Listing management</Text>
        <Text style={styles.copy}>Product creation, safety review, media moderation, pricing, fulfillment, refunds, disputes, and checkout stay on existing PulseSoc marketplace systems.</Text>
        <View style={styles.actionRow}>
          <Pressable style={styles.primaryButton} onPress={() => navigation.navigate("CameraStudio", { target: "marketplace", title: "Marketplace Media" })}>
            <Text style={styles.primaryText}>Capture Product Media</Text>
          </Pressable>
          <Pressable style={styles.secondaryButton} onPress={() => navigation.navigate("MarketplaceCreateGateway", { title: "Create Listing" })}>
            <Text style={styles.secondaryText}>Create Listing</Text>
          </Pressable>
        </View>
        {listings.slice(0, 5).map((listing) => (
          <ListingRow key={listing.id} listing={listing} onOpen={() => navigation.navigate("MarketplaceDetail", { listingId: listing.id, title: listing.title || "Marketplace" })} />
        ))}
        {!listings.length ? <Text style={styles.emptyText}>No marketplace listings loaded yet.</Text> : null}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Seller inventory</Text>
        <Text style={styles.copy}>Edit seller-owned listing fields and control visibility through PulseSoc marketplace review. Public Marketplace visibility remains approval-gated.</Text>
        <View style={styles.inventoryList}>
          {listings.slice(0, 8).map((listing) => (
            <Pressable
              key={`inventory-${listing.id}`}
              style={[styles.inventoryRow, editingListing?.id === listing.id && styles.inventoryRowActive]}
              onPress={() => startListingEdit(listing)}
            >
              <View style={styles.inventoryCopy}>
                <Text style={styles.listingTitle} numberOfLines={1}>{listing.title || "Marketplace listing"}</Text>
                <Text style={styles.listingMeta} numberOfLines={1}>{listing.price_label || "Request access"} · {listing.category || "Marketplace"}</Text>
              </View>
              <StatusPill listing={listing} />
            </Pressable>
          ))}
        </View>
        {!listings.length ? <Text style={styles.emptyText}>Seller inventory appears after products are created or loaded.</Text> : null}

        {editingListing ? (
          <View style={styles.editorBox}>
            <View style={styles.editorHeader}>
              <Text style={styles.editorTitle}>Edit listing #{editingListing.id}</Text>
              <StatusPill listing={editingListing} />
            </View>
            <TextInput style={styles.input} value={editTitle} onChangeText={setEditTitle} placeholder="Title" placeholderTextColor={colors.muted} />
            <TextInput
              style={[styles.input, styles.textArea]}
              value={editDescription}
              onChangeText={setEditDescription}
              placeholder="Description"
              placeholderTextColor={colors.muted}
              multiline
            />
            <View style={styles.twoCol}>
              <TextInput style={[styles.input, styles.flex]} value={editCategory} onChangeText={setEditCategory} placeholder="Category" placeholderTextColor={colors.muted} />
              <TextInput style={[styles.input, styles.flex]} value={editPriceLabel} onChangeText={setEditPriceLabel} placeholder="Price label" placeholderTextColor={colors.muted} />
            </View>
            <TextInput
              style={styles.input}
              value={editQuantity}
              onChangeText={setEditQuantity}
              placeholder="Inventory quantity"
              placeholderTextColor={colors.muted}
              keyboardType="numeric"
            />
            <View style={styles.actionRow}>
              <Pressable style={styles.primaryButton} disabled={busy === `edit:${editingListing.id}`} onPress={saveListingEdit}>
                <Text style={styles.primaryText}>{busy === `edit:${editingListing.id}` ? "Saving..." : "Save and Review"}</Text>
              </Pressable>
              <Pressable style={styles.secondaryButton} onPress={() => navigation.navigate("CameraStudio", { target: "marketplace", title: "Marketplace Media" })}>
                <Text style={styles.secondaryText}>Add Media</Text>
              </Pressable>
            </View>
            <View style={styles.actionRow}>
              <Pressable
                style={styles.secondaryButton}
                disabled={busy === `pause:${editingListing.id}` || statusKey(editingListing) === "paused"}
                onPress={() => mutateListingStatus(editingListing, "pause")}
              >
                <Text style={styles.secondaryText}>{busy === `pause:${editingListing.id}` ? "Pausing..." : "Pause"}</Text>
              </Pressable>
              <Pressable
                style={styles.secondaryButton}
                disabled={busy === `resume:${editingListing.id}` || statusKey(editingListing) === "live"}
                onPress={() => mutateListingStatus(editingListing, "resume")}
              >
                <Text style={styles.secondaryText}>{busy === `resume:${editingListing.id}` ? "Resuming..." : "Resume Review"}</Text>
              </Pressable>
              <Pressable
                style={styles.dangerButton}
                disabled={busy === `delete:${editingListing.id}` || statusKey(editingListing) === "removed"}
                onPress={() => mutateListingStatus(editingListing, "delete")}
              >
                <Text style={styles.dangerText}>{busy === `delete:${editingListing.id}` ? "Removing..." : "Remove"}</Text>
              </Pressable>
            </View>
            <Text style={styles.meta}>Updates are saved server-side and re-enter marketplace review when content changes. Checkout, payouts, fulfillment, disputes, and provider actions stay on safe fallback flows.</Text>
          </View>
        ) : null}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Product media gallery</Text>
        <Text style={styles.copy}>The gallery reuses marketplace media payloads and the shared native media viewer. Unsupported media falls back safely inside the viewer.</Text>
        <View style={styles.mediaGrid}>
          {mediaItems.slice(0, 8).map((item, index) => (
            <Pressable
              key={`${item.url}-${index}`}
              accessibilityLabel={`Open store media ${index + 1}`}
              accessibilityRole="button"
              style={styles.mediaTile}
              onPress={() => {
                setViewerIndex(index);
                setViewerOpen(true);
              }}
            >
              {item.thumbnailUrl || item.url ? <Image source={{ uri: item.thumbnailUrl || item.url }} style={styles.mediaImage} resizeMode="cover" /> : <Text style={styles.mediaFallback}>Media</Text>}
              <Text style={styles.mediaOverlay}>Open media</Text>
            </Pressable>
          ))}
        </View>
        {!mediaItems.length ? <Text style={styles.emptyText}>Media appears here after marketplace listings or product uploads are available.</Text> : null}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Orders and payouts</Text>
        <Text style={styles.copy}>Orders, seller fees, Stripe Connect onboarding, checkout, and payout release remain provider and backend controlled.</Text>
        {orders.slice(0, 4).map((order) => (
          <View key={`${order.id}-${order.created_at}`} style={styles.orderRow}>
            <Text style={styles.orderTitle}>{order.item_type || "Order"} #{order.item_id || order.id || "pending"}</Text>
            <Text style={styles.orderMeta}>{formatMoney(order.amount_cents || order.gross_amount_cents || 0, order.currency || "USD")} · {order.status || "pending"}</Text>
          </View>
        ))}
        {!orders.length ? <Text style={styles.emptyText}>No seller orders loaded.</Text> : null}
        {DIGITAL_COMMERCE_ENABLED ? (
          <View style={styles.actionRow}>
            <Pressable style={styles.primaryButton} disabled={busy === "payout"} onPress={startPayoutConnect}>
              <Text style={styles.primaryText}>{busy === "payout" ? "Checking..." : "Connect Payouts"}</Text>
            </Pressable>
            <Pressable style={styles.secondaryButton} onPress={() => Linking.openURL(sellerStoreWebUrl("payouts")).catch(() => undefined)}>
              <Text style={styles.secondaryText}>Payout Web</Text>
            </Pressable>
          </View>
        ) : (
          <Text style={styles.meta}>Payout onboarding and payout release are managed by PulseSoc and its payment provider. You will be notified when your payouts are ready.</Text>
        )}
      </Panel>

      <Panel>
        <Text style={styles.sectionTitle}>Trust and eligibility</Text>
        <View style={styles.actionRow}>
          <Pressable style={styles.secondaryButton} onPress={() => navigation.navigate("VerificationCenter", { title: "Verification Center", track: "business" })}>
            <Text style={styles.secondaryText}>Verification</Text>
          </Pressable>
          <Pressable style={styles.secondaryButton} onPress={() => navigation.navigate("SafetyHub", { title: "Safety Hub", section: "reports" })}>
            <Text style={styles.secondaryText}>Safety Hub</Text>
          </Pressable>
          <Pressable style={styles.secondaryButton} onPress={() => navigation.navigate("Premium")}>
            <Text style={styles.secondaryText}>Premium</Text>
          </Pressable>
        </View>
        <Text style={styles.copy}>Advanced tax forms, bank onboarding, disputes, refunds, fulfillment, and admin review stay on safe web/provider flows until native QA gates are ready.</Text>
      </Panel>

      <NativeMediaViewer visible={viewerOpen} items={mediaItems} initialIndex={viewerIndex} title="Store media" onClose={() => setViewerOpen(false)} />
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

function statusLabel(listing: MarketplaceListing) {
  const key = statusKey(listing);
  if (key === "live") return "Approved/live";
  if (key === "pending") return "Pending review";
  if (key === "out_of_stock") return "Out of stock";
  if (key === "removed") return "Removed";
  return key.replace(/_/g, " ");
}

function StatusPill({ listing }: { listing: MarketplaceListing }) {
  const key = statusKey(listing);
  const style =
    key === "live"
      ? styles.statusLive
      : key === "pending"
        ? styles.statusPending
        : key === "rejected" || key === "removed"
          ? styles.statusDanger
          : styles.statusNeutral;
  return <Text style={[styles.statusPill, style]}>{statusLabel(listing)}</Text>;
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
  const cover = listing.media?.[0] ? mediaDisplayUrl(listing.media[0]) : "";
  return (
    <Pressable style={styles.listingRow} onPress={onOpen}>
      {cover ? <Image source={{ uri: cover }} style={styles.listingImage} /> : <View style={styles.listingImageFallback} />}
      <View style={styles.listingBody}>
        <Text style={styles.listingTitle} numberOfLines={1}>{listing.title || "Marketplace listing"}</Text>
        <Text style={styles.listingMeta} numberOfLines={1}>{listing.price_label || "Request access"} · {listing.status || listing.approval_status || "review"}</Text>
      </View>
      <Text style={styles.chevron}>Open</Text>
    </Pressable>
  );
}

function formatMoney(cents: number, currency: string) {
  const amount = Number(cents || 0) / 100;
  return `${amount.toFixed(2)} ${String(currency || "USD").toUpperCase()}`;
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
