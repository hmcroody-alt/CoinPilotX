/**
 * One listing in the seller's list.
 *
 * Row anatomy, left to right: 64px thumbnail, title clamped to two lines,
 * optional star line, price, status LED with its label and the action it
 * implies, then a right column with trailing-7-day units and an Edit button.
 *
 * Two decisions worth naming:
 *
 * * **The star line is omitted, not faked.** No review aggregate exists in this
 *   API, so `rating` is null and the line does not render. A hardcoded 4.8 with
 *   a plausible review count would be indistinguishable from real data to
 *   everyone including the seller.
 * * **The title clamp is 2 lines at default text size and 3 when the OS font
 *   scale is enlarged.** Clamping to 2 regardless would cut a large-type user
 *   off mid-word on almost every row.
 */

import { Animated, Image, Pressable, StyleSheet, Text, View, useWindowDimensions } from "react-native";
import { storeLight } from "../../theme/storeLight";
import { useStorePress } from "../../theme/storeMotion";
import { StoreStatusLed } from "./StoreStatusLed";
import type { StoreListingHealth, StoreListingRow as StoreListingRowData } from "../../api/storeDashboard";

/**
 * Status copy and the action it implies, per health state.
 *
 * `label` is what appears beside the LED and is also what a screen reader
 * announces, which is why it is a sentence rather than a status code.
 */
export function listingStatusCopy(
  health: StoreListingHealth,
  quantity: number | null
): { label: string; action: string | null } {
  switch (health) {
    case "in_stock":
      return { label: quantity == null ? "Available" : `${quantity} in stock`, action: null };
    case "low_stock":
      return {
        label: quantity == null ? "Low stock" : `Only ${quantity} left`,
        action: "Add stock"
      };
    case "out_of_stock":
      return { label: "Out of stock — hidden", action: "Restock" };
    case "hidden":
      return { label: "Hidden from buyers", action: "Restock" };
    case "draft":
    default:
      return { label: "Draft — not published", action: "Finish listing" };
  }
}

export type StoreListingRowProps = {
  row: StoreListingRowData;
  /** Already formatted for the active locale. */
  priceText: string;
  /** Already formatted, e.g. "12 sold · 7d". Omitted when the listing sold none. */
  soldText: string | null;
  onPress: () => void;
  onEdit: () => void;
  onAction?: () => void;
  reducedMotion: boolean;
};

export function StoreListingRow({
  row,
  priceText,
  soldText,
  onPress,
  onEdit,
  onAction,
  reducedMotion
}: StoreListingRowProps) {
  const { fontScale } = useWindowDimensions();
  const rowPress = useStorePress(reducedMotion, 0.99);
  // The thumbnail grows on press rather than shrinking — the spec's one
  // deliberate inversion, so a tap reads as "look closer" rather than "pushed".
  const thumbPress = useStorePress(reducedMotion, 1.05);
  const editPress = useStorePress(reducedMotion, 0.96);

  const status = listingStatusCopy(row.health, row.quantity);
  const titleLines = fontScale > 1.15 ? 3 : 2;

  return (
    <Animated.View style={rowPress.style}>
      <Pressable
        style={styles.row}
        onPress={onPress}
        onPressIn={() => {
          rowPress.onPressIn();
          thumbPress.onPressIn();
        }}
        onPressOut={() => {
          rowPress.onPressOut();
          thumbPress.onPressOut();
        }}
        accessibilityRole="button"
        // Everything the row conveys visually, in one announcement, in reading
        // order: what it is, what it costs, whether it can be bought, and how
        // it is doing.
        accessibilityLabel={[row.title, priceText, status.label, soldText]
          .filter(Boolean)
          .join(", ")}
        accessibilityHint="Opens the listing"
      >
        <Animated.View style={thumbPress.style}>
          {row.thumbnailUrl ? (
            <Image source={{ uri: row.thumbnailUrl }} style={styles.thumb} />
          ) : (
            <View style={[styles.thumb, styles.thumbEmpty]}>
              <Text style={styles.thumbEmptyText}>{row.title.slice(0, 1).toUpperCase()}</Text>
            </View>
          )}
        </Animated.View>

        <View style={styles.body}>
          <Text style={styles.title} numberOfLines={titleLines}>
            {row.title}
          </Text>
          {row.rating != null ? (
            <Text style={styles.stars}>
              {"★".repeat(Math.round(row.rating))}
              <Text style={styles.reviewCount}> {row.reviewCount ?? 0}</Text>
            </Text>
          ) : null}
          {priceText ? <Text style={styles.price}>{priceText}</Text> : null}
          <View style={styles.statusRow}>
            <StoreStatusLed health={row.health} label={status.label} reducedMotion={reducedMotion} />
            {status.action && onAction ? (
              <Pressable
                onPress={onAction}
                hitSlop={8}
                accessibilityRole="link"
                accessibilityLabel={`${status.action} for ${row.title}`}
              >
                <Text style={styles.action}>{status.action}</Text>
              </Pressable>
            ) : null}
          </View>
        </View>

        <View style={styles.trailing}>
          {soldText ? (
            <Text style={styles.sold} numberOfLines={2}>
              {soldText}
            </Text>
          ) : null}
          <Animated.View style={editPress.style}>
            <Pressable
              style={styles.edit}
              onPress={onEdit}
              onPressIn={editPress.onPressIn}
              onPressOut={editPress.onPressOut}
              accessibilityRole="button"
              accessibilityLabel={`Edit ${row.title}`}
            >
              <Text style={styles.editText}>Edit</Text>
            </Pressable>
          </Animated.View>
        </View>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    gap: storeLight.space.gutter,
    paddingVertical: storeLight.space.card,
    paddingHorizontal: storeLight.space.card,
    backgroundColor: storeLight.bg.card,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: storeLight.border.hairline,
    // Comfortably above the 44pt minimum even with a one-line title.
    minHeight: 88
  },
  thumb: {
    width: storeLight.size.thumb,
    height: storeLight.size.thumb,
    borderRadius: storeLight.radius.thumb,
    backgroundColor: storeLight.bg.skeleton
  },
  thumbEmpty: { alignItems: "center", justifyContent: "center" },
  thumbEmptyText: { fontSize: 24, fontWeight: "700", color: storeLight.text.muted },
  body: { flex: 1, gap: 3 },
  title: { fontSize: 14, color: storeLight.text.primary, fontWeight: "600", lineHeight: 19 },
  stars: { fontSize: 12, color: storeLight.accent.star },
  reviewCount: { color: storeLight.text.link },
  price: { fontSize: 15, color: storeLight.text.primary, fontWeight: "700" },
  statusRow: { flexDirection: "row", alignItems: "center", gap: 10, flexWrap: "wrap", marginTop: 2 },
  action: { fontSize: 12, color: storeLight.text.link, fontWeight: "600" },
  trailing: { alignItems: "flex-end", justifyContent: "space-between", gap: 8, minWidth: 64 },
  sold: { fontSize: 11, color: storeLight.text.muted, textAlign: "right" },
  edit: {
    minWidth: 64,
    minHeight: storeLight.size.tapTarget,
    paddingHorizontal: 14,
    borderRadius: storeLight.radius.pill,
    borderWidth: 1,
    borderColor: storeLight.border.secondaryButton,
    backgroundColor: storeLight.bg.card,
    alignItems: "center",
    justifyContent: "center"
  },
  editText: { fontSize: 13, fontWeight: "600", color: storeLight.text.primary }
});
