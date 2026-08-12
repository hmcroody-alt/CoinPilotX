/**
 * The buyer's product page.
 *
 * This replaces the detail *modal* that used to sit on top of the Marketplace
 * grid. A modal was the wrong container for the primary commerce surface: it had
 * no route, so it could not be deep-linked, shared, or returned to after
 * checkout, and its back gesture dismissed the product rather than the step.
 *
 * The listing travels in the route params rather than being refetched. There is
 * no read-one endpoint — `/api/pulse/marketplace/search` is the only buyer-side
 * read — so a refetch here would mean a second search and a spinner in front of
 * data the caller already holds. `listingId` is carried alongside so identity
 * (save state, cart writes, reporting) never depends on the snapshot.
 *
 * What this screen deliberately does not render: `safety_score`,
 * `approval_status`, `publication_state`, `publication_label`, or any other
 * moderation or lifecycle field. A buyer only ever reaches an approved listing —
 * the lifecycle gate runs server-side in the search query — so surfacing the
 * gate's own state here would be showing the buyer the machinery, not the
 * product. Nor does it invent ratings, review counts, response rates, sales
 * counts, or delivery dates: the backend has none of those, and a fabricated
 * trust signal is worse than an absent one.
 */

import { NativeStackScreenProps } from "@react-navigation/native-stack";
import { Ionicons } from "@expo/vector-icons";
import { useMemo, useRef, useState } from "react";
import {
  Image,
  NativeScrollEvent,
  NativeSyntheticEvent,
  Pressable,
  ScrollView,
  Share,
  StyleSheet,
  Text,
  useWindowDimensions,
  View
} from "react-native";
import {
  MarketplaceListing,
  marketplaceSellerAuthor,
  marketplaceWebUrl,
  reportMarketplaceListing,
  startMarketplaceSellerChat
} from "../api/marketplace";
import { addToCart } from "../api/marketplaceCommerce";
import {
  canPurchaseMarketplaceListing as canPurchaseListing,
  isStocklessMarketplaceListing as isStockless,
  marketplaceAvailabilityCopy as availabilityCopy,
  marketplaceFulfillmentCopy as fulfillmentCopy,
  marketplaceListingFulfillment as listingFulfillment
} from "../api/marketplaceBuyerPresentation";
import { buyerErrorCopy } from "../api/marketplaceErrors";
import { sellerStoreInitial, sellerStoreName } from "../api/sellerIdentity";
import { conversationSplitEnabled } from "../api/conversationDomain";
import { mediaDisplayUrl } from "../api/feed";
import { useAuth } from "../session/auth";
import { ContentTranslation } from "../components/ContentTranslation";
import { mediaViewerItemFromPulseMedia, NativeMediaViewer } from "../components/NativeMediaViewer";
import { RootStackParamList } from "../navigation/types";
import { peekSaveState, useSavedState } from "../social/savedStore";
import { setSaved } from "../social/useSaveAction";
import { storeLight } from "../theme/marketplaceLight";
import { createThemedStyles } from "../theme/themedStyles";

type Props = NativeStackScreenProps<RootStackParamList, "MarketplaceProduct">;

/** The buyer actions that own a progress state of their own. */
type ProductAction = "save" | "report" | "message" | "cart" | "buy";

/** Quantity is capped to match the cart's own ceiling, so the two agree. */
const MAX_QTY = 20;

export function MarketplaceProductScreen({ route, navigation }: Props) {
  const listing = route.params?.listing as MarketplaceListing | undefined;
  const listingId = Number(route.params?.listingId || listing?.id || 0);
  const { width } = useWindowDimensions();
  const { authState } = useAuth();
  // Seller-only affordances are hidden on identity, not on a guess. Every QA
  // pass runs cross-account, so the common case is buyer !== seller and every
  // buyer action must be live; only the account that owns the listing loses
  // "Message seller" (you cannot open a DM with yourself) and the buy actions.
  const viewerUserId = Number(authState.user?.user_id || 0);
  const sellerUserId = Number(listing?.seller_user_id || 0);
  const isOwnListing = viewerUserId > 0 && sellerUserId > 0 && viewerUserId === sellerUserId;
  const [qty, setQty] = useState(1);
  // One action at a time, but each action reports its own progress. A shared
  // "busy" boolean made every button read "Please wait…" while a different
  // button was working, which is indistinguishable from a hang.
  const [pending, setPending] = useState<ProductAction | "">("");
  const busy = pending !== "";
  const [notice, setNotice] = useState("");
  const [mediaIndex, setMediaIndex] = useState(0);
  const [viewerOpen, setViewerOpen] = useState(false);
  const galleryWidth = useRef(width);
  galleryWidth.current = width;

  const media = useMemo(() => (listing?.media || []).filter(Boolean), [listing]);
  const viewerItems = useMemo(() => {
    if (!listing) return [];
    const author = marketplaceSellerAuthor(listing);
    return media.map((item) =>
      mediaViewerItemFromPulseMedia(item, {
        title: listing.title || "Marketplace listing",
        subtitle: listing.price_label || listing.category || "Marketplace",
        author,
        sourceUrl: marketplaceWebUrl(listingId)
      })
    );
  }, [listing, listingId, media]);

  // Same store and same key the grid card underneath uses, so saving here is
  // reflected there without either screen knowing about the other.
  const savedState = useSavedState("marketplace", listingId, listing?.saved);

  if (!listing || !listingId) {
    return (
      <View style={styles.unavailable}>
        <Ionicons name="pricetag-outline" size={34} color={storeLight.text.muted} />
        <Text style={styles.unavailableTitle}>This item is no longer available.</Text>
        <Pressable accessibilityRole="button" style={styles.unavailableButton} onPress={() => navigation.goBack()}>
          <Text style={styles.unavailableButtonText}>Back to Marketplace</Text>
        </Pressable>
      </View>
    );
  }

  const metadata = (listing.listing_metadata || {}) as Record<string, unknown>;
  const condition = readMetadata(metadata, "condition");
  const location = readMetadata(metadata, "location");
  const brand = readMetadata(metadata, "brand");
  const availability = availabilityCopy(listing);
  // The buyer is buying from a store. The account holder's personal name is
  // private-side identity and must never appear on this screen, so resolve the
  // store name once and use it for the card, the avatar, the chat title, and
  // the checkout hand-off — a single value cannot drift between them.
  const storeName = sellerStoreName(listing);
  const purchasable = canPurchaseListing(listing);
  // Availability is about the listing; buyability adds "and you are not the
  // one selling it". Keeping them separate means an owner previewing their own
  // page still sees honest stock copy instead of a false "Sold out".
  const canBuy = purchasable && !isOwnListing;
  const stockCeiling = isStockless(listing) ? MAX_QTY : Math.min(MAX_QTY, Math.max(1, Number(listing.quantity || 1)));
  const canNavigateStore = Boolean(listing.seller_public_player_id || listing.seller_username);

  function onGalleryScroll(event: NativeSyntheticEvent<NativeScrollEvent>) {
    const page = Math.round(event.nativeEvent.contentOffset.x / Math.max(1, galleryWidth.current));
    if (page !== mediaIndex) setMediaIndex(page);
  }

  async function handleSave() {
    if (busy) return;
    const wasSaved = peekSaveState("marketplace", listingId)?.saved ?? Boolean(listing?.saved);
    setPending("save");
    try {
      const outcome = await setSaved({ type: "marketplace", id: listingId }, !wasSaved);
      if (!outcome.ok && outcome.message) setNotice(outcome.message);
    } finally {
      setPending("");
    }
  }

  async function handleReport() {
    if (busy) return;
    setPending("report");
    try {
      await reportMarketplaceListing(listingId, "Needs review");
      setNotice("Thanks — this listing has been reported.");
    } catch (error) {
      setNotice(buyerErrorCopy(error, "This listing could not be reported."));
    } finally {
      setPending("");
    }
  }

  async function handleMessageSeller() {
    if (busy) return;
    // You cannot DM yourself, and you cannot DM a listing that hasn't loaded.
    if (!listing || isOwnListing) return;
    setPending("message");
    setNotice("");
    try {
      // The seller is resolved by canonical user id. Username and public Pulse
      // id ride along only as a fallback for a listing snapshot that reached
      // this screen without the id — never as the primary identity, and never
      // the listing id or the display name.
      const result = await startMarketplaceSellerChat(Number(listing.seller_user_id || 0), {
        username: listing.seller_username || undefined,
        publicPlayerId: listing.seller_public_player_id || undefined
      });
      if (!result.conversation_id) {
        setNotice("Seller chat is not available for this listing yet.");
        return;
      }
      // A message about a listing is commerce, so it belongs in the Commerce
      // Inbox — that is what makes Back land on the seller's commerce list.
      if (conversationSplitEnabled()) {
        navigation.navigate("BusinessOsMessages", { title: "Messages", focusConversationId: result.conversation_id });
      } else {
        // Routed by `seller_user_id` above; titled with the store. Presentation
        // and message ownership are different things and stay separate.
        navigation.navigate("Chat", { conversationId: result.conversation_id, title: sellerStoreName(listing) });
      }
    } catch (error) {
      setNotice(buyerErrorCopy(error, "Seller chat could not be opened."));
    } finally {
      setPending("");
    }
  }

  async function handleAddToCart() {
    if (busy) return;
    if (!listing || !canBuy) {
      setNotice(isOwnListing ? "This is your own listing." : "This item is no longer available.");
      return;
    }
    setPending("cart");
    setNotice("");
    try {
      // Add to cart never navigates. The buyer asked to keep shopping; sending
      // them to checkout here is the bug this screen exists to remove.
      await addToCart(listingId, qty);
      setNotice(`Added to cart · ${qty} × ${listing.title || "item"}`);
    } catch (error) {
      setNotice(buyerErrorCopy(error, "This item could not be added to your cart."));
    } finally {
      setPending("");
    }
  }

  function handleBuyNow() {
    if (busy) return;
    if (!listing || !canBuy) {
      setNotice(isOwnListing ? "This is your own listing." : "This item is no longer available.");
      return;
    }
    // Buy now bypasses the cart entirely — the backend's `buy_now` intent, not a
    // cart group of one, so nothing already in the cart is dragged into it.
    navigation.navigate("MarketplaceCheckout", {
      mode: "buy_now",
      listingId,
      itemTitle: listing.title || "Marketplace item",
      sellerUserId: Number(listing.seller_user_id || 0),
      sellerName: sellerStoreName(listing),
      priceLabel: listing.price_label || "",
      quantity: qty,
      fulfillment: listingFulfillment(listing)
    });
  }

  return (
    <View style={styles.shell}>
      <ScrollView style={styles.root} contentContainerStyle={styles.content}>
        <View style={styles.header}>
          <Pressable accessibilityRole="button" accessibilityLabel="Back to Marketplace" style={styles.iconButton} onPress={() => navigation.goBack()}>
            <Ionicons name="arrow-back" size={24} color={storeLight.text.primary} />
          </Pressable>
          <View style={styles.headerActions}>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Share this item"
              style={styles.iconButton}
              onPress={() =>
                Share.share({
                  title: listing.title || "Marketplace listing",
                  message: `${listing.title || "Marketplace listing"}\n${marketplaceWebUrl(listingId)}`
                }).catch(() => undefined)
              }
            >
              <Ionicons name="share-outline" size={23} color={storeLight.text.primary} />
            </Pressable>
            <Pressable
              accessibilityRole="button"
              accessibilityLabel={savedState.saved ? "Remove from saved items" : "Save this item"}
              accessibilityState={{ selected: savedState.saved, disabled: busy }}
              disabled={busy}
              style={styles.iconButton}
              onPress={handleSave}
            >
              <Ionicons name={savedState.saved ? "heart" : "heart-outline"} size={24} color={savedState.saved ? storeLight.status.error : storeLight.text.primary} />
            </Pressable>
            <Pressable accessibilityRole="button" accessibilityLabel="Open cart" style={styles.iconButton} onPress={() => navigation.navigate("MarketplaceCart", { title: "Cart" })}>
              <Ionicons name="cart-outline" size={24} color={storeLight.text.primary} />
            </Pressable>
          </View>
        </View>

        <View style={styles.gallery}>
          {media.length ? (
            <ScrollView
              horizontal
              pagingEnabled
              showsHorizontalScrollIndicator={false}
              onScroll={onGalleryScroll}
              scrollEventThrottle={32}
            >
              {media.map((item, index) => (
                <Pressable
                  key={`${listingId}-media-${index}`}
                  accessibilityRole="imagebutton"
                  accessibilityLabel={`Product image ${index + 1} of ${media.length}`}
                  onPress={() => setViewerOpen(true)}
                >
                  <Image source={{ uri: mediaDisplayUrl(item) }} style={[styles.galleryImage, { width }]} resizeMode="cover" />
                </Pressable>
              ))}
            </ScrollView>
          ) : (
            <View style={[styles.galleryImage, styles.galleryFallback, { width }]}>
              <Ionicons name="image-outline" size={38} color={storeLight.text.muted} />
              <Text style={styles.galleryFallbackText}>No product photos yet</Text>
            </View>
          )}
          {media.length > 1 ? (
            <View style={styles.galleryCount}>
              <Text style={styles.galleryCountText}>{mediaIndex + 1}/{media.length}</Text>
            </View>
          ) : null}
          {!purchasable ? (
            <View style={styles.soldScrim}>
              <Text style={styles.soldScrimText}>SOLD OUT</Text>
            </View>
          ) : null}
        </View>

        <View style={styles.card}>
          <Text style={styles.title}>{listing.title || "Marketplace listing"}</Text>
          <Text style={styles.price}>{listing.price_label || "Price shown at checkout"}</Text>
          <View style={styles.pillRow}>
            {listing.category ? <Text style={styles.pill}>{listing.category}</Text> : null}
            {condition ? <Text style={styles.pill}>{humanize(condition)}</Text> : null}
            <Text style={[styles.pill, purchasable ? styles.pillInStock : styles.pillSold]}>{availability}</Text>
          </View>

          {purchasable && !isStockless(listing) ? (
            <View style={styles.qtyRow}>
              <Text style={styles.qtyLabel}>Quantity</Text>
              <View style={styles.stepper}>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Decrease quantity"
                  accessibilityState={{ disabled: qty <= 1 }}
                  disabled={qty <= 1}
                  style={[styles.stepperButton, qty <= 1 && styles.disabled]}
                  onPress={() => setQty((current) => Math.max(1, current - 1))}
                >
                  <Ionicons name="remove" size={19} color={storeLight.text.primary} />
                </Pressable>
                <Text style={styles.stepperValue}>{qty}</Text>
                <Pressable
                  accessibilityRole="button"
                  accessibilityLabel="Increase quantity"
                  accessibilityState={{ disabled: qty >= stockCeiling }}
                  disabled={qty >= stockCeiling}
                  style={[styles.stepperButton, qty >= stockCeiling && styles.disabled]}
                  onPress={() => setQty((current) => Math.min(stockCeiling, current + 1))}
                >
                  <Ionicons name="add" size={19} color={storeLight.text.primary} />
                </Pressable>
              </View>
            </View>
          ) : null}

          <View style={styles.factList}>
            <Fact label="Category" value={[listing.category, listing.subcategory].filter(Boolean).join(" › ")} />
            {brand ? <Fact label="Brand" value={brand} /> : null}
            {condition ? <Fact label="Condition" value={humanize(condition)} /> : null}
            {location ? <Fact label="Location" value={location} /> : null}
            <Fact label="Delivery" value={fulfillmentCopy(listing)} />
            {!isStockless(listing) ? <Fact label="Availability" value={availability} /> : null}
          </View>
        </View>

        <Pressable
          accessibilityRole="button"
          accessibilityLabel={canNavigateStore ? `View ${storeName}'s store` : "Seller"}
          accessibilityState={{ disabled: !canNavigateStore }}
          disabled={!canNavigateStore}
          style={styles.sellerCard}
          onPress={() => navigation.navigate("MarketplaceDetail", { sellerUserId: Number(listing.seller_user_id || 0), title: storeName })}
        >
          <View style={styles.sellerAvatar}>
            <Text style={styles.sellerAvatarText}>{sellerStoreInitial(listing)}</Text>
          </View>
          <View style={styles.sellerBody}>
            <Text style={styles.sellerName}>{storeName}</Text>
            <View style={styles.sellerMetaRow}>
              <Ionicons name="storefront-outline" size={14} color={storeLight.status.success} />
              <Text style={styles.sellerMeta}>Marketplace seller</Text>
            </View>
            {location ? <Text style={styles.sellerMeta}>{location}</Text> : null}
          </View>
          {canNavigateStore ? <Text style={styles.viewStore}>View store</Text> : null}
        </Pressable>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>About this item</Text>
          <ContentTranslation
            contentType="marketplace"
            contentRef={listingId}
            text={listing.description || listing.short_description || "The seller has not added a description for this item."}
            textStyle={styles.description}
          />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Delivery</Text>
          <Protection icon="cube-outline" text={fulfillmentCopy(listing)} />
          <Protection icon="calendar-outline" text="Delivery timing is arranged with the seller after your order is confirmed." />
        </View>

        <View style={styles.section}>
          <Text style={styles.sectionTitle}>Buyer protection</Text>
          <Protection icon="lock-closed-outline" text="Payment is handled by PulseSoc secure checkout — your card details are never shared with the seller." />
          <Protection icon="receipt-outline" text="Your order and receipt appear in Purchase History as soon as payment is confirmed." />
          <Protection icon="refresh-outline" text="Returns and disputes for eligible orders are opened from the order itself." />
        </View>

        <View style={styles.secondaryRow}>
          {/* Message seller is hidden for exactly one viewer — the seller. Any
              other buyer, on any account, gets a live button. */}
          {isOwnListing ? null : (
            <Pressable
              accessibilityRole="button"
              accessibilityLabel="Message seller"
              accessibilityState={{ disabled: busy, busy: pending === "message" }}
              disabled={busy}
              style={[styles.secondaryAction, busy && styles.disabled]}
              onPress={handleMessageSeller}
            >
              <Ionicons name="chatbubble-outline" size={18} color={storeLight.text.link} />
              <Text style={styles.secondaryActionText}>{pending === "message" ? "Opening chat…" : "Message seller"}</Text>
            </Pressable>
          )}
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Report this listing"
            accessibilityState={{ disabled: busy, busy: pending === "report" }}
            disabled={busy}
            style={[styles.secondaryAction, busy && styles.disabled]}
            onPress={handleReport}
          >
            <Ionicons name="flag-outline" size={18} color={storeLight.text.link} />
            <Text style={styles.secondaryActionText}>{pending === "report" ? "Reporting…" : "Report"}</Text>
          </Pressable>
        </View>

        {notice ? <Text style={styles.notice} accessibilityLiveRegion="polite">{notice}</Text> : null}
      </ScrollView>

      {isOwnListing ? (
        <View style={styles.purchaseBar}>
          <Text style={styles.ownListingNote}>This is your listing. Buyers see the purchase options here.</Text>
        </View>
      ) : (
        <View style={styles.purchaseBar}>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={canBuy ? `Add ${qty} to cart` : "Sold out"}
            accessibilityState={{ disabled: busy || !canBuy, busy: pending === "cart" }}
            disabled={busy || !canBuy}
            style={[styles.addToCart, (busy || !canBuy) && styles.disabled]}
            onPress={handleAddToCart}
          >
            <Ionicons name="cart-outline" size={19} color={storeLight.text.link} />
            {/* Each button reports its own progress. A single shared "Please
                wait…" made every control look stuck whenever any one of them
                was working, which reads as a hang rather than as feedback. */}
            <Text style={styles.addToCartText}>
              {pending === "cart" ? "Adding…" : canBuy ? "Add to cart" : "Sold out"}
            </Text>
          </Pressable>
          <Pressable
            accessibilityRole="button"
            accessibilityLabel="Buy now"
            accessibilityState={{ disabled: busy || !canBuy }}
            disabled={busy || !canBuy}
            style={[styles.buyNow, (busy || !canBuy) && styles.disabled]}
            onPress={handleBuyNow}
          >
            <Text style={styles.buyNowText}>Buy now</Text>
          </Pressable>
        </View>
      )}

      <NativeMediaViewer
        visible={viewerOpen}
        items={viewerItems}
        initialIndex={mediaIndex}
        title={listing.title || "Marketplace media"}
        onClose={() => setViewerOpen(false)}
      />
    </View>
  );
}

function Fact({ label, value }: { label: string; value: string }) {
  if (!value) return null;
  return (
    <View style={styles.factRow}>
      <Text style={styles.factLabel}>{label}</Text>
      <Text style={styles.factValue}>{value}</Text>
    </View>
  );
}

function Protection({ icon, text }: { icon: keyof typeof Ionicons.glyphMap; text: string }) {
  return (
    <View style={styles.protectionRow}>
      <Ionicons name={icon} size={18} color={storeLight.status.success} />
      <Text style={styles.protectionText}>{text}</Text>
    </View>
  );
}

function readMetadata(metadata: Record<string, unknown>, key: string) {
  const value = metadata[key];
  return typeof value === "string" ? value.trim() : "";
}

function humanize(value: string) {
  return value.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}

const styles = createThemedStyles(() => ({
  addToCart: {
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
  addToCartText: { color: storeLight.text.link, fontSize: 15, fontWeight: "900" },
  buyNow: {
    alignItems: "center",
    backgroundColor: storeLight.accent.brandOnLight,
    borderRadius: 999,
    flex: 1,
    justifyContent: "center",
    minHeight: 52
  },
  buyNowText: { color: storeLight.cta.text, fontSize: 15, fontWeight: "900" },
  card: {
    backgroundColor: storeLight.bg.card,
    borderBottomColor: storeLight.border.hairline,
    borderBottomWidth: StyleSheet.hairlineWidth,
    gap: 10,
    padding: 16
  },
  content: { paddingBottom: 30 },
  description: { color: storeLight.text.primary, fontSize: 15, lineHeight: 23 },
  disabled: { opacity: 0.45 },
  factLabel: { color: storeLight.text.muted, fontSize: 13, width: 96 },
  factList: { gap: 5, marginTop: 4 },
  factRow: { flexDirection: "row", gap: 12 },
  factValue: { color: storeLight.text.primary, flex: 1, fontSize: 13, fontWeight: "600" },
  gallery: { backgroundColor: storeLight.bg.card, position: "relative" },
  galleryCount: {
    backgroundColor: "rgba(15,17,17,0.78)",
    borderRadius: 14,
    bottom: 12,
    paddingHorizontal: 10,
    paddingVertical: 5,
    position: "absolute",
    right: 12
  },
  galleryCountText: { color: "#FFFFFF", fontSize: 12, fontWeight: "800" },
  galleryFallback: { alignItems: "center", backgroundColor: storeLight.bg.skeleton, gap: 8, justifyContent: "center" },
  galleryFallbackText: { color: storeLight.text.muted, fontSize: 13, fontWeight: "700" },
  galleryImage: { aspectRatio: 1, backgroundColor: storeLight.bg.skeleton },
  header: {
    alignItems: "center",
    backgroundColor: storeLight.bg.card,
    flexDirection: "row",
    justifyContent: "space-between",
    minHeight: 56,
    paddingHorizontal: 8
  },
  headerActions: { alignItems: "center", flexDirection: "row", gap: 2 },
  iconButton: { alignItems: "center", height: 46, justifyContent: "center", width: 46 },
  notice: {
    color: storeLight.text.primary,
    fontSize: 13,
    fontWeight: "700",
    marginHorizontal: 16,
    marginTop: 14
  },
  ownListingNote: {
    color: storeLight.text.muted,
    flex: 1,
    fontSize: 13,
    fontWeight: "700",
    textAlign: "center"
  },
  pill: {
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
  pillInStock: { color: storeLight.status.success },
  pillRow: { flexDirection: "row", flexWrap: "wrap", gap: 8 },
  pillSold: { color: storeLight.status.error },
  price: { color: storeLight.status.success, fontSize: 26, fontWeight: "900" },
  protectionRow: { alignItems: "flex-start", flexDirection: "row", gap: 10 },
  protectionText: { color: storeLight.text.muted, flex: 1, fontSize: 13, lineHeight: 19 },
  purchaseBar: {
    backgroundColor: storeLight.bg.card,
    borderTopColor: storeLight.border.hairline,
    borderTopWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    gap: 10,
    paddingBottom: 14,
    paddingHorizontal: 14,
    paddingTop: 10
  },
  qtyLabel: { color: storeLight.text.primary, flex: 1, fontSize: 14, fontWeight: "800" },
  qtyRow: { alignItems: "center", flexDirection: "row", marginTop: 2 },
  root: { backgroundColor: storeLight.bg.page, flex: 1 },
  section: {
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
  secondaryAction: {
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
  secondaryActionText: { color: storeLight.text.link, fontSize: 13, fontWeight: "800" },
  secondaryRow: { flexDirection: "row", gap: 12, marginHorizontal: 16, marginTop: 14 },
  sellerAvatar: {
    alignItems: "center",
    backgroundColor: "#DDF8EE",
    borderRadius: 24,
    height: 48,
    justifyContent: "center",
    width: 48
  },
  sellerAvatarText: { color: storeLight.text.primary, fontSize: 18, fontWeight: "900" },
  sellerBody: { flex: 1, gap: 3, marginLeft: 11 },
  sellerCard: {
    alignItems: "center",
    backgroundColor: storeLight.bg.card,
    borderColor: storeLight.border.hairline,
    borderRadius: 14,
    borderWidth: StyleSheet.hairlineWidth,
    flexDirection: "row",
    marginHorizontal: 16,
    marginTop: 12,
    padding: 14
  },
  sellerMeta: { color: storeLight.text.muted, fontSize: 12, fontWeight: "600" },
  sellerMetaRow: { alignItems: "center", flexDirection: "row", flexWrap: "wrap", gap: 4 },
  sellerName: { color: storeLight.text.primary, fontSize: 15, fontWeight: "900" },
  shell: { backgroundColor: storeLight.bg.page, flex: 1 },
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
  soldScrimText: { color: "#FFFFFF", fontSize: 20, fontWeight: "900", letterSpacing: 2 },
  stepper: {
    alignItems: "center",
    borderColor: storeLight.border.secondaryButton,
    borderRadius: 999,
    borderWidth: 1,
    flexDirection: "row"
  },
  stepperButton: { alignItems: "center", height: 40, justifyContent: "center", width: 44 },
  stepperValue: { color: storeLight.text.primary, fontSize: 15, fontWeight: "900", minWidth: 28, textAlign: "center" },
  title: { color: storeLight.text.primary, fontSize: 22, fontWeight: "900", lineHeight: 28 },
  unavailable: {
    alignItems: "center",
    backgroundColor: storeLight.bg.page,
    flex: 1,
    gap: 12,
    justifyContent: "center",
    padding: 28
  },
  unavailableButton: {
    alignItems: "center",
    borderColor: storeLight.border.secondaryButton,
    borderRadius: 999,
    borderWidth: 1,
    justifyContent: "center",
    minHeight: 46,
    paddingHorizontal: 22
  },
  unavailableButtonText: { color: storeLight.text.link, fontSize: 14, fontWeight: "800" },
  unavailableTitle: { color: storeLight.text.primary, fontSize: 17, fontWeight: "900", textAlign: "center" },
  viewStore: { color: storeLight.text.link, fontSize: 13, fontWeight: "800" }
}));
