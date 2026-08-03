/**
 * The four-cell today strip — the third and last component this mission adds.
 *
 * It sits directly under the navy header and answers the question a seller opens
 * the app to ask: is anything waiting for me right now. Four numbers, and every
 * one of them is a link to the section that owns it, filtered to the thing the
 * number counts. A statistic the seller cannot act on belongs on Insights, not
 * here.
 *
 * The strip computes nothing. `todayStripCells` assembles the cells from
 * already-loaded owner values, including the decision that an unavailable cell
 * renders "—" and still links — because the section behind it can answer the
 * question even when the hub cannot summarise it.
 *
 * Each cell is its own accessibility element with an explicit link role and a
 * spoken destination, so "3" is heard as "3, To fulfil, opens Orders" rather
 * than as a loose number floating between two other loose numbers.
 */

import { Animated, Pressable, StyleSheet, Text, View } from "react-native";
import type { HubStripCell } from "../../api/businessHub";
import { hubLight } from "../../theme/hubLight";
import { useStorePress } from "../../theme/storeMotion";

export type TodayStripProps = {
  cells: HubStripCell[];
  onPressCell: (cell: HubStripCell) => void;
  reducedMotion: boolean;
};

export function TodayStrip({ cells, onPressCell, reducedMotion }: TodayStripProps) {
  return (
    <View style={styles.strip}>
      {cells.map((cell) => (
        <StripCell
          key={cell.key}
          cell={cell}
          onPress={() => onPressCell(cell)}
          reducedMotion={reducedMotion}
        />
      ))}
    </View>
  );
}

function StripCell({
  cell,
  onPress,
  reducedMotion
}: {
  cell: HubStripCell;
  onPress: () => void;
  reducedMotion: boolean;
}) {
  const press = useStorePress(reducedMotion, 0.96);
  const unavailable = cell.value === "—";

  return (
    <Animated.View style={[styles.cellWrap, press.style]}>
      <Pressable
        onPress={onPress}
        onPressIn={press.onPressIn}
        onPressOut={press.onPressOut}
        style={[styles.cell, cell.hot && styles.cellHot]}
        accessibilityRole="link"
        accessibilityLabel={
          unavailable
            ? `${cell.label}, not available. Opens ${cell.destination}.`
            : `${cell.value} ${cell.label}. Opens ${cell.destination}.`
        }
      >
        <Text
          style={[styles.value, cell.hot && styles.valueHot, unavailable && styles.valueMuted]}
          numberOfLines={1}
        >
          {cell.value}
        </Text>
        {/* Wraps rather than truncating: "Today's sales" must not become
            "Today's sa…" at large text sizes, or the number above it loses its
            subject. */}
        <Text style={styles.label}>{cell.label}</Text>
      </Pressable>
    </Animated.View>
  );
}

const styles = StyleSheet.create({
  strip: {
    flexDirection: "row",
    gap: 8,
    paddingHorizontal: hubLight.space.card
  },
  cellWrap: { flex: 1 },
  cell: {
    minHeight: hubLight.size.tapTarget + 12,
    backgroundColor: hubLight.bg.card,
    borderRadius: hubLight.radius.control,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: hubLight.border.hairline,
    paddingVertical: 8,
    paddingHorizontal: 8,
    gap: 2,
    justifyContent: "center"
  },
  cellHot: { borderColor: hubLight.urgent.border, backgroundColor: hubLight.bg.warning },
  value: { fontSize: 17, fontWeight: "800", color: hubLight.text.primary },
  valueHot: { color: hubLight.status.warning },
  valueMuted: { color: hubLight.text.muted },
  label: { fontSize: 10, fontWeight: "600", color: hubLight.text.muted, lineHeight: 13 }
});
