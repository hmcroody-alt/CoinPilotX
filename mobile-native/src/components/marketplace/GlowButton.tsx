/**
 * The breathing action button on a buying-grid card.
 *
 * Two variants, and the split is not decorative. "Add to cart" means the
 * platform handles the money; "Make offer" means you are about to talk to a
 * person. Those are different promises, so they get different fills — the cart
 * button takes the shared `STORE_CTA` brand gradient, the offer button the
 * deeper `offerCta` green. (This comment used to describe the cart button as
 * amber, which was the reference design's colour, never the shipped one.) The
 * colour is never the message. The label always states the action in words,
 * which is what the brief means by "glow conveys nothing on its own".
 *
 * ## Why the glow is three layers
 *
 * `Animated` on the native driver can only touch transform and opacity. Making a
 * *fill* brighten would mean animating `backgroundColor` on the JS thread, which
 * is the exact thing that costs frames in a virtualized grid. So the fill is
 * static and a lighter copy of it is faded in and out on top: same perceived
 * effect, opacity only, no bridge traffic. The gleam is a third layer — a narrow
 * white bar swept across on a much longer cycle.
 *
 * ## Why `index` and `visible` are required-feeling props
 *
 * They have defaults, but a grid that omits them gets the two failure modes the
 * brief calls out by name: every card pulsing in unison (which reads as a
 * system alert, not ambience), and forty animation loops running for six
 * visible cards. `index` staggers the phase; `visible` comes from the list's
 * viewability callback and parks the loop when the card scrolls away.
 *
 * ## The "Added ✓" state
 *
 * Confirmation lives in the label, not only in the header badge, because the
 * badge is at the top of the screen and the thumb is at the bottom. The caller
 * owns the timer (`MARKETPLACE_ONCE.addedConfirm`); this component just renders
 * whichever label it is handed and locks the press while confirming, so a second
 * tap during the dwell cannot add a second unit.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { LinearGradient } from "expo-linear-gradient";
import { Ionicons } from "@expo/vector-icons";
import { storeLight } from "../../theme/storeLight";
import { MARKETPLACE_CART_CTA, marketplaceLight } from "../../theme/marketplaceLight";
import { useStorePress } from "../../theme/storeMotion";
import { useMarketplaceGleam, useMarketplaceGlow } from "../../theme/marketplaceMotion";

export type GlowButtonVariant = "cart" | "offer";

export type GlowButtonProps = {
  variant: GlowButtonVariant;
  /** Visible text. Always states the action — never "＋" alone. */
  label: string;
  onPress: () => void;
  /** Announced instead of `label`, so it can name the item and the price. */
  accessibilityLabel: string;
  /** True during the "Added ✓" dwell. Suppresses the glow and locks the press. */
  confirming?: boolean;
  /** True while a request is in flight. Locks the press. */
  busy?: boolean;
  disabled?: boolean;
  /** From the list's viewability callback. False parks the ambient loops. */
  visible?: boolean;
  /** Position in the grid. Drives the glow's phase offset. */
  index?: number;
  reducedMotion: boolean;
};

export function GlowButton({
  variant,
  label,
  onPress,
  accessibilityLabel,
  confirming = false,
  busy = false,
  disabled = false,
  visible = true,
  index = 0,
  reducedMotion
}: GlowButtonProps) {
  const locked = disabled || busy || confirming;
  // The glow stops while confirming or locked: a button that keeps inviting a
  // press it will refuse is a small lie.
  const glow = useMarketplaceGlow(reducedMotion, { visible: visible && !locked, index });
  const gleam = useMarketplaceGleam(reducedMotion, { visible: visible && !locked, index });
  const press = useStorePress(reducedMotion, 0.97);

  const fill = variant === "cart" ? MARKETPLACE_CART_CTA : marketplaceLight.offerCta;
  const textColor = fill.text;

  return (
    <Animated.View style={press.style}>
      <Pressable
        onPress={locked ? undefined : onPress}
        onPressIn={locked ? undefined : press.onPressIn}
        onPressOut={locked ? undefined : press.onPressOut}
        disabled={locked}
        style={styles.pressable}
        accessibilityRole="button"
        accessibilityLabel={accessibilityLabel}
        accessibilityState={{ disabled: locked, busy }}
      >
        <LinearGradient colors={[fill.from, fill.to]} style={styles.fill}>
          {/* Layer 2: the breath. A lighter wash fading 0 → 0.35 → 0, which
              reads as the fill brightening without touching backgroundColor. */}
          <Animated.View
            pointerEvents="none"
            style={[
              styles.wash,
              { opacity: glow.interpolate({ inputRange: [0, 1], outputRange: [0, 0.35] }) }
            ]}
          />
          {/* Layer 3: the gleam. Narrow, fast across, and mostly absent — the
              opacity ramp keeps it invisible for ~80% of its long cycle. */}
          <Animated.View
            pointerEvents="none"
            style={[
              styles.gleam,
              {
                opacity: gleam.interpolate({
                  inputRange: [0, 0.82, 0.88, 0.94, 1],
                  outputRange: [0, 0, 0.5, 0, 0]
                }),
                transform: [
                  {
                    translateX: gleam.interpolate({
                      inputRange: [0, 0.82, 1],
                      outputRange: [-60, -60, 220]
                    })
                  },
                  { rotate: "18deg" }
                ]
              }
            ]}
          />
          <View style={styles.content}>
            {confirming ? (
              <Ionicons name="checkmark" size={15} color={textColor} />
            ) : (
              <Ionicons
                name={variant === "cart" ? "cart-outline" : "pricetag-outline"}
                size={15}
                color={textColor}
              />
            )}
            <Text style={[styles.label, { color: textColor }]} numberOfLines={1}>
              {label}
            </Text>
          </View>
        </LinearGradient>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  pressable: { borderRadius: storeLight.radius.control, overflow: "hidden" },
  fill: {
    minHeight: storeLight.size.tapTarget,
    borderRadius: storeLight.radius.control,
    alignItems: "center",
    justifyContent: "center",
    overflow: "hidden"
  },
  wash: { ...StyleSheet.absoluteFillObject, backgroundColor: "#FFFFFF" },
  gleam: {
    position: "absolute",
    top: -30,
    bottom: -30,
    width: 26,
    backgroundColor: "#FFFFFF"
  },
  content: { flexDirection: "row", alignItems: "center", gap: 6, paddingHorizontal: 10 },
  label: { fontSize: 13, fontWeight: "800", flexShrink: 1 }
});
