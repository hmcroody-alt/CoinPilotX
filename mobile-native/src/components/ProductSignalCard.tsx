/**
 * A shoppable Signal: a marketplace product rendered as a feed post.
 *
 * ## Why this is not `ItemGridCard`
 *
 * The obvious reuse is the marketplace's own product card, and it is the wrong
 * one. `components/marketplace/ItemGridCard.tsx` is built on `storeLight` and
 * `marketplaceLight` — the commerce surfaces run on a *white* page, and dropping
 * one of those cards into the feed would put a light card on the indigo field
 * `PulseBackground` draws. So this card is built the other way round: it takes
 * `PostCard`'s chrome — the same `home.borderSubtle` hairline, the same 14/16
 * insets, the same 48pt author avatar, the same action rail — and layers
 * commerce inside it. A product Signal should read as a Signal that happens to
 * sell something, not as a marketplace card that wandered in.
 *
 * The two cards are allowed to diverge for that reason, but not about facts:
 * both get their badge from `listingBadge` and their image from
 * `marketplaceListingThumbnail`, through `commerce/productSignal.ts`.
 *
 * ## Navigation-free by construction
 *
 * No route name and no navigator appear in this file. Every tap is a callback,
 * which is what lets the same card render in Home, Search, a creator profile, a
 * community feed or the marketplace itself — the mission's reusability
 * requirement is enforced by the absence of an import rather than by a comment
 * promising it. It is also what the marketplace component set already
 * guarantees, in as many words in `components/marketplace/index.ts`.
 *
 * ## Four tap targets, not one
 *
 * The card, the seller row, the primary CTA and each action-rail button are
 * separate targets because they mean different things, and nesting them would
 * make "look at this product" a way to accidentally open a stranger's store.
 * The card carries the product's full announcement — name, price, seller,
 * availability — and the nested controls carry their own labels and are
 * excluded from it, so a screen reader hears each fact once.
 *
 * ## What is not drawn
 *
 * There is no star rating and no review count, and the eyebrow line above the
 * product name never says "Trending". `ProductSignal` types all three as
 * nullable and the adapter sets them null, because no aggregate exists to fill
 * them — see that module for the full reasoning. The rating row here is written
 * to appear the moment the field is non-null and to be absent until then, so
 * the backend work is the only work left; nothing here has to be un-faked.
 *
 * FEATURED is different from missing data: it is real, it is a paid placement,
 * and it is disclosed as "Sponsored" to a screen reader in the same words
 * `SponsoredAdCard` and `ItemGridCard` already use, with a visible Promoted
 * eyebrow so the disclosure is not carried by assistive tech alone.
 */
import { memo } from "react";
import { Image, Pressable, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { useTranslation } from "../i18n";
import { colors } from "../theme/colors";
import { logiNexus } from "../theme/logiNexus";
import { createThemedStyles } from "../theme/themedStyles";
import { ContentCover } from "./covers/ContentCover";
import type { ProductSignal } from "../commerce/productSignal";

export type ProductSignalCardProps = {
  signal: ProductSignal;
  /** Open the product detail experience. */
  onOpenProduct: (signal: ProductSignal) => void;
  /** Open the seller's storefront or profile. Absent = the row is not a target. */
  onOpenSeller?: (signal: ProductSignal) => void;
  /** Open the full Marketplace. Absent = the shop action is not rendered. */
  onOpenMarketplace?: (signal: ProductSignal) => void;
  onLike?: (signal: ProductSignal) => void;
  onComment?: (signal: ProductSignal) => void;
  onShare?: (signal: ProductSignal) => void;
  liked?: boolean;
  testID?: string;
};

function ProductSignalCardBody({
  signal,
  onOpenProduct,
  onOpenSeller,
  onOpenMarketplace,
  onLike,
  onComment,
  onShare,
  liked = false,
  testID
}: ProductSignalCardProps) {
  const { t } = useTranslation();
  const sponsored = signal.context.kind === "featured";

  const contextLine =
    signal.context.kind === "featured"
      ? t("commerce:productSignal.contextPromoted")
      : signal.context.kind === "new"
        ? signal.context.category
          ? t("commerce:productSignal.contextNewIn", { category: signal.context.category })
          : t("commerce:productSignal.contextNew")
        : signal.context.kind === "category"
          ? t("commerce:productSignal.contextIn", { category: signal.context.category })
          : "";

  /**
   * One announcement for the whole card, so a screen reader hears the product
   * once rather than hearing four fragments and having to assemble them. The
   * price is included because a product card whose price is only visible is a
   * card that asks a blind user to open a checkout to find out what it costs.
   */
  const cardLabel = [
    signal.productName,
    signal.priceLabel,
    t("commerce:marketplace.sellerLine", { name: signal.sellerName }),
    sponsored ? t("commerce:productSignal.sponsored") : ""
  ]
    .filter(Boolean)
    .join(". ");

  return (
    <Pressable
      testID={testID}
      accessibilityRole="button"
      accessibilityLabel={cardLabel}
      accessibilityHint={t("commerce:productSignal.openHint")}
      style={({ pressed }) => [pressed && styles.cardPressed]}
      onPress={() => onOpenProduct(signal)}
    >
      <View style={styles.card}>
        <View style={styles.cardInset}>
          <Pressable
            testID="product-signal-seller"
            accessibilityRole="button"
            accessibilityLabel={t("commerce:productSignal.openSeller", { name: signal.sellerName })}
            style={styles.sellerRow}
            disabled={!onOpenSeller}
            onPress={(event) => {
              event.stopPropagation();
              onOpenSeller?.(signal);
            }}
          >
            {signal.sellerAvatarUrl ? (
              <Image source={{ uri: signal.sellerAvatarUrl }} style={styles.avatar} />
            ) : (
              <View style={styles.avatarFallback}>
                <Text style={styles.avatarInitial}>{signal.sellerInitial}</Text>
              </View>
            )}
            <View style={styles.sellerText}>
              <View style={styles.sellerNameRow}>
                <Text style={styles.sellerName} numberOfLines={1}>
                  {signal.sellerName}
                </Text>
                {/*
                  Renders only when the field is true. It is null on every
                  payload today because nothing states seller verification, and
                  `PostCard` keeps the checkmark for verified identity alone —
                  handing it to any seller would make "has a storefront" look
                  like "we checked who this is".
                */}
                {signal.sellerVerified ? (
                  <Ionicons name="checkmark-circle" size={15} color={colors.accent} />
                ) : null}
                <View style={styles.storeTag}>
                  <Ionicons name="storefront" size={9} color={colors.accentStrong} />
                  <Text style={styles.storeTagText}>{t("commerce:productSignal.storeTag")}</Text>
                </View>
              </View>
              <Text style={styles.sellerMeta} numberOfLines={1}>
                {signal.sellerHandle ? `@${signal.sellerHandle}` : t("commerce:marketplace.seller")}
              </Text>
            </View>
          </Pressable>

          {signal.description ? (
            <Text style={styles.description} numberOfLines={3}>
              {signal.description}
            </Text>
          ) : null}
        </View>

        <View style={styles.mediaWrap}>
          <ContentCover
            kind="listing"
            imageUrl={signal.mediaUrl}
            title={signal.productName}
            subtitle={signal.sellerName}
            category={signal.category ?? undefined}
            style={styles.media}
            testID="product-signal-media"
          />
        </View>

        <View style={styles.cardInset}>
          <View style={styles.productBlock}>
            {contextLine ? (
              <Text style={[styles.context, sponsored && styles.contextSponsored]} numberOfLines={1}>
                {contextLine}
              </Text>
            ) : null}
            <Text style={styles.productName} numberOfLines={2}>
              {signal.productName}
            </Text>
            {/*
              Absent, not zeroed. When the per-seller review aggregate lands the
              adapter fills `rating`/`reviewCount` in and this row starts
              drawing; until then there is nothing truthful to say.
            */}
            {signal.rating != null ? (
              <View style={styles.ratingRow}>
                <Ionicons name="star" size={12} color={colors.warning} />
                <Text style={styles.ratingText}>{signal.rating.toFixed(1)}</Text>
                {signal.reviewCount != null ? (
                  <Text style={styles.reviewCount}>
                    {t("commerce:productSignal.reviewCount", { count: signal.reviewCount })}
                  </Text>
                ) : null}
              </View>
            ) : null}
            <View style={styles.priceRow}>
              {signal.priceLabel ? <Text style={styles.price}>{signal.priceLabel}</Text> : null}
              {signal.fulfillment === "local" ? (
                <Text style={styles.fulfillment}>{t("commerce:productSignal.localPickup")}</Text>
              ) : signal.fulfillment === "platform" || signal.fulfillment === "both" ? (
                <Text style={styles.fulfillment}>{t("commerce:marketplace.shipping")}</Text>
              ) : null}
            </View>
            <Pressable
              testID="product-signal-cta"
              accessibilityRole="button"
              accessibilityLabel={t("commerce:productSignal.viewProductOf", {
                name: signal.productName
              })}
              style={({ pressed }) => [styles.cta, pressed && styles.ctaPressed]}
              onPress={(event) => {
                event.stopPropagation();
                onOpenProduct(signal);
              }}
            >
              <Ionicons name="bag-handle-outline" size={15} color={logiNexus.colors.home.backgroundDeepSpace} />
              <Text style={styles.ctaText}>{t("commerce:productSignal.viewProduct")}</Text>
            </Pressable>
          </View>

          <View style={styles.actionRow}>
            <Pressable
              testID="product-signal-like"
              accessibilityRole="button"
              accessibilityState={{ selected: liked }}
              accessibilityLabel={
                liked
                  ? t("commerce:productSignal.liked")
                  : t("commerce:productSignal.like")
              }
              style={({ pressed }) => [styles.actionButton, pressed && styles.actionButtonPressed]}
              disabled={!onLike}
              onPress={(event) => {
                event.stopPropagation();
                onLike?.(signal);
              }}
            >
              <Text style={[styles.actionIcon, liked && styles.actionIconActive]}>♥</Text>
              <Text style={[styles.actionText, liked && styles.actionTextActive]}>
                {t("commerce:productSignal.like")}
              </Text>
            </Pressable>
            <Pressable
              testID="product-signal-comment"
              accessibilityRole="button"
              accessibilityLabel={t("commerce:productSignal.comment")}
              style={({ pressed }) => [styles.actionButton, pressed && styles.actionButtonPressed]}
              disabled={!onComment}
              onPress={(event) => {
                event.stopPropagation();
                onComment?.(signal);
              }}
            >
              <Text style={styles.actionIcon}>◯</Text>
              <Text style={styles.actionText}>{t("commerce:productSignal.comment")}</Text>
            </Pressable>
            <Pressable
              testID="product-signal-share"
              accessibilityRole="button"
              accessibilityLabel={t("commerce:productSignal.share")}
              style={({ pressed }) => [styles.actionButton, pressed && styles.actionButtonPressed]}
              disabled={!onShare}
              onPress={(event) => {
                event.stopPropagation();
                onShare?.(signal);
              }}
            >
              <Text style={styles.actionIcon}>↗</Text>
              <Text style={styles.actionText}>{t("commerce:productSignal.share")}</Text>
            </Pressable>
            {onOpenMarketplace ? (
              <Pressable
                testID="product-signal-shop"
                accessibilityRole="button"
                accessibilityLabel={t("commerce:productSignal.openMarketplace")}
                style={({ pressed }) => [styles.actionButton, pressed && styles.actionButtonPressed]}
                onPress={(event) => {
                  event.stopPropagation();
                  onOpenMarketplace(signal);
                }}
              >
                <Ionicons name="storefront-outline" size={15} color={colors.accentStrong} />
                <Text style={[styles.actionText, styles.actionTextShop]}>
                  {t("commerce:productSignal.shop")}
                </Text>
              </Pressable>
            ) : null}
          </View>
        </View>
      </View>
    </Pressable>
  );
}

export const ProductSignalCard = memo(ProductSignalCardBody);

const styles = createThemedStyles(() => ({
  actionButton: {
    alignItems: "center",
    borderRadius: logiNexus.radius.medium,
    flex: 1,
    flexDirection: "row",
    gap: 6,
    justifyContent: "center",
    minHeight: 38,
    paddingVertical: 9
  },
  actionButtonPressed: {
    backgroundColor: "rgba(255, 255, 255, 0.05)"
  },
  actionIcon: {
    color: colors.muted,
    fontSize: 15,
    fontWeight: "900",
    lineHeight: 18
  },
  actionIconActive: {
    color: colors.danger
  },
  actionRow: {
    alignItems: "center",
    borderTopColor: logiNexus.colors.home.borderSubtle,
    borderTopWidth: 1,
    flexDirection: "row",
    marginTop: 10,
    paddingTop: 4
  },
  actionText: {
    color: colors.muted,
    fontSize: 11,
    fontWeight: "800"
  },
  actionTextActive: {
    color: colors.accent
  },
  actionTextShop: {
    color: colors.accentStrong
  },
  avatar: {
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.accentStrong,
    borderRadius: 24,
    borderWidth: 2,
    height: 48,
    width: 48
  },
  avatarFallback: {
    alignItems: "center",
    backgroundColor: colors.surfaceRaised,
    borderColor: colors.accentStrong,
    borderRadius: 24,
    borderWidth: 2,
    height: 48,
    justifyContent: "center",
    width: 48
  },
  avatarInitial: {
    ...logiNexus.typography.home.cardAuthor,
    color: colors.accentStrong
  },
  card: {
    borderBottomColor: logiNexus.colors.home.borderSubtle,
    borderBottomWidth: 1,
    paddingBottom: 14,
    paddingTop: 14
  },
  cardInset: {
    paddingHorizontal: 16
  },
  cardPressed: {
    backgroundColor: "rgba(255, 255, 255, 0.03)"
  },
  context: {
    ...logiNexus.typography.home.badge,
    color: colors.accentStrong,
    letterSpacing: 0.6,
    textTransform: "uppercase"
  },
  contextSponsored: {
    color: colors.warning
  },
  cta: {
    alignItems: "center",
    backgroundColor: colors.accent,
    borderRadius: logiNexus.radius.capsule,
    flexDirection: "row",
    gap: 7,
    justifyContent: "center",
    marginTop: 12,
    minHeight: 44,
    paddingHorizontal: 18
  },
  ctaPressed: {
    opacity: 0.82
  },
  ctaText: {
    ...logiNexus.typography.home.buttonPrimary,
    color: logiNexus.colors.home.backgroundDeepSpace
  },
  description: {
    ...logiNexus.typography.home.cardBody,
    color: colors.text,
    marginTop: 10
  },
  fulfillment: {
    ...logiNexus.typography.home.cardMetadata,
    color: colors.accent
  },
  media: {
    width: "100%"
  },
  mediaWrap: {
    aspectRatio: 1,
    backgroundColor: colors.surfaceRaised,
    marginTop: 12,
    overflow: "hidden",
    width: "100%"
  },
  price: {
    ...logiNexus.typography.home.heroSupporting,
    color: colors.text,
    fontSize: 20,
    fontWeight: "900",
    lineHeight: 25
  },
  priceRow: {
    alignItems: "center",
    flexDirection: "row",
    flexWrap: "wrap",
    gap: 10,
    marginTop: 6
  },
  productBlock: {
    borderTopColor: logiNexus.colors.home.borderSubtle,
    borderTopWidth: 1,
    marginTop: 12,
    paddingTop: 12
  },
  productName: {
    ...logiNexus.typography.home.cardAuthor,
    color: colors.text,
    marginTop: 4
  },
  ratingRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 5,
    marginTop: 5
  },
  ratingText: {
    ...logiNexus.typography.home.cardMetric,
    color: colors.text
  },
  reviewCount: {
    ...logiNexus.typography.home.cardMetadata,
    color: colors.muted
  },
  sellerMeta: {
    ...logiNexus.typography.home.cardMetadata,
    color: colors.muted,
    marginTop: 2
  },
  sellerName: {
    ...logiNexus.typography.home.cardAuthor,
    color: colors.text,
    flexShrink: 1
  },
  sellerNameRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 7
  },
  sellerRow: {
    alignItems: "center",
    flexDirection: "row",
    gap: 9,
    minWidth: 0
  },
  sellerText: {
    flex: 1
  },
  storeTag: {
    alignItems: "center",
    backgroundColor: colors.signalSoft,
    borderColor: logiNexus.colors.home.borderSubtle,
    borderRadius: logiNexus.radius.capsule,
    borderWidth: 1,
    flexDirection: "row",
    gap: 3,
    paddingHorizontal: 6,
    paddingVertical: 2
  },
  storeTagText: {
    color: colors.accentStrong,
    fontSize: 8,
    fontWeight: "900",
    letterSpacing: 0.5
  }
}));
