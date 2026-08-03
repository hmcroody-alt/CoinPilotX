/**
 * The RSVP capacity bar. Reads a derived Capacity (from eventsManager) and paints
 * the fill: green under the threshold, amber once "nearly full" (>90%), red when
 * sold out. When capacity is unknown the derivation returns fill 0 and this
 * component hides the track entirely — a bar with no denominator is a lie.
 */

import { StyleSheet, Text, View } from "react-native";
import { eventsLight } from "../../theme/eventsLight";
import type { Capacity } from "../../api/eventsManager";

export function CapacityBar({ capacity }: { capacity: Capacity }) {
  const showBar = typeof capacity.capacity === "number" && capacity.capacity > 0;
  const fillColor = capacity.full
    ? eventsLight.capacity.full
    : capacity.nearlyFull
      ? eventsLight.capacity.nearlyFull
      : eventsLight.capacity.to;

  return (
    <View style={styles.wrap} accessibilityLabel={capacity.a11yLabel}>
      {showBar ? (
        <View style={styles.track}>
          <View style={[styles.fill, { width: `${Math.round(capacity.fill * 100)}%`, backgroundColor: fillColor }]} />
        </View>
      ) : null}
      <Text style={[styles.label, capacity.nearlyFull ? styles.labelAmber : null, capacity.full ? styles.labelFull : null]}>
        {capacity.spotsLabel}
        {capacity.nearlyFull ? " · Nearly full" : ""}
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  wrap: { gap: 4 },
  track: {
    height: 6,
    borderRadius: 3,
    backgroundColor: eventsLight.capacity.track,
    overflow: "hidden"
  },
  fill: { height: 6, borderRadius: 3 },
  label: { fontSize: 12, fontWeight: "700", color: eventsLight.text.muted },
  labelAmber: { color: eventsLight.capacity.nearlyFull },
  labelFull: { color: eventsLight.capacity.full }
});
