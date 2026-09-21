/**
 * The one Marketplace suggestion the Messenger *inbox* is allowed to show.
 *
 * ## Where it renders, and the rule that puts it there
 *
 * Inside `MessengerScreen`'s `ListHeaderComponent`, above the "Recent
 * conversations" heading. Three of the brief's Messenger rules fall out of that
 * position rather than being defended by code:
 *
 *   * **It does not replace a conversation row.** It is not in `data` at all —
 *     it is part of the header, so `filteredConversations` is untouched and
 *     every thread the user has still renders, in order.
 *   * **It is not sticky.** The list sets no `stickyHeaderIndices`, so a header
 *     child scrolls away with the header. There is no "unpin me" code here
 *     because there is nothing pinning it.
 *   * **It is above the heading, not between the heading and the rows.** A
 *     section label that has been separated from the things it labels is worse
 *     than no label, and it would also be the one arrangement that genuinely
 *     reads as "commerce inserted into your conversations".
 *
 * And the rule that matters most is kept somewhere else entirely: nothing under
 * `src/commerce` is imported by `ChatScreen`. Inside a conversation there is no
 * commerce component to render, not a disabled one. A test asserts the import
 * graph, because "we remembered not to" is not a mechanism.
 *
 * ## Why the ••• menu is a sheet and not an expansion
 *
 * `CommerceFeedCard` expands its menu inline, which is right for a feed: the
 * card is a unit in a stream of units and the stream is expected to move. Here
 * the thing directly underneath is the user's list of people, and pushing it
 * down half a screen to show six options is precisely the "pushes chats down
 * aggressively" the brief forbids. A modal costs nothing and moves nothing.
 *
 * ## One row high
 *
 * `minHeight` and the radius/padding/gap are `ConversationRow`'s own numbers,
 * quoted deliberately so the strip occupies about the space of a single thread.
 * It borrows `surfaceElevated` — the same step-up the presence rail and quick
 * actions use — so it reads as part of the inbox's furniture rather than as a
 * conversation that arrived from a stranger.
 */
import { useCallback, useMemo, useRef, useState } from "react";
import { Image, Modal, Pressable, StyleSheet, Text, View } from "react-native";
import {
  CommerceExplanation,
  CommerceFeedbackAction,
  CommercePlacement,
  explainCommercePlacement,
  recordCommerceEngagement
} from "../api/commerceDiscovery";
import { useTranslation } from "../i18n/I18nContext";
import { formatCurrencyAmount } from "../i18n/format";
import { logiNexus } from "../theme/logiNexus";
import { messengerTheme } from "../theme/messengerTheme";
import { useCommerceImpression } from "./useCommerceImpression";

type Navigation = { navigate: (...args: any[]) => void };

export type MessengerCommerceStripProps = {
  placement: CommercePlacement;
  /**
   * Whether the strip is actually on screen.
   *
   * Computed by the screen, because the strip lives in a list *header* and
   * `onViewableItemsChanged` does not report headers. See `MessengerScreen`.
   */
  isViewable: boolean;
  /** Server-owned dwell in ms, from the serve response. */
  visibleDwellMs: number;
  navigation: Navigation;
  onFeedback: (placement: CommercePlacement, action: CommerceFeedbackAction) => void;
};

/**
 * The ••• menu, in the mission's order.
 *
 * Identical to the feed card's list on purpose. A hide control that offers
 * different options depending on which screen you found the product on is a
 * hide control the user has to re-learn, and "Don't recommend this seller"
 * means the same thing on both surfaces because both write to the same store.
 */
const MENU_ACTIONS: { action: CommerceFeedbackAction; key: string }[] = [
  { action: "hide", key: "commerce:discovery.menu.hide" },
  { action: "not_interested", key: "commerce:discovery.menu.notInterested" },
  { action: "see_fewer", key: "commerce:discovery.menu.seeFewer" },
  { action: "hide_seller", key: "commerce:discovery.menu.hideSeller" }
];

export function MessengerCommerceStrip({
  placement,
  isViewable,
  visibleDwellMs,
  navigation,
  onFeedback
}: MessengerCommerceStripProps) {
  const { t } = useTranslation();

  const [menuOpen, setMenuOpen] = useState(false);
  const [whyOpen, setWhyOpen] = useState(false);
  const [explanation, setExplanation] = useState<CommerceExplanation | null>(null);
  const [explaining, setExplaining] = useState(false);
  const clickingRef = useRef(false);

  // Same two-beacon rule as every other surface — see `useCommerceImpression`.
  useCommerceImpression({ placement, isViewable, visibleDwellMs });

  const dismissWith = useCallback(
    (action: CommerceFeedbackAction) => {
      setMenuOpen(false);
      // No local fold animation. The parent drops the strip on the next render
      // and the header reflows — which is one row's worth of movement at the
      // top of a list, not a hole in the middle of a stream, so there is
      // nothing here that needs easing over.
      onFeedback(placement, action);
    },
    [onFeedback, placement]
  );

  const handleOpenProduct = useCallback(async () => {
    if (clickingRef.current) return;
    clickingRef.current = true;
    // Before navigating: the transition unmounts this strip, and a beacon
    // started on the way out races its own component's teardown.
    await recordCommerceEngagement(placement, "click").catch(() => undefined);
    clickingRef.current = false;
    const listingId = placement.product.listingId;
    if (!listingId) return;
    navigation.navigate("MarketplaceProduct", {
      listingId,
      title: placement.product.title || undefined
    });
  }, [navigation, placement]);

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

  /** The server's formatted label wins; minor units are the fallback only. */
  const priceText = useMemo(() => {
    if (product.priceLabel) return product.priceLabel;
    if (!placement.priceMinor) return "";
    return formatCurrencyAmount(placement.priceMinor / 100, {
      currency: placement.priceCurrency || "USD"
    });
  }, [placement.priceCurrency, placement.priceMinor, product.priceLabel]);

  // Carried from the server, never inferred. Messenger serves organic and house
  // placements only, so this is "Recommended from Marketplace" or "Trending on
  // PulseSoc" — never "Sponsored", which would be a false statement about money
  // that did not change hands.
  const label = t(placement.labelKey || "commerce:discovery.label.recommended");

  if (!product.title || !product.listingId) return null;

  const subline = [product.sellerStoreName, priceText].filter(Boolean).join(" · ");

  return (
    <View style={styles.wrap} testID={`messenger-commerce-${placement.placementId}`}>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={[label, product.title, product.sellerStoreName, priceText]
          .filter(Boolean)
          .join(". ")}
        testID="messenger-commerce-body"
        onPress={handleOpenProduct}
        style={({ pressed }) => [styles.body, pressed && styles.bodyPressed]}
      >
        {product.coverImageUrl ? (
          <Image source={{ uri: product.coverImageUrl }} style={styles.cover} resizeMode="cover" />
        ) : (
          <View style={[styles.cover, styles.coverEmpty]} />
        )}
        <View style={styles.copy}>
          <Text style={styles.label} numberOfLines={1}>
            {label}
          </Text>
          <Text style={styles.title} numberOfLines={1}>
            {product.title}
          </Text>
          {subline ? (
            <Text style={styles.subline} numberOfLines={1}>
              {subline}
            </Text>
          ) : null}
        </View>
      </Pressable>
      <Pressable
        accessibilityRole="button"
        accessibilityLabel={t("commerce:discovery.menu.title")}
        testID="messenger-commerce-menu-button"
        hitSlop={10}
        onPress={() => setMenuOpen(true)}
        style={styles.menuButton}
      >
        <Text style={styles.menuGlyph}>⋯</Text>
      </Pressable>

      <Modal visible={menuOpen} transparent animationType="fade" onRequestClose={() => setMenuOpen(false)}>
        <Pressable style={styles.sheetBackdrop} onPress={() => setMenuOpen(false)}>
          {/* Swallows the tap so a press inside the sheet does not reach the
              backdrop and close it. */}
          <Pressable style={styles.sheet} onPress={() => undefined} testID="messenger-commerce-menu">
            <Text style={styles.sheetTitle}>{t("commerce:discovery.menu.title")}</Text>
            {MENU_ACTIONS.map((item) => (
              <Pressable
                key={item.action}
                accessibilityRole="button"
                testID={`messenger-commerce-menu-${item.action}`}
                onPress={() => dismissWith(item.action)}
                style={styles.menuItem}
              >
                <Text style={styles.menuItemText}>{t(item.key)}</Text>
              </Pressable>
            ))}
            <Pressable
              accessibilityRole="button"
              testID="messenger-commerce-menu-why"
              onPress={handleWhy}
              style={styles.menuItem}
            >
              <Text style={styles.menuItemText}>{t("commerce:discovery.menu.why")}</Text>
            </Pressable>
            <View style={styles.menuDivider} />
            <Pressable
              accessibilityRole="button"
              testID="messenger-commerce-menu-snooze"
              onPress={() => dismissWith("snooze")}
              style={styles.menuItem}
            >
              <Text style={styles.menuItemMuted}>{t("commerce:discovery.menu.snooze")}</Text>
            </Pressable>
          </Pressable>
        </Pressable>
      </Modal>

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
              testID="messenger-commerce-why-close"
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

const styles = StyleSheet.create({
  body: {
    alignItems: "center",
    flex: 1,
    flexDirection: "row",
    gap: 9
  },
  bodyPressed: {
    opacity: 0.72
  },
  copy: {
    flex: 1
  },
  cover: {
    backgroundColor: messengerTheme.surfaceRecessed,
    borderRadius: 10,
    height: 44,
    width: 44
  },
  coverEmpty: {
    borderColor: messengerTheme.border,
    borderWidth: 1
  },
  label: {
    color: messengerTheme.tealAccent,
    fontSize: 9,
    fontWeight: "800",
    letterSpacing: 0.7,
    textTransform: "uppercase"
  },
  menuButton: {
    paddingHorizontal: 6
  },
  menuDivider: {
    backgroundColor: messengerTheme.border,
    height: StyleSheet.hairlineWidth
  },
  menuGlyph: {
    color: messengerTheme.tertiaryText,
    fontSize: 18,
    fontWeight: "900"
  },
  menuItem: {
    paddingVertical: logiNexus.spacing.md
  },
  menuItemMuted: {
    color: messengerTheme.tertiaryText,
    fontSize: 15
  },
  menuItemText: {
    color: messengerTheme.primaryText,
    fontSize: 15
  },
  sheet: {
    backgroundColor: messengerTheme.background,
    borderColor: messengerTheme.border,
    borderRadius: 18,
    borderWidth: 1,
    padding: logiNexus.spacing.xl,
    width: "100%"
  },
  sheetBackdrop: {
    alignItems: "center",
    backgroundColor: "rgba(5, 9, 16, 0.7)",
    flex: 1,
    justifyContent: "center",
    padding: logiNexus.spacing.xl
  },
  sheetBody: {
    color: messengerTheme.secondaryText,
    fontSize: 14
  },
  sheetClose: {
    alignSelf: "flex-start",
    backgroundColor: messengerTheme.tealSoft,
    borderRadius: 999,
    marginTop: logiNexus.spacing.md,
    paddingHorizontal: logiNexus.spacing.lg,
    paddingVertical: logiNexus.spacing.sm
  },
  sheetCloseText: {
    color: messengerTheme.tealAccent,
    fontSize: 14,
    fontWeight: "800"
  },
  sheetFactor: {
    color: messengerTheme.secondaryText,
    fontSize: 13
  },
  sheetSectionTitle: {
    color: messengerTheme.tealAccent,
    fontSize: 11,
    fontWeight: "800",
    letterSpacing: 0.6,
    marginTop: logiNexus.spacing.sm,
    textTransform: "uppercase"
  },
  sheetTitle: {
    color: messengerTheme.primaryText,
    fontSize: 16,
    fontWeight: "900",
    marginBottom: logiNexus.spacing.sm
  },
  subline: {
    color: messengerTheme.secondaryText,
    fontSize: 11,
    marginTop: 1
  },
  title: {
    color: messengerTheme.primaryText,
    fontSize: 13,
    fontWeight: "800",
    marginTop: 1
  },
  wrap: {
    alignItems: "center",
    backgroundColor: messengerTheme.surfaceElevated,
    borderColor: messengerTheme.border,
    // `ConversationRow`'s own radius, padding, gap and minimum height. The strip
    // is meant to be about one thread tall — quoting the numbers rather than
    // picking neighbours keeps that true if the row is ever retuned.
    borderRadius: 13,
    borderWidth: 1,
    flexDirection: "row",
    gap: 9,
    marginBottom: 6,
    minHeight: 64,
    padding: 9
  }
});
