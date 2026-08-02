/**
 * The horizontally scrolling category rail above the buying grid.
 *
 * The chips come from the caller, which derives them from the listings actually
 * returned — the brief's instruction to "drive from the real category taxonomy"
 * read strictly. A hardcoded rail of seven categories would show Garden to a
 * user whose city has never had a garden listing, and tapping it would produce
 * an empty state that looks like a bug rather than an accurate answer.
 *
 * "For you" is the exception and is synthesised: it is the unfiltered feed, and
 * it is always first so the way back is in a fixed place.
 *
 * The rail is a `tablist` for the same reason the mode toggle is: exactly one is
 * selected and choosing another replaces content in place. `accessibilityState`
 * carries the selection, so the filled navy chip is a visual echo of something
 * already announced rather than the only signal.
 */

import { useEffect, useRef } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { storeLight } from "../../theme/storeLight";
import { marketplaceLight } from "../../theme/marketplaceLight";

/** The unfiltered feed. Not a real category, so it gets a key nothing can collide with. */
export const CATEGORY_ALL = "__all__";

export type CategoryChip = {
  /** `CATEGORY_ALL`, or a category slug straight from the listing data. */
  key: string;
  label: string;
};

export type CategoryChipRailProps = {
  categories: readonly CategoryChip[];
  active: string;
  onChange: (key: string) => void;
};

export function CategoryChipRail({ categories, active, onChange }: CategoryChipRailProps) {
  const scroller = useRef<ScrollView>(null);
  const offsets = useRef<Record<string, number>>({});

  // Bring the selected chip into view when selection changes from outside the
  // rail — a summary chip tapping through to a filtered view, say. Without this
  // the rail can sit showing "For you" while the feed shows Furniture.
  useEffect(() => {
    const x = offsets.current[active];
    if (x == null) return;
    scroller.current?.scrollTo({ x: Math.max(0, x - 12), animated: true });
  }, [active]);

  return (
    <View style={styles.wrap}>
      <ScrollView
        ref={scroller}
        horizontal
        showsHorizontalScrollIndicator={false}
        contentContainerStyle={styles.row}
        accessibilityRole="tablist"
      >
        {categories.map((chip) => {
          const selected = chip.key === active;
          return (
            <Pressable
              key={chip.key}
              onLayout={(event) => {
                offsets.current[chip.key] = event.nativeEvent.layout.x;
              }}
              onPress={() => onChange(chip.key)}
              style={[styles.chip, selected && styles.chipActive]}
              accessibilityRole="tab"
              accessibilityState={{ selected }}
              accessibilityLabel={chip.label}
            >
              <Text style={[styles.label, selected && styles.labelActive]} numberOfLines={1}>
                {chip.label}
              </Text>
            </Pressable>
          );
        })}
      </ScrollView>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: {
    backgroundColor: storeLight.bg.page,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: storeLight.border.hairline
  },
  row: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: storeLight.space.card,
    paddingVertical: 8
  },
  chip: {
    minHeight: 34,
    paddingHorizontal: 14,
    borderRadius: storeLight.radius.pill,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: storeLight.border.hairline,
    backgroundColor: marketplaceLight.chip.bg,
    alignItems: "center",
    justifyContent: "center"
  },
  chipActive: {
    backgroundColor: marketplaceLight.chip.activeBg,
    borderColor: marketplaceLight.chip.activeBg
  },
  label: { fontSize: 13, fontWeight: "600", color: marketplaceLight.chip.text },
  labelActive: { color: marketplaceLight.chip.activeText }
});
