/**
 * The days / hours / minutes countdown tiles on the next-event hero. Reads a
 * derived Countdown (from eventsManager) and renders whichever face it is in:
 *
 *   • "days"  → three unit tiles (days · hours · minutes)
 *   • "soon"  → a single "Starting soon" pill (inside the final hour)
 *   • "live"  → "Live now" (the hero hands off to the live banner)
 *   • "ended" → "Ended"
 *
 * The component never computes time itself — the phase and numbers are the
 * derivation layer's job, so the a11y sentence stays the single source.
 */

import { StyleSheet, Text, View } from "react-native";
import { eventsLight } from "../../theme/eventsLight";
import type { Countdown } from "../../api/eventsManager";

export function CountdownUnits({ countdown, onDark }: { countdown: Countdown; onDark?: boolean }) {
  if (countdown.phase !== "days") {
    return (
      <View style={styles.pill} accessibilityLabel={countdown.sentence}>
        <Text style={styles.pillText}>{countdown.short}</Text>
      </View>
    );
  }
  return (
    <View style={styles.row} accessibilityLabel={countdown.sentence}>
      <Unit value={countdown.days} label="days" onDark={onDark} />
      <Unit value={countdown.hours} label="hrs" onDark={onDark} />
      <Unit value={countdown.minutes} label="min" onDark={onDark} />
    </View>
  );
}

function Unit({ value, label, onDark }: { value: number; label: string; onDark?: boolean }) {
  return (
    <View style={[styles.tile, onDark ? styles.tileOnDark : null]} accessibilityElementsHidden importantForAccessibility="no-hide-descendants">
      <Text style={[styles.number, onDark ? styles.numberOnDark : null]}>{String(value).padStart(2, "0")}</Text>
      <Text style={[styles.unit, onDark ? styles.unitOnDark : null]}>{label}</Text>
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: "row", gap: 8 },
  tile: {
    minWidth: 52,
    paddingVertical: 6,
    paddingHorizontal: 8,
    borderRadius: eventsLight.radius.control,
    backgroundColor: eventsLight.countdown.tileBg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: eventsLight.countdown.tileBorder,
    alignItems: "center"
  },
  tileOnDark: {
    backgroundColor: "rgba(255,255,255,0.14)",
    borderColor: "rgba(255,255,255,0.22)"
  },
  number: { fontSize: 18, fontWeight: "800", color: eventsLight.countdown.number },
  numberOnDark: { color: eventsLight.cover.onCover },
  unit: { fontSize: 10, fontWeight: "700", color: eventsLight.countdown.unit, textTransform: "uppercase" },
  unitOnDark: { color: eventsLight.cover.onCoverMuted },
  pill: {
    alignSelf: "flex-start",
    paddingVertical: 5,
    paddingHorizontal: 12,
    borderRadius: eventsLight.radius.pill,
    backgroundColor: eventsLight.live.bannerBg,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: eventsLight.live.bannerBorder
  },
  pillText: { fontSize: 13, fontWeight: "800", color: eventsLight.live.label }
});
