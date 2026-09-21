/**
 * One Marketplace recommendation, as its own Home row.
 *
 * ## The rule this component has to keep
 *
 * A commerce unit is a sibling of the posts around it, never a layer over one.
 * `commerceRows.ts` makes that structurally true — placement is a flat-list
 * transform, so there is no way to express an overlay — and this file's job is
 * not to undo it: nothing here is `position: "absolute"`, nothing here renders
 * outside its own row, and the ••• menu expands *inside* the card rather than
 * floating over the post below it. The one thing that does leave the row is the
 * "Why am I seeing this?" sheet, which is a `Modal` the user explicitly opened,
 * which is a different thing from a card that covers a caption unasked.
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
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Animated, Image, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import {
  CommerceFeedbackAction,
  CommercePlacement,
  explainCommercePlacement,
  recordCommerceEngagement,
  type CommerceExplanation
} from "../api/commerceDiscovery";
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
  placement: CommercePlacement;
  /** From Home's `onViewableItemsChanged`, same signal `SponsoredAdCard` takes. */
  isViewable: boolean;
  /** Server-owned dwell, in ms. The percentage is the list's — see the header. */
  visibleDwellMs: number;
  edgeInset?: number;
  navigation: Navigation;
  onFeedback: (placement: CommercePlacement, action: CommerceFeedbackAction) => void;
};

/** How long the row takes to fold away after a negative action. */
const COLLAPSE_MS = logiNexus.motion.quick;

/**
 * The ••• menu, in the order the mission specifies.
 *
 * `snooze` is last and separated in the UI because it is the only item that is
 * about the whole feature rather than about this card — "stop showing me any of
 * these for a month" sitting flush against "hide this one" invites the wrong
 * tap.
 */
const MENU_ACTIONS: { action: CommerceFeedbackAction; key: string }[] = [
  { action: "hide", key: "commerce:discovery.menu.hide" },
  { action: "not_interested", key: "commerce:discovery.menu.notInterested" },
  { action: "see_fewer", key: "commerce:discovery.menu.seeFewer" },
  { action: "hide_seller", key: "commerce:discovery.menu.hideSeller" }
];

export function CommerceFeedCard({
  placement,
  isViewable,
  visibleDwellMs,
  edgeInset = 12,
  navigation,
  onFeedback
}: CommerceFeedCardProps) {
  const { t } = useTranslation();
  const reducedMotion = useLogiNexusReducedMotion();

  const [menuOpen, setMenuOpen] = useState(false);
  const [whyOpen, setWhyOpen] = useState(false);
  const [explanation, setExplanation] = useState<CommerceExplanation | null>(null);
  const [explaining, setExplaining] = useState(false);
  const [saved, setSaved] = useState(false);

  const collapse = useRef(new Animated.Value(1)).current;
  const measuredHeight = useRef(0);
  const [collapsing, setCollapsing] = useState(false);

  const clickingRef = useRef(false);

  // "Served and drawn", then "actually seen" once the dwell is satisfied. The
  // rule is shared with the Reels chip rather than restated here — see
  // `useCommerceImpression`.
  useCommerceImpression({ placement, isViewable, visibleDwellMs });

  /**
   * Fold the row away, *then* tell the parent.
   *
   * Sequenced rather than concurrent because the parent's response is to remove
   * the row entirely, and a row that vanishes on the same frame as the tap
   * leaves the posts around it snapping together under the user's thumb. The
   * height is the one measured by `onLayout`, so the fold ends exactly where
   * the removal begins and there is no visible step between them.
   */
  const dismissWith = useCallback(
    (action: CommerceFeedbackAction) => {
      setMenuOpen(false);
      if (reducedMotion || measuredHeight.current <= 0) {
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
    [collapse, onFeedback, placement, reducedMotion]
  );

  const handleOpenProduct = useCallback(async () => {
    if (clickingRef.current) return;
    clickingRef.current = true;
    // Recorded before navigating, not after: the screen transition unmounts
    // this card, and a beacon started on the way out is a beacon that races
    // its own component's teardown.
    await recordCommerceEngagement(placement, "click").catch(() => undefined);
    clickingRef.current = false;
    const listingId = placement.product.listingId;
    if (!listingId) return;
    navigation.navigate("MarketplaceProduct", {
      listingId,
      title: placement.product.title || undefined
    });
  }, [navigation, placement]);

  const handleSave = useCallback(() => {
    // Optimistic, and never reverted: the server owns duplicate suppression, so
    // the honest states are "asked to save" and "not yet". Flipping the label
    // back would invite a second save for one that may well have landed.
    setSaved(true);
    recordCommerceEngagement(placement, "save").catch(() => undefined);
    const listingId = placement.product.listingId;
    if (listingId) saveMarketplaceListing(listingId).catch(() => undefined);
  }, [placement]);

  const handleWhy = useCallback(() => {
    setMenuOpen(false);
    setWhyOpen(true);
    setExplaining(true);
    explainCommercePlacement(placement)
      .then((result) => setExplanation(result))
      .catch(() => setExplanation(null))
      .finally(() => setExplaining(false));
  }, [placement]);

  const product = placement.product;

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

  // `labelKey` is whatever the server sent — "recommended" for organic, or
  // "trending" for a house promotion. It is never turned into "Sponsored" here:
  // the class is carried, not inferred, and an unpaid placement wearing an ad
  // label is the one error in this system with a legal shape.
  const label = t(placement.labelKey || "commerce:discovery.label.recommended");
  const subtitle = t(`commerce:discovery.subtitle.${placement.reason || "popular"}`);

  const cardBody = (
    <View style={styles.card} onLayout={(event) => {
      if (!collapsing) measuredHeight.current = event.nativeEvent.layout.height;
    }}>
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
          accessibilityLabel={t("commerce:discovery.menu.title")}
          testID="commerce-card-menu-button"
          onPress={() => setMenuOpen((open) => !open)}
          style={styles.menuButton}
        >
          <Text style={styles.menuGlyph}>⋯</Text>
        </Pressable>
      </View>

      {menuOpen ? (
        <View style={styles.menu} testID="commerce-card-menu">
          {MENU_ACTIONS.map((item) => (
            <Pressable
              key={item.action}
              style={styles.menuItem}
              accessibilityRole="button"
              testID={`commerce-card-menu-${item.action}`}
              onPress={() => dismissWith(item.action)}
            >
              <Text style={styles.menuItemText}>{t(item.key)}</Text>
            </Pressable>
          ))}
          <Pressable
            style={styles.menuItem}
            accessibilityRole="button"
            testID="commerce-card-menu-why"
            onPress={handleWhy}
          >
            <Text style={styles.menuItemText}>{t("commerce:discovery.menu.why")}</Text>
          </Pressable>
          <View style={styles.menuDivider} />
          <Pressable
            style={styles.menuItem}
            accessibilityRole="button"
            testID="commerce-card-menu-snooze"
            onPress={() => dismissWith("snooze")}
          >
            <Text style={styles.menuItemMuted}>{t("commerce:discovery.menu.snooze")}</Text>
          </Pressable>
        </View>
      ) : null}

      <Pressable
        accessibilityRole="button"
        accessibilityLabel={[product.title, product.sellerStoreName, priceText].filter(Boolean).join(". ")}
        testID="commerce-card-body"
        onPress={handleOpenProduct}
        style={styles.body}
      >
        {product.coverImageUrl ? (
          <Image source={{ uri: product.coverImageUrl }} style={styles.cover} resizeMode="cover" />
        ) : (
          <View style={[styles.cover, styles.coverEmpty]} />
        )}
        <View style={styles.details}>
          <Text style={styles.title} numberOfLines={2}>
            {product.title}
          </Text>
          {product.sellerStoreName ? (
            <Text style={styles.store} numberOfLines={1}>
              {product.sellerStoreName}
            </Text>
          ) : null}
          {product.rating > 0 ? (
            <Text style={styles.rating}>
              {`★ ${formatNumber(product.rating, { maximumFractionDigits: 1 })}`}
              {product.ratingCount > 0 ? `  (${formatNumber(product.ratingCount)})` : ""}
            </Text>
          ) : null}
          {priceText ? <Text style={styles.price}>{priceText}</Text> : null}
        </View>
      </Pressable>

      <View style={styles.actions}>
        <Pressable
          accessibilityRole="button"
          testID="commerce-card-view"
          onPress={handleOpenProduct}
          style={styles.primaryAction}
        >
          <Text style={styles.primaryActionText}>{t("commerce:discovery.action.viewProduct")}</Text>
        </Pressable>
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ selected: saved }}
          testID="commerce-card-save"
          onPress={handleSave}
          style={styles.secondaryAction}
        >
          <Text style={styles.secondaryActionText}>
            {saved ? t("commerce:discovery.action.saved") : t("commerce:discovery.action.save")}
          </Text>
        </Pressable>
      </View>
    </View>
  );

  return (
    <View style={{ marginHorizontal: edgeInset }} testID={`commerce-card-${placement.placementId}`}>
      {collapsing ? (
        <Animated.View
          style={{
            height: collapse.interpolate({ inputRange: [0, 1], outputRange: [0, measuredHeight.current] }),
            opacity: collapse,
            overflow: "hidden"
          }}
        >
          {cardBody}
        </Animated.View>
      ) : (
        cardBody
      )}

      <Modal visible={whyOpen} transparent animationType="fade" onRequestClose={() => setWhyOpen(false)}>
        <Pressable style={styles.sheetBackdrop} onPress={() => setWhyOpen(false)}>
          <Pressable style={styles.sheet} onPress={() => undefined}>
            <Text style={styles.sheetTitle}>{t("commerce:discovery.why.title")}</Text>
            {explaining ? (
              <Text style={styles.sheetBody}>{t("commerce:discovery.why.loading")}</Text>
            ) : explanation ? (
              <>
                <Text style={styles.sheetBody}>
                  {t(`commerce:discovery.subtitle.${explanation.reason || placement.reason || "popular"}`)}
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
              onPress={() => setWhyOpen(false)}
              style={styles.sheetClose}
            >
              <Text style={styles.sheetCloseText}>{t("commerce:discovery.why.close")}</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>
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
  menuButton: { paddingHorizontal: logiNexus.spacing.sm },
  menuGlyph: { color: colors.muted, fontSize: 20, fontWeight: "900" },
  menu: {
    backgroundColor: colors.surfaceRaised,
    borderRadius: logiNexus.radius.medium,
    borderWidth: 1,
    borderColor: colors.border,
    marginBottom: logiNexus.spacing.md,
    overflow: "hidden"
  },
  menuItem: {
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.md
  },
  menuItemText: { ...logiNexus.typography.body, color: colors.text },
  menuItemMuted: { ...logiNexus.typography.body, color: colors.muted },
  menuDivider: { height: StyleSheet.hairlineWidth, backgroundColor: colors.border },
  body: { flexDirection: "row", gap: logiNexus.spacing.md },
  cover: {
    width: 96,
    height: 96,
    borderRadius: logiNexus.radius.medium,
    backgroundColor: colors.background
  },
  coverEmpty: { borderWidth: 1, borderColor: colors.border },
  details: { flex: 1, gap: 2 },
  title: { ...logiNexus.typography.body, color: colors.text, fontWeight: "900" },
  store: { ...logiNexus.typography.metadata, color: colors.muted },
  rating: { ...logiNexus.typography.metadata, color: colors.accentStrong },
  price: { ...logiNexus.typography.sectionTitle, color: colors.text, marginTop: 2 },
  actions: {
    flexDirection: "row",
    alignItems: "center",
    gap: logiNexus.spacing.sm,
    marginTop: logiNexus.spacing.md
  },
  primaryAction: {
    backgroundColor: colors.signalDim,
    borderRadius: logiNexus.radius.capsule,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.sm
  },
  primaryActionText: { ...logiNexus.typography.button, color: colors.accent },
  secondaryAction: {
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    borderColor: colors.border,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.sm
  },
  secondaryActionText: { ...logiNexus.typography.button, color: colors.muted },
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
