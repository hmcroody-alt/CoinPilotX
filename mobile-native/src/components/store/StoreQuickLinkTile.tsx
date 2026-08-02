/**
 * One tile of the "Manage your store" grid.
 *
 * The subtitle is a live status line, not a description — "12 items · 3 low"
 * rather than "Manage your inventory". A tile that restates its own label is a
 * row of wasted pixels; a tile that reports its section's current state saves
 * the seller a tap to find out nothing changed.
 *
 * `disabled` exists because two of the six destinations in the reference design
 * (Shipping settings, Returns policy) have no screen in this app. Rendering
 * them greyed with an honest subtitle is better than either hiding them — which
 * would silently drop half the spec — or wiring them to an unrelated screen,
 * which would be a lie the seller only discovers after tapping.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { storeLight } from "../../theme/storeLight";
import { useStorePress } from "../../theme/storeMotion";

export type StoreQuickLinkTileProps = {
  icon: string;
  label: string;
  /** Live status from real data, or the reason the tile is disabled. */
  subtitle: string;
  onPress?: () => void;
  disabled?: boolean;
  reducedMotion: boolean;
};

export function StoreQuickLinkTile({
  icon,
  label,
  subtitle,
  onPress,
  disabled = false,
  reducedMotion
}: StoreQuickLinkTileProps) {
  const press = useStorePress(reducedMotion, 0.97);

  return (
    <Animated.View style={[styles.wrap, press.style]}>
      <Pressable
        style={[styles.tile, disabled && styles.tileDisabled]}
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        disabled={disabled || !onPress}
        accessibilityRole="button"
        accessibilityState={{ disabled }}
        accessibilityLabel={`${label}. ${subtitle}`}
      >
        <Ionicons
          name={icon as never}
          size={20}
          color={disabled ? storeLight.text.muted : storeLight.text.primary}
        />
        <View style={styles.body}>
          <Text style={[styles.label, disabled && styles.muted]} numberOfLines={1}>
            {label}
          </Text>
          <Text style={styles.subtitle} numberOfLines={2}>
            {subtitle}
          </Text>
        </View>
        {!disabled ? (
          <Ionicons name="chevron-forward" size={16} color={storeLight.text.muted} />
        ) : null}
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  wrap: { flex: 1 },
  tile: {
    flexDirection: "row",
    alignItems: "center",
    gap: 10,
    padding: storeLight.space.card,
    borderRadius: storeLight.radius.card,
    backgroundColor: storeLight.bg.card,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    minHeight: 64
  },
  tileDisabled: { opacity: 0.6 },
  body: { flex: 1, gap: 2 },
  label: { fontSize: 13, fontWeight: "700", color: storeLight.text.primary },
  muted: { color: storeLight.text.muted },
  subtitle: { fontSize: 11, color: storeLight.text.muted }
});
