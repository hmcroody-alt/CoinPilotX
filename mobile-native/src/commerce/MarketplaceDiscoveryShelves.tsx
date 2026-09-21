/**
 * The Marketplace shelves: horizontal rails of recommended products above the
 * browse grid.
 *
 * ## Why these are rails and not more grid
 *
 * The grid below is a *result set* — it answers the search, the category and
 * the sort, and its ordering is the user's. A shelf is a different kind of
 * claim: "Because you viewed", "From sellers you follow", each a small titled
 * group with its own justification. Mixing recommended products into the grid
 * would make the grid's ordering partly ours and partly theirs with no seam the
 * user can see, which is the version of this feature that quietly becomes
 * untrustworthy. A rail is visibly a different object.
 *
 * Every shelf is built from the same ranked pool filtered to one reason code,
 * so a product appears under "Trending" only when the ranker genuinely called
 * it trending. The heading is an i18n key from the server
 * (`commerce:discovery.module.<reason>`), never prose off the wire.
 *
 * ## The dwell constant, stated rather than hidden
 *
 * Every other surface takes `visibleDwellMs` from its serve response, because
 * the viewability contract has one owner and that owner is the server. The
 * `/marketplace/modules` endpoint returns shelves and nothing else — it carries
 * no `visible_dwell_ms`. So this file holds a local default, and it is written
 * as a named constant with this note attached rather than as a bare `1000`
 * inside a call, because it is the one place in the system where the client
 * decides that number. If the endpoint grows the field, delete the constant.
 *
 * ## Percentage vs dwell
 *
 * As everywhere else: the *dwell* is honoured exactly, the *percentage* is
 * whatever the owning list reports. These rails report at 60%, matching the
 * server's threshold rather than beating it, because a card in a horizontal
 * rail is small and a stricter percentage on a 150pt card starts excluding
 * cards that are plainly on screen.
 */
import { useCallback, useMemo, useRef, useState } from "react";
import { FlatList, Image, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import {
  CommerceFeedbackAction,
  CommerceModule,
  CommercePlacement,
  explainCommercePlacement,
  recordCommerceEngagement,
  type CommerceExplanation
} from "../api/commerceDiscovery";
import { saveMarketplaceListing } from "../api/marketplace";
import { useTranslation } from "../i18n/I18nContext";
import { formatCurrencyAmount, formatNumber } from "../i18n/format";
import { storeLight } from "../theme/marketplaceLight";
import { useCommerceImpression } from "./useCommerceImpression";

type Navigation = { navigate: (...args: any[]) => void };

/** See the header: the only client-owned dwell in the system, and why. */
const SHELF_VISIBLE_DWELL_MS = 1000;

/**
 * Frozen at module scope because RN throws if `viewabilityConfig` changes
 * identity after mount, and an inline object literal changes it every render.
 */
const VIEWABILITY_CONFIG = {
  itemVisiblePercentThreshold: 60,
  minimumViewTime: 0
};

/**
 * The ••• menu, in the order the mission specifies. `snooze` is separated
 * below because it is about the whole feature rather than about this card.
 */
const MENU_ACTIONS: { action: CommerceFeedbackAction; key: string }[] = [
  { action: "hide", key: "commerce:discovery.menu.hide" },
  { action: "not_interested", key: "commerce:discovery.menu.notInterested" },
  { action: "see_fewer", key: "commerce:discovery.menu.seeFewer" },
  { action: "hide_seller", key: "commerce:discovery.menu.hideSeller" }
];

export type MarketplaceDiscoveryShelvesProps = {
  modules: CommerceModule[];
  navigation: Navigation;
  onFeedback: (placement: CommercePlacement, action: CommerceFeedbackAction) => void;
};

export function MarketplaceDiscoveryShelves({
  modules,
  navigation,
  onFeedback
}: MarketplaceDiscoveryShelvesProps) {
  // No shelves is the normal answer, and it renders as nothing at all — not as
  // an empty container with padding, which would leave a gap above the grid.
  if (!modules.length) return null;

  return (
    <View testID="marketplace-discovery-shelves">
      {modules.map((module) => (
        <DiscoveryShelf key={module.key} module={module} navigation={navigation} onFeedback={onFeedback} />
      ))}
    </View>
  );
}

function DiscoveryShelf({
  module,
  navigation,
  onFeedback
}: {
  module: CommerceModule;
  navigation: Navigation;
  onFeedback: (placement: CommercePlacement, action: CommerceFeedbackAction) => void;
}) {
  const { t } = useTranslation();
  const [viewableIds, setViewableIds] = useState<ReadonlySet<string>>(new Set());

  // Held in a ref so the callback identity is stable for the life of the rail.
  // RN also rejects a changed `onViewableItemsChanged` after mount.
  const onViewableItemsChanged = useRef(({ viewableItems }: { viewableItems: { key?: string; item?: CommercePlacement }[] }) => {
    const next = new Set<string>();
    for (const entry of viewableItems) {
      const id = entry?.item?.placementId;
      if (id) next.add(id);
    }
    setViewableIds(next);
  }).current;

  // `titleKey` is the server's; the reason code is the fallback for a shelf that
  // arrived without one. Neither is ever rendered raw.
  const heading = t(module.titleKey || `commerce:discovery.module.${module.reason || "popular"}`);

  return (
    <View style={styles.shelf} testID={`marketplace-shelf-${module.key}`}>
      <Text style={styles.shelfHeading} testID={`marketplace-shelf-heading-${module.key}`}>
        {heading}
      </Text>
      <FlatList
        horizontal
        showsHorizontalScrollIndicator={false}
        data={module.placements}
        keyExtractor={(item) => item.placementId}
        contentContainerStyle={styles.rail}
        viewabilityConfig={VIEWABILITY_CONFIG}
        onViewableItemsChanged={onViewableItemsChanged}
        renderItem={({ item }) => (
          <ShelfCard
            placement={item}
            isViewable={viewableIds.has(item.placementId)}
            navigation={navigation}
            onFeedback={onFeedback}
          />
        )}
      />
    </View>
  );
}

function ShelfCard({
  placement,
  isViewable,
  navigation,
  onFeedback
}: {
  placement: CommercePlacement;
  isViewable: boolean;
  navigation: Navigation;
  onFeedback: (placement: CommercePlacement, action: CommerceFeedbackAction) => void;
}) {
  const { t } = useTranslation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [whyOpen, setWhyOpen] = useState(false);
  const [explaining, setExplaining] = useState(false);
  const [explanation, setExplanation] = useState<CommerceExplanation | null>(null);
  const [saved, setSavedState] = useState(false);
  const clickingRef = useRef(false);

  useCommerceImpression({ placement, isViewable, visibleDwellMs: SHELF_VISIBLE_DWELL_MS });

  const product = placement.product;

  /**
   * The server's formatted label wins: it was produced where the listing's own
   * currency and locale are known. Re-deriving a price on the client is how a
   * listing priced in one currency ends up displayed in another.
   */
  const priceText = useMemo(() => {
    if (product.priceLabel) return product.priceLabel;
    if (!placement.priceMinor) return "";
    return formatCurrencyAmount(placement.priceMinor / 100, { currency: placement.priceCurrency || "USD" });
  }, [placement.priceCurrency, placement.priceMinor, product.priceLabel]);

  const dismissWith = useCallback(
    (action: CommerceFeedbackAction) => {
      // No local fold: the hook removes the card from the shelf, and drops the
      // shelf when it was the last one. Animating a card out and *then* having
      // its row vanish underneath it is two motions for one decision.
      setMenuOpen(false);
      onFeedback(placement, action);
    },
    [onFeedback, placement]
  );

  const handleOpenProduct = useCallback(async () => {
    if (clickingRef.current) return;
    clickingRef.current = true;
    // Recorded before navigating: the transition unmounts this card, and a
    // beacon started on the way out races its own component's teardown.
    await recordCommerceEngagement(placement, "click").catch(() => undefined);
    clickingRef.current = false;
    const listingId = product.listingId;
    if (!listingId) return;
    navigation.navigate("MarketplaceProduct", { listingId, title: product.title || undefined });
  }, [navigation, placement, product.listingId, product.title]);

  const handleSave = useCallback(() => {
    // Optimistic and never reverted: the server owns duplicate suppression, so
    // the honest states are "asked to save" and "not yet".
    setSavedState(true);
    recordCommerceEngagement(placement, "save").catch(() => undefined);
    if (product.listingId) saveMarketplaceListing(product.listingId).catch(() => undefined);
  }, [placement, product.listingId]);

  const handleWhy = useCallback(() => {
    setMenuOpen(false);
    setWhyOpen(true);
    setExplaining(true);
    explainCommercePlacement(placement)
      .then((result) => setExplanation(result))
      .catch(() => setExplanation(null))
      .finally(() => setExplaining(false));
  }, [placement]);

  // A card with no product is not a card. Guarded rather than rendered empty,
  // because a shelf of blank tiles is worse than a shorter shelf.
  if (!product?.title || !product?.listingId) return null;

  const ratingText = product.rating ? `★ ${formatNumber(product.rating, { maximumFractionDigits: 1 })}` : "";

  return (
    <View style={styles.card} testID={`marketplace-shelf-card-${placement.placementId}`}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={product.title}
        onPress={handleOpenProduct}
        testID="marketplace-shelf-card-body"
      >
        <View style={styles.imageWrap}>
          {product.coverImageUrl ? (
            <Image source={{ uri: product.coverImageUrl }} style={styles.image} resizeMode="cover" />
          ) : (
            <View style={styles.image} />
          )}
          <Pressable
            accessibilityRole="button"
            accessibilityLabel={t("commerce:discovery.menu.title")}
            hitSlop={8}
            onPress={() => setMenuOpen(true)}
            style={styles.menuButton}
            testID="marketplace-shelf-card-menu-button"
          >
            <Text style={styles.menuGlyph}>•••</Text>
          </Pressable>
        </View>
        <Text style={styles.title} numberOfLines={2}>
          {product.title}
        </Text>
        {product.sellerStoreName ? (
          <Text style={styles.store} numberOfLines={1}>
            {product.sellerStoreName}
          </Text>
        ) : null}
        <View style={styles.metaRow}>
          {priceText ? <Text style={styles.price}>{priceText}</Text> : null}
          {ratingText ? <Text style={styles.rating}>{ratingText}</Text> : null}
        </View>
      </Pressable>
      <Pressable
        accessibilityRole="button"
        onPress={handleSave}
        style={styles.saveButton}
        testID="marketplace-shelf-card-save"
      >
        <Text style={styles.saveText}>
          {t(saved ? "commerce:discovery.action.saved" : "commerce:discovery.action.save")}
        </Text>
      </Pressable>

      {/* A Modal, not an inline expansion: the thing directly underneath a rail
          is the browse grid, and pushing it down to show six options moves the
          products the user is actually looking at. A modal costs no layout. */}
      <Modal visible={menuOpen} transparent animationType="fade" onRequestClose={() => setMenuOpen(false)}>
        <Pressable style={styles.scrim} onPress={() => setMenuOpen(false)} testID="marketplace-shelf-card-menu-scrim">
          <View style={styles.menu} testID="marketplace-shelf-card-menu">
            {MENU_ACTIONS.map((item) => (
              <Pressable
                key={item.action}
                accessibilityRole="button"
                onPress={() => dismissWith(item.action)}
                style={styles.menuItem}
                testID={`marketplace-shelf-card-menu-${item.action}`}
              >
                <Text style={styles.menuItemText}>{t(item.key)}</Text>
              </Pressable>
            ))}
            <Pressable
              accessibilityRole="button"
              onPress={handleWhy}
              style={styles.menuItem}
              testID="marketplace-shelf-card-menu-why"
            >
              <Text style={styles.menuItemText}>{t("commerce:discovery.menu.why")}</Text>
            </Pressable>
            <View style={styles.menuDivider} />
            <Pressable
              accessibilityRole="button"
              onPress={() => dismissWith("snooze")}
              style={styles.menuItem}
              testID="marketplace-shelf-card-menu-snooze"
            >
              <Text style={styles.menuItemText}>{t("commerce:discovery.menu.snooze")}</Text>
            </Pressable>
          </View>
        </Pressable>
      </Modal>

      <Modal visible={whyOpen} transparent animationType="fade" onRequestClose={() => setWhyOpen(false)}>
        <Pressable style={styles.scrim} onPress={() => setWhyOpen(false)}>
          <View style={styles.why} testID="marketplace-shelf-card-why">
            <Text style={styles.whyTitle}>{t("commerce:discovery.why.title")}</Text>
            {explaining ? (
              <Text style={styles.whyBody}>{t("commerce:discovery.why.loading")}</Text>
            ) : explanation?.factors?.length ? (
              <>
                <Text style={styles.whyBody}>{t("commerce:discovery.why.factorsTitle")}</Text>
                {explanation.factors.map((factor) => (
                  <Text key={factor} style={styles.whyFactor}>
                    {t(`commerce:discovery.factor.${factor}`)}
                  </Text>
                ))}
              </>
            ) : (
              <Text style={styles.whyBody}>{t("commerce:discovery.why.unavailable")}</Text>
            )}
            <Pressable
              accessibilityRole="button"
              onPress={() => setWhyOpen(false)}
              style={styles.whyClose}
              testID="marketplace-shelf-card-why-close"
            >
              <Text style={styles.whyCloseText}>{t("commerce:discovery.why.close")}</Text>
            </Pressable>
          </View>
        </Pressable>
      </Modal>
    </View>
  );
}

const CARD_WIDTH = 150;

const styles = StyleSheet.create({
  shelf: { marginBottom: 18 },
  shelfHeading: {
    color: storeLight.text.primary,
    fontSize: 15,
    fontWeight: "800",
    marginBottom: 8
  },
  rail: { gap: 10, paddingRight: 4 },
  card: { width: CARD_WIDTH },
  imageWrap: { position: "relative" },
  image: {
    backgroundColor: storeLight.bg.skeleton,
    borderRadius: 10,
    height: CARD_WIDTH,
    width: CARD_WIDTH
  },
  menuButton: {
    alignItems: "center",
    backgroundColor: "rgba(255,255,255,0.92)",
    borderRadius: 12,
    height: 24,
    justifyContent: "center",
    position: "absolute",
    right: 6,
    top: 6,
    width: 24
  },
  menuGlyph: { color: storeLight.text.primary, fontSize: 12, fontWeight: "800", lineHeight: 13 },
  title: { color: storeLight.text.primary, fontSize: 13, fontWeight: "600", marginTop: 6 },
  store: { color: storeLight.text.muted, fontSize: 11, marginTop: 2 },
  metaRow: { alignItems: "center", flexDirection: "row", gap: 6, marginTop: 3 },
  price: { color: storeLight.text.primary, fontSize: 14, fontWeight: "800" },
  rating: { color: storeLight.text.muted, fontSize: 11 },
  saveButton: {
    alignItems: "center",
    borderColor: storeLight.border.hairline,
    borderRadius: 8,
    borderWidth: 1,
    marginTop: 6,
    paddingVertical: 6
  },
  saveText: { color: storeLight.text.primary, fontSize: 12, fontWeight: "700" },
  scrim: {
    alignItems: "center",
    backgroundColor: "rgba(0,0,0,0.35)",
    flex: 1,
    justifyContent: "center",
    padding: 24
  },
  menu: {
    backgroundColor: storeLight.bg.card,
    borderRadius: 14,
    paddingVertical: 6,
    width: "100%"
  },
  menuItem: { paddingHorizontal: 18, paddingVertical: 13 },
  menuItemText: { color: storeLight.text.primary, fontSize: 15 },
  menuDivider: { backgroundColor: storeLight.border.hairline, height: 1, marginVertical: 4 },
  why: { backgroundColor: storeLight.bg.card, borderRadius: 14, padding: 18, width: "100%" },
  whyTitle: { color: storeLight.text.primary, fontSize: 16, fontWeight: "800", marginBottom: 8 },
  whyBody: { color: storeLight.text.muted, fontSize: 13 },
  whyFactor: { color: storeLight.text.primary, fontSize: 13, marginTop: 4 },
  whyClose: { alignSelf: "flex-end", marginTop: 14 },
  whyCloseText: { color: storeLight.text.link, fontSize: 14, fontWeight: "700" }
});
