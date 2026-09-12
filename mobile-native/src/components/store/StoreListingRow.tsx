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

/**
 * How this row participates in selection mode — §16–§20.
 *
 * `null` is the normal list: no checkbox, tapping opens the listing. Anything
 * else means the seller is picking rows, and the whole row becomes the target.
 */
export type StoreRowSelection = {
  selected: boolean;
  onToggle: () => void;
  /**
   * Why the pending bulk action cannot touch this row, or `null` if it can.
   *
   * A blocked row is still *selectable*. That is deliberate and it is the one
   * decision here most likely to be read as a bug: the obvious design makes a
   * blocked row unselectable, which quietly removes it from the seller's count
   * and turns "Publish 18" into a surprise at 14. Instead the row stays
   * pickable, wears the disabled wash, and says why — so the number on the
   * confirm button and the number of rows the seller ticked describe the same
   * set, and the shortfall is visible up front rather than in the result.
   */
  blockedReason: string | null;
};

export type StoreListingRowProps = {
  row: StoreListingRowData;
  /** Already formatted for the active locale. */
  priceText: string;
  /** Already formatted, e.g. "12 sold · 7d". Omitted when the listing sold none. */
  soldText: string | null;
  onPress: () => void;
  onEdit: () => void;
  onAction?: () => void;
  /** Enters selection mode without leaving the list — long-press on any row. */
  onLongPress?: () => void;
  /** Absent outside selection mode. */
  selection?: StoreRowSelection | null;
  reducedMotion: boolean;
};

/**
 * The tick box.
 *
 * Drawn rather than imported so the checked state is a *shape* (a tick) and not
 * only a fill: selection must survive a seller who cannot distinguish the green
 * from the white, which is the same reason `select.selectedBorder` is three
 * steps deeper than the brand green.
 */
function StoreRowCheckbox({ selected }: { selected: boolean }) {
  return (
    <View style={[styles.checkbox, selected ? styles.checkboxOn : null]}>
      {selected ? <Text style={styles.checkboxTick}>✓</Text> : null}
    </View>
  );
}

export function StoreListingRow({
  row,
  priceText,
  soldText,
  onPress,
  onEdit,
  onAction,
  onLongPress,
  selection,
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

  const selecting = !!selection;
  const blocked = selection?.blockedReason ?? null;

  return (
    <Animated.View style={rowPress.style}>
      <Pressable
        style={[
          styles.row,
          blocked ? styles.rowBlocked : null,
          selection?.selected ? styles.rowSelected : null
        ]}
        // In selection mode the whole row is the checkbox. Routing the tap to
        // the editor instead would make picking six listings a six-screen round
        // trip, and tapping a row you meant to tick and landing in an edit form
        // is the kind of thing that loses a half-built selection.
        onPress={selecting ? selection!.onToggle : onPress}
        onLongPress={onLongPress}
        onPressIn={() => {
          rowPress.onPressIn();
          thumbPress.onPressIn();
        }}
        onPressOut={() => {
          rowPress.onPressOut();
          thumbPress.onPressOut();
        }}
        accessibilityRole={selecting ? "checkbox" : "button"}
        // `selected` rather than `checked` is what iOS VoiceOver announces for a
        // row in a picking list; both are set so TalkBack reads the tick too.
        accessibilityState={
          selecting ? { selected: selection!.selected, checked: selection!.selected } : undefined
        }
        // Everything the row conveys visually, in one announcement, in reading
        // order: what it is, what it costs, whether it can be bought, and how
        // it is doing.
        // "Price required" and "2 things left" are read out too. A seller using
        // VoiceOver gets the same task list a sighted seller sees, rather than
        // the silence the blank price used to leave behind.
        // In selection mode the blocked reason joins them, because a seller who
        // cannot see the wash has no other way to learn this row will not move.
        accessibilityLabel={[row.title, price?.text, remaining, status.label, soldText, blocked]
          .filter(Boolean)
          .join(", ")}
        accessibilityHint={selecting ? undefined : "Opens the listing"}
      >
        {selecting ? <StoreRowCheckbox selected={selection!.selected} /> : null}

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
          {/* Why this row will not move, stated on the row itself rather than
              only in the confirm button's blocked count. "4 blocked" tells a
              seller how many; only this tells them which, and which is what
              they need to go fix. */}
          {blocked ? <Text style={styles.blockedReason}>{blocked}</Text> : null}
          <View style={styles.statusRow}>
            <StoreStatusLed health={row.health} label={status.label} reducedMotion={reducedMotion} />
            {/* The inline action navigates away, which would abandon a
                half-built selection. Suppressed while picking; the row's own
                status LED and label still render, so nothing is hidden. */}
            {status.action && onAction && !selecting ? (
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
          {selecting ? null : (
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
          )}
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
  /**
   * Selected. A left rule rather than a full border, because the row already
   * has a hairline underneath it and boxing every picked row turns a list of
   * six into six cards.
   */
  rowSelected: {
    backgroundColor: storeLight.select.selected,
    borderLeftWidth: 3,
    borderLeftColor: storeLight.select.selectedBorder,
    // Keeps the thumbnail aligned with unselected rows despite the new rule.
    paddingLeft: storeLight.space.card - 3
  },
  /**
   * Blocked for the pending action. Applied *under* `rowSelected`, so a
   * selected-and-blocked row reads as selected first — which is honest, because
   * it is in the seller's count.
   */
  rowBlocked: { backgroundColor: storeLight.select.disabled },
  checkbox: {
    width: 22,
    height: 22,
    borderRadius: 4,
    borderWidth: 2,
    borderColor: storeLight.border.secondaryButton,
    backgroundColor: storeLight.bg.card,
    alignItems: "center",
    justifyContent: "center",
    // Centred against the 64pt thumbnail beside it.
    alignSelf: "center"
  },
  checkboxOn: {
    borderColor: storeLight.select.selectedBorder,
    backgroundColor: storeLight.select.selectedBorder
  },
  checkboxTick: {
    color: storeLight.text.onDark,
    fontSize: 14,
    fontWeight: "900",
    lineHeight: 16
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
  /**
   * "No readiness check yet" / "1 thing left" — why the bulk action skips this
   * row. Its own colour, measured against the disabled wash rather than the
   * white card; see `storeLightContrast.test.ts` for why `status.warning` is
   * not reused here.
   */
  blockedReason: {
    fontSize: 12,
    fontWeight: "600",
    color: storeLight.select.disabledReason,
    marginTop: 1
  },
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
