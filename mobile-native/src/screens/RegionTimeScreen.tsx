import { useEffect, useMemo, useState } from "react";
import { Pressable, ScrollView, StyleSheet, Text, View } from "react-native";
import { Screen } from "../components/Screen";
import { getDeviceTimeZone } from "../core/localTime";
import { useLocalTime, useTimeZonePreference } from "../core/TimeZoneContext";
import { colors } from "../theme/colors";

// A curated set of common IANA zones for manual override. Automatic detection
// still covers every zone the device reports; this list is only for the manual
// picker so users can pin a specific city when they choose to.
const COMMON_ZONES: { id: string; label: string }[] = [
  { id: "Pacific/Honolulu", label: "Honolulu" },
  { id: "America/Anchorage", label: "Anchorage" },
  { id: "America/Los_Angeles", label: "Los Angeles" },
  { id: "America/Denver", label: "Denver" },
  { id: "America/Chicago", label: "Chicago" },
  { id: "America/New_York", label: "New York" },
  { id: "America/Sao_Paulo", label: "São Paulo" },
  { id: "Europe/London", label: "London" },
  { id: "Europe/Paris", label: "Paris" },
  { id: "Africa/Lagos", label: "Lagos" },
  { id: "Europe/Moscow", label: "Moscow" },
  { id: "Asia/Dubai", label: "Dubai" },
  { id: "Asia/Kolkata", label: "Kolkata" },
  { id: "Asia/Singapore", label: "Singapore" },
  { id: "Asia/Tokyo", label: "Tokyo" },
  { id: "Australia/Sydney", label: "Sydney" },
  { id: "Pacific/Auckland", label: "Auckland" },
  { id: "UTC", label: "UTC" }
];

export function RegionTimeScreen() {
  const { timeZone, locale, automatic, manualTimeZone, setTimeZoneOverride } = useTimeZonePreference();
  const localTime = useLocalTime();
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const interval = setInterval(() => setNow(new Date()), 15_000);
    return () => clearInterval(interval);
  }, []);

  const deviceZone = getDeviceTimeZone();
  const sampleTime = useMemo(
    () => localTime.absolute(now, { withTime: true, withZoneName: true }),
    [localTime, now]
  );
  const zoneCityLabel = timeZone.split("/").pop()?.replace(/_/g, " ") || timeZone;

  return (
    <Screen
      title="Language, Region & Time"
      subtitle="PulseSOC shows every date and time in your local zone. Times adjust automatically as you travel."
    >
      <View style={styles.panel}>
        <Text style={styles.panelLabel}>Active time zone</Text>
        <Text style={styles.activeZone}>{zoneCityLabel}</Text>
        <Text style={styles.muted}>{timeZone}</Text>
        <Text style={styles.sample}>{sampleTime}</Text>
        <Text style={styles.mutedSmall}>
          {automatic ? `Automatic · following this device (${deviceZone})` : "Manual override active"}
        </Text>
        <Text style={styles.mutedSmall}>Locale {locale} · clock format follows your device settings</Text>
      </View>

      <View style={styles.panel}>
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ selected: automatic }}
          accessibilityLabel={`Automatic time zone${automatic ? ", selected" : ""}`}
          style={[styles.row, automatic && styles.rowSelected]}
          onPress={() => setTimeZoneOverride(null)}
        >
          <View style={styles.rowText}>
            <Text style={styles.rowTitle}>Automatic</Text>
            <Text style={styles.mutedSmall}>Use this device&apos;s time zone ({deviceZone})</Text>
          </View>
          {automatic ? <Text style={styles.check}>✓</Text> : null}
        </Pressable>
      </View>

      <Text style={styles.sectionHeading}>Set a specific time zone</Text>
      <View style={styles.panel}>
        <ScrollView style={styles.zoneList} nestedScrollEnabled>
          {COMMON_ZONES.map((zone) => {
            const selected = !automatic && manualTimeZone === zone.id;
            const zoneTime = localTime.absolute(now, { timeZone: zone.id, withTime: true });
            return (
              <Pressable
                key={zone.id}
                accessibilityRole="button"
                accessibilityState={{ selected }}
                accessibilityLabel={`${zone.label}, ${zoneTime}${selected ? ", selected" : ""}`}
                style={[styles.row, selected && styles.rowSelected]}
                onPress={() => setTimeZoneOverride(zone.id)}
              >
                <View style={styles.rowText}>
                  <Text style={styles.rowTitle}>{zone.label}</Text>
                  <Text style={styles.mutedSmall}>
                    {zone.id} · {zoneTime}
                  </Text>
                </View>
                {selected ? <Text style={styles.check}>✓</Text> : null}
              </Pressable>
            );
          })}
        </ScrollView>
      </View>
    </Screen>
  );
}

const styles = StyleSheet.create({
  panel: {
    backgroundColor: colors.surface,
    borderRadius: 18,
    borderWidth: StyleSheet.hairlineWidth,
    borderColor: colors.border,
    padding: 16,
    gap: 6,
    marginBottom: 14
  },
  panelLabel: { color: colors.muted, fontSize: 12, letterSpacing: 0.4, textTransform: "uppercase" },
  activeZone: { color: colors.text, fontSize: 22, fontWeight: "700" },
  sample: { color: colors.accent, fontSize: 16, fontWeight: "600", marginTop: 4 },
  muted: { color: colors.muted, fontSize: 13 },
  mutedSmall: { color: colors.muted, fontSize: 12 },
  sectionHeading: { color: colors.text, fontSize: 15, fontWeight: "700", marginBottom: 8, marginLeft: 4 },
  zoneList: { maxHeight: 360 },
  row: {
    flexDirection: "row",
    alignItems: "center",
    justifyContent: "space-between",
    paddingVertical: 12,
    paddingHorizontal: 6,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.border,
    gap: 12
  },
  rowSelected: { backgroundColor: colors.surfaceRaised, borderRadius: 12 },
  rowText: { flex: 1, gap: 2 },
  rowTitle: { color: colors.text, fontSize: 15, fontWeight: "600" },
  check: { color: colors.accent, fontSize: 18, fontWeight: "800" }
});
