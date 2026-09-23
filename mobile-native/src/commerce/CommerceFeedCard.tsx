/**
 * A horizontal strip of Marketplace recommendations, as its own Home row.
 *
 * ## The rule this component has to keep
 *
 * A commerce unit is a sibling of the posts around it, never a layer over one.
 * `commerceRows.ts` makes that structurally true — placement is a flat-list
 * transform, so there is no way to express an overlay — and this file's job is
 * not to undo it: nothing here is `position: "absolute"` outside its own tile,
 * nothing renders outside its own row, and the ••• menu is a sheet the user
 * explicitly opened rather than a layer that arrives unasked over a caption.
 *
 * ## Why a rail rather than one card
 *
 * §2. One card per row spends the feed's whole commerce budget — two rows in a
 * page — on two products, which makes every subsequent question a trade between
 * showing more commerce and showing more feed. A rail shows several products in
 * the vertical space of one, and, more importantly, it carries a *heading* and a
 * "See all": the strip is a doorway to Marketplace rather than a thing the feed
 * is trying to sell you on its own.
 *
 * That matters most when the rail is short. On a thin catalogue the strip holds
 * one product, and the heading and "See all" are then the only route to the
 * products the feed could not show — so they render for a one-tile strip
 * exactly as they do for a four-tile one, rather than being hidden as chrome.
 *
 * ## Why each tile owns the whole ••• menu
 *
 * The alternative is a row-level menu, and it immediately has to answer "hide
 * *which* one". Splitting the six actions across two menus — per-product on the
 * tile, per-feature on the header — means a user hunting for "not interested"
 * has to know which of two menus the author filed it under. Every action here is
 * therefore on the tile, matching `MarketplaceDiscoveryShelves`, and row-level
 * dismissal falls out for free: hide the last tile and the strip has nothing
 * left, so `injectCommerceRows` emits no row at all.
 *
 * ## Two impressions, deliberately
 *
 * `recordCommerceImpression(placement, {visible: false})` fires on mount —
 * "this was served and drawn" — and a second call with `visible: true` fires
 * once the row has held the viewability threshold. The server files them under
 * different dedup keys so "served" and "actually seen" stay separable. Folding
 * them into one call would make every card that blurred past during a fast
 * scroll count as seen, which is the number every recommender is tempted to
 * report and the one nobody should.
 *
 * ## Why the threshold is the list's, not the server's
 *
 * The serve response carries `visible_percent_threshold` (60) so the rule lives
 * in one place. But the *percentage* is evaluated by Home's `FlatList`, which
 * reports viewability at 72% for every row it owns — one list cannot hold two
 * percentage thresholds without swapping in `viewabilityConfigCallbackPairs`,
 * and RN throws if those change after mount. So this card honours the server's
 * **dwell** exactly and inherits the list's stricter **percentage**. The
 * direction matters: 72 ≥ 60 means we under-count visible impressions rather
 * than over-count them, and an analytics gap is a smaller lie than a claim that
 * someone saw a product they did not.
 */
import { useCallback, useMemo, useRef, useState } from "react";
import { Animated, FlatList, Image, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import {
  CommerceFeedbackAction,
  CommercePlacement,
  explainCommercePlacement,
  recordCommerceEngagement,
  type CommerceExplanation
} from "../api/commerceDiscovery";
import { attributeCommerceClick } from "./attribution";
import { useCommerceImpression } from "./useCommerceImpression";
import { saveMarketplaceListing } from "../api/marketplace";
import { useTranslation } from "../i18n/I18nContext";
import { formatCurrencyAmount, formatNumber } from "../i18n/format";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { useLogiNexusReducedMotion } from "../theme/logiNexusMotion";
import { createThemedStyles } from "../theme/themedStyles";

type Navigation = { navigate: (...args: any[]) => void };


export type CommerceFeedCardProps = {
  /**
   * The strip's products, already filtered to what this row may show.
   *
   * `injectCommerceRows` has removed anything the user dismissed and never
   * emits this row with an empty list, so there is no "no products" state to
   * render here — a strip with nothing in it is a row that does not exist.
   */
  placements: CommercePlacement[];
  /** From Home's `onViewableItemsChanged`, same signal `SponsoredAdCard` takes. */
  isViewable: boolean;
  /** Server-owned dwell, in ms. The percentage is the list's — see the header. */
  visibleDwellMs: number;
  edgeInset?: number;
  navigation: Navigation;
  onFeedback: (placement: CommercePlacement, action: CommerceFeedbackAction) => void;
};

/** How long the row takes to fold away after its last product is dismissed. */
const COLLAPSE_MS = logiNexus.motion.quick;

/** Tile geometry. The cover is square so a rail of mixed art has one baseline. */
const TILE_WIDTH = 148;

/**
 * The ••• menu, in the order the mission specifies.
 *
 * `snooze` is last and separated in the UI because it is the only item that is
 * about the whole feature rather than about this product — "stop showing me any
 * of these for a month" sitting flush against "hide this one" invites the wrong
 * tap.
 */
const MENU_ACTIONS: { action: CommerceFeedbackAction; key: string }[] = [
  { action: "hide", key: "commerce:discovery.menu.hide" },
  { action: "not_interested", key: "commerce:discovery.menu.notInterested" },
  { action: "see_fewer", key: "commerce:discovery.menu.seeFewer" },
  { action: "hide_seller", key: "commerce:discovery.menu.hideSeller" }
];

/**
 * Frozen at module scope: RN throws if `viewabilityConfig` changes identity
 * after mount, and an inline literal changes it on every render.
 */
const RAIL_VIEWABILITY = { itemVisiblePercentThreshold: 60, minimumViewTime: 0 };

export function CommerceFeedCard({
  placements,
  isViewable,
  visibleDwellMs,
  edgeInset = 12,
  navigation,
  onFeedback
}: CommerceFeedCardProps) {
  const { t } = useTranslation();
  const reducedMotion = useLogiNexusReducedMotion();

  const collapse = useRef(new Animated.Value(1)).current;
  const measuredHeight = useRef(0);
  const [collapsing, setCollapsing] = useState(false);

  /**
   * Which tiles the rail currently has on screen.
   *
   * The row's own `isViewable` says the *strip* is in the feed's viewport; it
   * says nothing about a tile scrolled off the right-hand edge. Reporting those
   * as seen would inflate exactly the number §18 exists to make trustworthy, so
   * a tile counts as visible only when both are true.
   */
  const [viewableIds, setViewableIds] = useState<ReadonlySet<string>>(new Set());
  const onRailViewableChanged = useRef(
    ({ viewableItems }: { viewableItems: { item?: CommercePlacement }[] }) => {
      const next = new Set<string>();
      for (const entry of viewableItems) {
        const id = entry?.item?.placementId;
        if (id) next.add(id);
      }
      setViewableIds(next);
    }
  ).current;

  /**
   * Which product's ••• menu is open, held here rather than in the tile.
   *
   * The menu has six items and a tile is 148pt wide, so it cannot open inside
   * one. The obvious fix — a Modal — is the wrong one: a commerce unit that
   * floats over the feed is the exact shape §1 forbids, and once the pattern is
   * established nothing stops the next iteration from opening it on a scroll
   * rather than on a tap. So the menu opens *in the card*, full width, below the
   * rail, and the card simply gets taller. The row pushes the posts below it
   * down; it never covers them.
   *
   * The consequence is that the menu must say which product it is about, since
   * it is no longer visually attached to the tile that opened it. That is what
   * `menuFor.product.title` in the header is for.
   */
  const [menuFor, setMenuFor] = useState<CommercePlacement | null>(null);
  const [whyFor, setWhyFor] = useState<CommercePlacement | null>(null);
  const [explanation, setExplanation] = useState<CommerceExplanation | null>(null);
  const [explaining, setExplaining] = useState(false);

  const handleWhy = useCallback(() => {
    const target = menuFor;
    if (!target) return;
    setMenuFor(null);
    setWhyFor(target);
    setExplaining(true);
    explainCommercePlacement(target)
      .then((result) => setExplanation(result))
      .catch(() => setExplanation(null))
      .finally(() => setExplaining(false));
  }, [menuFor]);

  /**
   * Fold the row away, *then* tell the parent — but only for the last product.
   *
   * Dismissing one tile of several is a change inside the rail: the parent drops
   * it from `placements`, the remaining tiles close up horizontally, and the
   * row's height never changes, so there is nothing to ease. Dismissing the
   * *last* one removes the row itself, and a row that vanishes on the same frame
   * as the tap leaves the posts around it snapping together under the user's
   * thumb. The height is the one measured by `onLayout`, so the fold ends
   * exactly where the removal begins and there is no visible step between them.
   */
  const dismissWith = useCallback(
    (placement: CommercePlacement, action: CommerceFeedbackAction) => {
      setMenuFor(null);
      const lastOne = placements.length <= 1 || action === "snooze";
      if (!lastOne || reducedMotion || measuredHeight.current <= 0) {
        onFeedback(placement, action);
        return;
      }
      setCollapsing(true);
      Animated.timing(collapse, {
        toValue: 0,
        duration: COLLAPSE_MS,
        // Height cannot run on the native driver. One 150ms animation on one
        // row is the whole cost, and it only ever runs on an explicit tap.
        useNativeDriver: false
      }).start(() => onFeedback(placement, action));
    },
    [collapse, onFeedback, placements.length, reducedMotion]
  );

  /**
   * The strip's heading, taken from the product that leads it.
   *
   * `labelKey` is whatever the server sent — "recommended" for organic, or
   * "trending" for a house promotion. It is never turned into "Sponsored" here:
   * the class is carried, not inferred, and an unpaid placement wearing an ad
   * label is the one error in this system with a legal shape.
   */
  const lead = placements[0];
  const label = t(lead?.labelKey || "commerce:discovery.label.recommended");
  const subtitle = t(`commerce:discovery.subtitle.${lead?.reason || "popular"}`);

  const handleSeeAll = useCallback(() => {
    // §9: Marketplace is the commerce-dense destination, and this is the door.
    // Deliberately not an engagement beacon for any one product — nobody clicked
    // a product, and attributing this to the lead tile would credit it with a
    // click it did not receive.
    navigation.navigate("Marketplace");
  }, [navigation]);

  // A row with nothing in it is a row that should not have been emitted.
  // Guarded rather than rendered empty so a future caller cannot produce a
  // heading and a "See all" floating over no products.
  if (!placements.length) return null;

  const stripBody = (
    <View
      style={styles.card}
      testID="commerce-strip-card"
      onLayout={(event) => {
        if (!collapsing) measuredHeight.current = event.nativeEvent.layout.height;
      }}
    >
      <View style={styles.header}>
        <View style={styles.headerText}>
          <Text style={styles.label}>{label}</Text>
          <Text style={styles.subtitle} numberOfLines={1}>
            {subtitle}
          </Text>
        </View>
        <Pressable
          hitSlop={10}
          accessibilityRole="button"
          accessibilityLabel={t("commerce:discovery.action.seeAll")}
          testID="commerce-strip-see-all"
          onPress={handleSeeAll}
          style={styles.seeAll}
        >
          <Text style={styles.seeAllText}>{`${t("commerce:discovery.action.seeAll")} ›`}</Text>
        </Pressable>
      </View>

      <FlatList
        horizontal
        showsHorizontalScrollIndicator={false}
        data={placements}
        keyExtractor={(item) => item.placementId}
        contentContainerStyle={styles.rail}
        viewabilityConfig={RAIL_VIEWABILITY}
        onViewableItemsChanged={onRailViewableChanged}
        // §16: virtualization is what makes the covers lazy. A tile that has not
        // been rendered has no `Image`, so it issues no request — the rail costs
        // what is on screen rather than what the server sent.
        initialNumToRender={2}
        windowSize={3}
        removeClippedSubviews
        testID="commerce-strip-rail"
        renderItem={({ item }) => (
          <StripTile
            placement={item}
            isViewable={isViewable && viewableIds.has(item.placementId)}
            visibleDwellMs={visibleDwellMs}
            navigation={navigation}
            onRequestMenu={setMenuFor}
          />
        )}
      />

      {menuFor ? (
        <View style={styles.menu} testID="commerce-strip-menu">
          {/* Names the product, because a full-width menu below the rail is no
              longer visually attached to the tile that opened it and "hide
              this" has to be answerable. */}
          <Text style={styles.menuSubject} numberOfLines={1} testID="commerce-strip-menu-subject">
            {menuFor.product.title}
          </Text>
          {MENU_ACTIONS.map((item) => (
            <Pressable
              key={item.action}
              accessibilityRole="button"
              testID={`commerce-strip-menu-${item.action}`}
              onPress={() => dismissWith(menuFor, item.action)}
              style={styles.menuItem}
            >
              <Text style={styles.menuItemText}>{t(item.key)}</Text>
            </Pressable>
          ))}
          <Pressable
            accessibilityRole="button"
            testID="commerce-strip-menu-why"
            onPress={handleWhy}
            style={styles.menuItem}
          >
            <Text style={styles.menuItemText}>{t("commerce:discovery.menu.why")}</Text>
          </Pressable>
          <View style={styles.menuDivider} />
          <Pressable
            accessibilityRole="button"
            testID="commerce-strip-menu-snooze"
            onPress={() => dismissWith(menuFor, "snooze")}
            style={styles.menuItem}
          >
            <Text style={styles.menuItemMuted}>{t("commerce:discovery.menu.snooze")}</Text>
          </Pressable>
        </View>
      ) : null}

      {/* The one Modal, and only because the user asked a question. "Why am I
          seeing this?" is a detour out of the feed rather than a feed control,
          and it is opened by an explicit tap on an explicit item — never by
          scrolling past. */}
      <Modal visible={!!whyFor} transparent animationType="fade" onRequestClose={() => setWhyFor(null)}>
        <Pressable style={styles.sheetBackdrop} onPress={() => setWhyFor(null)}>
          {/* Swallows the tap so a press inside the sheet does not reach the
              backdrop and close it. */}
          <Pressable style={styles.sheet} onPress={() => undefined} testID="commerce-why-sheet">
            <Text style={styles.sheetTitle}>{t("commerce:discovery.why.title")}</Text>
            {explaining ? (
              <Text style={styles.sheetBody}>{t("commerce:discovery.why.loading")}</Text>
            ) : explanation ? (
              <>
                <Text style={styles.sheetBody}>
                  {t(`commerce:discovery.subtitle.${explanation.reason || whyFor?.reason || "popular"}`)}
                </Text>
                {explanation.factors.length > 0 ? (
                  <>
                    <Text style={styles.sheetSectionTitle}>{t("commerce:discovery.why.factorsTitle")}</Text>
                    {explanation.factors.map((factor) => (
                      <Text key={factor} style={styles.sheetFactor}>
                        {`• ${t(`commerce:discovery.factor.${factor}`)}`}
                      </Text>
                    ))}
                  </>
                ) : null}
              </>
            ) : (
              <Text style={styles.sheetBody}>{t("commerce:discovery.why.unavailable")}</Text>
            )}
            <Pressable
              accessibilityRole="button"
              testID="commerce-why-close"
              onPress={() => setWhyFor(null)}
              style={styles.sheetClose}
            >
              <Text style={styles.sheetCloseText}>{t("commerce:discovery.why.close")}</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>
    </View>
  );

  return (
    <View style={{ marginHorizontal: edgeInset }} testID={`commerce-strip-${lead.placementId}`}>
      {collapsing ? (
        <Animated.View
          style={{
            height: collapse.interpolate({ inputRange: [0, 1], outputRange: [0, measuredHeight.current] }),
            opacity: collapse,
            overflow: "hidden"
          }}
        >
          {stripBody}
        </Animated.View>
      ) : (
        stripBody
      )}
    </View>
  );
}

/**
 * One product in the rail.
 *
 * Owns its impression, its click and its save, because all three are statements
 * about *this product*. It does not own the menu: six options do not fit in
 * 148pt, and the alternative to a full-width menu in the card is a popover over
 * the feed. So the tile asks, and the strip opens.
 */
function StripTile({
  placement,
  isViewable,
  visibleDwellMs,
  navigation,
  onRequestMenu
}: {
  placement: CommercePlacement;
  isViewable: boolean;
  visibleDwellMs: number;
  navigation: Navigation;
  onRequestMenu: (placement: CommercePlacement) => void;
}) {
  const { t } = useTranslation();
  const [saved, setSaved] = useState(false);
  const clickingRef = useRef(false);

  // "Served and drawn", then "actually seen" once the dwell is satisfied. The
  // rule is shared with every other surface rather than restated here — see
  // `useCommerceImpression`.
  useCommerceImpression({ placement, isViewable, visibleDwellMs });

  const product = placement.product;

  const handleOpenProduct = useCallback(async () => {
    if (clickingRef.current) return;
    clickingRef.current = true;
    // Recorded before navigating, not after: the screen transition unmounts
    // this tile, and a beacon started on the way out is a beacon that races
    // its own component's teardown.
    // Opens the attribution window before the beacon, not after: the await
    // below can take as long as the network does, and the product screen is
    // already mounting by then. Attributing first means a fast navigation
    // cannot beat its own click into the store and arrive unattributed.
    attributeCommerceClick(placement);
    await recordCommerceEngagement(placement, "click").catch(() => undefined);
    clickingRef.current = false;
    const listingId = product.listingId;
    if (!listingId) return;
    navigation.navigate("MarketplaceProduct", {
      listingId,
      title: product.title || undefined
    });
  }, [navigation, placement, product.listingId, product.title]);

  const handleSave = useCallback(() => {
    // Optimistic, and never reverted: the server owns duplicate suppression, so
    // the honest states are "asked to save" and "not yet". Flipping the label
    // back would invite a second save for one that may well have landed.
    setSaved(true);
    recordCommerceEngagement(placement, "save").catch(() => undefined);
    if (product.listingId) saveMarketplaceListing(product.listingId).catch(() => undefined);
  }, [placement, product.listingId]);

  /**
   * The server's formatted label wins.
   *
   * It was produced where the listing's own currency and locale are known. The
   * minor-units fallback exists for a placement that arrived without one, and
   * it is a fallback rather than the primary path because re-deriving a price
   * on the client is how a listing priced in one currency ends up displayed in
   * another.
   */
  const priceText = useMemo(() => {
    if (product.priceLabel) return product.priceLabel;
    if (!placement.priceMinor) return "";
    return formatCurrencyAmount(placement.priceMinor / 100, { currency: placement.priceCurrency || "USD" });
  }, [placement.priceCurrency, placement.priceMinor, product.priceLabel]);

  // A tile with no product is not a tile. Guarded rather than rendered empty,
  // because a rail of blank boxes is worse than a shorter rail.
  if (!product?.title || !product?.listingId) return null;

  const ratingText = product.rating > 0 ? `★ ${formatNumber(product.rating, { maximumFractionDigits: 1 })}` : "";

  return (
    <View style={styles.tile} testID={`commerce-tile-${placement.placementId}`}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={[product.title, product.sellerStoreName, priceText].filter(Boolean).join(". ")}
        testID="commerce-tile-body"
        onPress={handleOpenProduct}
      >
        {product.coverImageUrl ? (
          <Image source={{ uri: product.coverImageUrl }} style={styles.cover} resizeMode="cover" />
        ) : (
          <View style={[styles.cover, styles.coverEmpty]} />
        )}
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

      {/* Save and ••• share a row so neither costs the rail extra height, and
          both sit in normal flow — nothing in a tile is absolutely positioned,
          which is what keeps a commerce unit a row rather than a layer. */}
      <View style={styles.tileActions}>
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ selected: saved }}
          testID="commerce-tile-save"
          onPress={handleSave}
          style={styles.saveButton}
        >
          <Text style={styles.saveText} numberOfLines={1}>
            {saved ? t("commerce:discovery.action.saved") : t("commerce:discovery.action.save")}
          </Text>
        </Pressable>
        <Pressable
          hitSlop={8}
          accessibilityRole="button"
          accessibilityLabel={t("commerce:discovery.menu.title")}
          testID="commerce-tile-menu-button"
          onPress={() => onRequestMenu(placement)}
          style={styles.menuButton}
        >
          <Text style={styles.menuGlyph}>⋯</Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = createThemedStyles(() => ({
  // Translucent, like `SponsoredAdCard`: the feed sits on the shared ambient
  // background, and an opaque card punches a rectangular hole in it every few
  // posts — the one discontinuity a scrolling eye reliably catches.
  card: {
    backgroundColor: logiNexus.colors.home.surfaceGlass,
    borderRadius: logiNexus.radius.large,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.md,
    marginVertical: logiNexus.spacing.sm
  },
  header: {
    flexDirection: "row",
    alignItems: "flex-start",
    justifyContent: "space-between",
    marginBottom: logiNexus.spacing.md
  },
  headerText: { flex: 1, gap: 2 },
  label: {
    ...logiNexus.typography.label,
    color: colors.accent,
    letterSpacing: 0.6,
    textTransform: "uppercase"
  },
  subtitle: {
    ...logiNexus.typography.metadata,
    color: colors.muted
  },
  /**
   * "See all ›" is a text affordance, not a button.
   *
   * §9 wants Marketplace to stay the commerce-dense destination, and a filled
   * pill next to a rail of products competes with the products for the tap. The
   * accent colour and the chevron are enough to read as a link.
   */
  seeAll: { paddingLeft: logiNexus.spacing.md, paddingVertical: 2 },
  seeAllText: { ...logiNexus.typography.button, color: colors.accent },
  /**
   * The rail's own padding. It sits on `contentContainerStyle` rather than on
   * the list so the last tile can scroll clear of the card's right edge instead
   * of being clipped flush against it.
   */
  rail: { gap: logiNexus.spacing.md, paddingRight: logiNexus.spacing.sm },
  tile: { width: TILE_WIDTH },
  /**
   * Save and ••• side by side.
   *
   * The ••• wants to be overlaid on the cover — it is the cheapest place to put
   * it — and that is exactly why it is not there. `position: "absolute"` is how
   * a row stops being a row, and `CommerceFeedCard.test.tsx` asserts the whole
   * card is free of it so that the next person to want a floating affordance has
   * to argue for it rather than add one.
   */
  tileActions: {
    flexDirection: "row",
    alignItems: "stretch",
    gap: logiNexus.spacing.xs,
    marginTop: logiNexus.spacing.sm
  },
  menuButton: {
    width: 30,
    alignItems: "center",
    justifyContent: "center",
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    borderColor: colors.border
  },
  menuGlyph: { color: colors.muted, fontSize: 18, fontWeight: "900", lineHeight: 18 },
  /**
   * The menu, inline and full width.
   *
   * Deliberately not a popover. A commerce unit that floats over the feed is the
   * shape §1 forbids; a card that grows taller pushes the posts below it down
   * instead of covering them, which is the behaviour the whole placement engine
   * is built to guarantee.
   */
  menu: {
    marginTop: logiNexus.spacing.md,
    backgroundColor: colors.surfaceRaised,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    borderColor: colors.border,
    overflow: "hidden"
  },
  /** Which product the menu is about — see the state's own comment. */
  menuSubject: {
    ...logiNexus.typography.metadata,
    color: colors.muted,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingTop: logiNexus.spacing.md
  },
  menuItem: {
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.md
  },
  menuItemText: { ...logiNexus.typography.body, color: colors.text },
  menuItemMuted: { ...logiNexus.typography.body, color: colors.muted },
  menuDivider: { height: StyleSheet.hairlineWidth, backgroundColor: colors.border },
  /** Square, so a rail of portrait and landscape art shares one baseline. */
  cover: {
    width: TILE_WIDTH,
    height: TILE_WIDTH,
    borderRadius: logiNexus.radius.medium,
    backgroundColor: colors.background
  },
  coverEmpty: { borderWidth: 1, borderColor: colors.border },
  /**
   * Two lines, fixed.
   *
   * `numberOfLines={2}` alone lets a one-line title sit a row higher than its
   * neighbour and drags the whole tile's price up with it, so a rail of mixed
   * title lengths has prices at three different heights. A fixed height costs
   * one blank line on short titles and buys an aligned rail.
   */
  title: {
    ...logiNexus.typography.metadata,
    color: colors.text,
    fontWeight: "900",
    marginTop: logiNexus.spacing.sm,
    height: 34
  },
  store: { ...logiNexus.typography.metadata, color: colors.muted },
  metaRow: {
    flexDirection: "row",
    alignItems: "baseline",
    justifyContent: "space-between",
    gap: logiNexus.spacing.xs,
    marginTop: 2
  },
  rating: { ...logiNexus.typography.metadata, color: colors.accentStrong },
  price: { ...logiNexus.typography.body, color: colors.text, fontWeight: "900" },
  saveButton: {
    flex: 1,
    alignItems: "center",
    justifyContent: "center",
    backgroundColor: colors.signalDim,
    borderRadius: logiNexus.radius.capsule,
    paddingVertical: logiNexus.spacing.xs
  },
  saveText: { ...logiNexus.typography.metadata, color: colors.accent },
  sheetBackdrop: {
    flex: 1,
    backgroundColor: "rgba(5, 9, 16, 0.7)",
    alignItems: "center",
    justifyContent: "center",
    padding: logiNexus.spacing.xl
  },
  sheet: {
    width: "100%",
    backgroundColor: colors.surfaceRaised,
    borderRadius: logiNexus.radius.panel,
    borderWidth: 1,
    borderColor: colors.border,
    padding: logiNexus.spacing.xl,
    gap: logiNexus.spacing.sm
  },
  sheetTitle: { ...logiNexus.typography.sectionTitle, color: colors.text },
  sheetBody: { ...logiNexus.typography.body, color: colors.muted },
  sheetSectionTitle: {
    ...logiNexus.typography.label,
    color: colors.accent,
    marginTop: logiNexus.spacing.sm,
    textTransform: "uppercase",
    letterSpacing: 0.6
  },
  sheetFactor: { ...logiNexus.typography.metadata, color: colors.muted },
  sheetClose: {
    alignSelf: "flex-start",
    marginTop: logiNexus.spacing.md,
    backgroundColor: colors.signalDim,
    borderRadius: logiNexus.radius.capsule,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.sm
  },
  sheetCloseText: { ...logiNexus.typography.button, color: colors.accent }
}));
