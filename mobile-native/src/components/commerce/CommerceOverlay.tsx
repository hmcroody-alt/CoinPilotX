/**
 * The shoppable half of every PulseDrop publication, on both surfaces.
 *
 * ## Why this exists at all
 *
 * `services/pulsedrop/reel_composer.py` renders a 9:16 video with no title, no
 * price, no badge and no call to action burnt into it. That is not an omission;
 * it is the point. A Reel published in March is still in the feed in September,
 * and pixels cannot be re-priced. Everything perishable is rendered here, live,
 * over the video, from `post.commerce` -- which the server re-reads from
 * `marketplace_listings` on every single serialization.
 *
 * The same argument holds, less dramatically, for a PulseDrop Signal: its image
 * is the seller's product photo and its body text is written once, so the price,
 * the stock state and the call to action still have to come from somewhere
 * current. They come from here, from the identical payload.
 *
 * So this component is the only thing that makes a PulseDrop publication
 * shoppable. If it does not render, a Reel is a silent product video and a
 * Signal is an ordinary post about something you cannot buy.
 *
 * ## One component, two surfaces
 *
 * `surface` selects the presentation, and *only* the presentation. What renders,
 * when it renders, what a screen reader hears and which taps are live are
 * identical on both, because they are the same commercial rules over the same
 * payload -- and a second component would be a second place for those rules to
 * drift. The differences are honestly cosmetic:
 *
 *  - `reel` draws over video: translucent dark fill, capped at 78% width so the
 *    action rail on the right keeps its full touch targets.
 *  - `signal` draws on the post card's own opaque surface: full width, a hairline
 *    border rather than a scrim, and a slightly larger thumbnail because it is
 *    not competing with moving pixels behind it.
 *
 * The default comes from `commerce.surface`, which the server already sets to
 * the format it published, so a caller that omits the prop still gets the right
 * treatment rather than an arbitrary one.
 *
 * ## Where it sits
 *
 * On a Reel: inside `ReelPlayerCard`'s caption block, above the caption, which
 * puts it clear of three things it must never collide with: the action rail on
 * the right (`bottom: contentBottom + 56`), the bottom navigator (`contentBottom`
 * is measured from where the dock *lives*, not where it currently is), and the
 * home indicator. It needs no safe-area maths of its own precisely because it
 * is a child of the block that already did it. Rendering it as a sibling with
 * `position: absolute` would re-derive those offsets and drift from them.
 *
 * On a Signal: the first child of `PostCard`'s `cardInset`, between the media
 * and the social-context row -- inside the text column, so it inherits that
 * column's padding instead of fighting the full-bleed media above it.
 *
 * ## Why the card is a card and not a bar
 *
 * The brief asks for restraint, and a full-width bar across a 9:16 video eats
 * the subject. On the Reel this is a left-aligned block capped at 78% width so
 * the action rail's touch targets stay unobstructed. On both surfaces the price
 * carries the largest weight in the group -- it is the fact the viewer is
 * deciding on, and the secondary metadata is deliberately quieter than it.
 *
 * ## What it refuses to do
 *
 * It never says "Buy Now", "Only 2 left", "Deal" or anything else implying
 * urgency or a discount. The tap opens a product page; it cannot take money. It
 * never renders a price the overlay's own state forbids (see
 * `commercePriceVisible`). And it never renders an enabled button without a
 * route, because `/pulse/marketplace/<id>` 404s for withdrawn listings and a
 * button to a 404 is worse than no button.
 */

import { memo, useMemo } from "react";
import { AccessibilityInfo, Image, Pressable, StyleSheet, Text, View } from "react-native";
import type { PulseCommerceOverlay } from "../../api/pulseCommerceOverlay";
import {
  commerceBlock,
  commerceCtaEnabled,
  commercePriceVisible,
} from "../../api/pulseCommerceOverlay";
import { useTranslation } from "../../i18n/I18nContext";
import { colors } from "../../theme/colors";

/**
 * Which presentation to draw. Not which rules to apply -- there is one set of
 * those and both surfaces get it.
 */
export type CommerceOverlaySurface = "reel" | "signal";

export type CommerceOverlayProps = {
  commerce: PulseCommerceOverlay;
  /**
   * Defaults to the surface the server published to. Pass it explicitly only
   * when the host disagrees with the payload -- a Reel replayed inside a feed
   * card, say, where the *container* decides the treatment, not the origin.
   */
  surface?: CommerceOverlaySurface;
  /**
   * Open the product. The caller owns navigation because this component has no
   * business knowing whether it is inside a stack, a modal or a deep link.
   */
  onOpenProduct: (commerce: PulseCommerceOverlay) => void;
  /** Open the merchant's store. A distinct destination from the product. */
  onOpenSeller: (commerce: PulseCommerceOverlay) => void;
  /**
   * Suppress the entry animation. Wired from `AccessibilityInfo`'s reduce-motion
   * setting by the caller; there is no motion in this build, but the prop is
   * part of the contract so adding a reveal later cannot skip the check.
   */
  reduceMotion?: boolean;
  compact?: boolean;
};

function CommerceOverlayComponent({
  commerce,
  surface,
  onOpenProduct,
  onOpenSeller,
  compact = false,
}: CommerceOverlayProps) {
  const { t } = useTranslation();
  // The payload's own `surface` is the default, not a fallback of last resort:
  // the server set it to the format it actually published, so it is the more
  // reliable of the two answers whenever the caller has not deliberately
  // overridden it. Anything unrecognised reads as a Signal, which is the
  // treatment that survives being placed anywhere -- the Reel variant assumes
  // dark video behind it and is unreadable on a light surface.
  const variant: CommerceOverlaySurface = surface || (commerce.surface === "reel" ? "reel" : "signal");
  const isReel = variant === "reel";
  const product = commerce.product;
  const block = commerceBlock(commerce);
  const ctaEnabled = commerceCtaEnabled(commerce);

  // Price and state are read through the same helpers the server used, rather
  // than from the presence of a string, so a price that arrives when it should
  // not is still not shown. `price_label` empty is also honoured: a restored
  // cache entry has been stripped of it on purpose (`commerceOverlayForCache`).
  const showPrice = commercePriceVisible(commerce) && Boolean(product.price_label);
  const showState = block !== "";

  const labelText = commerce.label?.i18n_key
    ? t(commerce.label.i18n_key, { defaultValue: commerce.label.fallback || "" })
    : commerce.label?.fallback || "";
  const stateText = commerce.availability?.i18n_key
    ? t(commerce.availability.i18n_key, { defaultValue: commerce.availability.fallback || "" })
    : commerce.availability?.fallback || "";
  const ctaText = commerce.cta?.i18n_key
    ? t(commerce.cta.i18n_key, { defaultValue: commerce.cta.fallback || "" })
    : commerce.cta?.fallback || "";

  /**
   * One sentence, assembled by the server, read as a single unit.
   *
   * The visual block is five separate Texts; a screen reader walking them
   * announces "Trending", "Aurora Desk Lamp", "$49.00", "Northlight Studio",
   * "View product" as five unrelated fragments. Grouping them and supplying the
   * server's sentence makes the announcement match what a sighted user takes in
   * at a glance -- and, critically, it carries the same disclosure rule: for a
   * withdrawn product the server's sentence is the state alone, with no price
   * in it, so the accessible surface cannot leak what the visual one hides.
   */
  const accessibilityLabel = commerce.accessibility_text || [labelText, product.title].filter(Boolean).join(". ");

  const storeName = commerce.seller?.store_name || commerce.seller?.username || "";

  const rootStyle = useMemo(
    () => [styles.root, isReel ? null : styles.rootSignal, compact && isReel ? styles.rootCompact : null],
    [compact, isReel]
  );

  return (
    <View style={rootStyle}>
      {/*
        The whole card is one accessibility element, not six. A reader walking
        the individual Texts announces "Trending", "Aurora Desk Lamp", "$49.00",
        "Northlight Studio", "View product" as five unrelated fragments; grouped,
        it announces the sentence a sighted user takes in at a glance.

        Grouping is done here rather than on the wrapper View so the group is
        still the *button* -- hiding descendants behind a non-interactive parent
        was the first version of this and it made the CTA unreachable by
        VoiceOver, which is a worse outcome than fragmented reading.
      */}
      <Pressable
        style={({ pressed }) => [
          styles.card,
          isReel ? null : styles.cardSignal,
          pressed && ctaEnabled ? (isReel ? styles.cardPressed : styles.cardSignalPressed) : null,
        ]}
        accessible
        // Not a button when there is nowhere to go. An unroutable state keeps
        // the card as static information rather than offering a tap that
        // silently does nothing, which reads as a broken screen.
        accessibilityRole={ctaEnabled ? "button" : "text"}
        accessibilityLabel={accessibilityLabel}
        accessibilityHint={ctaEnabled && ctaText ? ctaText : undefined}
        accessibilityState={{ disabled: !ctaEnabled }}
        disabled={!ctaEnabled}
        // Both surfaces nest this inside a larger tap target -- the post card's
        // open-post Pressable, the Reel's tap-to-mute. The product is a
        // different destination from either, so the press stops here. Optional
        // chaining because a synthetic press (tests, accessibility activation)
        // arrives without an event.
        onPress={(event) => {
          event?.stopPropagation?.();
          onOpenProduct(commerce);
        }}
      >
        {product.image_url ? (
          <Image
            source={{ uri: product.image_url }}
            style={[styles.thumb, isReel ? null : styles.thumbSignal]}
            // `cover` and a fixed square: the product photo is cropped to the
            // centre rather than squashed. A stretched product is the one thing
            // that makes a storefront look fake.
            resizeMode="cover"
          />
        ) : (
          <View style={[styles.thumb, isReel ? null : styles.thumbSignal, styles.thumbEmpty]} />
        )}

        <View style={styles.copy}>
          <View style={styles.chipRow}>
            {labelText ? (
              <View style={styles.labelChip}>
                <Text style={styles.labelChipText} numberOfLines={1}>
                  {labelText}
                </Text>
              </View>
            ) : null}
            {showState ? (
              <View style={[styles.stateChip, block === "REMOVED" || block === "UNAVAILABLE" ? styles.stateChipGone : null]}>
                <Text style={styles.stateChipText} numberOfLines={1}>
                  {stateText}
                </Text>
              </View>
            ) : null}
          </View>

          {product.title ? (
            // Two lines, then ellipsis. Marketplace titles are seller-written and
            // run long; truncating mid-word with no marker reads as a bug.
            <Text style={[styles.title, isReel ? null : styles.titleSignal]} numberOfLines={2}>
              {product.title}
            </Text>
          ) : null}

          <View style={styles.metaRow}>
            {showPrice ? (
              // Heavier and larger than everything beside it, and never
              // re-formatted here: the server formatted it in the listing's own
              // currency, and a client-side `toFixed` would render ¥4900 as
              // $49.00 for a Japanese seller.
              <Text style={[styles.price, isReel ? null : styles.priceSignal]} numberOfLines={1}>
                {product.price_label}
              </Text>
            ) : null}
            {storeName ? (
              <Text style={styles.store} numberOfLines={1}>
                {storeName}
              </Text>
            ) : null}
          </View>
        </View>

        {ctaEnabled && ctaText ? (
          <View style={[styles.cta, isReel ? null : styles.ctaSignal]}>
            <Text style={[styles.ctaText, isReel ? null : styles.ctaTextSignal]} numberOfLines={1}>
              {ctaText}
            </Text>
          </View>
        ) : null}
      </Pressable>

      {/*
        The merchant is a separate tap target from the product, because they are
        separate destinations and PulseDrop is neither of them. Collapsing the
        two would make the store name decorative and, worse, would imply the
        publisher and the merchant are the same account.
      */}
      {storeName && commerce.seller?.route ? (
        <Pressable
          accessibilityRole="link"
          accessibilityLabel={t("commerce:pulsedrop.seller.openSeller", {
            defaultValue: `Open the ${storeName} store`,
            name: storeName,
          })}
          style={styles.sellerLink}
          hitSlop={8}
          onPress={(event) => {
            event?.stopPropagation?.();
            onOpenSeller(commerce);
          }}
        >
          <Text style={styles.sellerLinkText} numberOfLines={1}>
            {t("commerce:pulsedrop.seller.visitStore", { defaultValue: "Visit store" })}
          </Text>
        </Pressable>
      ) : null}
    </View>
  );
}

/**
 * Memoized on the overlay object identity.
 *
 * A Reels feed re-renders on every scroll frame, mute toggle and progress tick,
 * and a Home feed re-renders on every viewability change and badge poll. The
 * overlay object is replaced only when the post is re-fetched, so identity is
 * exactly the right comparison: it re-renders when the price could have changed
 * and not once in between.
 */
export const CommerceOverlay = memo(CommerceOverlayComponent);

/** Announce a state change to a screen reader without moving focus. */
export function announceCommerceState(text: string) {
  if (!text) return;
  AccessibilityInfo.announceForAccessibility(text);
}

const styles = StyleSheet.create({
  root: {
    // Capped so the action rail on the right keeps its full touch targets. A
    // full-width block here is what makes Like unreachable on a small phone.
    maxWidth: "78%",
    marginBottom: 10,
  },
  rootCompact: { maxWidth: "88%", marginBottom: 6 },
  // Nothing overlaps a post card, so the cap comes off. The margins put it
  // between the media above and the reaction summary below without touching
  // either; `PostCard`'s inset supplies the horizontal padding.
  rootSignal: { maxWidth: "100%", marginTop: 10, marginBottom: 4 },
  card: {
    alignItems: "center",
    backgroundColor: "rgba(6, 14, 20, 0.78)",
    borderColor: "rgba(50, 230, 179, 0.34)",
    borderRadius: 16,
    borderWidth: 1,
    flexDirection: "row",
    gap: 10,
    paddingHorizontal: 10,
    paddingVertical: 9,
  },
  cardPressed: { backgroundColor: "rgba(9, 22, 30, 0.92)" },
  // On a post card there is no video to scrim, so the translucent fill becomes a
  // muddy patch over the card colour. A near-flat raised surface with a hairline
  // accent border reads as part of the card instead of stuck on top of it.
  cardSignal: {
    backgroundColor: colors.surfaceRaised,
    borderColor: "rgba(50, 230, 179, 0.24)",
    paddingHorizontal: 11,
    paddingVertical: 11,
  },
  cardSignalPressed: { backgroundColor: "#162836" },
  thumb: { backgroundColor: "rgba(255,255,255,0.06)", borderRadius: 11, height: 46, width: 46 },
  thumbSignal: { borderRadius: 12, height: 54, width: 54 },
  thumbEmpty: { borderColor: "rgba(255,255,255,0.10)", borderWidth: 1 },
  copy: { flexShrink: 1, gap: 3 },
  chipRow: { alignItems: "center", flexDirection: "row", gap: 6 },
  labelChip: {
    backgroundColor: "rgba(50, 230, 179, 0.16)",
    borderRadius: 7,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  labelChipText: { color: colors.accent, fontSize: 10, fontWeight: "800", letterSpacing: 0.5 },
  stateChip: {
    backgroundColor: "rgba(243, 196, 97, 0.16)",
    borderRadius: 7,
    paddingHorizontal: 6,
    paddingVertical: 2,
  },
  stateChipGone: { backgroundColor: "rgba(154, 168, 183, 0.18)" },
  stateChipText: { color: colors.text, fontSize: 10, fontWeight: "700" },
  title: { color: colors.text, fontSize: 13, fontWeight: "700", lineHeight: 17 },
  titleSignal: { fontSize: 14, lineHeight: 19 },
  metaRow: { alignItems: "baseline", flexDirection: "row", flexWrap: "wrap", gap: 8 },
  // The price outweighs the store name by size and weight, not by colour alone.
  price: { color: colors.text, fontSize: 15, fontWeight: "900", letterSpacing: 0.2 },
  priceSignal: { fontSize: 16 },
  store: { color: colors.muted, flexShrink: 1, fontSize: 11, fontWeight: "600" },
  cta: {
    backgroundColor: colors.accent,
    borderRadius: 10,
    paddingHorizontal: 10,
    paddingVertical: 7,
  },
  ctaSignal: { paddingHorizontal: 12, paddingVertical: 8 },
  ctaText: { color: "#04120d", fontSize: 11, fontWeight: "900", letterSpacing: 0.3 },
  ctaTextSignal: { fontSize: 12 },
  sellerLink: { paddingTop: 5 },
  sellerLinkText: { color: colors.muted, fontSize: 11, fontWeight: "700" },
});
