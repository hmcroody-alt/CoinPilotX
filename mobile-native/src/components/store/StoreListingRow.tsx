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
    // Not "Out of stock". The shelf may be full; what is missing is the count,
    // and the fix is to enter one rather than to reorder from a supplier. Saying
    // "Out of stock" here sends a seller to solve a problem they do not have,
    // and is exactly what the old client did to every untracked listing.
    case "unknown_stock":
      return { label: "No stock count — buyers can't order", action: "Add stock count" };
    case "hidden":
      return { label: "Hidden from buyers", action: "Restock" };
    case "draft":
      return { label: "Draft — not published", action: "Finish listing" };
    default:
      // A state this build does not recognise. Say so rather than filing it
      // under Draft, which is what the previous `default` did and would have
      // mislabelled every future state as unpublished.
      return { label: "Needs attention", action: "Review listing" };
  }
}

/**
 * Merchant-facing phrasing for each blocker code, as an imperative the seller
 * can act on. §7 asks the row to state the exact remaining work, which means
 * "Add price", not "MISSING_PRICE" and not "incomplete".
 *
 * Keyed by the server's vocabulary
 * (`services/business_os/marketplace/listing_readiness.py`). A code with no
 * entry is deliberately *counted but not named* — see `listingRemainingCopy` —
 * because inventing a phrase for a code this build has never seen would put
 * words in the server's mouth.
 */
const BLOCKER_COPY: Record<string, string> = {
  MISSING_TITLE: "Add title",
  MISSING_CATEGORY: "Add category",
  NO_VALID_MEDIA: "Add photo",
  MISSING_PRICE: "Add price",
  RESTRICTED_PRODUCT: "Resolve restriction"
};

/**
 * "2 things left · Add price + photo" — §7.
 *
 * Returns `null` when there is nothing left *or* when no verdict arrived. Those
 * are different situations and both correctly render no line: the row must not
 * claim a listing is complete on the strength of a payload that never said so.
 *
 * The count comes from every blocker; only the ones this build can phrase are
 * listed. So an unrecognised code still shows up in "3 things left" even when it
 * cannot be named, which keeps the number honest rather than quietly shrinking
 * the work to what this build happens to understand.
 */
export function listingRemainingCopy(readiness: StoreListingRowData["readiness"]): string | null {
  if (!readiness || !Array.isArray(readiness.blockers)) return null;
  const blockers = readiness.blockers;
  if (blockers.length === 0) return null;
  const named = blockers.map((code) => BLOCKER_COPY[code]).filter(Boolean);
  const count = `${blockers.length} thing${blockers.length === 1 ? "" : "s"} left`;
  return named.length ? `${count} · ${named.join(" + ")}` : count;
}

/**
 * What the price line says — §12.
 *
 * A blank price used to render as nothing at all (GAP 23). That is safe from the
 * worse failure of printing "Free" or "$0.00" over an unpriced listing, but it
 * tells the seller nothing, and silence is indistinguishable from a row that
 * simply has no price element. So the gap gets a name.
 *
 * `required` drives the styling: this is the seller's own store, where an
 * unpriced listing is a task, not a fact about the product.
 */
export function listingPriceCopy(
  priceText: string,
  readiness: StoreListingRowData["readiness"]
): { text: string; required: boolean } | null {
  if (priceText) return { text: priceText, required: false };
  // Only on the server's say-so. Without a verdict the row cannot tell an
  // unpriced listing from a payload that omitted the field, and "Price required"
  // on a listing that has a price would be its own lie.
  if (readiness?.blockers?.includes("MISSING_PRICE")) {
    return { text: "Price required", required: true };
  }
  return null;
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
  const price = listingPriceCopy(priceText, row.readiness);
  const remaining = listingRemainingCopy(row.readiness);
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
        // "Price required" and "2 things left" are read out too. A seller using
        // VoiceOver gets the same task list a sighted seller sees, rather than
        // the silence the blank price used to leave behind.
        accessibilityLabel={[row.title, price?.text, remaining, status.label, soldText]
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
          {price ? (
            <Text style={[styles.price, price.required ? styles.priceRequired : null]}>
              {price.text}
            </Text>
          ) : null}
          {remaining ? <Text style={styles.remaining}>{remaining}</Text> : null}
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
  /**
   * "Price required" in the attention colour, not the price colour. It occupies
   * the price slot but it is a task, and styling it like a price would make an
   * unpriced listing read as priced at a glance.
   */
  priceRequired: { color: storeLight.status.warning },
  /** "2 things left · Add price + photo". Quieter than the price above it. */
  remaining: { fontSize: 12, color: storeLight.status.warning, marginTop: 1 },
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
