/**
 * One offer waiting on the seller.
 *
 * Reading order, top to bottom: who made it and how long ago, what it is on and
 * what that item lists for, how much they offered, and the three things you can
 * do about it. The offer amount is the largest number on the card because it is
 * the thing being decided; the list price sits beside it small so the comparison
 * is available without being the headline.
 *
 * ## The three buttons go down together
 *
 * Accept, Counter and Decline all disable the instant any one of them is
 * pressed, driven by `offerActionsDisabled` rather than by three local booleans.
 * The alternative — disabling only the pressed button — leaves Accept and
 * Decline racing each other across a network hop, and that race has money on the
 * end of it. Inline progress replaces the label of the pressed button only, so
 * it stays obvious which decision is in flight.
 *
 * ## Freshness
 *
 * Under thirty minutes an offer gets the green left edge, a pinging dot, and an
 * occasional shimmer. All three are ambience: the relative time is written out
 * in text beside them, so nothing about "this is new" depends on seeing motion
 * or colour.
 *
 * ## Formatting
 *
 * Every string this component renders arrives formatted. It takes `amountText`,
 * not `amountMinor`, and `ageText`, not a timestamp — currency and relative time
 * are locale work that belongs in the screen with the existing utilities, not
 * duplicated in a card that would then carry its own locale assumptions.
 */

import { Animated, Image, Pressable, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import { storeLight } from "../../theme/storeLight";
import { marketplaceLight } from "../../theme/marketplaceLight";
import { useStoreAmbient, useStorePress } from "../../theme/storeMotion";
import { MARKETPLACE_AMBIENT } from "../../theme/marketplaceMotion";
import { offerActionsDisabled, type MarketplaceOffer, type OfferAction } from "../../api/marketplaceOffers";

export type OfferCardProps = {
  offer: MarketplaceOffer;
  /** True while the offer is under the freshness window. Drives the accents. */
  fresh: boolean;
  /** Formatted offer amount, e.g. "$95.00". */
  amountText: string;
  /** Formatted list price, e.g. "$120.00". */
  listPriceText: string;
  /** Formatted relative time, e.g. "12 min ago". */
  ageText: string;
  /** Accept, with the amount already in it: "Accept $95.00". */
  acceptLabel: string;
  onAccept: () => void;
  onCounter: () => void;
  onDecline: () => void;
  /** Opens the item. */
  onPressItem: () => void;
  reducedMotion: boolean;
};

const PROGRESS_LABEL: Record<OfferAction, string> = {
  accept: "Accepting…",
  counter: "Opening…",
  decline: "Declining…",
  withdraw: "Withdrawing…"
};

export function OfferCard({
  offer,
  fresh,
  amountText,
  listPriceText,
  ageText,
  acceptLabel,
  onAccept,
  onCounter,
  onDecline,
  onPressItem,
  reducedMotion
}: OfferCardProps) {
  const { fontScale } = useWindowDimensions();
  const disabled = offerActionsDisabled(offer);
  const pending = offer.pending ?? null;

  const ping = useStoreAmbient(MARKETPLACE_AMBIENT.offerPing, reducedMotion, {
    enabled: fresh,
    resetTo: 0
  });
  const shimmer = useStoreAmbient(MARKETPLACE_AMBIENT.offerShimmer, reducedMotion, {
    enabled: fresh,
    resetTo: 0
  });
  const acceptPress = useStorePress(reducedMotion, 0.97);

  // At large font scales the three buttons cannot share a row without each
  // becoming a two-character stub, so they stack. Measuring the scale rather
  // than the width because it is the text that grows, not the card.
  const stackActions = fontScale > 1.2;

  return (
    <View
      style={[styles.card, fresh && styles.cardFresh]}
      accessibilityLabel={[
        `Offer from ${offer.buyerName}`,
        offer.itemTitle,
        `listed at ${listPriceText}`,
        `offered ${amountText}`,
        ageText
      ].join(", ")}
    >
      {fresh ? (
        <Animated.View
          pointerEvents="none"
          style={[
            styles.shimmer,
            {
              opacity: shimmer.interpolate({
                inputRange: [0, 0.86, 0.92, 0.98, 1],
                outputRange: [0, 0, 0.12, 0, 0]
              }),
              transform: [
                {
                  translateX: shimmer.interpolate({
                    inputRange: [0, 0.86, 1],
                    outputRange: [-80, -80, 420]
                  })
                },
                { rotate: "16deg" }
              ]
            }
          ]}
        />
      ) : null}

      <Pressable
        style={styles.head}
        onPress={onPressItem}
        accessibilityRole="button"
        accessibilityLabel={`Open ${offer.itemTitle}`}
      >
        {offer.buyerAvatarUrl ? (
          <Image source={{ uri: offer.buyerAvatarUrl }} style={styles.avatar} />
        ) : (
          <View style={[styles.avatar, styles.avatarEmpty]}>
            <Text style={styles.avatarText}>{offer.buyerName.slice(0, 1).toUpperCase()}</Text>
          </View>
        )}

        <View style={styles.headText}>
          <View style={styles.nameRow}>
            <Text style={styles.name} numberOfLines={1}>
              {offer.buyerName}
            </Text>
            {fresh ? (
              <Animated.View
                style={[
                  styles.dot,
                  {
                    opacity: ping.interpolate({
                      inputRange: [0, 0.5, 1],
                      outputRange: [0.35, 1, 0.35]
                    }),
                    transform: [
                      {
                        scale: ping.interpolate({
                          inputRange: [0, 0.5, 1],
                          outputRange: [0.8, 1.25, 0.8]
                        })
                      }
                    ]
                  }
                ]}
                accessibilityElementsHidden
                importantForAccessibility="no"
              />
            ) : null}
            <Text style={styles.age}>{ageText}</Text>
          </View>
          <Text style={styles.item} numberOfLines={2}>
            {offer.itemTitle}
          </Text>
          <Text style={styles.listed}>Listed {listPriceText}</Text>
        </View>

        {offer.itemThumbnailUrl ? (
          <Image source={{ uri: offer.itemThumbnailUrl }} style={styles.thumb} />
        ) : null}
      </Pressable>

      <Text style={styles.amount} accessibilityElementsHidden importantForAccessibility="no">
        {amountText}
      </Text>

      <View style={[styles.actions, stackActions && styles.actionsStacked]}>
        <Animated.View style={[styles.acceptWrap, acceptPress.style]}>
          <Pressable
            style={[styles.accept, disabled && styles.buttonDisabled]}
            onPress={disabled ? undefined : onAccept}
            onPressIn={disabled ? undefined : acceptPress.onPressIn}
            onPressOut={disabled ? undefined : acceptPress.onPressOut}
            disabled={disabled}
            accessibilityRole="button"
            // The amount is inside the label deliberately: "Accept" alone,
            // announced out of context, does not say what is being agreed to.
            accessibilityLabel={acceptLabel}
            accessibilityState={{ disabled, busy: pending === "accept" }}
          >
            <Text style={styles.acceptText} numberOfLines={1}>
              {pending === "accept" ? PROGRESS_LABEL.accept : acceptLabel}
            </Text>
          </Pressable>
        </Animated.View>

        <Pressable
          style={[styles.counter, disabled && styles.buttonDisabled]}
          onPress={disabled ? undefined : onCounter}
          disabled={disabled}
          accessibilityRole="button"
          accessibilityLabel={`Counter the offer from ${offer.buyerName}`}
          accessibilityHint="Opens a sheet to name your own price"
          accessibilityState={{ disabled, busy: pending === "counter" }}
        >
          <Text style={styles.counterText}>
            {pending === "counter" ? PROGRESS_LABEL.counter : "Counter"}
          </Text>
        </Pressable>

        <Pressable
          style={[styles.decline, disabled && styles.buttonDisabled]}
          onPress={disabled ? undefined : onDecline}
          disabled={disabled}
          accessibilityRole="button"
          accessibilityLabel={`Decline the offer of ${amountText} from ${offer.buyerName}`}
          accessibilityState={{ disabled, busy: pending === "decline" }}
        >
          <Text style={styles.declineText}>
            {pending === "decline" ? PROGRESS_LABEL.decline : "Decline"}
          </Text>
        </Pressable>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: storeLight.bg.card,
    borderRadius: storeLight.radius.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    padding: storeLight.space.card,
    gap: 10,
    overflow: "hidden"
  },
  cardFresh: {
    borderColor: marketplaceLight.offer.freshBorder,
    borderLeftWidth: marketplaceLight.offer.freshEdgeWidth,
    borderLeftColor: marketplaceLight.offer.freshEdge
  },
  shimmer: {
    position: "absolute",
    top: -60,
    bottom: -60,
    width: 48,
    backgroundColor: storeLight.status.success
  },
  head: { flexDirection: "row", gap: 10, alignItems: "flex-start" },
  avatar: { width: 36, height: 36, borderRadius: 18, backgroundColor: storeLight.bg.skeleton },
  avatarEmpty: { alignItems: "center", justifyContent: "center" },
  avatarText: { fontSize: 15, fontWeight: "700", color: storeLight.text.muted },
  headText: { flex: 1, gap: 2 },
  nameRow: { flexDirection: "row", alignItems: "center", gap: 6, flexWrap: "wrap" },
  name: { fontSize: 14, fontWeight: "700", color: storeLight.text.primary, flexShrink: 1 },
  dot: { width: 7, height: 7, borderRadius: 4, backgroundColor: storeLight.status.success },
  age: { fontSize: 12, color: storeLight.text.muted },
  item: { fontSize: 13, color: storeLight.text.primary, lineHeight: 18 },
  listed: { fontSize: 12, color: storeLight.text.muted },
  thumb: {
    width: 44,
    height: 44,
    borderRadius: storeLight.radius.thumb,
    backgroundColor: storeLight.bg.skeleton
  },
  amount: { fontSize: 26, fontWeight: "800", color: marketplaceLight.offer.amount },
  actions: { flexDirection: "row", alignItems: "center", gap: 8 },
  actionsStacked: { flexDirection: "column", alignItems: "stretch" },
  acceptWrap: { flex: 1 },
  accept: {
    minHeight: storeLight.size.tapTarget,
    borderRadius: storeLight.radius.control,
    backgroundColor: storeLight.status.success,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 12
  },
  acceptText: { fontSize: 14, fontWeight: "800", color: storeLight.text.onDark },
  counter: {
    minHeight: storeLight.size.tapTarget,
    minWidth: 84,
    borderRadius: storeLight.radius.control,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 12
  },
  counterText: { fontSize: 13, fontWeight: "700", color: storeLight.text.primary },
  decline: {
    minHeight: storeLight.size.tapTarget,
    minWidth: 72,
    alignItems: "center",
    justifyContent: "center",
    paddingHorizontal: 8
  },
  declineText: { fontSize: 13, fontWeight: "600", color: marketplaceLight.offer.decline },
  // Dimming rather than hiding: the row must stay readable while a decision is
  // in flight, because the thing in flight is the decision it describes.
  buttonDisabled: { opacity: 0.55 }
});
