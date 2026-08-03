/**
 * One row of "top performers": rank, thumbnail, title, a meta line and revenue.
 *
 * Rank 1 takes gold; every other rank is a muted number. Gold means "the one",
 * and giving it to three rows in a row would mean nothing. The rank is also
 * announced in words ("Ranked first"), because a coloured numeral is not a
 * signal to anyone using a screen reader.
 *
 * The meta line is chosen by {@link itemMeta} in `insightsRules`, not here. Which
 * fact a row shows — sold out, low stock, delisted, order count — is a product
 * decision with a documented priority order, and it belongs somewhere testable
 * rather than inside a render.
 */

import { Animated, Image, Pressable, StyleSheet, Text, View } from "react-native";
import { Ionicons } from "@expo/vector-icons";
import { insightsLight, sourceColors } from "../../theme/insightsLight";
import { useStorePress } from "../../theme/storeMotion";
import type { ItemMeta } from "../../api/insightsRules";

export type RankedListingRowProps = {
  /** 1-based. Only 1 is gold. */
  rank: number;
  title: string;
  imageUrl: string | null;
  /** From `itemMeta` — text plus the tone that decides its colour. */
  meta: ItemMeta;
  /** e.g. "$420.00" — already formatted. */
  revenue: string;
  /** "store" or "marketplace"; tints the placeholder so the surface is legible. */
  source: "store" | "marketplace";
  /** Where tapping goes, for the label: "Opens the listing". */
  destinationHint: string;
  onPress: () => void;
  reducedMotion: boolean;
};

const ORDINALS = ["", "first", "second", "third", "fourth", "fifth"];

function ordinal(rank: number): string {
  return ORDINALS[rank] || `number ${rank}`;
}

export function RankedListingRow({
  rank,
  title,
  imageUrl,
  meta,
  revenue,
  source,
  destinationHint,
  onPress,
  reducedMotion
}: RankedListingRowProps) {
  const press = useStorePress(reducedMotion, 0.985);
  const gold = rank === 1;
  const tint = sourceColors(source);

  return (
    <Animated.View style={press.style}>
      <Pressable
        style={styles.row}
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        accessibilityRole="button"
        accessibilityLabel={`Ranked ${ordinal(rank)}. ${title}. ${meta.text}. ${revenue}. ${destinationHint}.`}
      >
        <Text
          style={[styles.rank, gold ? styles.rankGold : null]}
          accessibilityElementsHidden
          importantForAccessibility="no"
        >
          {rank}
        </Text>

        {imageUrl ? (
          <Image
            source={{ uri: imageUrl }}
            style={styles.thumb}
            accessibilityElementsHidden
            importantForAccessibility="no"
          />
        ) : (
          <View
            style={[styles.thumb, styles.thumbEmpty, { backgroundColor: `${tint.from}22` }]}
            accessibilityElementsHidden
            importantForAccessibility="no"
          >
            <Ionicons name="pricetag-outline" size={20} color={tint.to} />
          </View>
        )}

        <View style={styles.body}>
          <Text style={styles.title} numberOfLines={1}>
            {title}
          </Text>
          <Text
            style={[styles.meta, meta.tone === "warn" ? styles.metaWarn : null]}
            numberOfLines={1}
          >
            {meta.text}
          </Text>
        </View>

        <Text style={styles.revenue} numberOfLines={1}>
          {revenue}
        </Text>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: "row",
    alignItems: "center",
    gap: insightsLight.space.gutter,
    paddingVertical: 10,
    paddingHorizontal: insightsLight.space.card,
    minHeight: insightsLight.size.tapTarget + 24,
    backgroundColor: insightsLight.bg.card,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: insightsLight.border.hairline
  },
  rank: {
    width: 18,
    fontSize: 15,
    fontWeight: "800",
    color: insightsLight.text.muted,
    textAlign: "center"
  },
  rankGold: { color: insightsLight.rankGold },
  thumb: {
    width: insightsLight.size.thumb,
    height: insightsLight.size.thumb,
    borderRadius: insightsLight.radius.thumb,
    backgroundColor: insightsLight.bg.skeleton
  },
  thumbEmpty: { alignItems: "center", justifyContent: "center" },
  body: { flex: 1, gap: 3 },
  title: { fontSize: 14, fontWeight: "700", color: insightsLight.text.primary },
  meta: { fontSize: 12, color: insightsLight.text.muted },
  metaWarn: { color: insightsLight.status.warning, fontWeight: "700" },
  revenue: { fontSize: 14, fontWeight: "800", color: insightsLight.text.primary }
});
