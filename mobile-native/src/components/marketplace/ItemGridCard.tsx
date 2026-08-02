/**
 * One item in the buying grid.
 *
 * Anatomy: square image with a badge top-left and a heart top-right, then price
 * (bold, first — it is what the eye is scanning for), title clamped to one line
 * at default size, a meta line of distance and seller, and the action button.
 *
 * ## Three tap targets, not one
 *
 * The card, the heart and the action button are separate targets, because they
 * mean three different things and nesting them would make the heart a way to
 * accidentally open a listing. The card carries the item's full announcement;
 * the heart and the button carry their own labels and are excluded from the
 * card's, so a screen reader hears each once.
 *
 * ## FEATURED is a paid placement, and says so
 *
 * The badge reads FEATURED, but the accessibility label announces "Sponsored" —
 * matching `SponsoredAdCard`, which is this app's existing disclosure and uses
 * exactly that word. A visible "Promoted" line sits under the badge as well, so
 * the disclosure is not carried by a screen reader alone. Boost is what a seller
 * buys to get here; a buyer is entitled to know that before they tap.
 *
 * ## The fulfillment split
 *
 * Whether this card shows Add to cart or Make offer is *not* decided here and is
 * not guessed from the category. The caller passes `action`, derived from the
 * listing's fulfillment data. A card with neither gets no button rather than a
 * default one — an item whose fulfillment is unknown should send you to the
 * detail page, not into a checkout.
 */

import { memo } from "react";
import { Animated, Image, Pressable, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { storeLight } from "../../theme/storeLight";
import { marketplaceLight } from "../../theme/marketplaceLight";
import { useStorePress } from "../../theme/storeMotion";
import { useMarketplaceHeartPop, useMarketplaceSoldWipe } from "../../theme/marketplaceMotion";
import { GlowButton, type GlowButtonVariant } from "./GlowButton";

export type ItemGridBadge = "new" | "featured" | null;

export type ItemGridAction = {
  variant: GlowButtonVariant;
  label: string;
  accessibilityLabel: string;
  onPress: () => void;
  confirming?: boolean;
  busy?: boolean;
};

export type ItemGridCardProps = {
  id: string;
  title: string;
  /** Formatted, e.g. "$120.00". */
  priceText: string;
  /** Formatted original price when this is a drop. Rendered struck through. */
  originalPriceText?: string | null;
  imageUrl?: string | null;
  badge: ItemGridBadge;
  /** e.g. "2.4 mi · Dana R. 4.9★" or "Ships free". Already localized. */
  metaText: string;
  /** True when `metaText` is a fulfillment promise rather than a distance. */
  metaIsFulfillment?: boolean;
  saved: boolean;
  onToggleSave: () => void;
  onPress: () => void;
  sold?: boolean;
  action?: ItemGridAction | null;
  /** From the list's viewability callback. Parks the button's ambient loops. */
  visible?: boolean;
  index?: number;
  reducedMotion: boolean;
};

function ItemGridCardBase({
  title,
  priceText,
  originalPriceText,
  imageUrl,
  badge,
  metaText,
  metaIsFulfillment = false,
  saved,
  onToggleSave,
  onPress,
  sold = false,
  action,
  visible = true,
  index = 0,
  reducedMotion
}: ItemGridCardProps) {
  const { fontScale } = useWindowDimensions();
  const press = useStorePress(reducedMotion, 0.985);
  const heart = useMarketplaceHeartPop(reducedMotion, saved);
  const wipe = useMarketplaceSoldWipe(reducedMotion, sold);

  // The image grows slightly on press while the card shrinks — the same
  // deliberate inversion the Store listing row uses, so a tap reads as "look
  // closer" rather than "pushed away".
  const imagePress = useStorePress(reducedMotion, 1.08);
  const titleLines = fontScale > 1.15 ? 2 : 1;

  const badgeText = badge === "featured" ? "FEATURED" : badge === "new" ? "NEW" : null;

  return (
    <Animated.View style={[styles.card, press.style]}>
      <Pressable
        onPress={onPress}
        onPressIn={() => {
          press.onPressIn();
          imagePress.onPressIn();
        }}
        onPressOut={() => {
          press.onPressOut();
          imagePress.onPressOut();
        }}
        accessibilityRole="button"
        accessibilityLabel={[
          title,
          priceText,
          originalPriceText ? `reduced from ${originalPriceText}` : null,
          metaText,
          // "Sponsored" rather than "Featured": the word that discloses the paid
          // placement, matching SponsoredAdCard.
          badge === "featured" ? "Sponsored" : badge === "new" ? "New listing" : null,
          sold ? "Sold" : null
        ]
          .filter(Boolean)
          .join(", ")}
        accessibilityHint="Opens the item"
      >
        <View style={styles.imageWrap}>
          <Animated.View style={[styles.imageInner, imagePress.style]}>
            {imageUrl ? (
              <Image source={{ uri: imageUrl }} style={styles.image} />
            ) : (
              <View style={[styles.image, styles.imageEmpty]}>
                <Ionicons name="image-outline" size={22} color={storeLight.text.muted} />
              </View>
            )}
          </Animated.View>

          {badgeText ? (
            <View
              style={[styles.badge, badge === "featured" ? styles.badgeFeatured : styles.badgeNew]}
              accessibilityElementsHidden
              importantForAccessibility="no"
            >
              <Text
                style={[
                  styles.badgeText,
                  badge === "featured" ? styles.badgeTextFeatured : styles.badgeTextNew
                ]}
              >
                {badgeText}
              </Text>
            </View>
          ) : null}

          {sold ? (
            <Animated.View
              style={[
                styles.soldOverlay,
                {
                  opacity: wipe,
                  transform: [
                    { translateX: wipe.interpolate({ inputRange: [0, 1], outputRange: [-40, 0] }) }
                  ]
                }
              ]}
              accessibilityElementsHidden
              importantForAccessibility="no"
            >
              <Text style={styles.soldText}>SOLD</Text>
            </Animated.View>
          ) : null}
        </View>
      </Pressable>

      {/* Outside the card Pressable so it is a target in its own right rather
          than a region of the card that happens to do something else. */}
      <Pressable
        style={styles.heart}
        onPress={onToggleSave}
        hitSlop={8}
        accessibilityRole="button"
        accessibilityLabel={saved ? `Saved. Remove ${title} from saved` : `Save ${title}`}
        accessibilityState={{ selected: saved }}
      >
        <Animated.View style={{ transform: [{ scale: heart }] }}>
          <Ionicons
            name={saved ? "heart" : "heart-outline"}
            size={18}
            color={saved ? storeLight.status.error : storeLight.text.primary}
          />
        </Animated.View>
      </Pressable>

      <View style={styles.body}>
        <View style={styles.priceRow}>
          <Text style={styles.price}>{priceText}</Text>
          {originalPriceText ? <Text style={styles.original}>{originalPriceText}</Text> : null}
        </View>
        <Text style={styles.title} numberOfLines={titleLines}>
          {title}
        </Text>
        <Text
          style={[styles.meta, metaIsFulfillment && styles.metaFulfillment]}
          numberOfLines={1}
        >
          {metaText}
        </Text>
        {badge === "featured" ? <Text style={styles.promoted}>Promoted</Text> : null}

        {action ? (
          <GlowButton
            variant={action.variant}
            label={action.label}
            accessibilityLabel={action.accessibilityLabel}
            onPress={action.onPress}
            confirming={action.confirming}
            busy={action.busy}
            disabled={sold}
            visible={visible}
            index={index}
            reducedMotion={reducedMotion}
          />
        ) : null}
      </View>
    </Animated.View>
  );
}

/**
 * Memoised because the buying grid is virtualized and re-renders on every
 * viewability change. Without this, scrolling re-renders every mounted card to
 * flip one card's `visible` flag.
 */
export const ItemGridCard = memo(ItemGridCardBase);

const styles = StyleSheet.create({
  card: {
    flex: 1,
    backgroundColor: storeLight.bg.card,
    borderRadius: marketplaceLight.grid.radius,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    overflow: "hidden"
  },
  imageWrap: { width: "100%", aspectRatio: marketplaceLight.grid.imageAspect, overflow: "hidden" },
  imageInner: { flex: 1 },
  image: { width: "100%", height: "100%", backgroundColor: storeLight.bg.skeleton },
  imageEmpty: { alignItems: "center", justifyContent: "center" },
  badge: {
    position: "absolute",
    top: 8,
    left: 8,
    paddingHorizontal: 7,
    paddingVertical: 3,
    borderRadius: 4
  },
  badgeFeatured: { backgroundColor: marketplaceLight.badge.featuredBg },
  badgeNew: { backgroundColor: marketplaceLight.badge.newBg },
  badgeText: { fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  badgeTextFeatured: { color: marketplaceLight.badge.featuredText },
  badgeTextNew: { color: marketplaceLight.badge.newText },
  soldOverlay: {
    ...StyleSheet.absoluteFillObject,
    backgroundColor: marketplaceLight.badge.soldOverlay,
    alignItems: "center",
    justifyContent: "center"
  },
  soldText: {
    fontSize: 18,
    fontWeight: "900",
    letterSpacing: 3,
    color: marketplaceLight.badge.soldText
  },
  heart: {
    position: "absolute",
    top: 4,
    right: 4,
    width: storeLight.size.tapTarget,
    height: storeLight.size.tapTarget,
    alignItems: "center",
    justifyContent: "center"
  },
  body: { padding: 10, gap: 3 },
  priceRow: { flexDirection: "row", alignItems: "baseline", gap: 6 },
  price: { fontSize: 16, fontWeight: "800", color: storeLight.text.primary },
  original: {
    fontSize: 12,
    color: storeLight.text.muted,
    textDecorationLine: "line-through"
  },
  title: { fontSize: 13, color: storeLight.text.primary, lineHeight: 17 },
  meta: { fontSize: 11, color: storeLight.text.muted },
  metaFulfillment: { color: storeLight.status.success, fontWeight: "700" },
  promoted: { fontSize: 10, color: storeLight.text.muted, fontStyle: "italic" }
});
